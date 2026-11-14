import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from .models import Company, Entry, Item, Membership, post_entry, reverse_entry


class StockTests(TestCase):
    def setUp(self):
        self.company = Company.objects.get(is_primary=True)
        self.company.name = "A"
        self.company.save(update_fields=["name"])
        self.other = Company.objects.create(name="B")
        self.user = get_user_model().objects.create_user(
            "owner", password="test-password"
        )
        self.member = Membership.objects.create(
            user=self.user,
            company=self.company,
            role="admin",
            must_change_password=False,
        )
        self.item = Item.objects.create(
            company=self.company, name="Chicken", category="Chicken"
        )
        self.foreign = Item.objects.create(company=self.other, name="Chicken")
        self.client.force_login(self.user)
        self.today = timezone.localdate()

    def post(self, kind, qty, day=None, item=None, token=None):
        return post_entry(
            self.company,
            self.user,
            kind,
            day or self.today,
            "",
            token or uuid.uuid4(),
            [(item or self.item).pk and ((item or self.item).pk, Decimal(qty))],
        )

    def test_daily_stock_sheet_and_csv(self):
        self.post("opening", "10", self.today - timedelta(days=1))
        self.post("purchase", "5")
        self.post("sale", "3")
        response = self.client.get("/reports/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["rows"][0][2:], [10, 5, 15, 3, 12])
        self.assertNotContains(response, "Opening added")
        csv = self.client.get("/reports/?format=csv").content.decode("utf-8-sig")
        self.assertIn("Opening stock", csv)
        self.assertIn("Chicken", csv)
        self.assertNotIn("Amount", csv)

    def test_report_date_category_item_filters_and_csv(self):
        yesterday = self.today - timedelta(days=1)
        self.post("opening", "10", yesterday)
        self.post("purchase", "5")
        self.post("sale", "3")
        Item.objects.create(company=self.company, name="Fish", category="Fish")
        filters = {"date": yesterday.isoformat(), "category": "Chicken", "item": str(self.item.pk)}
        response = self.client.get("/reports/", filters)
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertEqual(response.context["rows"][0], [yesterday, "Chicken", 10, 0, 10, 0, 10])
        self.assertContains(response, 'name="date"')
        self.assertContains(response, 'name="item"')
        csv = self.client.get("/reports/", {**filters, "format": "csv"}).content.decode("utf-8-sig")
        self.assertIn(f"{yesterday},Chicken,10.000,0,10.000,0,10.000", csv)
        self.assertNotIn("Fish", csv)
        response = self.client.get("/reports/", {"item": str(self.foreign.pk), "date": "invalid"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_item"], "")
        self.assertEqual(response.context["start"], self.today)
        self.assertNotIn(self.foreign, response.context["filter_items"])

    def test_simple_forms_have_no_notes_and_delete_needs_only_confirmation(self):
        for kind in ["purchase", "sale", "opening"]:
            self.assertNotContains(
                self.client.get("/entries/" + kind + "/"), 'name="note"'
            )
        response = self.client.get("/entries/purchase/")
        payload = {
            "token": response.context["token"],
            "item": [str(self.item.pk)],
            "quantity": ["5"],
            "note": "Ignored text",
        }
        self.assertEqual(
            self.client.post("/entries/purchase/", payload).status_code, 302
        )
        entry = Entry.objects.get(company=self.company)
        self.assertEqual(entry.note, "")
        page = self.client.get(f"/entries/{entry.pk}/delete/")
        self.assertNotContains(page, 'name="reason"')
        self.assertContains(page, "Yes, delete")
        self.assertEqual(
            self.client.post(f"/entries/{entry.pk}/delete/").status_code, 302
        )
        entry.refresh_from_db()
        self.assertEqual(entry.reversed_by, self.user)
        self.assertEqual(entry.reversal_reason, "Deleted by user confirmation")
        self.assertEqual(self.client.get("/reports/").context["totals"][-1], 0)

    def test_home_category_card_lists_fixed_categories_and_filters_items(self):
        Item.objects.create(company=self.company, name="Fish item", category="Fish")
        self.assertContains(self.client.get("/"), "Browse categories")
        response = self.client.get("/categories/")
        self.assertEqual(
            [row["name"] for row in response.context["categories"]],
            ["Chicken", "Mutton", "Fish", "Others"],
        )
        self.assertEqual(response.context["categories"][2]["count"], 1)
        filtered = self.client.get("/items/?category=Fish")
        self.assertEqual(
            list(filtered.context["items"].values_list("name", flat=True)),
            ["Fish item"],
        )
        self.assertEqual(filtered.context["form"].initial["category"], "Fish")
        self.member.role = "worker"
        self.member.save()
        self.assertEqual(self.client.get("/categories/").status_code, 403)
        self.assertNotContains(self.client.get("/"), "Browse categories")

    def test_report_totals_and_remaining_stock_match_saved_movements(self):
        self.post("opening", "20", self.today - timedelta(days=1))
        self.post("sale", "4", self.today - timedelta(days=1))
        self.post("purchase", "8")
        self.post("sale", "7")
        second = Item.objects.create(
            company=self.company, name="Fish pieces", category="Fish"
        )
        self.post("opening", "10", item=second)
        purchase = self.post("purchase", "5", item=second)
        self.post("sale", "2", item=second)
        response = self.client.get("/reports/")
        self.assertEqual(
            response.context["headers"],
            [
                "Date",
                "Item",
                "Opening stock",
                "Purchase",
                "Total",
                "Sale",
                "Closing stock",
            ],
        )
        self.assertEqual(response.context["totals"], [26, 13, 39, 9, 30])
        for row in response.context["rows"]:
            item = Item.objects.get(company=self.company, name=row[1])
            balance = sum(
                (-line.quantity if line.entry.kind == "sale" else line.quantity)
                for line in item.line_set.filter(
                    entry__reversed_at__isnull=True
                ).select_related("entry")
            )
            self.assertEqual(row[-1], balance)
            self.assertEqual(row[2] + row[3], row[4])
            self.assertEqual(row[4] - row[5], row[6])
        filtered = self.client.get("/reports/?category=Fish")
        self.assertEqual(filtered.context["totals"], [10, 5, 15, 2, 13])
        reverse_entry(self.company, self.user, purchase.pk, "Duplicate purchase")
        self.assertEqual(self.client.get("/reports/").context["totals"][-1], 25)

    def test_dates_are_automatic_and_cannot_be_overridden(self):
        response = self.client.get("/entries/purchase/")
        self.assertNotContains(response, 'type="date"')
        data = {
            "token": response.context["token"],
            "item": [str(self.item.pk)],
            "quantity": ["5"],
            "date": "2000-01-01",
        }
        self.assertEqual(self.client.post("/entries/purchase/", data).status_code, 302)
        self.assertEqual(Entry.objects.get(company=self.company).date, self.today)
        response = self.client.get("/reports/?start=2000-01-01&end=2000-01-02")
        self.assertEqual(response.context["start"], self.today)
        self.assertEqual(response.context["end"], self.today)
        self.assertContains(response, 'type="date"')
        self.assertEqual(response.context["rows"][0][-1], 5)

    def test_opening_includes_purchases_and_closing_carries_to_following_days(self):
        start = self.today - timedelta(days=2)
        self.post("opening", "10", start)
        self.post("purchase", "5", start)
        sale = self.post("sale", "3", start)
        self.post("purchase", "4", start + timedelta(days=1))
        self.post("sale", "6", start + timedelta(days=1))
        url = f"/reports/?start={start}&end={self.today}"

        def daily_rows():
            from unittest.mock import patch

            result = []
            for offset in range(3):
                with patch(
                    "inventory.views.timezone.localdate",
                    return_value=start + timedelta(days=offset),
                ):
                    result.extend(self.client.get("/reports/").context["rows"])
            return result

        rows = daily_rows()
        self.assertEqual(
            [row[2:] for row in rows],
            [[10, 5, 15, 3, 12], [12, 4, 16, 6, 10], [10, 0, 10, 0, 10]],
        )
        self.assertEqual(
            self.client.get("/reports/").context["rows"][0][2:], [10, 0, 10, 0, 10]
        )
        self.post("purchase", "2", start)
        rows = daily_rows()
        self.assertEqual([row[4] for row in rows], [17, 18, 12])
        self.assertEqual([row[-1] for row in rows], [14, 12, 12])
        reverse_entry(self.company, self.user, sale.pk, "Duplicate sale")
        rows = daily_rows()
        self.assertEqual([row[-1] for row in rows], [17, 15, 15])
        import csv
        from io import StringIO

        exported = list(
            csv.reader(
                StringIO(
                    self.client.get(url + "&format=csv").content.decode("utf-8-sig")
                )
            )
        )
        self.assertEqual(len(exported[0]), 7)
        self.assertNotIn("Opening added", exported[0])
        self.assertEqual([Decimal(value) for value in exported[1][2:]], rows[-1][2:])

    def test_cross_tenant_items_and_entry_ids_are_rejected(self):
        with self.assertRaises(Item.DoesNotExist):
            self.post("purchase", "1", item=self.foreign)
        foreign_entry = Entry.objects.create(
            company=self.other,
            kind="opening",
            created_by=self.user,
            token=uuid.uuid4(),
            date=self.today,
        )
        self.assertEqual(
            self.client.get(f"/entries/{foreign_entry.pk}/delete/").status_code, 404
        )
        self.assertEqual(
            self.client.get(f"/items/?edit={self.foreign.pk}").status_code, 404
        )

    def test_negative_stock_and_backdated_changes_roll_back(self):
        with self.assertRaises(ValidationError):
            self.post("sale", "1")
        self.assertEqual(Entry.objects.filter(company=self.company).count(), 0)
        opening = self.post("opening", "10", self.today - timedelta(days=2))
        self.post("sale", "8", self.today - timedelta(days=1))
        self.post("purchase", "10")
        with self.assertRaises(ValidationError):
            reverse_entry(self.company, self.user, opening.pk, "wrong entry")
        opening.refresh_from_db()
        self.assertIsNone(opening.reversed_at)

    def test_delete_sale_restores_stock_and_keeps_audit(self):
        self.post("opening", "10")
        sale = self.post("sale", "3")
        reverse_entry(self.company, self.user, sale.pk, "Duplicate")
        sale.refresh_from_db()
        self.assertEqual(sale.reversed_by, self.user)
        self.assertEqual(sale.reversal_reason, "Duplicate")
        self.assertEqual(self.client.get("/reports/").context["rows"][0][-1], 10)

    def test_opening_must_be_first_and_piece_quantity_whole(self):
        self.post("purchase", "2")
        with self.assertRaises(ValidationError):
            self.post("opening", "1")
        self.item.unit = "pc"
        self.item.save()
        with self.assertRaises(ValidationError):
            self.post("purchase", "0.5")

    def test_items_use_pieces_and_reject_fractional_entry(self):
        from .forms import ItemForm
        from django.db import IntegrityError, transaction

        self.assertNotIn("unit", ItemForm().fields)
        self.assertEqual(self.item.unit, "pc")
        data = {
            "name": "Piece item",
            "category": "Chicken",
            "unit": "kg",
            "active": "on",
        }
        self.assertEqual(self.client.post("/items/", data).status_code, 302)
        self.assertEqual(Item.objects.get(name="Piece item").unit, "pc")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Item.objects.create(company=self.company, name="Bad unit", unit="kg")
        response = self.client.get("/entries/purchase/")
        response = self.client.post(
            "/entries/purchase/",
            {
                "token": response.context["token"],
                "date": str(self.today),
                "item": [str(self.item.pk)],
                "quantity": ["1.5"],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "whole numbers")
        self.assertFalse(Entry.objects.filter(company=self.company).exists())
        self.post("purchase", "3")
        row = self.client.get("/reports/").context["rows"][0]
        self.assertEqual(len(row), 7)
        self.assertEqual(row[-1], 3)

    def test_opening_has_no_notes_and_accepts_digits_only(self):
        response = self.client.get("/entries/opening/")
        self.assertNotContains(response, 'name="note"')
        self.assertContains(response, 'inputmode="numeric"')
        self.assertContains(response, 'placeholder="Pieces"')
        token = response.context["token"]
        data = {
            "token": token,
            "date": str(self.today),
            "item": [str(self.item.pk)],
            "quantity": ["2.5"],
            "note": "Should be ignored",
        }
        for invalid in ["2.5", "2.0", "1e2", "-2"]:
            data["quantity"] = [invalid]
            response = self.client.post("/entries/opening/", data)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Decimals are not allowed")
        self.assertFalse(Entry.objects.filter(company=self.company).exists())
        data["quantity"] = ["12"]
        self.assertEqual(self.client.post("/entries/opening/", data).status_code, 302)
        entry = Entry.objects.get(company=self.company)
        self.assertEqual(entry.note, "")
        self.assertEqual(entry.lines.get().quantity, 12)

    def test_token_is_idempotent(self):
        token = uuid.uuid4()
        first = self.post("purchase", "2", token=token)
        second = self.post("purchase", "2", token=token)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.lines.count(), 1)

    def test_viewer_cannot_write_or_manage_users(self):
        self.member.role = "viewer"
        self.member.save()
        for path in [
            "/items/",
            "/users/",
            "/entries/purchase/",
            "/entries/sale/",
            "/entries/opening/",
        ]:
            self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.get("/reports/").status_code, 200)

    def test_password_change_required_and_legacy_routes_closed(self):
        self.member.must_change_password = True
        self.member.save()
        self.assertRedirects(self.client.get("/reports/"), "/password/")
        self.assertEqual(self.client.get("/admin/").status_code, 403)
        self.assertEqual(self.client.get("/invoice/1/").status_code, 404)

    def test_user_creation_cannot_escape_company_or_escalate(self):
        response = self.client.post(
            "/users/",
            {
                "username": "new-user",
                "temporary_password": "Long-random-password-948!",
                "role": "viewer",
                "active": "on",
                "company": self.other.pk,
                "is_superuser": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(username="new-user")
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_staff)
        self.assertEqual(user.membership.company, self.company)
        self.assertTrue(user.membership.must_change_password)

    def test_category_filter_and_csv_formula_escape(self):
        Item.objects.create(company=self.company, name="=1+1", category="Others")
        response = self.client.get("/reports/?category=Others&format=csv")
        self.assertIn("'=1+1", response.content.decode("utf-8-sig"))
        self.assertNotIn("Chicken", response.content.decode())

    def test_item_category_is_required_dropdown_with_four_choices(self):
        from django import forms
        from .forms import ItemForm

        form = ItemForm()
        self.assertIsInstance(form.fields["category"].widget, forms.Select)
        self.assertEqual(
            [value for value, label in form.fields["category"].choices if value],
            ["Chicken", "Mutton", "Fish", "Others"],
        )
        for category in ["Meat", "Beef", "", "chicken"]:
            invalid = ItemForm(
                data={
                    "name": "New item",
                    "category": category,
                    "unit": "kg",
                    "active": "on",
                }
            )
            self.assertFalse(invalid.is_valid())
            self.assertIn("category", invalid.errors)
        data = {"name": "New item", "category": "Fish", "unit": "kg", "active": "on"}
        self.assertEqual(self.client.post("/items/", data).status_code, 302)
        self.assertEqual(Item.objects.get(name="New item").category, "Fish")
        self.assertEqual(
            self.client.get("/reports/").context["categories"],
            ["Chicken", "Mutton", "Fish", "Others"],
        )

    def test_http_entry_submission_and_retry(self):
        response = self.client.get("/entries/purchase/")
        data = {
            "token": response.context["token"],
            "date": str(self.today),
            "item": [str(self.item.pk)],
            "quantity": ["4"],
            "note": "",
        }
        self.assertEqual(self.client.post("/entries/purchase/", data).status_code, 302)
        self.assertEqual(self.client.post("/entries/purchase/", data).status_code, 302)
        self.assertEqual(Entry.objects.filter(company=self.company).count(), 1)

    def test_feature_permissions_are_enforced_on_post(self):
        self.member.role = "operator"
        self.member.features = ["purchase"]
        self.member.save()
        self.assertEqual(self.client.get("/entries/purchase/").status_code, 200)
        self.assertEqual(self.client.post("/entries/sale/", {}).status_code, 403)
        self.assertEqual(self.client.get("/reports/?format=csv").status_code, 403)
        home = self.client.get("/")
        self.assertContains(home, "Add or manage purchases")
        self.assertNotContains(home, "Add or manage sales")
        self.assertNotContains(home, 'href="/reports/"')

    def test_home_and_sidebar_follow_role_and_item_removal_preserves_history(self):
        self.assertContains(self.client.get("/"), 'aria-label="Main navigation"')
        self.assertContains(self.client.get("/"), "Add, edit or remove items")
        entry = self.post("purchase", "4")
        self.assertEqual(
            self.client.post(f"/items/{self.item.pk}/remove/").status_code, 302
        )
        self.item.refresh_from_db()
        self.assertFalse(self.item.active)
        self.assertEqual(entry.lines.get().quantity, 4)
        self.assertEqual(self.client.get("/reports/").context["rows"][0][-1], 4)
        self.member.role = "viewer"
        self.member.save()
        home = self.client.get("/")
        self.assertContains(home, "Open stock sheet")
        self.assertNotContains(home, "Add or manage purchases")
        self.assertNotContains(home, 'href="/users/"')
        self.assertEqual(
            self.client.post(f"/items/{self.item.pk}/remove/").status_code, 403
        )

    def test_password_change_unlocks_account(self):
        self.member.must_change_password = True
        self.member.save()
        response = self.client.post(
            "/password/",
            {
                "old_password": "test-password",
                "new_password1": "Different-good-password-824!",
                "new_password2": "Different-good-password-824!",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.member.refresh_from_db()
        self.assertFalse(self.member.must_change_password)

    def test_admin_cannot_edit_platform_owner(self):
        owner = get_user_model().objects.create_superuser(
            "platform", password="platform-password"
        )
        membership = Membership.objects.create(
            user=owner, company=self.company, role="admin"
        )
        self.assertEqual(
            self.client.get(f"/users/?edit={membership.pk}").status_code, 403
        )

    def test_inactive_company_and_receipt_isolation(self):
        entry = self.post("purchase", "1")
        self.assertContains(self.client.get(f"/document/{entry.pk}/"), "00")
        self.assertNotContains(self.client.get(f"/document/{entry.pk}/"), "price")
        self.member.company = self.other
        self.member.save()
        self.assertEqual(self.client.get(f"/document/{entry.pk}/").status_code, 403)
        self.member.company = self.company
        self.member.save()
        self.company.active = False
        self.company.save(update_fields=["active"])
        self.assertEqual(self.client.get("/reports/").status_code, 403)

    def test_legacy_stock_import_preserves_quantities_without_prices(self):
        import importlib

        from django.apps import apps

        from shop.models import Product

        owner = get_user_model().objects.create_superuser(
            "import-owner", password="owner-password"
        )
        legacy = Product.objects.create(
            name="Legacy fish",
            unit="pc",
            category="Fish",
            stock=Decimal("12.750"),
            sale_price=200,
            average_cost=100,
        )
        migration = importlib.import_module(
            "inventory.migrations.0002_import_current_stock"
        )
        migration.import_stock(apps, None)
        imported = Item.objects.get(name=legacy.name)
        self.assertEqual(imported.line_set.get().quantity, Decimal("12.750"))
        self.assertEqual(imported.line_set.get().entry.kind, "opening")
        self.assertEqual(owner.membership.company_id, imported.company_id)
        legacy.refresh_from_db()
        self.assertEqual(legacy.sale_price, 200)

    def test_owner_opens_home_directly_and_company_creation_is_removed(self):
        owner = get_user_model().objects.create_superuser(
            "new-owner", password="Strong-private-password-281!"
        )
        self.client.force_login(owner)
        response = self.client.get("/")
        self.assertContains(response, "Stock operations")
        self.assertNotContains(response, "Companies")
        self.assertEqual(owner.membership.company_id, self.company.pk)
        self.assertEqual(self.client.get("/companies/").status_code, 404)
        self.assertEqual(
            self.client.post("/companies/", {"name": "Another shop"}).status_code, 404
        )
        self.assertEqual(self.client.get("/users/").status_code, 200)

    def test_single_workspace_migration_keeps_records_and_assigns_existing_staff(self):
        import importlib
        from django.apps import apps

        owner = get_user_model().objects.create_superuser(
            "workspace-owner", password="Strong-private-password-281!"
        )
        Membership.objects.create(
            user=owner, company=self.company, role="admin", must_change_password=False
        )
        colleague = get_user_model().objects.create_user(
            "colleague", password="Strong-private-password-492!"
        )
        membership = Membership.objects.create(
            user=colleague,
            company=self.other,
            role="operator",
            must_change_password=False,
        )
        entry = self.post("purchase", "6")
        self.company.is_primary = False
        self.company.save(update_fields=["is_primary"])
        migration = importlib.import_module(
            "inventory.migrations.0006_single_internal_workspace"
        )
        migration.configure_workspace(apps, None)
        membership.refresh_from_db()
        self.assertEqual(membership.company_id, self.company.pk)
        self.assertEqual(Entry.objects.get(pk=entry.pk).lines.get().quantity, 6)
        self.assertTrue(Item.objects.filter(pk=self.foreign.pk).exists())
        self.client.force_login(colleague)
        self.assertEqual(self.client.get("/reports/").context["rows"][0][-1], 6)
