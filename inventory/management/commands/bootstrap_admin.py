"""Synchronize the platform administrator with owner-managed hosting credentials."""

import os

from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Create or update the platform admin using the two private hosting environment variables."

    def handle(self, *args, **options):
        username = os.environ.get("MEATFLOW_BOOTSTRAP_USERNAME", "").strip()
        password = os.environ.get("MEATFLOW_BOOTSTRAP_PASSWORD", "")
        if not username and not password:
            return
        User = get_user_model()
        if not username or not password:
            raise CommandError(
                "Set both MEATFLOW_BOOTSTRAP_USERNAME and MEATFLOW_BOOTSTRAP_PASSWORD, or remove both."
            )
        user = User(username=username, is_staff=True, is_superuser=True, is_active=True)
        try:
            User._meta.get_field("username").clean(username, user)
            password_validation.validate_password(password, user)
            with transaction.atomic():
                target = (
                    User.objects.select_for_update().filter(username=username).first()
                )
                if target and not target.is_superuser:
                    raise CommandError(
                        "Username belongs to a regular user. No accounts changed."
                    )
                if not target:
                    admins = list(
                        User.objects.select_for_update()
                        .filter(is_superuser=True)
                        .order_by("pk")[:2]
                    )
                    if len(admins) > 1:
                        raise CommandError(
                            "Multiple administrators exist. Set the username to the exact administrator to update."
                        )
                    target = admins[0] if admins else None
                if target:
                    target.username = username
                    target.is_active = True
                    target.is_staff = True
                    if not target.check_password(password):
                        target.set_password(password)
                    target.save(
                        update_fields=["username", "password", "is_active", "is_staff"]
                    )
                else:
                    User.objects.create_superuser(username=username, password=password)
        except ValidationError:
            raise CommandError(
                "Use a valid username and a strong, non-common password unrelated to the username."
            ) from None
        self.stdout.write(
            self.style.SUCCESS(
                "Administrator login synchronized with MEATFLOW_BOOTSTRAP_USERNAME and MEATFLOW_BOOTSTRAP_PASSWORD."
            )
        )
