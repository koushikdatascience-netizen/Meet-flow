from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import django.core.validators
from decimal import Decimal


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="Product",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=160, unique=True)),
                ("category", models.CharField(blank=True, max_length=80)),
                (
                    "unit",
                    models.CharField(
                        choices=[("kg", "Kilogram"), ("pc", "Piece")],
                        default="kg",
                        max_length=2,
                    ),
                ),
                (
                    "sale_price",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=12,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0"))
                        ],
                    ),
                ),
                (
                    "stock",
                    models.DecimalField(
                        decimal_places=3,
                        default=0,
                        max_digits=13,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0"))
                        ],
                    ),
                ),
                (
                    "average_cost",
                    models.DecimalField(
                        decimal_places=2,
                        default=0,
                        max_digits=12,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0"))
                        ],
                    ),
                ),
                (
                    "low_stock_at",
                    models.DecimalField(decimal_places=3, default=2, max_digits=13),
                ),
                (
                    "image",
                    models.ImageField(blank=True, null=True, upload_to="products/"),
                ),
                ("details", models.TextField(blank=True)),
                ("active", models.BooleanField(default=True)),
            ],
        ),
        migrations.CreateModel(
            name="Purchase",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("supplier", models.CharField(blank=True, max_length=160)),
                (
                    "created_at",
                    models.DateTimeField(
                        default=django.utils.timezone.now, editable=False
                    ),
                ),
                ("notes", models.TextField(blank=True)),
                ("posted", models.BooleanField(default=False, editable=False)),
            ],
        ),
        migrations.CreateModel(
            name="Sale",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("customer_name", models.CharField(blank=True, max_length=160)),
                (
                    "created_at",
                    models.DateTimeField(
                        default=django.utils.timezone.now, editable=False
                    ),
                ),
                (
                    "payment_method",
                    models.CharField(
                        choices=[
                            ("cash", "Cash"),
                            ("upi", "UPI"),
                            ("card", "Card"),
                            ("other", "Other"),
                        ],
                        default="cash",
                        max_length=8,
                    ),
                ),
                (
                    "discount",
                    models.DecimalField(
                        decimal_places=2,
                        default=0,
                        max_digits=12,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0"))
                        ],
                    ),
                ),
                ("notes", models.TextField(blank=True)),
                ("posted", models.BooleanField(default=False, editable=False)),
            ],
        ),
        migrations.CreateModel(
            name="PurchaseLine",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "quantity",
                    models.DecimalField(
                        decimal_places=3,
                        max_digits=13,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0.001"))
                        ],
                    ),
                ),
                (
                    "unit_cost",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=12,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0"))
                        ],
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="shop.product"
                    ),
                ),
                (
                    "purchase",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lines",
                        to="shop.purchase",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="SaleLine",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "quantity",
                    models.DecimalField(
                        decimal_places=3,
                        max_digits=13,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0.001"))
                        ],
                    ),
                ),
                (
                    "unit_price",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=12,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0"))
                        ],
                    ),
                ),
                (
                    "cost_at_sale",
                    models.DecimalField(
                        decimal_places=2, default=0, editable=False, max_digits=12
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="shop.product"
                    ),
                ),
                (
                    "sale",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lines",
                        to="shop.sale",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="StockMovement",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "quantity_delta",
                    models.DecimalField(decimal_places=3, max_digits=13),
                ),
                ("reason", models.CharField(max_length=30)),
                ("reference", models.CharField(max_length=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="shop.product"
                    ),
                ),
            ],
        ),
    ]
