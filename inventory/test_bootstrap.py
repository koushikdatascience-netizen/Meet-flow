import os
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase


class BootstrapTests(TestCase):
    def run_bootstrap(self, username="", password=""):
        with patch.dict(
            os.environ,
            {
                "MEATFLOW_BOOTSTRAP_USERNAME": username,
                "MEATFLOW_BOOTSTRAP_PASSWORD": password,
            },
        ):
            output = StringIO()
            call_command("bootstrap_admin", stdout=output)
            return output.getvalue()

    def test_no_credentials_creates_no_default_account(self):
        self.run_bootstrap()
        self.assertFalse(get_user_model().objects.exists())

    def test_creates_hashed_admin_and_allows_login(self):
        password = "Random-test-password-876!"
        output = self.run_bootstrap("platform-owner", password)
        user = get_user_model().objects.get(username="platform-owner")
        self.assertTrue(user.is_staff and user.is_superuser)
        self.assertTrue(user.check_password(password))
        self.assertNotIn(password, output)
        self.assertTrue(self.client.login(username=user.username, password=password))
        self.assertRedirects(self.client.get("/"), "/companies/")

    def test_redeploy_does_not_reset_password_or_create_another_admin(self):
        self.run_bootstrap("platform-owner", "First-test-password-876!")
        self.run_bootstrap("another-owner", "Different-test-password-987!")
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertTrue(
            get_user_model().objects.get().check_password("First-test-password-876!")
        )

    def test_does_not_promote_existing_username(self):
        user = get_user_model().objects.create_user(
            "staff-user", password="Original-test-password-876!"
        )
        with self.assertRaises(CommandError):
            self.run_bootstrap("staff-user", "Different-test-password-987!")
        user.refresh_from_db()
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.check_password("Original-test-password-876!"))

    def test_rejects_weak_or_incomplete_credentials(self):
        for username, password in [
            ("owner", "12345678"),
            ("owner", ""),
            ("", "Random-test-password-876!"),
        ]:
            with self.assertRaises(CommandError):
                self.run_bootstrap(username, password)
        self.assertFalse(get_user_model().objects.exists())
