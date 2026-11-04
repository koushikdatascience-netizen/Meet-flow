from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase, Client

from .models import Product, Purchase, PurchaseLine, Sale, SaleLine, AuditEvent
from .services import post_purchase, post_sale


class ItemScreenTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser(
            "owner", password="testing-password"
        )
        self.client.force_login(self.owner)
        self.product = Product.objects.create(
            name="Chicken", sale_price=260, default_purchase_price=180
        )

    def data(self, **overrides):
        values = {
            "name": "Fish",
            "unit": "kg",
            "default_purchase_price": "200",
            "sale_price": "300",
        }
        values.update(overrides)
        return values

    def test_main_menu_has_items_and_form_requires_only_four_fields(self):
        response = self.client.get("/pos/")
        self.assertContains(response, 'href="/items/">Items')
        response = self.client.get("/items/new/")
        form = response.context["form"]
        self.assertEqual(
            {name for name, field in form.fields.items() if field.required},
            {"name", "unit", "default_purchase_price", "sale_price"},
        )
        self.assertContains(response, "More options (optional)")

    def test_add_item_defaults_then_immediately_appears_in_purchase_picker(self):
        response = self.client.post("/items/new/", self.data(), follow=True)
        self.assertEqual(response.status_code, 200)
        product = Product.objects.get(name="Fish")
        self.assertEqual(product.stock, 0)
        self.assertEqual(product.default_purchase_price, 200)
        self.assertEqual(product.sale_price, 300)
        self.assertEqual(product.low_stock_at, 2)
        self.assertEqual(product.tax_rate, 0)
        products = self.client.get("/purchases/new/").context["products"]
        self.assertEqual(
            next(p for p in products if p["id"] == product.pk)["price"], "200.00"
        )
        self.assertTrue(
            AuditEvent.objects.filter(action="item.created", actor="owner").exists()
        )

    def test_invalid_prices_and_duplicate_name_keep_form_values_without_saving(self):
        response = self.client.post(
            "/items/new/", self.data(default_purchase_price="-1")
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("default_purchase_price", response.context["form"].errors)
        self.assertEqual(response.context["form"]["name"].value(), "Fish")
        response = self.client.post("/items/new/", self.data(name="Chicken"))
        self.assertIn("name", response.context["form"].errors)
        self.assertEqual(Product.objects.count(), 1)

    def test_edit_preserves_stock_cost_and_historical_invoice(self):
        purchase = Purchase.objects.create()
        PurchaseLine.objects.create(
            purchase=purchase, product=self.product, quantity=10, unit_cost=180
        )
        post_purchase(purchase.pk)
        sale = Sale.objects.create()
        SaleLine.objects.create(
            sale=sale, product=self.product, quantity=1, unit_price=260
        )
        post_sale(sale.pk)
        response = self.client.post(
            f"/items/{self.product.pk}/edit/",
            self.data(name="Chicken breast", stock="999", average_cost="1", unit="kg"),
        )
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 9)
        self.assertEqual(self.product.average_cost, 180)
        self.assertEqual(self.product.sale_price, 300)
        self.assertEqual(self.product.default_purchase_price, 200)
        sale.refresh_from_db()
        self.assertEqual(sale.total, 260)
        self.assertEqual(sale.lines.get().product_name, "Chicken")
        self.assertTrue(AuditEvent.objects.filter(action="item.changed").exists())

    def test_unit_cannot_change_after_stock_movements(self):
        purchase = Purchase.objects.create()
        PurchaseLine.objects.create(
            purchase=purchase, product=self.product, quantity=10, unit_cost=180
        )
        post_purchase(purchase.pk)
        response = self.client.post(
            f"/items/{self.product.pk}/edit/", self.data(name="Chicken", unit="pc")
        )
        self.assertIn("unit", response.context["form"].errors)
        self.product.refresh_from_db()
        self.assertEqual(self.product.unit, "kg")

    def test_delete_is_confirmed_and_reversible_without_touching_history(self):
        purchase = Purchase.objects.create()
        PurchaseLine.objects.create(
            purchase=purchase, product=self.product, quantity=10, unit_cost=180
        )
        post_purchase(purchase.pk)
        response = self.client.get(f"/items/{self.product.pk}/delete/")
        self.assertContains(
            response, "Past bills, purchases and stock records stay unchanged."
        )
        self.product.refresh_from_db()
        self.assertTrue(self.product.active)
        self.client.post(f"/items/{self.product.pk}/delete/")
        self.product.refresh_from_db()
        self.assertFalse(self.product.active)
        self.assertEqual(self.product.stock, 10)
        self.assertTrue(PurchaseLine.objects.filter(product=self.product).exists())
        self.assertEqual(self.client.get("/items/").context["page"].paginator.count, 0)
        self.assertContains(self.client.get("/items/?show=removed"), "Chicken")
        self.assertEqual(self.client.get("/pos/").context["products"], [])
        self.assertEqual(self.client.get("/purchases/new/").context["products"], [])
        self.assertEqual(
            self.client.get(f"/items/{self.product.pk}/restore/").status_code, 405
        )
        self.client.post(f"/items/{self.product.pk}/restore/")
        self.product.refresh_from_db()
        self.assertTrue(self.product.active)
        self.assertEqual(self.product.stock, 10)
        self.assertTrue(AuditEvent.objects.filter(action="item.removed").exists())
        self.assertTrue(AuditEvent.objects.filter(action="item.restored").exists())

    def test_inventory_manager_can_manage_items_but_cashier_cannot(self):
        call_command("setup_roles", verbosity=0)
        manager = get_user_model().objects.create_user("stock", is_staff=True)
        manager.groups.add(Group.objects.get(name="Inventory Manager"))
        self.client.force_login(manager)
        self.assertEqual(self.client.get("/items/").status_code, 200)
        self.assertEqual(self.client.post("/items/new/", self.data()).status_code, 302)
        self.assertEqual(
            self.client.post(f"/items/{self.product.pk}/delete/").status_code, 302
        )
        cashier = get_user_model().objects.create_user("cashier", is_staff=True)
        cashier.groups.add(Group.objects.get(name="Cashier"))
        self.client.force_login(cashier)
        self.assertNotContains(self.client.get("/pos/"), 'href="/items/">Items')
        for path in (
            "/items/",
            "/items/new/",
            f"/items/{self.product.pk}/edit/",
            f"/items/{self.product.pk}/delete/",
            f"/items/{self.product.pk}/restore/",
        ):
            self.assertEqual(self.client.get(path).status_code, 403)
            self.assertEqual(self.client.post(path, self.data()).status_code, 403)

    def test_search_pagination_and_optional_values(self):
        for index in range(27):
            Product.objects.create(name=f"Test {index:02}", sale_price=10)
        response = self.client.get("/items/")
        self.assertEqual(len(response.context["page"]), 25)
        response = self.client.get("/items/?page=2")
        self.assertEqual(len(response.context["page"]), 3)
        response = self.client.get("/items/?q=chick")
        self.assertEqual(response.context["page"].paginator.count, 1)
        self.assertContains(response, "Chicken")
        self.client.post(
            f"/items/{self.product.pk}/edit/",
            self.data(
                name="Chicken",
                category="Poultry",
                low_stock_at="3.5",
                hsn="0207",
                tax_rate="0",
            ),
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.category, "Poultry")
        self.assertEqual(self.product.low_stock_at, Decimal("3.5"))

    def test_item_posts_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        for path in (
            "/items/new/",
            f"/items/{self.product.pk}/edit/",
            f"/items/{self.product.pk}/delete/",
            f"/items/{self.product.pk}/restore/",
        ):
            self.assertEqual(client.post(path, self.data()).status_code, 403)
