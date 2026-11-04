from django.contrib.auth import get_user_model
from django.test import TestCase
from .models import Product, Sale, SaleLine, ShopSettings
from .services import post_sale
from .states import normalize_state


class BillingStateTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser(
            "owner", password="testing-password"
        )
        self.client.force_login(self.owner)
        self.product = Product.objects.create(
            name="Chicken",
            sale_price=100,
            stock=10,
            average_cost=50,
            hsn="0207",
            tax_rate=18,
        )

    def tax_profile(self):
        return ShopSettings.objects.create(
            name="Shop",
            address="Shop address",
            gstin="27ABCDE1234F1Z5",
            state_code="27",
            signature="Owner",
            mode="tax",
        )

    def payload(self, **extra):
        response = self.client.get("/pos/")
        data = {
            "token": str(response.context["form"]["token"].value()),
            "discount": "0",
            "payment_method": "cash",
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "1",
            "items-MAX_NUM_FORMS": "100",
            "items-0-product": str(self.product.pk),
            "items-0-quantity": "1",
        }
        data.update(extra)
        return data

    def test_plain_receipt_hides_gst_fields_and_does_not_require_a_state(self):
        response = self.client.get("/pos/")
        self.assertNotIn("customer_state", response.context["form"].fields)
        self.assertNotIn("customer_gstin", response.context["form"].fields)
        self.assertContains(response, "Discount &amp; customer details")
        self.assertEqual(self.client.post("/pos/", self.payload()).status_code, 302)
        self.assertEqual(Sale.objects.get().tax_total, 0)

    def test_old_receipt_form_state_input_no_longer_blocks_checkout(self):
        response = self.client.post(
            "/pos/",
            self.payload(
                customer_state="UP", customer_gstin="not-needed-for-a-receipt"
            ),
        )
        self.assertEqual(response.status_code, 302)
        sale = Sale.objects.get()
        self.assertEqual(sale.customer_state, "")
        self.assertEqual(sale.customer_gstin, "")
        self.assertEqual(sale.total, 100)

    def test_tax_mode_uses_a_named_state_dropdown(self):
        self.tax_profile()
        response = self.client.get("/pos/")
        self.assertContains(response, '<select name="customer_state"')
        self.assertContains(response, "Uttar Pradesh")
        self.assertContains(response, 'value="09"')
        self.assertFalse(response.context["form"].fields["customer_state"].required)

    def test_one_digit_or_state_abbreviation_is_normalized(self):
        self.tax_profile()
        for value in ("9", "UP", "Uttar Pradesh", "09"):
            response = self.client.post("/pos/", self.payload(customer_state=value))
            self.assertEqual(response.status_code, 302)
            sale = Sale.objects.latest("pk")
            self.assertEqual(sale.customer_state, "09")
            self.assertEqual(sale.lines.get().igst, 18)

    def test_invalid_tax_state_is_a_field_error_and_does_not_change_stock(self):
        self.tax_profile()
        response = self.client.post("/pos/", self.payload(customer_state="wrong"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("customer_state", response.context["form"].errors)
        self.assertEqual(Sale.objects.count(), 0)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 10)

    def test_customer_gstin_fills_its_state_when_not_selected(self):
        self.tax_profile()
        response = self.client.post(
            "/pos/",
            self.payload(
                customer_name="Customer",
                customer_address="Customer address",
                customer_gstin="29abcde1234f1z5",
            ),
        )
        self.assertEqual(response.status_code, 302)
        sale = Sale.objects.get()
        self.assertEqual(sale.customer_state, "29")
        self.assertEqual(sale.customer_gstin, "29ABCDE1234F1Z5")
        self.assertEqual(sale.lines.get().igst, 18)

    def test_conflicting_gstin_and_state_still_rejected(self):
        self.tax_profile()
        response = self.client.post(
            "/pos/",
            self.payload(
                customer_name="Customer",
                customer_address="Customer address",
                customer_gstin="29ABCDE1234F1Z5",
                customer_state="09",
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "matching state")
        self.assertEqual(Sale.objects.count(), 0)

    def test_service_accepts_one_digit_tax_state_and_plain_receipt_abbreviation(self):
        sale = Sale.objects.create(customer_state="UP")
        SaleLine.objects.create(
            sale=sale, product=self.product, quantity=1, unit_price=100
        )
        post_sale(sale.pk)
        self.tax_profile()
        tax_sale = Sale.objects.create(customer_state="9")
        SaleLine.objects.create(
            sale=tax_sale, product=self.product, quantity=1, unit_price=100
        )
        post_sale(tax_sale.pk)
        tax_sale.refresh_from_db()
        self.assertEqual(tax_sale.customer_state, "09")

    def test_state_normalization_retains_leading_zero_and_rejects_unknown_values(self):
        self.assertEqual(normalize_state(" 09 "), "09")
        self.assertEqual(normalize_state("delhi"), "07")
        self.assertEqual(normalize_state(""), "")
        for invalid in ("00", "999", "??"):
            with self.assertRaises(ValueError):
                normalize_state(invalid)
