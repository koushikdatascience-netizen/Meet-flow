from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import transaction
from django.test import TestCase
from .models import (
    Product,
    Sale,
    SaleLine,
    Purchase,
    PurchaseLine,
    ReturnDocument,
    ReturnLine,
    ShopSettings,
    StockMovement,
    AuditEvent,
)
from .services import post_sale, post_purchase, post_return


class WorkflowTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            name="Chicken",
            sale_price=100,
            stock=20,
            average_cost=50,
            hsn="0207",
            tax_rate=18,
        )
        self.owner = get_user_model().objects.create_superuser(
            "owner", password="testing-password"
        )

    def sale(self, discount=0):
        sale = Sale.objects.create(discount=discount)
        line = SaleLine.objects.create(
            sale=sale, product=self.product, quantity=2, unit_price=100
        )
        post_sale(sale.pk, actor=self.owner)
        sale.refresh_from_db()
        line.refresh_from_db()
        return sale, line

    def refund(self, sale, line, quantity=1, restock=False):
        document = ReturnDocument.objects.create(
            sale=sale, reason="Customer returned item"
        )
        ReturnLine.objects.create(
            document=document, sale_line=line, quantity=quantity, restock=restock
        )
        return document

    def tax_profile(self, mode="tax"):
        return ShopSettings.objects.create(
            name="Test Shop",
            address="Test street",
            gstin="27ABCDE1234F1Z5",
            state_code="27",
            signature="Shop owner",
            mode=mode,
        )

    def test_partial_returns_prorate_discount_and_never_exceed_original(self):
        sale, line = self.sale(discount=20)
        one = self.refund(sale, line)
        post_return(one.pk, actor=self.owner)
        one.refresh_from_db()
        self.assertEqual(one.amount, 90)
        two = self.refund(sale, line)
        post_return(two.pk)
        two.refresh_from_db()
        self.assertEqual(one.amount + two.amount, sale.total)
        extra = self.refund(sale, line)
        with self.assertRaises(ValidationError):
            post_return(extra.pk)
        self.assertFalse(ReturnDocument.objects.get(pk=extra.pk).posted)

    def test_spoiled_return_does_not_restock(self):
        sale, line = self.sale()
        document = self.refund(sale, line, restock=False)
        post_return(document.pk)
        self.product.refresh_from_db()
        document.refresh_from_db()
        self.assertEqual(self.product.stock, 18)
        self.assertEqual(document.profit_reversal, 100)

    def test_saleable_return_restores_stock_and_acquisition_cost(self):
        sale, line = self.sale()
        document = self.refund(sale, line, restock=True)
        post_return(document.pk)
        self.product.refresh_from_db()
        document.refresh_from_db()
        self.assertEqual(self.product.stock, 19)
        self.assertEqual(document.profit_reversal, 50)
        self.assertTrue(
            StockMovement.objects.filter(
                reason="sale_return", quantity_delta=1
            ).exists()
        )

    def test_posted_return_and_lines_are_immutable(self):
        sale, line = self.sale()
        document = self.refund(sale, line)
        return_line = document.lines.get()
        post_return(document.pk)
        with self.assertRaises(ValidationError):
            post_return(document.pk)
        document.reason = "Edited"
        with self.assertRaises(ValidationError):
            document.save()
        with self.assertRaises(ValidationError):
            return_line.save()
        with self.assertRaises(ValidationError), transaction.atomic():
            ReturnDocument.objects.filter(pk=document.pk).delete()
        with self.assertRaises(ValidationError), transaction.atomic():
            ReturnLine.objects.filter(pk=return_line.pk).delete()

    def test_return_cannot_use_another_sales_line(self):
        sale, line = self.sale()
        other, other_line = self.sale()
        document = self.refund(sale, other_line)
        with self.assertRaises(ValidationError):
            post_return(document.pk)

    def test_duplicate_original_lines_in_one_return_are_rejected(self):
        sale, line = self.sale()
        document = self.refund(sale, line)
        ReturnLine.objects.create(document=document, sale_line=line, quantity=1)
        with self.assertRaises(ValidationError):
            post_return(document.pk)

    def test_supplier_return_reduces_stock_and_records_valuation_variance(self):
        purchase = Purchase.objects.create(supplier="Supplier")
        line = PurchaseLine.objects.create(
            purchase=purchase, product=self.product, quantity=2, unit_cost=100
        )
        post_purchase(purchase.pk)
        self.product.refresh_from_db()
        cost = self.product.average_cost
        document = ReturnDocument.objects.create(
            purchase=purchase, reason="Supplier correction"
        )
        ReturnLine.objects.create(document=document, purchase_line=line, quantity=2)
        post_return(document.pk)
        self.product.refresh_from_db()
        document.refresh_from_db()
        self.assertEqual(self.product.stock, 20)
        self.assertEqual(document.amount, 200)
        self.assertEqual(document.valuation_variance, 200 - 2 * cost)
        self.assertEqual(self.product.average_cost, cost)

    def test_supplier_return_cannot_make_stock_negative(self):
        purchase = Purchase.objects.create()
        line = PurchaseLine.objects.create(
            purchase=purchase, product=self.product, quantity=30, unit_cost=100
        )
        post_purchase(purchase.pk)
        self.product.stock = 0
        self.product.save(update_fields=["stock"])
        document = ReturnDocument.objects.create(
            purchase=purchase, reason="Return unavailable stock"
        )
        ReturnLine.objects.create(document=document, purchase_line=line, quantity=30)
        with self.assertRaises(ValidationError):
            post_return(document.pk)
        self.assertFalse(ReturnDocument.objects.get(pk=document.pk).posted)

    def test_tax_invoice_discount_and_intrastate_split(self):
        self.tax_profile()
        sale, line = self.sale(discount=20)
        self.assertEqual(line.taxable_amount, 180)
        self.assertEqual(line.cgst, Decimal("16.20"))
        self.assertEqual(line.sgst, Decimal("16.20"))
        self.assertEqual(line.igst, 0)
        self.assertEqual(sale.total, Decimal("212.40"))
        self.assertEqual(sale.gross_profit, 80)
        self.assertLessEqual(len(sale.invoice_number), 16)

    def test_interstate_uses_igst(self):
        self.tax_profile()
        sale = Sale.objects.create(customer_state="29")
        line = SaleLine.objects.create(
            sale=sale, product=self.product, quantity=1, unit_price=100
        )
        post_sale(sale.pk)
        line.refresh_from_db()
        self.assertEqual(line.igst, 18)
        self.assertEqual(line.cgst + line.sgst, 0)

    def test_bill_of_supply_does_not_charge_tax(self):
        self.tax_profile(mode="supply")
        sale, line = self.sale()
        self.assertEqual(sale.tax_total, 0)
        self.assertEqual(sale.total, 200)

    def test_many_small_lines_allocate_discount_without_negative_values(self):
        sale = Sale.objects.create(discount=Decimal("0.03"))
        for _ in range(4):
            SaleLine.objects.create(
                sale=sale, product=self.product, quantity=Decimal("0.001"), unit_price=5
            )
        post_sale(sale.pk)
        sale.refresh_from_db()
        self.assertEqual(sale.total, Decimal("0.01"))
        self.assertTrue(all(line.taxable_amount >= 0 for line in sale.lines.all()))
        self.assertEqual(
            sum(line.discount_amount for line in sale.lines.all()), Decimal("0.03")
        )

    def test_oversized_calculated_total_is_rejected_without_stock_change(self):
        self.product.stock = 2000
        self.product.save(update_fields=["stock"])
        sale = Sale.objects.create()
        SaleLine.objects.create(
            sale=sale,
            product=self.product,
            quantity=1000,
            unit_price=Decimal("9999999999.99"),
        )
        with self.assertRaises(ValidationError):
            post_sale(sale.pk)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 2000)
        self.assertFalse(Sale.objects.get(pk=sale.pk).posted)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_tax_inclusive_high_value_requires_customer_details(self):
        self.tax_profile()
        sale = Sale.objects.create()
        SaleLine.objects.create(
            sale=sale, product=self.product, quantity=1, unit_price=49999
        )
        with self.assertRaises(ValidationError):
            post_sale(sale.pk)
        self.assertFalse(Sale.objects.get(pk=sale.pk).posted)

    def test_profile_changes_do_not_rewrite_posted_invoice(self):
        profile = self.tax_profile()
        sale, line = self.sale()
        profile.name = "Changed shop"
        profile.save()
        self.assertEqual(
            Sale.objects.get(pk=sale.pk).invoice_snapshot["name"], "Test Shop"
        )
        self.client.force_login(self.owner)
        response = self.client.get(f"/invoice/{sale.pk}/?paper=thermal")
        self.assertContains(response, "Test Shop")
        self.assertNotContains(response, "Changed shop")
        self.assertContains(response, "Tax invoice")

    def test_tax_refunds_reconcile_original_components(self):
        self.tax_profile()
        sale, line = self.sale(discount=20)
        total = Decimal("0")
        for _ in range(2):
            document = self.refund(sale, line)
            post_return(document.pk)
            document.refresh_from_db()
            total += document.amount
            self.assertEqual(document.tax_amount, Decimal("16.20"))
        self.assertEqual(total, sale.total)

    def test_missing_hsn_blocks_tax_invoice_and_rolls_back(self):
        self.tax_profile()
        self.product.hsn = ""
        self.product.save()
        sale = Sale.objects.create()
        SaleLine.objects.create(
            sale=sale, product=self.product, quantity=1, unit_price=100
        )
        with self.assertRaises(ValidationError):
            post_sale(sale.pk)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 20)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_payments_and_profit_reports_include_refunds(self):
        sale, line = self.sale()
        document = self.refund(sale, line, restock=True)
        post_return(document.pk)
        self.client.force_login(self.owner)
        response = self.client.get("/reports/")
        self.assertEqual(response.context["sales_total"], 100)
        self.assertEqual(response.context["gross_profit"], 50)
        self.assertEqual(response.context["payment_summary"][0]["net"], 100)

    def test_audit_records_actor_and_cannot_be_modified_or_deleted(self):
        sale, line = self.sale()
        event = AuditEvent.objects.get(action="sale.posted")
        self.assertEqual(event.actor, "owner")
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            AuditEvent.objects.filter(pk=event.pk).update(actor="someone")
        with self.assertRaises(ValidationError), transaction.atomic():
            event.delete()


