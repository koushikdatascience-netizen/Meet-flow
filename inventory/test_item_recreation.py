import uuid
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import Company, Item, Membership, post_entry, reverse_entry


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ItemRecreationTests(TestCase):
    def setUp(self):
        self.company = Company.objects.get(is_primary=True)
        self.user = get_user_model().objects.create_user("item-admin", password="test")
        self.member = Membership.objects.create(
            user=self.user, company=self.company, role="admin"
        )
        self.client.force_login(self.user)

    def test_new_item_is_active_without_checkbox(self):
        self.assertRedirects(
            self.client.post("/items/", {"name": "Katla", "category": "Fish"}),
            "/items/",
        )
        self.assertTrue(Item.objects.get(name="Katla").active)
        self.assertContains(self.client.get("/items/"), "Katla")

    def test_removed_zero_stock_item_is_restored_without_duplicate_or_lost_history(
        self,
    ):
        item = Item.objects.create(company=self.company, name="Katla", category="Fish")
        entry = post_entry(
            self.company,
            self.user,
            "purchase",
            timezone.localdate(),
            "",
            uuid.uuid4(),
            [(item.pk, Decimal(10))],
        )
        reverse_entry(self.company, self.user, entry.pk, "Cleanup")
        self.client.post(f"/items/{item.pk}/remove/")
        self.assertRedirects(
            self.client.post("/items/", {"name": "katla", "category": "Fish"}),
            "/items/",
        )
        item.refresh_from_db()
        self.assertTrue(item.active)
        self.assertEqual(
            Item.objects.filter(company=self.company, name__iexact="katla").count(), 1
        )
        self.assertEqual(item.line_set.get().entry_id, entry.pk)
        self.assertEqual(self.client.get("/reports/").context["totals"][-1], 0)

    def test_active_duplicate_identifies_category_even_when_filtered_out(self):
        Item.objects.create(company=self.company, name="Katla", category="Fish")
        response = self.client.post(
            "/items/?category=Chicken", {"name": "Katla", "category": "Chicken"}
        )
        self.assertContains(response, "already exists in Fish")
        self.assertEqual(Item.objects.filter(name="Katla").count(), 1)

    def test_add_only_staff_cannot_restore_removed_item(self):
        item = Item.objects.create(
            company=self.company, name="Katla", category="Fish", active=False
        )
        self.member.role = "worker"
        self.member.features = ["items_add"]
        self.member.save()
        response = self.client.post("/items/", {"name": "Katla", "category": "Fish"})
        self.assertContains(response, "Ask an administrator to restore")
        item.refresh_from_db()
        self.assertFalse(item.active)

    def test_stock_modules_accept_new_entries_after_deletion(self):
        for kind in ("opening", "purchase", "sale"):
            with self.subTest(kind=kind):
                item = Item.objects.create(
                    company=self.company, name=f"Katla {kind}", category="Fish"
                )
                if kind == "sale":
                    post_entry(
                        self.company,
                        self.user,
                        "purchase",
                        timezone.localdate(),
                        "",
                        uuid.uuid4(),
                        [(item.pk, Decimal(20))],
                    )
                url = f"/entries/{kind}/"
                form = self.client.get(url)
                self.assertContains(form, f'data-id="{item.pk}"')
                self.assertRedirects(
                    self.client.post(
                        url,
                        {
                            "token": form.context["token"],
                            "item": item.pk,
                            "quantity": 5,
                        },
                    ),
                    url,
                )
                old = item.line_set.get(entry__kind=kind).entry
                self.assertRedirects(
                    self.client.post(f"/entries/{old.pk}/delete/"), url
                )
                form = self.client.get(url)
                self.assertRedirects(
                    self.client.post(
                        url,
                        {
                            "token": form.context["token"],
                            "item": item.pk,
                            "quantity": 7,
                        },
                    ),
                    url,
                )
                self.assertEqual(
                    item.line_set.filter(
                        entry__kind=kind, entry__reversed_at__isnull=True
                    )
                    .get()
                    .quantity,
                    7,
                )

    def test_deleted_username_is_explained_and_new_username_can_be_added(self):
        user = get_user_model().objects.create_user("old-staff", password="1234")
        member = Membership.objects.create(
            user=user, company=self.company, role="worker"
        )
        self.client.post(f"/users/{member.pk}/delete/")
        data = {
            "username": "old-staff",
            "temporary_password": "5678",
            "role": "worker",
            "active": "on",
            "features": ["purchase_add"],
        }
        self.assertContains(
            self.client.post("/users/", data), "reserved for its history"
        )
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        data["username"] = "new-staff"
        self.assertRedirects(self.client.post("/users/", data), "/users/")
        new = Membership.objects.get(user__username="new-staff")
        self.assertTrue(new.user.is_active)
        self.assertTrue(new.user.check_password("5678"))

    def test_disabled_user_can_be_edited_and_reactivated(self):
        user = get_user_model().objects.create_user(
            "disabled-staff", password="1234", is_active=False
        )
        member = Membership.objects.create(
            user=user, company=self.company, role="worker"
        )
        self.assertRedirects(
            self.client.post(
                f"/users/?edit={member.pk}",
                {
                    "username": user.username,
                    "role": "worker",
                    "active": "on",
                    "features": ["purchase_add"],
                },
            ),
            "/users/",
        )
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password("1234"))
