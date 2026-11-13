"""Deployment settings regressions, evaluated in fresh processes without network access."""

import json
import os
import subprocess
import sys
from django.test import SimpleTestCase


class RenderSettingsTests(SimpleTestCase):
    def settings_process(self, overrides):
        environment = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(
                ("DJANGO_", "POSTGRES_", "RENDER", "DATABASE_URL", "SQLITE_PATH")
            )
        }
        environment.update(
            {
                "DJANGO_SECRET_KEY": "x" * 64,
                "RENDER": "true",
                "RENDER_EXTERNAL_HOSTNAME": "example.onrender.com",
            }
        )
        environment.update(overrides)
        return subprocess.run(
            [
                sys.executable,
                "-c",
                "import json; from config import settings as s; print(json.dumps({'debug':s.DEBUG,'hosts':s.ALLOWED_HOSTS,'origins':s.CSRF_TRUSTED_ORIGINS,'database':s.DATABASES['default']['ENGINE'],'proxy':s.SECURE_PROXY_SSL_HEADER,'secure_cookie':s.SESSION_COOKIE_SECURE}))",
            ],
            env=environment,
            capture_output=True,
            text=True,
        )

    def test_render_uses_postgres_and_trusts_only_assigned_host(self):
        result = self.settings_process(
            {"DATABASE_URL": "postgresql://test:test@localhost:5432/test"}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertFalse(data["debug"])
        self.assertIn("example.onrender.com", data["hosts"])
        self.assertNotIn("*", data["hosts"])
        self.assertIn("https://example.onrender.com", data["origins"])
        self.assertEqual(data["database"], "django.db.backends.postgresql")
        self.assertTrue(data["secure_cookie"])
        self.assertEqual(data["proxy"], ["HTTP_X_FORWARDED_PROTO", "https"])

    def test_render_refuses_ephemeral_sqlite(self):
        result = self.settings_process({})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Ephemeral SQLite is disabled", result.stderr)

    def test_render_refuses_weak_secret(self):
        result = self.settings_process(
            {
                "DATABASE_URL": "postgresql://test:test@localhost/test",
                "DJANGO_SECRET_KEY": "short",
            }
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("at least 50 characters", result.stderr)
