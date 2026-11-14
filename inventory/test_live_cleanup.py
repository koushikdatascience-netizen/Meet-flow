import hashlib
import json
import os
import uuid
import zlib
from io import StringIO
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
from decimal import Decimal
from .models import (
    Company,
    Item,
    ItemCategory,
    Membership,
    Entry,
    Line,
    StockClearBackup,
    post_entry,
)
from shop.models import Product, Purchase, PurchaseLine
from shop.services import post_purchase


class LiveStockCleanupTests(TestCase):
    def setUp(self):
        self.company = Company.objects.get(is_primary=True)
        self.user = get_user_model().objects.create_superuser(
            "owner-cleanup", password="Strong-test-password-914!"
        )
        self.membership = Membership.objects.create(
            user=self.user,
            company=self.company,
            role="admin",
            must_change_password=False,
        )
        self.item = Item.objects.create(
            company=self.company, name="Stock item", category="Chicken"
        )
        post_entry(
            self.company,
            self.user,
            "opening",
            timezone.localdate(),
            "",
            uuid.uuid4(),
            [(self.item.pk, Decimal(5))],
        )
        self.product = Product.objects.create(name="Old product", sale_price=0)
        purchase = Purchase.objects.create()
        PurchaseLine.objects.create(
            purchase=purchase, product=self.product, quantity=3, unit_cost=0
        )
        post_purchase(purchase.pk)

    def clear(self, request_id="", render="true"):
        with patch.dict(
            os.environ, {"MEATFLOW_CLEAR_STOCK_ONCE": request_id, "RENDER": render}
        ):
            output = StringIO()
            call_command("clear_live_stock", stdout=output)
            return output.getvalue()

    def test_cleanup_is_atomic_backed_up_and_preserves_users_roles_categories(self):
        count = ItemCategory.objects.count()
        self.clear(str(uuid.uuid4()))
        self.assertFalse(Item.objects.exists())
        self.assertFalse(Entry.objects.exists())
        self.assertFalse(Line.objects.exists())
        self.assertFalse(Product.objects.exists())
        self.assertFalse(Purchase.objects.exists())
        self.assertEqual(ItemCategory.objects.count(), count)
        self.assertTrue(
            get_user_model()
            .objects.get(pk=self.user.pk)
            .check_password("Strong-test-password-914!")
        )
        self.assertEqual(Membership.objects.get(pk=self.membership.pk).role, "admin")
        backup = StockClearBackup.objects.get()
        raw = zlib.decompress(bytes(backup.compressed_data))
        self.assertEqual(hashlib.sha256(raw).hexdigest(), backup.sha256)
        data = json.loads(raw)
        self.assertTrue(any(row["model"] == "inventory.item" for row in data))
        self.assertTrue(any(row["model"] == "shop.purchaseline" for row in data))
        self.assertFalse(any(row["model"].startswith("auth.") for row in data))

    def test_same_id_never_clears_new_stock_after_redeploy(self):
        request_id = str(uuid.uuid4())
        self.clear(request_id)
        Item.objects.create(company=self.company, name="New stock", category="Fish")
        self.clear(request_id)
        self.assertTrue(Item.objects.filter(name="New stock").exists())
        self.assertEqual(StockClearBackup.objects.count(), 1)

    def test_no_flag_or_non_render_never_clears_local_stock(self):
        self.clear("")
        with self.assertRaises(CommandError):
            self.clear(str(uuid.uuid4()), render="")
        self.assertTrue(Item.objects.filter(pk=self.item.pk).exists())
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())
        self.assertFalse(StockClearBackup.objects.exists())

    def test_failed_backup_rolls_back_without_deleting(self):
        with patch(
            "inventory.management.commands.clear_live_stock.StockClearBackup.objects.create",
            side_effect=RuntimeError("backup unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                self.clear(str(uuid.uuid4()))
        self.assertTrue(Item.objects.filter(pk=self.item.pk).exists())
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())
