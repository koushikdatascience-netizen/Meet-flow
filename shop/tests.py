from decimal import Decimal
from django.test import TestCase
from django.db import transaction, IntegrityError
from django.core.exceptions import ValidationError
from .models import Product, Purchase, PurchaseLine, Sale, SaleLine, StockMovement
from .services import post_purchase, post_sale


class InventoryTests(TestCase):
    def setUp(self):
        self.p = Product.objects.create(name="Chicken", sale_price=220)

    def test_purchase_sale_and_no_duplicate_post(self):
        purchase = Purchase.objects.create(supplier="Supplier")
        PurchaseLine.objects.create(
            purchase=purchase, product=self.p, quantity=20, unit_cost=160
        )
        post_purchase(purchase.pk)
        self.p.refresh_from_db()
        self.assertEqual(self.p.stock, Decimal("20"))
        sale = Sale.objects.create()
        SaleLine.objects.create(
            sale=sale, product=self.p, quantity=Decimal("1.5"), unit_price=220
        )
        post_sale(sale.pk)
        self.p.refresh_from_db()
        self.assertEqual(self.p.stock, Decimal("18.5"))
        sale.refresh_from_db()
        self.assertEqual(sale.total, Decimal("330"))
        self.assertEqual(sale.gross_profit, Decimal("90"))
        with self.assertRaises(ValidationError):
            post_sale(sale.pk)
        self.p.refresh_from_db()
        self.assertEqual(self.p.stock, Decimal("18.5"))

    def test_insufficient_stock_does_not_post(self):
        sale = Sale.objects.create()
        SaleLine.objects.create(sale=sale, product=self.p, quantity=1, unit_price=220)
        with self.assertRaises(ValidationError):
            post_sale(sale.pk)
        self.assertFalse(Sale.objects.get(pk=sale.pk).posted)
        self.assertEqual(StockMovement.objects.count(), 0)


class IntegrityTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            name="Chicken", sale_price=Decimal("220"), stock=10, average_cost=100
        )

    def make_sale(self, quantity="1", price="220", discount="0"):
        sale = Sale.objects.create(discount=Decimal(discount))
        line = SaleLine.objects.create(
            sale=sale,
            product=self.product,
            quantity=Decimal(quantity),
            unit_price=Decimal(price),
        )
        return sale, line

    def test_posted_sales_cannot_be_deleted_including_bulk(self):
        sale, line = self.make_sale()
        post_sale(sale.pk)
        for delete in (
            lambda: Sale.objects.filter(pk=sale.pk).delete(),
            lambda: SaleLine.objects.filter(pk=line.pk).delete(),
            lambda: Sale.objects.get(pk=sale.pk).delete(),
        ):
            with self.assertRaises(ValidationError), transaction.atomic():
                delete()
        self.assertTrue(Sale.objects.filter(pk=sale.pk).exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 9)

    def test_posted_purchase_cannot_be_deleted_or_edited(self):
        purchase = Purchase.objects.create()
        line = PurchaseLine.objects.create(
            purchase=purchase, product=self.product, quantity=1, unit_cost=100
        )
        post_purchase(purchase.pk)
        with self.assertRaises(ValidationError), transaction.atomic():
            Purchase.objects.filter(pk=purchase.pk).delete()
        line.unit_cost = 200
        with self.assertRaises(ValidationError):
            line.save()

    def test_stale_objects_and_new_lines_cannot_edit_posted_sale(self):
        sale, line = self.make_sale()
        post_sale(sale.pk)
        sale.notes = "Changed from stale draft"
        with self.assertRaises(ValidationError):
            sale.save()
        line.quantity = 2
        with self.assertRaises(ValidationError):
            line.save()
        with self.assertRaises(ValidationError):
            SaleLine.objects.create(
                sale=sale, product=self.product, quantity=1, unit_price=1
            )
        with self.assertRaises(ValidationError):
            Sale.objects.filter(pk=sale.pk).update(notes="Changed")
        with self.assertRaises(ValidationError):
            SaleLine.objects.filter(pk=line.pk).update(quantity=2)

    def test_moving_posted_line_to_draft_is_blocked(self):
        sale, line = self.make_sale()
        post_sale(sale.pk)
        line.sale = Sale.objects.create()
        with self.assertRaises(ValidationError):
            line.save()

    def test_drafts_can_be_edited_and_deleted(self):
        sale, line = self.make_sale()
        line.quantity = 2
        line.save()
        sale.notes = "Draft"
        sale.save()
        sale.delete()
        self.assertFalse(SaleLine.objects.filter(pk=line.pk).exists())

    def test_bill_snapshots_survive_product_changes(self):
        sale, line = self.make_sale()
        post_sale(sale.pk)
        self.product.name = "Renamed"
        self.product.unit = "pc"
        self.product.save()
        line.refresh_from_db()
        self.assertEqual(line.product_name, "Chicken")
        self.assertEqual(line.product_unit, "kg")

    def test_money_uses_half_up_rounding_per_line(self):
        sale, line = self.make_sale("0.001", "5")
        self.assertEqual(line.amount, Decimal("0.01"))
        self.assertEqual(sale.total, Decimal("0.01"))
        post_sale(sale.pk)
        sale.refresh_from_db()
        self.assertEqual(sale.gross_profit, Decimal("-0.09"))

    def test_discount_validated_and_rollback_leaves_stock_unchanged(self):
        for discount in ("221",):
            sale, line = self.make_sale(discount=discount)
            with self.assertRaises(ValidationError):
                post_sale(sale.pk)
            self.assertFalse(Sale.objects.get(pk=sale.pk).posted)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_repeated_product_lines_check_combined_stock(self):
        sale, line = self.make_sale("6")
        SaleLine.objects.create(
            sale=sale, product=self.product, quantity=6, unit_price=220
        )
        with self.assertRaises(ValidationError):
            post_sale(sale.pk)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)

    def test_inactive_product_cannot_be_sold(self):
        self.product.active = False
        self.product.save()
        sale, line = self.make_sale()
        with self.assertRaises(ValidationError):
            post_sale(sale.pk)

    def test_admin_bulk_delete_disabled(self):
        from django.contrib import admin
        from django.contrib.auth import get_user_model
        from django.test import RequestFactory
        from .admin import SaleAdmin, PurchaseAdmin

        request = RequestFactory().get("/admin/")
        request.user = get_user_model().objects.create_superuser(
            "owner", password="test-password"
        )
        for model, admin_class in ((Sale, SaleAdmin), (Purchase, PurchaseAdmin)):
            self.assertNotIn(
                "delete_selected", admin_class(model, admin.site).get_actions(request)
            )


class OperationsTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        self.owner = get_user_model().objects.create_superuser(
            "owner", password="test-password"
        )
        self.product = Product.objects.create(
            name="Chicken", sale_price=220, stock=10, average_cost=100
        )

    def adjustment(self, quantity, reason="wastage"):
        from .models import StockAdjustment

        return StockAdjustment.objects.create(
            product=self.product,
            quantity_delta=quantity,
            reason=reason,
            notes="Physical count",
        )

    def test_wastage_posting_and_duplicate_protection(self):
        from .services import post_adjustment
        from .models import StockAdjustment

        adjustment = self.adjustment(-2)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)
        post_adjustment(adjustment.pk)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 8)
        self.assertEqual(StockMovement.objects.get().quantity_delta, -2)
        with self.assertRaises(ValidationError):
            post_adjustment(adjustment.pk)
        with self.assertRaises(ValidationError), transaction.atomic():
            StockAdjustment.objects.filter(pk=adjustment.pk).delete()

    def test_invalid_adjustments_do_not_change_stock(self):
        from .services import post_adjustment

        for qty in (0, 1, -11):
            adjustment = self.adjustment(qty)
            with self.assertRaises(ValidationError):
                post_adjustment(adjustment.pk)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_count_correction_can_add_stock(self):
        from .services import post_adjustment

        adjustment = self.adjustment(2, "correction")
        post_adjustment(adjustment.pk)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 12)

    def test_reports_include_expenses_and_stock_losses(self):
        from .models import Expense
        from .services import post_adjustment

        Expense.objects.create(category="Rent", description="Daily cost", amount=50)
        adjustment = self.adjustment(-2)
        post_adjustment(adjustment.pk)
        self.client.force_login(self.owner)
        response = self.client.get("/reports/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["expenses"], 50)
        self.assertEqual(response.context["adjustment_value"], -200)
        self.assertEqual(response.context["operating_profit"], -250)

    def test_reports_and_invoices_require_staff(self):
        self.assertEqual(self.client.get("/reports/").status_code, 302)
        self.assertEqual(self.client.get("/invoice/1/").status_code, 302)

    def test_invoice_has_historical_product_and_rounded_total(self):
        sale = Sale.objects.create()
        SaleLine.objects.create(
            sale=sale, product=self.product, quantity=Decimal("0.001"), unit_price=5
        )
        post_sale(sale.pk)
        self.product.name = "Changed name"
        self.product.save()
        self.client.force_login(self.owner)
        response = self.client.get(f"/invoice/{sale.pk}/")
        self.assertContains(response, "Chicken")
        self.assertNotContains(response, "Changed name")
        self.assertContains(response, "0.01")

    def test_invalid_report_dates_fall_back_safely(self):
        self.client.force_login(self.owner)
        self.assertEqual(
            self.client.get("/reports/?start=invalid&end=invalid").status_code, 200
        )

    def test_home_redirects_to_admin(self):
        self.assertRedirects(
            self.client.get("/"), "/admin/login/?next=/", fetch_redirect_response=False
        )


class DatabaseConstraintTests(TestCase):
    def test_negative_stock_and_discount_rejected_by_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.create(name="Invalid", sale_price=1, stock=-1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Sale.objects.create(discount=-1)


class AdditionalRegressionTests(TestCase):
    def test_stale_product_admin_form_preserves_stock(self):
        from .admin import ProductAdmin
        from django.contrib import admin

        product = Product.objects.create(
            name="Chicken", sale_price=220, stock=10, average_cost=100
        )
        stale = Product.objects.get(pk=product.pk)
        sale = Sale.objects.create()
        SaleLine.objects.create(sale=sale, product=product, quantity=2, unit_price=220)
        post_sale(sale.pk)
        stale.name = "Renamed"
        ProductAdmin(Product, admin.site).save_model(None, stale, None, True)
        product.refresh_from_db()
        self.assertEqual(product.stock, 8)
        self.assertEqual(product.name, "Renamed")

    def test_repeated_purchase_lines_have_one_weighted_average(self):
        product = Product.objects.create(
            name="Chicken", sale_price=220, stock=1, average_cost=Decimal("0.01")
        )
        purchase = Purchase.objects.create()
        PurchaseLine.objects.create(
            purchase=purchase, product=product, quantity=1, unit_cost=Decimal("0.02")
        )
        PurchaseLine.objects.create(
            purchase=purchase, product=product, quantity=1, unit_cost=Decimal("0.03")
        )
        post_purchase(purchase.pk)
        product.refresh_from_db()
        self.assertEqual(product.average_cost, Decimal("0.02"))
        self.assertEqual(product.stock, 3)

    def test_csv_export_is_authenticated_and_contains_totals(self):
        from django.contrib.auth import get_user_model

        owner = get_user_model().objects.create_superuser(
            "owner", password="test-password"
        )
        self.client.force_login(owner)
        response = self.client.get("/reports/?format=csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        self.assertIn("Estimated operating profit", response.content.decode())


from django.test import SimpleTestCase


class BackupTests(SimpleTestCase):
    def test_backup_restores_database_and_media_and_refuses_overwrite(self):
        import sqlite3
        import tempfile
        from contextlib import closing
        from pathlib import Path
        from unittest.mock import patch
        from zipfile import ZipFile
        from django.core.management import call_command, CommandError
        from django.db import connection

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            database = root / "source.sqlite3"
            with closing(sqlite3.connect(database)) as conn:
                conn.execute("CREATE TABLE example (name TEXT)")
                conn.execute("INSERT INTO example VALUES ('Chicken')")
                conn.commit()
            media = root / "media"
            media.mkdir()
            (media / "sample.txt").write_text("test image placeholder")
            target = root / "backup.zip"
            with (
                patch.dict(
                    connection.settings_dict,
                    NAME=str(database),
                    ENGINE="django.db.backends.sqlite3",
                ),
                self.settings(MEDIA_ROOT=media),
            ):
                call_command("backup_shop", output=str(target), verbosity=0)
                with self.assertRaises(CommandError):
                    call_command("backup_shop", output=str(target), verbosity=0)
            with ZipFile(target) as archive:
                self.assertIn("media/sample.txt", archive.namelist())
                restored = root / "restored.sqlite3"
                restored.write_bytes(archive.read("db.sqlite3"))
            with closing(sqlite3.connect(restored)) as conn:
                self.assertEqual(
                    conn.execute("PRAGMA integrity_check").fetchone()[0], "ok"
                )
                self.assertEqual(
                    conn.execute("SELECT name FROM example").fetchone()[0], "Chicken"
                )


from django.test import TransactionTestCase, skipUnlessDBFeature


class MigrationTests(TransactionTestCase):
    def test_existing_bill_details_are_backfilled(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes("shop")
        try:
            executor.migrate([("shop", "0001_initial")])
            apps = executor.loader.project_state([("shop", "0001_initial")]).apps
            product = apps.get_model("shop", "Product").objects.create(
                name="Existing chicken", sale_price=220
            )
            sale = apps.get_model("shop", "Sale").objects.create(posted=True)
            line = apps.get_model("shop", "SaleLine").objects.create(
                sale=sale, product=product, quantity=1, unit_price=220
            )
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            migrated = (
                executor.loader.project_state(latest)
                .apps.get_model("shop", "SaleLine")
                .objects.get(pk=line.pk)
            )
            self.assertEqual(migrated.product_name, "Existing chicken")
            self.assertEqual(migrated.product_unit, "kg")
        finally:
            MigrationExecutor(connection).migrate(latest)


class ConcurrentPostingTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_sales_cannot_oversell(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import connections

        product = Product.objects.create(
            name="Chicken", sale_price=220, stock=1, average_cost=100
        )
        sales = []
        for _ in range(2):
            sale = Sale.objects.create()
            SaleLine.objects.create(
                sale=sale, product=product, quantity=1, unit_price=220
            )
            sales.append(sale.pk)
        barrier = Barrier(2)

        def post(pk):
            try:
                barrier.wait(timeout=10)
                try:
                    post_sale(pk)
                    return True
                except ValidationError:
                    return False
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(post, sales))
        self.assertEqual(sorted(outcomes), [False, True])
        product.refresh_from_db()
        self.assertEqual(product.stock, 0)
        self.assertEqual(StockMovement.objects.count(), 1)
