import uuid

from django.conf import settings
from django.db import migrations
from django.utils import timezone


def import_stock(apps, schema_editor):
    Company = apps.get_model("inventory", "Company")
    Item = apps.get_model("inventory", "Item")
    Entry = apps.get_model("inventory", "Entry")
    Line = apps.get_model("inventory", "Line")
    Membership = apps.get_model("inventory", "Membership")
    Product = apps.get_model("shop", "Product")
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    owner = (
        User.objects.filter(is_superuser=True, is_active=True).order_by("pk").first()
    )
    products = list(Product.objects.all())
    if not products and not owner:
        return
    company = Company.objects.create(name="My shop")
    if products and not owner:
        raise RuntimeError(
            "Create an active superuser before upgrading so imported opening stock has an accountable owner."
        )
    if owner:
        Membership.objects.create(
            user=owner, company=company, role="admin", must_change_password=False
        )
    entry = None
    for product in products:
        item = Item.objects.create(
            company=company,
            name=product.name,
            category=product.category,
            unit=product.unit,
            active=product.active,
        )
        if product.stock:
            if entry is None:
                entry = Entry.objects.create(
                    company=company,
                    kind="opening",
                    date=timezone.localdate(),
                    note="Imported current stock at stock-only upgrade. Earlier financial history remains in the legacy tables.",
                    created_by=owner,
                    token=uuid.uuid4(),
                )
            Line.objects.create(entry=entry, item=item, quantity=product.stock)


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0001_initial"),
        ("shop", "0011_product_default_purchase_price_and_more"),
    ]
    operations = [migrations.RunPython(import_stock)]
