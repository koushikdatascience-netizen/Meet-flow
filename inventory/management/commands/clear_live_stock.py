"""One-use, Render-only stock cleanup with an atomic, persistent backup."""

import os
import uuid

from django.core.management.base import BaseCommand, CommandError

from inventory.models import StockClearBackup
from inventory.cleanup import clear_stock


class Command(BaseCommand):
    help = "Clear live Render stock once, retaining a compressed database backup and all accounts, roles and categories."

    def handle(self, *args, **options):
        value = os.environ.get("MEATFLOW_CLEAR_STOCK_ONCE", "").strip()
        if not value:
            return
        if os.environ.get("RENDER") != "true":
            raise CommandError(
                "Stock cleanup is enabled only on Render. Local data was not changed."
            )
        try:
            request_id = uuid.UUID(value)
        except ValueError:
            raise CommandError("MEATFLOW_CLEAR_STOCK_ONCE must be a UUID.") from None
        backup = clear_stock(request_id)
        self.stdout.write(
            f"Live stock cleanup complete. Persistent verified backup #{backup.pk} retained. Users, roles and categories preserved. Remove MEATFLOW_CLEAR_STOCK_ONCE from Render."
        )
