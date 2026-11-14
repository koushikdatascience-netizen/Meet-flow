"""One-use, Render-only stock cleanup with an atomic, persistent backup."""

import hashlib
import os
import uuid
import zlib

from django.apps import apps
from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from inventory.models import Company, StockClearBackup

# Explicit dependency order. Accounts, memberships, categories, configuration,
# invoice numbering and previous backups are deliberately excluded.
DELETE_ORDER = [
    "inventory.EntryRevision",
    "inventory.Line",
    "inventory.Entry",
    "inventory.Item",
    "shop.ReturnLine",
    "shop.ReturnDocument",
    "shop.StockMovement",
    "shop.StockAdjustment",
    "shop.SaleLine",
    "shop.Sale",
    "shop.PurchaseLine",
    "shop.Purchase",
    "shop.Expense",
    "shop.AuditEvent",
    "shop.Product",
]


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
        with transaction.atomic():
            list(Company.objects.select_for_update().order_by("pk"))
            if StockClearBackup.objects.filter(request_id=request_id).exists():
                self.stdout.write(
                    "Stock cleanup already completed for this ID. No data changed."
                )
                return
            models = [apps.get_model(label) for label in DELETE_ORDER]
            counts = {model._meta.label: model.objects.count() for model in models}
            objects = (
                obj
                for model in reversed(models)
                for obj in model.objects.all().order_by("pk").iterator()
            )
            raw = serializers.serialize("json", objects).encode("utf-8")
            compressed = zlib.compress(raw)
            digest = hashlib.sha256(raw).hexdigest()
            if hashlib.sha256(zlib.decompress(compressed)).hexdigest() != digest:
                raise CommandError("Backup verification failed. No data changed.")
            backup = StockClearBackup.objects.create(
                request_id=request_id,
                counts=counts,
                sha256=digest,
                compressed_data=compressed,
            )
            # This explicitly authorized reset bypasses normal document deletion
            # guards only for the allowlisted tables, within the backup transaction.
            quote = connection.ops.quote_name
            with connection.cursor() as cursor:
                for model in models:
                    cursor.execute("DELETE FROM " + quote(model._meta.db_table))
            self.stdout.write(
                f"Live stock cleanup complete. Persistent verified backup #{backup.pk} retained. Users, roles and categories preserved. Remove MEATFLOW_CLEAR_STOCK_ONCE from Render."
            )
