import csv
import io
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from .models import (
    Product,
    Purchase,
    PurchaseLine,
    Sale,
    SaleLine,
    ReturnDocument,
    ReturnLine,
    ShopSettings,
    StockMovement,
)
from .services import post_purchase, post_sale, post_return


class ReportRegisterTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser(
            "owner", password="testing-password"
        )
        self.client.force_login(self.owner)
        self.chicken = Product.objects.create(
            name="Chicken", sale_price=260, hsn="0207"
        )
        self.eggs = Product.objects.create(
            name="Eggs", unit="pc", sale_price=8, hsn="0407"
        )
        self.purchase_a = self.purchase(self.chicken, 10, 180, "Alpha Foods")
        self.purchase_b = self.purchase(self.eggs, 30, 5, "Beta Farm")
        self.sale_a, self.line_a = self.sale(self.chicken, 2, 260, "cash", "Anita", 20)
        self.sale_b, self.line_b = self.sale(self.eggs, 6, 8, "upi", "Rahul")
        self.refund = ReturnDocument.objects.create(
            sale=self.sale_a, reason="Customer return", payment_method="cash"
        )
        ReturnLine.objects.create(
            document=self.refund, sale_line=self.line_a, quantity=1, restock=True
        )
        post_return(self.refund.pk)
        self.credit = ReturnDocument.objects.create(
            purchase=self.purchase_b, reason="Supplier return"
        )
        ReturnLine.objects.create(
            document=self.credit, purchase_line=self.purchase_b.lines.get(), quantity=10
        )
        post_return(self.credit.pk)

    def purchase(self, product, quantity, cost, supplier):
        document = Purchase.objects.create(supplier=supplier)
        PurchaseLine.objects.create(
            purchase=document, product=product, quantity=quantity, unit_cost=cost
        )
        post_purchase(document.pk)
        return document

    def sale(self, product, quantity, price, payment="cash", customer="", discount=0):
        document = Sale.objects.create(
            customer_name=customer, payment_method=payment, discount=discount
        )
        line = SaleLine.objects.create(
            sale=document, product=product, quantity=quantity, unit_price=price
        )
        post_sale(document.pk)
        document.refresh_from_db()
        line.refresh_from_db()
        return document, line

    def report(self, tab="overview", **filters):
        response = self.client.get("/reports/", {"tab": tab, **filters})
        self.assertEqual(response.status_code, 200)
        return response

    def test_overview_reconciles_bills_purchases_refunds_and_supplier_credits(self):
        response = self.report()
        self.assertEqual(response.context["sales_total"], 298)
        self.assertEqual(response.context["purchase_total"], 1900)
        self.assertEqual(response.context["gross_profit"], 88)
        self.assertEqual(response.context["operating_profit"], 88)
        self.assertEqual(response.context["refunds"], 250)
        self.assertEqual(response.context["purchase_returns"], 50)
        self.assertEqual(response.context["purchase_count"], 2)
        self.assertContains(response, "Latest purchases")
        self.assertContains(response, "View all sales")
        self.assertContains(response, "View all purchases")
        daily = response.context["daily_rows"][0]
        self.assertEqual(daily["net_sales"], 298)
        self.assertEqual(daily["net_purchases"], 1900)
        self.assertEqual(daily["bills"], 2)
        self.assertEqual(
            sum(row["net"] for row in response.context["payment_summary"]), 298
        )

    def test_sales_register_search_payment_and_totals_match_rows(self):
        response = self.report("sales", payment="upi", q="Rahul")
        rows = list(response.context["page"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], self.sale_b.pk)
        self.assertEqual(rows[0]["amount"], 48)
        self.assertEqual(response.context["report_metrics"][1]["value"], 48)
        self.assertContains(response, f"/invoice/{self.sale_b.pk}/")
        response = self.report("sales", q=self.sale_a.invoice_number)
        self.assertEqual(response.context["row_count"], 1)
        self.assertEqual(response.context["page"][0]["discount"], 20)
        self.assertEqual(response.context["page"][0]["gross_profit"], 140)

    def test_purchase_register_supplier_filter_and_supplier_totals(self):
        response = self.report("purchases")
        self.assertEqual(response.context["row_count"], 2)
        suppliers = {row["name"]: row for row in response.context["supplier_rows"]}
        self.assertEqual(suppliers["Alpha Foods"]["net"], 1800)
        self.assertEqual(suppliers["Beta Farm"]["received"], 150)
        self.assertEqual(suppliers["Beta Farm"]["credits"], 50)
        self.assertEqual(suppliers["Beta Farm"]["net"], 100)
        self.assertContains(response, f"/purchases/{self.purchase_a.pk}/")
        filtered = self.report("purchases", supplier="Beta Farm")
        self.assertEqual(filtered.context["row_count"], 1)
        self.assertEqual(filtered.context["report_metrics"][1]["value"], 150)
        self.assertEqual(filtered.context["page"][0]["quantity"], "30 pc")
        searched = self.report("purchases", q=f"Purchase #{self.purchase_a.pk}")
        self.assertEqual(searched.context["row_count"], 1)

    def test_items_reconcile_costs_quantities_and_profit_without_mixing_units(self):
        response = self.report("items")
        items = {row["name"]: row for row in response.context["page"]}
        chicken = items["Chicken"]
        eggs = items["Eggs"]
        self.assertEqual(chicken["sold"], 2)
        self.assertEqual(chicken["customer_returns"], 1)
        self.assertEqual(chicken["restocked"], 1)
        self.assertEqual(chicken["net_sold"], 1)
        self.assertEqual(chicken["revenue"], 250)
        self.assertEqual(chicken["gross_profit"], 70)
        self.assertEqual(eggs["unit"], "pc")
        self.assertEqual(eggs["net_sold"], 6)
        self.assertEqual(eggs["purchased"], 30)
        self.assertEqual(eggs["supplier_returns"], 10)
        self.assertEqual(eggs["net_purchased"], 20)
        self.assertEqual(eggs["purchase_value"], 100)
        self.assertEqual(eggs["gross_profit"], 18)
        self.assertEqual(
            sum(row["gross_profit"] for row in items.values()),
            response.context["gross_profit"],
        )
        self.assertEqual(response.context["report_metrics"][3]["value"], "1 kg, 6 pc")
        self.assertEqual(self.report("items", q="egg").context["row_count"], 1)

    def test_returns_register_has_original_references_and_credit_details(self):
        response = self.report("returns")
        self.assertEqual(response.context["row_count"], 2)
        self.assertContains(response, self.sale_a.invoice_number)
        self.assertContains(response, "Customer refund")
        self.assertContains(response, "Supplier return")
        self.assertContains(response, f"/returns/{self.refund.pk}/")
        filtered = self.report("returns", q="Beta Farm")
        self.assertEqual(filtered.context["row_count"], 1)
        self.assertEqual(filtered.context["page"][0]["amount"], 50)

    def test_return_of_an_old_bill_is_counted_on_the_credit_date(self):
        product = Product.objects.create(
            name="Old fish", sale_price=200, stock=5, average_cost=100
        )
        with patch(
            "shop.services.timezone.now",
            return_value=timezone.now() - timedelta(days=10),
        ):
            old, line = self.sale(product, 1, 200)
        credit = ReturnDocument.objects.create(
            sale=old, reason="Old bill returned today"
        )
        ReturnLine.objects.create(
            document=credit, sale_line=line, quantity=1, restock=False
        )
        post_return(credit.pk)
        response = self.report("sales")
        self.assertEqual(response.context["row_count"], 2)
        self.assertEqual(response.context["sales_total"], 98)
        self.assertEqual(response.context["gross_profit"], -112)
        response = self.report("items", q="Old fish")
        row = response.context["page"][0]
        self.assertEqual(row["net_sold"], -1)
        self.assertEqual(row["revenue"], -200)
        self.assertEqual(row["gross_profit"], -200)
        self.assertEqual(self.report("returns").context["row_count"], 3)

    def test_credit_of_an_old_purchase_still_appears_in_supplier_summary(self):
        with patch(
            "shop.services.timezone.now",
            return_value=timezone.now() - timedelta(days=10),
        ):
            old = self.purchase(self.chicken, 2, 100, "Old Vendor")
        credit = ReturnDocument.objects.create(
            purchase=old, reason="Old goods returned today"
        )
        ReturnLine.objects.create(
            document=credit, purchase_line=old.lines.get(), quantity=1
        )
        post_return(credit.pk)
        response = self.report("purchases")
        self.assertEqual(response.context["row_count"], 2)
        supplier = next(
            row
            for row in response.context["supplier_rows"]
            if row["name"] == "Old Vendor"
        )
        self.assertEqual(supplier["receipts"], 0)
        self.assertEqual(supplier["credits"], 100)
        self.assertEqual(supplier["net"], -100)
        self.assertEqual(response.context["purchase_total"], 1800)

    def test_historical_reports_do_not_use_current_selling_or_buying_prices(self):
        self.chicken.sale_price = 999
        self.chicken.default_purchase_price = 1
        self.chicken.save(update_fields=["sale_price", "default_purchase_price"])
        response = self.report("sales")
        self.assertEqual(response.context["sales_total"], 298)
        self.assertEqual(response.context["gross_profit"], 88)
        self.assertEqual(response.context["purchase_total"], 1900)

    def test_gst_is_excluded_from_item_revenue_and_refunded_with_credit(self):
        ShopSettings.objects.create(
            name="Shop",
            address="Street",
            gstin="27ABCDE1234F1Z5",
            state_code="27",
            signature="Owner",
            mode="tax",
        )
        self.chicken.tax_rate = 18
        self.chicken.save(update_fields=["tax_rate"])
        sale, line = self.sale(self.chicken, 1, 100)
        response = self.report("items", q="Chicken")
        self.assertEqual(response.context["page"][0]["revenue"], 350)
        self.assertEqual(response.context["tax_collected"], 18)
        credit = ReturnDocument.objects.create(sale=sale, reason="Tax invoice return")
        ReturnLine.objects.create(
            document=credit, sale_line=line, quantity=1, restock=True
        )
        post_return(credit.pk)
        response = self.report("items", q="Chicken")
        self.assertEqual(response.context["page"][0]["revenue"], 250)
        self.assertEqual(response.context["tax_collected"], 0)

    def test_date_and_search_filters_are_preserved_in_navigation_and_csv(self):
        today = timezone.localdate().isoformat()
        response = self.report(
            "sales", start=today, end=today, q="Anita", payment="cash"
        )
        export = parse_qs(urlparse(response.context["export_url"]).query)
        self.assertEqual(export["q"], ["Anita"])
        self.assertEqual(export["payment"], ["cash"])
        self.assertEqual(export["tab"], ["sales"])
        for link in response.context["period_links"]:
            self.assertIn("tab=sales", link["url"])
            self.assertIn("q=Anita", link["url"])
        for tab in response.context["report_tabs"]:
            self.assertIn(f"start={today}", tab["url"])
            self.assertNotIn("q=", tab["url"])

    def test_csv_matches_selected_register_filters(self):
        response = self.client.get(
            "/reports/", {"tab": "sales", "payment": "upi", "format": "csv"}
        )
        rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Bill"], self.sale_b.invoice_number)
        self.assertEqual(Decimal(rows[0]["Bill total"]), 48)
        response = self.client.get(
            "/reports/",
            {"tab": "purchases", "supplier": "Alpha Foods", "format": "csv"},
        )
        rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(Decimal(rows[0]["Purchase cost"]), 1800)

    def test_csv_download_includes_every_matching_page(self):
        self.eggs.stock = 100
        self.eggs.save(update_fields=["stock"])
        for _ in range(26):
            self.sale(self.eggs, 1, 8)
        response = self.report("sales", payment="cash")
        self.assertEqual(response.context["row_count"], 27)
        self.assertEqual(len(response.context["page"]), 25)
        self.assertTrue(response.context["next_page_url"])
        exported = self.client.get("/reports/" + response.context["export_url"])
        rows = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 27)
        printed = self.client.get("/reports/" + response.context["print_url"])
        self.assertTrue(printed.context["print_all"])
        self.assertEqual(len(printed.context["page"]), 27)
        self.assertFalse(printed.context["page"].has_other_pages())

    def test_csv_neutralizes_formula_text_from_suppliers_and_customers(self):
        self.purchase(self.chicken, 1, 180, "=2+2")
        response = self.client.get(
            "/reports/", {"tab": "purchases", "format": "csv", "q": "=2+2"}
        )
        rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(rows[0]["Supplier"], "'=2+2")

    def test_unknown_tab_and_empty_search_are_safe_and_reports_do_not_change_stock(
        self,
    ):
        before = list(Product.objects.values_list("pk", "stock", "average_cost"))
        movements = StockMovement.objects.count()
        response = self.report("unknown", q="ignored")
        self.assertEqual(response.context["tab"], "overview")
        self.assertEqual(
            self.report("sales", q="does not exist").context["row_count"], 0
        )
        self.assertEqual(
            list(Product.objects.values_list("pk", "stock", "average_cost")), before
        )
        self.assertEqual(StockMovement.objects.count(), movements)

    def test_staff_without_financial_report_permission_cannot_download_registers(self):
        call_command("setup_roles", verbosity=0)
        cashier = get_user_model().objects.create_user("cashier", is_staff=True)
        cashier.groups.add(Group.objects.get(name="Cashier"))
        self.client.force_login(cashier)
        for tab in ("overview", "sales", "purchases", "items", "returns"):
            self.assertEqual(
                self.client.get("/reports/", {"tab": tab}).status_code, 403
            )
            self.assertEqual(
                self.client.get("/reports/", {"tab": tab, "format": "csv"}).status_code,
                403,
            )

    def test_switching_report_tabs_keeps_the_preset_highlighted(self):
        response = self.report("sales", period="week")
        purchase_link = next(
            tab["url"]
            for tab in response.context["report_tabs"]
            if tab["key"] == "purchases"
        )
        response = self.client.get("/reports/" + purchase_link)
        self.assertEqual(response.context["period"], "week")
        self.assertTrue(
            next(
                link["active"]
                for link in response.context["period_links"]
                if link["label"] == "Last 7 days"
            )
        )
