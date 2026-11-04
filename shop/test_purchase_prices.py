from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from .forms import PurchaseEntryLineForm
from .models import Product, Purchase, PurchaseLine, Sale
from .services import post_purchase


class PurchasePriceTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            name="Chicken", sale_price=260, default_purchase_price=180
        )
        self.owner = get_user_model().objects.create_superuser(
            "owner", password="testing-password"
        )
        self.client.force_login(self.owner)

    def suggested(self):
        return (
            Product.objects.with_purchase_price()
            .get(pk=self.product.pk)
            .suggested_purchase_price
        )

    def purchase(self, cost, quantity=10, created_at=None, post=True):
        values = {"created_at": created_at} if created_at else {}
        purchase = Purchase.objects.create(**values)
        PurchaseLine.objects.create(
            purchase=purchase, product=self.product, quantity=quantity, unit_cost=cost
        )
        if post:
            post_purchase(purchase.pk)
        return purchase

    def payload(self, cost=None):
        response = self.client.get("/purchases/new/")
        data = {
            "token": str(response.context["form"]["token"].value()),
            "supplier": "Supplier",
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "1",
            "items-MAX_NUM_FORMS": "100",
            "items-0-product": str(self.product.pk),
            "items-0-quantity": "10",
        }
        if cost is not None:
            data["items-0-unit_cost"] = str(cost)
        return data

    def test_first_purchase_suggests_default_buying_price(self):
        self.assertEqual(self.suggested(), 180)
        response = self.client.get("/purchases/new/")
        self.assertEqual(response.context["products"][0]["price"], "180.00")

    def test_last_actual_cost_is_used_instead_of_weighted_average(self):
        self.purchase(100)
        self.purchase(200)
        self.product.refresh_from_db()
        self.assertEqual(self.product.average_cost, 150)
        self.assertEqual(self.suggested(), 200)
        self.assertEqual(self.product.default_purchase_price, 180)
        self.assertEqual(self.product.sale_price, 260)

    def test_draft_purchase_does_not_change_suggested_price(self):
        self.purchase(190)
        self.purchase(999, post=False)
        self.assertEqual(self.suggested(), 190)

    def test_posting_order_wins_over_creation_date_and_id(self):
        older = self.purchase(
            210, created_at=timezone.now() - timedelta(days=20), post=False
        )
        posted_at = timezone.now()
        with patch("shop.services.timezone.now", return_value=posted_at):
            self.purchase(190)
        with patch(
            "shop.services.timezone.now", return_value=posted_at + timedelta(seconds=1)
        ):
            post_purchase(older.pk)
        self.assertEqual(self.suggested(), 210)

    def test_zero_actual_purchase_price_does_not_fall_back_to_default(self):
        self.purchase(0)
        self.assertEqual(self.suggested(), 0)

    def test_changing_default_does_not_replace_last_actual_price(self):
        self.purchase(190)
        self.product.default_purchase_price = 220
        self.product.save(update_fields=["default_purchase_price"])
        self.assertEqual(self.suggested(), 190)

    def test_blank_cost_uses_suggestion_and_saves_actual_line_price(self):
        response = self.client.post("/purchases/new/", self.payload(""))
        self.assertEqual(response.status_code, 302)
        purchase = Purchase.objects.get()
        self.assertTrue(purchase.posted)
        self.assertEqual(purchase.lines.get().unit_cost, 180)
        self.assertEqual(purchase.total, 1800)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)

    def test_omitted_cost_uses_last_posted_price(self):
        self.purchase(195)
        response = self.client.post("/purchases/new/", self.payload())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Purchase.objects.latest("pk").lines.get().unit_cost, 195)

    def test_price_override_becomes_the_next_suggestion(self):
        response = self.client.post("/purchases/new/", self.payload(205))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.suggested(), 205)
        self.product.refresh_from_db()
        self.assertEqual(self.product.default_purchase_price, 180)
        self.assertEqual(self.product.sale_price, 260)

    def test_invalid_cost_is_not_replaced_with_a_valid_suggestion(self):
        for invalid in ("-1", "wrong"):
            form = PurchaseEntryLineForm(
                data={"product": self.product.pk, "quantity": 1, "unit_cost": invalid}
            )
            self.assertFalse(form.is_valid())
            self.assertIn("unit_cost", form.errors)
        self.assertEqual(Purchase.objects.count(), 0)

    def test_selling_price_and_profit_use_correct_sources(self):
        self.purchase(180)
        response = self.client.get("/pos/")
        self.assertEqual(response.context["products"][0]["price"], "260.00")
        self.assertNotContains(response, "Default buying price")
        data = {
            "token": str(response.context["form"]["token"].value()),
            "discount": "0",
            "payment_method": "cash",
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "1",
            "items-MAX_NUM_FORMS": "100",
            "items-0-product": str(self.product.pk),
            "items-0-quantity": "1.5",
        }
        self.assertEqual(self.client.post("/pos/", data).status_code, 302)
        sale = Sale.objects.get()
        self.assertEqual(sale.total, 390)
        self.assertEqual(sale.gross_profit, 120)
        self.assertEqual(sale.lines.get().cost_at_sale, 180)

    def test_buying_price_is_validated_and_visible_in_product_setup(self):
        response = self.client.get("/admin/shop/product/add/")
        self.assertContains(response, "Default buying price")
        self.assertContains(response, "Selling price")
        self.product.default_purchase_price = Decimal("-1")
        with self.assertRaises(ValidationError):
            self.product.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.create(
                name="Invalid", sale_price=260, default_purchase_price=-1
            )

    def test_suggestions_for_multiple_products_use_one_query(self):
        Product.objects.create(name="Fish", sale_price=300, default_purchase_price=200)
        with self.assertNumQueries(1):
            values = list(
                Product.objects.with_purchase_price().values_list(
                    "name", "suggested_purchase_price"
                )
            )
        self.assertEqual(
            dict(values), {"Chicken": Decimal("180"), "Fish": Decimal("200")}
        )
