from decimal import Decimal
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .forms import UserForm
from .models import Company, Item, Membership, post_entry


class ActionPermissionTests(TestCase):
    def setUp(self):
        self.company = Company.objects.get(is_primary=True)
        self.user = get_user_model().objects.create_user("sam", password="1234")
        self.member = Membership.objects.create(user=self.user, company=self.company, role="worker", features=[])
        self.item = Item.objects.create(company=self.company, name="Chicken", category="Chicken")
        self.client.force_login(self.user)

    def grants(self, *features):
        self.member.features = list(features)
        self.member.save()

    def entry(self, kind="purchase"):
        return post_entry(self.company, self.user, kind, timezone.localdate(), "", uuid.uuid4(), [(self.item.pk, Decimal("10"))])

    def test_old_password_flag_is_ignored_and_password_cannot_be_changed_by_staff(self):
        self.member.must_change_password = True
        self.member.save()
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertRedirects(self.client.get("/password/"), "/")
        self.client.post("/password/", {"old_password": "1234", "new_password1": "5678", "new_password2": "5678"})
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("1234"))
        self.assertEqual(self.client.get("/users/").status_code, 403)

    def test_item_add_edit_delete_are_independent_including_direct_requests(self):
        self.grants("items_add")
        self.assertContains(self.client.get("/"), "Item Master")
        self.assertEqual(self.client.post("/items/", {"name": "Fish", "category": "Fish", "active": "on"}).status_code, 302)
        self.assertEqual(self.client.post(f"/items/?edit={self.item.pk}", {"name": "Changed", "category": "Chicken", "active": "on"}).status_code, 403)
        self.assertEqual(self.client.post(f"/items/{self.item.pk}/remove/").status_code, 403)
        self.grants("items_edit")
        self.assertNotContains(self.client.get("/items/"), "Save item")
        self.assertEqual(self.client.post(f"/items/?edit={self.item.pk}", {"name": "Chicken", "category": "Chicken"}).status_code, 403)
        self.assertEqual(self.client.post(f"/items/?edit={self.item.pk}", {"name": "Updated", "category": "Chicken", "active": "on"}).status_code, 302)
        self.grants("items_delete")
        self.assertEqual(self.client.post("/items/", {"name": "Mutton", "category": "Mutton", "active": "on"}).status_code, 403)
        self.assertEqual(self.client.post(f"/items/{self.item.pk}/remove/").status_code, 302)

    def test_each_stock_module_can_have_edit_or_delete_without_add(self):
        for kind in ("opening", "purchase", "sale"):
            # Separate items keep the opening-first and stock constraints meaningful.
            self.item = Item.objects.create(company=self.company, name=f"Item {kind}", category="Others")
            if kind == "sale":
                self.entry("purchase")
            entry = self.entry(kind)
            url = f"/entries/{kind}/"
            self.grants(f"{kind}_edit")
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'id="entry-form"')
            self.assertEqual(self.client.post(url, {}).status_code, 403)
            edit = self.client.get(f"{url}?edit={entry.pk}")
            self.assertEqual(edit.status_code, 200)
            result = self.client.post(f"{url}?edit={entry.pk}", {"token": edit.context["token"], "version": 0, "item": self.item.pk, "quantity": 9})
            self.assertEqual(result.status_code, 302)
            self.assertEqual(self.client.post(f"/entries/{entry.pk}/delete/").status_code, 403)
            self.grants(f"{kind}_delete")
            self.assertEqual(self.client.get(f"{url}?edit={entry.pk}").status_code, 403)
            self.assertEqual(self.client.post(f"/entries/{entry.pk}/delete/").status_code, 302)

    def test_purchase_grants_do_not_enable_other_modules(self):
        self.grants("purchase_add", "purchase_edit", "purchase_delete")
        self.assertEqual(self.client.get("/entries/purchase/").status_code, 200)
        for path in ("/items/", "/entries/opening/", "/entries/sale/", "/reports/", "/categories/"):
            self.assertEqual(self.client.get(path).status_code, 403)

    def test_reports_view_and_download_can_be_selected_independently(self):
        self.grants("reports_view")
        self.assertContains(self.client.get("/reports/"), "Daily stock sheet")
        self.assertNotContains(self.client.get("/reports/"), 'value="csv"')
        self.assertEqual(self.client.get("/reports/?format=csv").status_code, 403)
        self.grants("reports_download")
        self.assertContains(self.client.get("/reports/"), "Download stock report")
        self.assertNotContains(self.client.get("/reports/"), "Daily stock sheet")
        self.assertEqual(self.client.get("/reports/?format=csv")["Content-Type"], "text/csv; charset=utf-8")
        self.grants("reports_view", "reports_download")
        self.assertContains(self.client.get("/reports/"), 'value="csv"')

    def test_password_policy_and_multi_select_are_saved_without_forcing_password_change(self):
        self.member.role = "admin"
        self.member.save()
        response = self.client.post("/users/", {"username": "jo", "temporary_password": "1234", "role": "worker", "active": "on", "features": ["items_add", "purchase_delete", "reports_view"]})
        self.assertEqual(response.status_code, 302)
        created = Membership.objects.get(user__username="jo")
        self.assertFalse(created.must_change_password)
        self.assertEqual(created.features, ["items_add", "purchase_delete", "reports_view"])
        self.assertTrue(created.user.check_password("1234"))
        form = UserForm({"username": "adm", "role": "admin", "temporary_password": "1234"})
        self.assertFalse(form.is_valid())
        form = UserForm({"username": "adm", "role": "admin", "temporary_password": "12345678"})
        self.assertTrue(form.is_valid(), form.errors)
        self.client.logout()
        self.assertTrue(self.client.login(username="jo", password="1234"))
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_upgrade_clears_existing_prompts_and_preserves_exact_legacy_access(self):
        from importlib import import_module
        from django.apps import apps

        self.member.features = ["purchase", "edit", "reports"]
        self.member.must_change_password = True
        self.member.save()
        original_hash = self.user.password
        import_module("inventory.migrations.0014_staff_actions_and_passwords").update_memberships(apps, None)
        self.member.refresh_from_db()
        self.user.refresh_from_db()
        self.assertFalse(self.member.must_change_password)
        self.assertEqual(self.member.features, ["purchase_add", "purchase_edit", "reports_view", "reports_download"])
        self.assertEqual(self.user.password, original_hash)
        self.assertNotIn("purchase_delete", self.member.allowed_features)
        self.assertNotIn("items_add", self.member.allowed_features)
