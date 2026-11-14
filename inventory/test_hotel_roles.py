from django.contrib.auth import get_user_model
from django.test import TestCase
from .models import Company, Membership
from .forms import UserForm


class HotelRoleTests(TestCase):
    def setUp(self):
        self.company = Company.objects.get(is_primary=True)
        self.user = get_user_model().objects.create_user(
            "hotel-staff", password="Strong-test-password-284!"
        )
        self.member = Membership.objects.create(
            company=self.company,
            user=self.user,
            role="cook",
            features=["sale"],
            must_change_password=False,
        )
        self.client.force_login(self.user)

    def test_hotel_roles_are_available(self):
        values = dict(UserForm().fields["role"].choices)
        for role in ["manager", "worker", "support", "cook", "server"]:
            self.assertIn(role, values)

    def test_cook_can_record_stock_out_but_not_purchases_users_or_delete(self):
        self.assertEqual(self.client.get("/entries/sale/").status_code, 200)
        for path in [
            "/entries/purchase/",
            "/entries/opening/",
            "/users/",
            "/items/",
            "/reports/",
            "/entries/1/delete/",
        ]:
            self.assertEqual(self.client.get(path).status_code, 403)

    def test_every_hotel_role_obeys_explicit_feature_permissions(self):
        for role in ["manager", "worker", "support", "cook", "server"]:
            self.member.role = role
            self.member.features = ["purchase"]
            self.member.save()
            self.assertEqual(self.client.get("/entries/purchase/").status_code, 200)
            self.assertEqual(self.client.get("/entries/sale/").status_code, 403)
            self.assertEqual(self.client.get("/users/").status_code, 403)
            self.assertEqual(self.client.get("/items/").status_code, 403)

    def test_admin_can_create_staff_role_with_selected_access(self):
        owner = get_user_model().objects.create_superuser(
            "owner-test", password="Strong-test-password-594!"
        )
        self.client.force_login(owner)
        response = self.client.post(
            "/users/",
            {
                "username": "new-cook",
                "temporary_password": "Strong-test-password-817!",
                "role": "cook",
                "features": ["sale"],
                "active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        membership = Membership.objects.get(user__username="new-cook")
        self.assertEqual(membership.role, "cook")
        self.assertEqual(membership.allowed_features, ["sale_add"])
        self.assertFalse(membership.must_change_password)
        self.assertContains(self.client.get("/users/"), "Server / serving staff")
