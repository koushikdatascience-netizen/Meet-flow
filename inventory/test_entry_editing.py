from datetime import timedelta
from decimal import Decimal
import uuid
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from .models import (
    Company,
    Membership,
    Item,
    Entry,
    EntryRevision,
    post_entry,
    edit_stock_entry,
)


class EntryEditingTests(TestCase):
    def setUp(self):
        self.company = Company.objects.get(is_primary=True)
        self.user = get_user_model().objects.create_user(
            "editor", password="Long-test-password-813!"
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
        self.client.force_login(self.user)
        self.today = timezone.localdate()

    def entry(self, kind, qty, day=None):
        return post_entry(
            self.company,
            self.user,
            kind,
            day or self.today,
            "",
            uuid.uuid4(),
            [(self.item.pk, Decimal(qty))],
        )

    def edit(self, entry, qty, token=None, version=0):
        return edit_stock_entry(
            self.company,
            self.user,
            entry.pk,
            token or uuid.uuid4(),
            version,
            [(self.item.pk, Decimal(qty))],
        )

    def test_purchase_can_be_corrected_when_sales_depend_on_it(self):
        purchase = self.entry("purchase", 10)
        self.entry("sale", 7)
        self.edit(purchase, 9)
        self.assertEqual(self.client.get("/reports/").context["totals"][-1], 2)
        revision = EntryRevision.objects.get(entry=purchase)
        self.assertEqual(revision.previous["lines"][0]["quantity"], "10.000")
        self.assertEqual(revision.actor, self.user)
        with self.assertRaises(ValidationError):
            self.edit(purchase, 6, version=1)
        purchase.refresh_from_db()
        self.assertEqual(purchase.lines.get().quantity, 9)
        self.assertEqual(purchase.version, 1)
        self.assertEqual(EntryRevision.objects.count(), 1)

    def test_opening_edit_preserves_date_and_recalculates_carry_forward(self):
        opening = self.entry("opening", 10, self.today - timedelta(days=1))
        self.entry("sale", 3)
        self.edit(opening, 12)
        opening.refresh_from_db()
        self.assertEqual(opening.date, self.today - timedelta(days=1))
        self.assertEqual(self.client.get("/reports/").context["totals"][-1], 9)

    def test_sale_edit_is_idempotent_and_stale_edits_are_rejected(self):
        self.entry("purchase", 10)
        sale = self.entry("sale", 3)
        token = uuid.uuid4()
        self.edit(sale, 4, token)
        self.edit(sale, 4, token)
        self.assertEqual(EntryRevision.objects.count(), 1)
        with self.assertRaises(ValidationError):
            self.edit(sale, 5)
        self.assertEqual(self.client.get("/reports/").context["totals"][-1], 6)

    def test_edit_form_prefills_and_saves_then_returns_to_history(self):
        entry = self.entry("purchase", 10)
        url = f"/entries/purchase/?edit={entry.pk}"
        response = self.client.get(url)
        self.assertEqual(response.context["initial_rows"][0]["quantity"], "10")
        self.assertContains(response, "Save changes")
        data = {
            "token": response.context["token"],
            "version": 0,
            "item": [str(self.item.pk)],
            "quantity": ["8"],
        }
        self.assertRedirects(self.client.post(url, data), "/entries/purchase/")
        entry.refresh_from_db()
        self.assertEqual(entry.lines.get().quantity, 8)

    def test_edit_permission_is_separate_and_deleted_entries_cannot_be_edited(self):
        entry = self.entry("purchase", 10)
        self.member.role = "worker"
        self.member.features = ["purchase"]
        self.member.save()
        self.assertEqual(
            self.client.get(f"/entries/purchase/?edit={entry.pk}").status_code, 403
        )
        self.member.features = ["purchase", "edit"]
        self.member.save()
        self.assertEqual(
            self.client.get(f"/entries/purchase/?edit={entry.pk}").status_code, 200
        )
        from .models import reverse_entry

        reverse_entry(self.company, self.user, entry.pk, "Wrong entry")
        self.assertEqual(
            self.client.get(f"/entries/purchase/?edit={entry.pk}").status_code, 404
        )
