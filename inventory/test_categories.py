from django.contrib.auth import get_user_model
from django.test import TestCase
from .models import Company, ItemCategory, Item, Membership, Line


class CategoryManagementTests(TestCase):
    def setUp(self):
        self.company = Company.objects.get(is_primary=True)
        self.user = get_user_model().objects.create_user(
            "category-admin", password="Strong-test-password-681!"
        )
        self.member = Membership.objects.create(
            user=self.user,
            company=self.company,
            role="admin",
            must_change_password=False,
        )
        self.client.force_login(self.user)

    def test_add_custom_category_and_select_for_new_item(self):
        self.assertRedirects(
            self.client.post("/categories/", {"name": "Vegetables"}), "/categories/"
        )
        category = ItemCategory.objects.get(company=self.company, name="Vegetables")
        self.assertContains(self.client.get("/items/"), "Vegetables")
        response = self.client.post(
            "/items/", {"name": "Potato", "category": category.name, "active": "on"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Item.objects.get(name="Potato").category, "Vegetables")
        self.assertIn("Vegetables", self.client.get("/reports/").context["categories"])

    def test_rename_updates_items_and_report_filter(self):
        category = ItemCategory.objects.get(company=self.company, name="Fish")
        item = Item.objects.create(
            company=self.company, name="Fish pieces", category="Fish"
        )
        self.assertEqual(
            self.client.post(
                f"/categories/?edit={category.pk}", {"name": "Seafood"}
            ).status_code,
            302,
        )
        item.refresh_from_db()
        self.assertEqual(item.category, "Seafood")
        response = self.client.get("/reports/?category=Seafood")
        self.assertEqual(response.context["rows"][0][1], item.name)
        self.assertEqual(
            self.client.get("/items/?category=Seafood")
            .context["form"]
            .initial["category"],
            "Seafood",
        )

    def test_delete_empty_category_and_reject_in_use(self):
        empty = ItemCategory.objects.create(company=self.company, name="Empty")
        self.assertEqual(
            self.client.post(f"/categories/{empty.pk}/delete/").status_code, 302
        )
        self.assertFalse(ItemCategory.objects.filter(pk=empty.pk).exists())
        category = ItemCategory.objects.get(company=self.company, name="Chicken")
        Item.objects.create(
            company=self.company, name="Chicken item", category="Chicken", active=False
        )
        response = self.client.post(f"/categories/{category.pk}/delete/")
        self.assertContains(response, "Move the items")
        self.assertTrue(ItemCategory.objects.filter(pk=category.pk).exists())

    def test_permissions_scope_and_duplicate_names(self):
        response = self.client.post("/categories/", {"name": "chicken"})
        self.assertContains(response, "already exists")
        other = Company.objects.create(name="Archive")
        foreign = ItemCategory.objects.create(company=other, name="Other category")
        self.assertEqual(
            self.client.post(
                f"/categories/?edit={foreign.pk}", {"name": "Changed"}
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(f"/categories/{foreign.pk}/delete/").status_code, 404
        )
        self.member.role = "manager"
        self.member.save()
        self.assertEqual(
            self.client.post("/categories/", {"name": "Forbidden"}).status_code, 403
        )
