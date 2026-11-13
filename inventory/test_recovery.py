import os
import uuid
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .models import AdminPasswordReset


class RecoveryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "existing-admin", password="Original-long-password-18!"
        )

    def recover(self, password="", request_id="", username=""):
        with patch.dict(
            os.environ,
            {
                "MEATFLOW_RESET_PASSWORD": password,
                "MEATFLOW_RESET_ID": request_id,
                "MEATFLOW_RESET_USERNAME": username,
            },
        ):
            output = StringIO()
            call_command("recover_admin", stdout=output)
            return output.getvalue()

    def test_reset_once_does_not_reapply_after_user_changes_password(self):
        request_id = str(uuid.uuid4())
        password = "New-random-password-982!"
        output = self.recover(password, request_id)
        self.assertNotIn(password, output)
        self.assertIn(self.user.username, output)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(password))
        self.assertTrue(
            self.client.login(username=self.user.username, password=password)
        )
        self.user.set_password("Later-private-password-627!")
        self.user.save()
        self.recover(password, request_id)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Later-private-password-627!"))
        self.assertEqual(AdminPasswordReset.objects.count(), 1)

    def test_absent_or_invalid_recovery_does_not_change_account(self):
        self.recover()
        for password, request_id in [
            ("weak", str(uuid.uuid4())),
            ("Strong-test-password-19!", "not-uuid"),
            ("", str(uuid.uuid4())),
        ]:
            with self.assertRaises(CommandError):
                self.recover(password, request_id)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Original-long-password-18!"))
        self.assertFalse(AdminPasswordReset.objects.exists())

    def test_multiple_admins_require_explicit_username(self):
        get_user_model().objects.create_superuser(
            "second-admin", password="Another-long-password-982!"
        )
        with self.assertRaises(CommandError):
            self.recover("New-random-password-982!", str(uuid.uuid4()))
        self.recover("New-random-password-982!", str(uuid.uuid4()), "existing-admin")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("New-random-password-982!"))

    def test_regular_user_is_never_promoted_or_reset(self):
        get_user_model().objects.create_user(
            "regular-user", password="Another-long-password-982!"
        )
        with self.assertRaises(CommandError):
            self.recover("New-random-password-982!", str(uuid.uuid4()), "regular-user")
        self.assertFalse(AdminPasswordReset.objects.exists())
