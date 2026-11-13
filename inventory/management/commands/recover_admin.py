"""Explicit, one-use administrator recovery controlled by the hosting account owner."""

import os
import uuid

from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from inventory.models import AdminPasswordReset, Membership


class Command(BaseCommand):
    help = "Reset an existing active admin once per recovery ID using private environment variables."

    def handle(self, *args, **options):
        password = os.environ.get("MEATFLOW_RESET_PASSWORD", "")
        request_id = os.environ.get("MEATFLOW_RESET_ID", "").strip()
        username = os.environ.get("MEATFLOW_RESET_USERNAME", "").strip()
        if not password and not request_id and not username:
            return
        if not password or not request_id:
            raise CommandError(
                "Set MEATFLOW_RESET_PASSWORD and MEATFLOW_RESET_ID, or remove all reset variables."
            )
        try:
            request_id = uuid.UUID(request_id)
        except ValueError:
            raise CommandError("MEATFLOW_RESET_ID must be a UUID.") from None
        with transaction.atomic():
            # Unique insert serializes concurrent attempts for the same recovery ID.
            if AdminPasswordReset.objects.filter(request_id=request_id).exists():
                self.stdout.write("Recovery ID already used. No password changed.")
                return
            candidates = (
                get_user_model()
                .objects.select_for_update()
                .filter(is_superuser=True, is_active=True)
                .order_by("pk")
            )
            if username:
                candidates = candidates.filter(username=username)
            users = list(candidates[:2])
            if len(users) != 1:
                raise CommandError(
                    "Specify MEATFLOW_RESET_USERNAME matching one existing active superuser. No account changed."
                )
            user = users[0]
            # Recheck after acquiring the user lock.
            if AdminPasswordReset.objects.filter(request_id=request_id).exists():
                self.stdout.write("Recovery ID already used. No password changed.")
                return
            try:
                password_validation.validate_password(password, user)
            except ValidationError:
                raise CommandError(
                    "Choose a strong, non-common password unrelated to the username."
                ) from None
            AdminPasswordReset.objects.create(request_id=request_id, user=user)
            user.set_password(password)
            user.save(update_fields=["password"])
            Membership.objects.filter(user=user).update(must_change_password=False)
        self.stdout.write(
            "Administrator password reset completed. Username: " + user.username
        )
        self.stdout.write(
            "Remove all MEATFLOW_RESET_* and MEATFLOW_BOOTSTRAP_* variables from Render. This recovery ID cannot be reused."
        )
