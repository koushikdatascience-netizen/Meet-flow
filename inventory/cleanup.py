"""Atomic stock reset shared by the owner screen and optional deployment command."""

import hashlib
import zlib
from django.apps import apps
from django.core import serializers
from django.core.management.base import CommandError
from django.db import connection, transaction
from .models import Company, StockClearBackup

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


def clear_stock(request_id):
    with transaction.atomic():
        list(Company.objects.select_for_update().order_by("pk"))
        if StockClearBackup.objects.filter(request_id=request_id).exists():
            return StockClearBackup.objects.get(request_id=request_id)
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
    return backup
