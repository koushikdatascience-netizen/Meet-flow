import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import Company, Entry, Item, Membership, post_entry


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class DeleteFlowTests(TestCase):
    def setUp(self):
        self.company = Company.objects.get(is_primary=True)
        self.admin = get_user_model().objects.create_user(
            "delete-admin", password="test"
        )
        self.member = Membership.objects.create(
            user=self.admin, company=self.company, role="admin"
        )
        self.client.force_login(self.admin)

    def entry(self, item, kind, quantity=10, actor=None):
        return post_entry(
            self.company,
            actor or self.admin,
            kind,
            timezone.localdate(),
            "",
            uuid.uuid4(),
            [(item.pk, Decimal(quantity))],
        )

    def test_user_delete_requires_confirmation_disables_login_and_preserves_history(
        self,
    ):
        user = get_user_model().objects.create_user("departed", password="test")
        member = Membership.objects.create(
            user=user, company=self.company, role="worker"
        )
        item = Item.objects.create(
            company=self.company, name="History", category="Chicken"
        )
        entry = self.entry(item, "purchase", actor=user)
        url = f"/users/{member.pk}/delete/"
        self.assertContains(self.client.get("/users/"), url)
        self.assertContains(self.client.get(url), "Yes, delete")
        self.assertTrue(Membership.objects.filter(pk=member.pk).exists())
        self.assertRedirects(self.client.post(url), "/users/")
        self.assertFalse(Membership.objects.filter(pk=member.pk).exists())
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertEqual(Entry.objects.get(pk=entry.pk).created_by_id, user.pk)
        self.assertFalse(self.client.login(username="departed", password="test"))

    def test_user_delete_rejects_self_owner_staff_and_foreign_members(self):
        self.assertEqual(
            self.client.post(f"/users/{self.member.pk}/delete/").status_code, 403
        )
        owner = get_user_model().objects.create_superuser("owner", password="test")
        owner_member = Membership.objects.create(
            user=owner, company=self.company, role="admin"
        )
        self.assertEqual(
            self.client.post(f"/users/{owner_member.pk}/delete/").status_code, 403
        )
        other = Company.objects.create(name="Other")
        foreign = Membership.objects.create(
            user=get_user_model().objects.create_user("foreign"), company=other
        )
        self.assertEqual(
            self.client.post(f"/users/{foreign.pk}/delete/").status_code, 404
        )
        self.member.role = "worker"
        self.member.save()
        self.assertEqual(
            self.client.post(f"/users/{owner_member.pk}/delete/").status_code, 403
        )

    def test_item_delete_leaves_main_list_and_can_be_restored_by_edit(self):
        item = Item.objects.create(
            company=self.company, name="Removable", category="Chicken"
        )
        self.entry(item, "purchase")
        url = f"/items/{item.pk}/remove/"
        self.assertContains(self.client.get(url), "Yes, remove")
        self.assertTrue(Item.objects.get(pk=item.pk).active)
        self.assertRedirects(self.client.post(url), "/items/")
        self.assertNotIn(item, self.client.get("/items/").context["items"])
        self.assertIn(item, self.client.get("/items/?removed=1").context["items"])
        self.assertRedirects(
            self.client.post(
                f"/items/?edit={item.pk}",
                {"name": "Restored", "category": "Chicken", "active": "on"},
            ),
            "/items/",
        )
        item.refresh_from_db()
        self.assertTrue(item.active)
        self.assertEqual(item.name, "Restored")
        self.assertEqual(item.line_set.count(), 1)

    def test_all_stock_modules_edit_delete_and_keep_history_out_of_active_list(self):
        for kind in ("opening", "purchase", "sale"):
            with self.subTest(kind=kind):
                item = Item.objects.create(
                    company=self.company, name=f"Flow {kind}", category="Chicken"
                )
                if kind == "sale":
                    self.entry(item, "purchase", 20)
                entry = self.entry(item, kind)
                url = f"/entries/{kind}/"
                edit_url = f"{url}?edit={entry.pk}"
                form = self.client.get(edit_url)
                self.assertContains(form, "Save changes")
                self.assertRedirects(
                    self.client.post(
                        edit_url,
                        {
                            "token": form.context["token"],
                            "version": entry.version,
                            "item": item.pk,
                            "quantity": 9,
                        },
                    ),
                    url,
                )
                self.assertEqual(entry.lines.get().quantity, 9)
                delete_url = f"/entries/{entry.pk}/delete/"
                self.assertContains(self.client.get(delete_url), "Yes, delete")
                entry.refresh_from_db()
                self.assertIsNone(entry.reversed_at)
                self.assertRedirects(self.client.post(delete_url), url)
                self.assertNotIn(
                    entry, self.client.get(url).context["entries"].object_list
                )
                self.assertIn(
                    entry,
                    self.client.get(url + "?deleted=1").context["entries"].object_list,
                )

    def test_blocked_stock_delete_shows_error_and_keeps_entry(self):
        item = Item.objects.create(
            company=self.company, name="Dependent", category="Chicken"
        )
        purchase = self.entry(item, "purchase")
        self.entry(item, "sale", 7)
        response = self.client.post(f"/entries/{purchase.pk}/delete/")
        self.assertContains(response, "This change would make stock negative")
        purchase.refresh_from_db()
        self.assertIsNone(purchase.reversed_at)
