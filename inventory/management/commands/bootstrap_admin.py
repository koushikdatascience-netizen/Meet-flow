"""Optional first administrator creation for hosts without an interactive shell."""

import os

from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Create the first platform admin from private environment variables; never reset an account."

    def handle(self, *args, **options):
        username = os.environ.get("MEATFLOW_BOOTSTRAP_USERNAME", "").strip()
        password = os.environ.get("MEATFLOW_BOOTSTRAP_PASSWORD", "")
        if not username and not password:
            return
        User = get_user_model()
        if User.objects.filter(is_superuser=True).exists():
            self.stdout.write(
                "An administrator already exists. Bootstrap skipped; no accounts changed."
            )
            return
        if not username or not password:
            raise CommandError(
                "Set both MEATFLOW_BOOTSTRAP_USERNAME and MEATFLOW_BOOTSTRAP_PASSWORD, or remove both."
            )
        user = User(username=username, is_staff=True, is_superuser=True, is_active=True)
        try:
            User._meta.get_field("username").clean(username, user)
            password_validation.validate_password(password, user)
            with transaction.atomic():
                if User.objects.filter(username=username).exists():
                    raise CommandError(
                        "Bootstrap username is already in use. Existing accounts are never promoted or reset."
                    )
                User.objects.create_superuser(username=username, password=password)
        except ValidationError:
            raise CommandError(
                "Use a valid username and a strong, non-common password unrelated to the username."
            ) from None
        self.stdout.write(
            self.style.SUCCESS(
                "First administrator created. Remove both MEATFLOW_BOOTSTRAP variables from the hosting environment."
            )
        )