class BillingTests(TestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)
        self.cashier = get_user_model().objects.create_user(
            "cashier", password="testing-password", is_staff=True
        )
        self.cashier.groups.add(Group.objects.get(name="Cashier"))
        self.client.force_login(self.cashier)
        self.product = Product.objects.create(
            name="Chicken", sale_price=100, stock=10, average_cost=50
        )

    def payload(self):
        response = self.client.get("/pos/")
        return {
            "token": str(response.context["form"]["token"].value()),
            "discount": "0",
            "payment_method": "cash",
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "1",
            "items-MAX_NUM_FORMS": "100",
            "items-0-product": str(self.product.pk),
            "items-0-quantity": "2",
        }

    def test_cashier_can_bill_with_server_prices_and_retry_safely(self):
        payload = self.payload()
        payload["items-0-unit_price"] = "0.01"
        response = self.client.post("/pos/", payload)
        sale = Sale.objects.get()
        self.assertRedirects(
            response, f"/invoice/{sale.pk}/", fetch_redirect_response=False
        )
        self.assertTrue(sale.posted)
        self.assertEqual(sale.total, 200)
        self.client.post("/pos/", payload)
        self.assertEqual(Sale.objects.count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 8)
        self.assertEqual(AuditEvent.objects.get(action="sale.posted").actor, "cashier")

    def test_cashier_cannot_access_reports_refunds_or_settings(self):
        self.assertEqual(self.client.get("/reports/").status_code, 403)
        self.assertEqual(self.client.get("/returns/1/").status_code, 403)
        self.assertEqual(self.client.get("/admin/shop/shopsettings/").status_code, 403)

    def test_invalid_checkout_does_not_save_any_partial_records(self):
        payload = self.payload()
        payload["items-0-quantity"] = "11"
        response = self.client.post("/pos/", payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Insufficient stock")
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_checkout_tokens_are_bound_to_session(self):
        import uuid

        payload = self.payload()
        payload["token"] = str(uuid.uuid4())
        response = self.client.post("/pos/", payload)
        self.assertContains(response, "expired")
        self.assertEqual(Sale.objects.count(), 0)

    def test_piece_quantities_must_be_whole_numbers(self):
        self.product.unit = "pc"
        self.product.save()
        payload = self.payload()
        payload["items-0-quantity"] = "1.5"
        self.assertContains(self.client.post("/pos/", payload), "whole numbers")
        self.assertEqual(Sale.objects.count(), 0)

    def test_health_endpoint_is_available(self):
        response = self.client.get("/healthz/")
        self.assertEqual(response.json(), {"status": "ok"})


class ReturnScreenTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser(
            "owner", password="testing-password"
        )
        self.client.force_login(self.owner)
        self.product = Product.objects.create(
            name="Chicken", sale_price=100, stock=10, average_cost=50
        )
        self.sale = Sale.objects.create()
        self.line = SaleLine.objects.create(
            sale=self.sale, product=self.product, quantity=4, unit_price=100
        )
        post_sale(self.sale.pk)

    def test_owner_can_post_partial_return_and_retry_without_duplicate(self):
        url = f"/return/sale/{self.sale.pk}/"
        response = self.client.get(url)
        payload = {
            "token": str(response.context["form"]["token"].value()),
            "reason": "Spoiled item",
            "payment_method": "cash",
            "returns-TOTAL_FORMS": "1",
            "returns-INITIAL_FORMS": "1",
            "returns-MIN_NUM_FORMS": "0",
            "returns-MAX_NUM_FORMS": "1000",
            "returns-0-source_line": str(self.line.pk),
            "returns-0-quantity": "1",
        }
        response = self.client.post(url, payload)
        document = ReturnDocument.objects.get()
        self.assertRedirects(
            response, f"/returns/{document.pk}/", fetch_redirect_response=False
        )
        self.client.post(url, payload)
        self.assertEqual(ReturnDocument.objects.count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 6)
        response = self.client.get(f"/returns/{document.pk}/")
        self.assertContains(response, "100.00")


from django.test import SimpleTestCase


class RecoveryTests(SimpleTestCase):
    def test_verified_backup_can_restore_separately_and_detect_tampering(self):
        import sqlite3, tempfile
        from contextlib import closing
        from pathlib import Path
        from unittest.mock import patch
        from zipfile import ZipFile, ZIP_DEFLATED
        from django.db import connection
        from django.core.management import CommandError

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.sqlite3"
            with closing(sqlite3.connect(source)) as conn:
                conn.execute("CREATE TABLE example (value TEXT)")
                conn.execute("INSERT INTO example VALUES ('restored')")
                conn.commit()
            media = root / "media"
            media.mkdir()
            (media / "sample.txt").write_text("original")
            backup = root / "backup.zip"
            with (
                patch.dict(
                    connection.settings_dict,
                    NAME=str(source),
                    ENGINE="django.db.backends.sqlite3",
                ),
                self.settings(MEDIA_ROOT=media),
            ):
                call_command("backup_shop", output=str(backup), verbosity=0)
            call_command("verify_backup", str(backup), verbosity=0)
            destination = root / "recovery"
            call_command(
                "restore_backup", str(backup), destination=str(destination), verbosity=0
            )
            self.assertEqual(
                (destination / "media" / "sample.txt").read_text(), "original"
            )
            with closing(sqlite3.connect(destination / "db.sqlite3")) as conn:
                self.assertEqual(
                    conn.execute("SELECT value FROM example").fetchone()[0], "restored"
                )
            with self.assertRaises(CommandError):
                call_command(
                    "restore_backup", str(backup), destination=str(destination)
                )
            tampered = root / "tampered.zip"
            with (
                ZipFile(backup) as original,
                ZipFile(tampered, "w", ZIP_DEFLATED) as target,
            ):
                for name in original.namelist():
                    target.writestr(
                        name,
                        b"changed"
                        if name == "media/sample.txt"
                        else original.read(name),
                    )
            with self.assertRaises(CommandError):
                call_command("verify_backup", str(tampered))


class SecurityTests(TestCase):
    def test_checkout_and_refund_posts_require_csrf(self):
        from django.test import Client

        owner = get_user_model().objects.create_superuser(
            "owner", password="testing-password"
        )
        client = Client(enforce_csrf_checks=True)
        client.force_login(owner)
        self.assertEqual(client.post("/pos/", {}).status_code, 403)
        self.assertEqual(client.post("/return/sale/1/", {}).status_code, 403)


class FastEntryTests(TestCase):
    def setUp(self):
        call_command("setup_roles", verbosity=0)
        self.owner = get_user_model().objects.create_superuser(
            "owner", password="testing-password"
        )
        self.client.force_login(self.owner)
        self.product = Product.objects.create(
            name="Chicken", sale_price=100, stock=10, average_cost=50
        )

    def bill_payload(self):
        response = self.client.get("/pos/")
        return {
            "token": str(response.context["form"]["token"].value()),
            "discount": "0",
            "payment_method": "upi",
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "1",
            "items-MAX_NUM_FORMS": "100",
            "items-0-product": str(self.product.pk),
            "items-0-quantity": "2",
        }

    def purchase_payload(self):
        response = self.client.get("/purchases/new/")
        return {
            "token": str(response.context["form"]["token"].value()),
            "supplier": "Local supplier",
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "1",
            "items-MAX_NUM_FORMS": "100",
            "items-0-product": str(self.product.pk),
            "items-0-quantity": "3",
            "items-0-unit_cost": "60",
        }

    def test_two_products_are_saved_on_one_bill(self):
        second = Product.objects.create(
            name="Fish", sale_price=150, stock=5, average_cost=80
        )
        payload = self.bill_payload()
        payload.update(
            {
                "items-TOTAL_FORMS": "2",
                "items-1-product": str(second.pk),
                "items-1-quantity": "1.5",
            }
        )
        response = self.client.post("/pos/", payload)
        self.assertEqual(response.status_code, 302)
        sale = Sale.objects.get()
        self.assertEqual(sale.lines.count(), 2)
        self.assertEqual(sale.total, 425)
        self.product.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.product.stock, 8)
        self.assertEqual(second.stock, Decimal("3.5"))

    def test_zero_stock_is_explained_and_cannot_be_sold(self):
        self.product.stock = 0
        self.product.save(update_fields=["stock"])
        response = self.client.get("/pos/")
        self.assertFalse(response.context["has_sale_stock"])
        self.assertContains(response, "No stock is available to sell yet.")
        self.assertContains(response, "Add a purchase")
        payload = self.bill_payload()
        self.assertContains(self.client.post("/pos/", payload), "Insufficient stock")
        self.assertEqual(Sale.objects.count(), 0)

    def test_save_and_next_returns_to_blank_billing_screen_and_keeps_last_bill(self):
        payload = self.bill_payload()
        payload["action"] = "next"
        response = self.client.post("/pos/", payload, follow=True)
        self.assertEqual(response.redirect_chain, [("/pos/", 302)])
        self.assertEqual(response.context["last_sale"].total, 200)
        self.assertContains(response, "Last bill")
        self.assertContains(response, "Total ₹200.00")
        self.assertNotEqual(
            str(response.context["form"]["token"].value()), payload["token"]
        )
        self.client.post("/pos/", payload)
        self.assertEqual(Sale.objects.count(), 1)

    def test_save_and_print_uses_thermal_invoice_without_a_second_sale(self):
        payload = self.bill_payload()
        payload["action"] = "print"
        response = self.client.post("/pos/", payload, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["auto_print"])
        self.assertTrue(response.context["thermal"])
        self.assertEqual(Sale.objects.count(), 1)

    def test_purchase_saves_and_posts_in_one_action_and_can_be_retried(self):
        payload = self.purchase_payload()
        response = self.client.post("/purchases/new/", payload, follow=True)
        self.assertContains(response, "Stock updated")
        purchase = Purchase.objects.get()
        self.assertTrue(purchase.posted)
        self.assertEqual(purchase.total, 180)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 13)
        self.client.post("/purchases/new/", payload)
        self.assertEqual(Purchase.objects.count(), 1)
        self.assertEqual(StockMovement.objects.filter(reason="purchase").count(), 1)

    def test_invalid_purchase_keeps_entries_and_does_not_change_stock(self):
        payload = self.purchase_payload()
        payload["items-0-unit_cost"] = "-1"
        response = self.client.post("/purchases/new/", payload)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["formset"].errors)
        self.assertEqual(response.context["form"]["supplier"].value(), "Local supplier")
        self.assertEqual(Purchase.objects.count(), 0)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)

    def test_inventory_manager_can_use_purchase_screen_but_cashier_cannot(self):
        inventory = get_user_model().objects.create_user("stock", is_staff=True)
        inventory.groups.add(Group.objects.get(name="Inventory Manager"))
        self.client.force_login(inventory)
        payload = self.purchase_payload()
        self.assertEqual(self.client.post("/purchases/new/", payload).status_code, 302)
        cashier = get_user_model().objects.create_user("cashier", is_staff=True)
        cashier.groups.add(Group.objects.get(name="Cashier"))
        self.client.force_login(cashier)
        self.assertEqual(self.client.get("/purchases/new/").status_code, 403)
        self.assertEqual(self.client.get("/purchases/1/").status_code, 403)

    def test_purchase_token_must_come_from_this_session(self):
        import uuid

        payload = self.purchase_payload()
        payload["token"] = str(uuid.uuid4())
        self.assertContains(self.client.post("/purchases/new/", payload), "expired")
        self.assertEqual(Purchase.objects.count(), 0)

    def test_report_shortcuts_use_shop_local_dates(self):
        from datetime import timedelta
        from django.utils import timezone

        today = timezone.localdate()
        for period, start, end in [
            ("today", today, today),
            ("yesterday", today - timedelta(days=1), today - timedelta(days=1)),
            ("week", today - timedelta(days=6), today),
            ("month", today.replace(day=1), today),
        ]:
            response = self.client.get("/reports/", {"period": period})
            self.assertEqual(response.context["start"], start)
            self.assertEqual(response.context["end"], end)
        response = self.client.get("/reports/")
        self.assertEqual(response.context["start"], today)
        self.assertContains(response, "More financial details")

    def test_custom_report_dates_override_shortcut(self):
        response = self.client.get(
            "/reports/?period=today&start=2026-10-01&end=2026-10-10"
        )
        self.assertEqual(str(response.context["start"]), "2026-10-01")
        self.assertEqual(str(response.context["end"]), "2026-10-10")
        self.assertEqual(response.context["period"], "custom")


class SimpleNavigationTests(TestCase):
    def test_home_opens_the_right_work_screen_for_the_role(self):
        call_command("setup_roles", verbosity=0)
        for name, url in [
            ("Cashier", "/pos/"),
            ("Inventory Manager", "/purchases/new/"),
        ]:
            user = get_user_model().objects.create_user(name, is_staff=True)
            user.groups.add(Group.objects.get(name=name))
            self.client.force_login(user)
            self.assertRedirects(
                self.client.get("/"), url, fetch_redirect_response=False
            )

    def test_invalid_report_dates_show_clear_feedback(self):
        owner = get_user_model().objects.create_superuser(
            "owner", password="testing-password"
        )
        self.client.force_login(owner)
        response = self.client.get("/reports/?start=not-a-date&end=invalid")
        self.assertContains(response, "Choose a valid date range")
