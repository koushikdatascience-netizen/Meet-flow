from decimal import Decimal, ROUND_HALF_UP
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils import timezone

POSITIVE = MinValueValidator(Decimal("0.001"))
NONNEGATIVE = MinValueValidator(Decimal("0"))


def money(value):
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class DocumentQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError(
            "Bulk document updates are disabled; use posting services or save a draft"
        )

    def bulk_create(self, *args, **kwargs):
        raise ValidationError(
            "Bulk document creation is disabled; save drafts individually"
        )


class PostedDocument(models.Model):
    objects = DocumentQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        with transaction.atomic():
            current = (
                type(self).objects.select_for_update().filter(pk=self.pk).first()
                if self.pk
                else None
            )
            if current and current.posted:
                raise ValidationError("Posted documents cannot be edited")
            return super().save(*args, **kwargs)


class DocumentLine(models.Model):
    objects = DocumentQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        parent_field = "sale" if hasattr(self, "sale_id") else "purchase"
        parent_model = self._meta.get_field(parent_field).related_model
        parent_ids = {getattr(self, parent_field + "_id")}
        if self.pk:
            old_id = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list(parent_field + "_id", flat=True)
                .first()
            )
            if old_id:
                parent_ids.add(old_id)
        with transaction.atomic():
            parents = list(
                parent_model.objects.select_for_update()
                .filter(pk__in=parent_ids)
                .order_by("pk")
            )
            if any(parent.posted for parent in parents):
                raise ValidationError("Lines of posted documents cannot be edited")
            return super().save(*args, **kwargs)


class ProductQuerySet(models.QuerySet):
    def with_purchase_price(self):
        from django.db.models.functions import Coalesce

        latest = (
            PurchaseLine.objects.filter(
                product_id=models.OuterRef("pk"), purchase__posted=True
            )
            .order_by(
                Coalesce("purchase__posted_at", "purchase__created_at").desc(),
                "-purchase_id",
                "-pk",
            )
            .values("unit_cost")[:1]
        )
        return self.annotate(
            suggested_purchase_price=Coalesce(
                models.Subquery(
                    latest,
                    output_field=models.DecimalField(max_digits=12, decimal_places=2),
                ),
                models.F("default_purchase_price"),
                output_field=models.DecimalField(max_digits=12, decimal_places=2),
            )
        )


class Product(models.Model):
    objects = ProductQuerySet.as_manager()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(stock__gte=0), name="product_stock_nonnegative"
            ),
            models.CheckConstraint(
                condition=models.Q(average_cost__gte=0), name="product_cost_nonnegative"
            ),
            models.CheckConstraint(
                condition=models.Q(default_purchase_price__gte=0),
                name="product_default_buying_price_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(sale_price__gte=0), name="product_price_nonnegative"
            ),
        ]

    class Unit(models.TextChoices):
        KG = "kg", "Kilogram"
        PIECE = "pc", "Piece"

    name = models.CharField(max_length=160, unique=True)
    category = models.CharField(max_length=80, blank=True)
    unit = models.CharField(max_length=2, choices=Unit.choices, default=Unit.KG)
    default_purchase_price = models.DecimalField(
        "Default buying price",
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[NONNEGATIVE],
        help_text="Per kg/piece. Used until a purchase is posted; later purchases use the last actual buying price.",
    )
    sale_price = models.DecimalField(
        "Selling price",
        max_digits=12,
        decimal_places=2,
        validators=[NONNEGATIVE],
        help_text="Per kg/piece. Filled automatically on bills.",
    )
    stock = models.DecimalField(
        max_digits=13, decimal_places=3, default=0, validators=[NONNEGATIVE]
    )
    average_cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, validators=[NONNEGATIVE]
    )
    low_stock_at = models.DecimalField(
        max_digits=13, decimal_places=3, default=2, validators=[NONNEGATIVE]
    )
    image = models.ImageField(upload_to="products/", blank=True, null=True)
    details = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    hsn = models.CharField(max_length=8, blank=True)
    tax_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[NONNEGATIVE, MaxValueValidator(100)],
    )

    def clean(self):
        super().clean()
        if self.pk:
            original = Product.objects.get(pk=self.pk)
            if (
                original.unit != self.unit
                and StockMovement.objects.filter(product_id=self.pk).exists()
            ):
                raise ValidationError(
                    {
                        "unit": "Unit cannot change after stock movements. Create a new product instead."
                    }
                )

    def __str__(self):
        return self.name


class Purchase(PostedDocument):
    entry_token = models.UUIDField(null=True, blank=True, unique=True, editable=False)

    class Meta:
        permissions = [("post_purchase", "Can post purchases")]

    supplier = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    notes = models.TextField(blank=True)
    posted = models.BooleanField(default=False, editable=False)
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)

    def __str__(self):
        return f"Purchase #{self.pk}"

    @property
    def total(self):
        return sum(
            (money(x.quantity * x.unit_cost) for x in self.lines.all()), Decimal("0.00")
        )


class PurchaseLine(DocumentLine):
    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="purchase_quantity_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(unit_cost__gte=0), name="purchase_cost_nonnegative"
            ),
        ]

    purchase = models.ForeignKey(
        Purchase, related_name="lines", on_delete=models.CASCADE
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.DecimalField(
        max_digits=13, decimal_places=3, validators=[POSITIVE]
    )
    unit_cost = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[NONNEGATIVE]
    )

    @property
    def amount(self):
        return money(self.quantity * self.unit_cost)

    def __str__(self):
        return str(self.product)


class Sale(PostedDocument):
    class Meta:
        permissions = [
            ("post_sale", "Can post sales"),
            ("view_reports", "Can view financial reports"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(discount__gte=0), name="sale_discount_nonnegative"
            )
        ]

    class Payment(models.TextChoices):
        CASH = "cash", "Cash"
        UPI = "upi", "UPI"
        CARD = "card", "Card"
        OTHER = "other", "Other"

    customer_name = models.CharField(max_length=160, blank=True)
    customer_address = models.TextField(blank=True)
    customer_gstin = models.CharField(max_length=15, blank=True)
    customer_state = models.CharField(max_length=2, blank=True)
    invoice_number = models.CharField(
        max_length=16, blank=True, null=True, unique=True, editable=False
    )
    invoice_snapshot = models.JSONField(default=dict, blank=True, editable=False)
    charged_total = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, editable=False
    )
    tax_total = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, editable=False
    )
    taxable_total = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, editable=False
    )
    checkout_token = models.UUIDField(
        null=True, blank=True, unique=True, editable=False
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    payment_method = models.CharField(
        max_length=8, choices=Payment.choices, default=Payment.CASH
    )
    discount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, validators=[NONNEGATIVE]
    )
    notes = models.TextField(blank=True)
    posted = models.BooleanField(default=False, editable=False)
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)

    def __str__(self):
        return f"Bill #{self.pk}"

    @property
    def subtotal(self):
        return sum((x.amount for x in self.lines.all()), Decimal("0.00"))

    @property
    def total(self):
        return (
            self.charged_total
            if self.posted and self.invoice_snapshot
            else self.subtotal - self.discount
        )

    @property
    def gross_profit(self):
        return (
            self.taxable_total if self.posted and self.invoice_snapshot else self.total
        ) - sum(
            (money(x.quantity * x.cost_at_sale) for x in self.lines.all()),
            Decimal("0.00"),
        )


class SaleLine(DocumentLine):
    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="sale_quantity_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price__gte=0), name="sale_price_nonnegative"
            ),
        ]

    sale = models.ForeignKey(Sale, related_name="lines", on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.DecimalField(
        max_digits=13, decimal_places=3, validators=[POSITIVE]
    )
    unit_price = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[NONNEGATIVE]
    )
    discount_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, editable=False
    )
    taxable_amount = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, editable=False
    )
    cgst = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, editable=False
    )
    sgst = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, editable=False
    )
    igst = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, editable=False
    )
    hsn = models.CharField(max_length=8, blank=True, editable=False)
    tax_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=0, editable=False
    )
    product_name = models.CharField(max_length=160, blank=True, editable=False)
    product_unit = models.CharField(max_length=2, blank=True, editable=False)
    cost_at_sale = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, editable=False
    )

    @property
    def amount(self):
        return money(self.quantity * self.unit_price)

    def __str__(self):
        return str(self.product)


class StockMovement(models.Model):
    objects = DocumentQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Posted stock movements cannot be edited")
        return super().save(*args, **kwargs)

    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity_delta = models.DecimalField(max_digits=13, decimal_places=3)
    reason = models.CharField(max_length=30)
    reference = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.product}: {self.quantity_delta}"


@receiver(pre_delete, sender=Purchase)
@receiver(pre_delete, sender=Sale)
def protect_posted_document(sender, instance, using, **kwargs):
    current = (
        sender.objects.using(using).select_for_update().filter(pk=instance.pk).first()
    )
    if current and current.posted:
        raise ValidationError("Posted documents cannot be deleted")


@receiver(pre_delete, sender=PurchaseLine)
@receiver(pre_delete, sender=SaleLine)
def protect_posted_line(sender, instance, using, **kwargs):
    parent_field = "sale" if sender is SaleLine else "purchase"
    parent_model = sender._meta.get_field(parent_field).related_model
    parent = (
        parent_model.objects.using(using)
        .select_for_update()
        .filter(pk=getattr(instance, parent_field + "_id"))
        .first()
    )
    if parent and parent.posted:
        raise ValidationError("Lines of posted documents cannot be deleted")


class StockAdjustment(PostedDocument):
    class Meta:
        permissions = [("post_stockadjustment", "Can post stock adjustments")]

    class Reason(models.TextChoices):
        WASTAGE = "wastage", "Wastage / spoilage"
        CORRECTION = "correction", "Physical stock correction"

    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity_delta = models.DecimalField(max_digits=13, decimal_places=3)
    reason = models.CharField(max_length=20, choices=Reason.choices)
    notes = models.TextField(help_text="Explain the loss or stock count difference.")
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    posted = models.BooleanField(default=False, editable=False)
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)
    cost_at_posting = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, editable=False
    )

    def clean(self):
        super().clean()
        if self.quantity_delta is not None:
            if self.quantity_delta == 0:
                raise ValidationError(
                    {"quantity_delta": "Enter a non-zero adjustment."}
                )
            if self.reason == self.Reason.WASTAGE and self.quantity_delta > 0:
                raise ValidationError(
                    {
                        "quantity_delta": "Wastage must reduce stock (use a negative quantity)."
                    }
                )
        if not self.notes.strip():
            raise ValidationError({"notes": "Explain the adjustment."})

    def __str__(self):
        return f"Adjustment #{self.pk}"

    @property
    def value(self):
        return money(self.quantity_delta * self.cost_at_posting)


class Expense(models.Model):
    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="expense_amount_positive"
            )
        ]

    date = models.DateField(default=timezone.localdate)
    category = models.CharField(max_length=80)
    description = models.CharField(max_length=200)
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.description


pre_delete.connect(protect_posted_document, sender=StockAdjustment)


class ShopSettings(models.Model):
    class Mode(models.TextChoices):
        RECEIPT = "receipt", "Simple receipt (no GST)"
        SUPPLY = "supply", "Bill of supply"
        TAX = "tax", "GST tax invoice"

    name = models.CharField(max_length=160, default="MeatFlow")
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    gstin = models.CharField(max_length=15, blank=True)
    state_code = models.CharField(max_length=2, blank=True)
    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.RECEIPT)
    footer = models.CharField(max_length=200, default="Thank you!")
    signature = models.CharField(
        max_length=160, blank=True, help_text="Authorised signatory name for invoices."
    )

    def save(self, *args, **kwargs):
        self.pk = 1
        return super().save(*args, **kwargs)

    def clean(self):
        import re

        if self.mode != self.Mode.RECEIPT:
            if not self.address.strip() or not self.signature.strip():
                raise ValidationError(
                    "Address and authorised signatory are required for GST documents."
                )
            if not re.fullmatch(
                r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9]", self.gstin
            ):
                raise ValidationError({"gstin": "Enter a valid 15-character GSTIN."})
            if self.state_code != self.gstin[:2]:
                raise ValidationError(
                    {"state_code": "State code must match the GSTIN."}
                )

    def __str__(self):
        return self.name


class InvoiceSequence(models.Model):
    financial_year = models.CharField(max_length=10, unique=True)
    next_number = models.PositiveIntegerField(default=1)


class ReturnDocument(PostedDocument):
    submission_token = models.UUIDField(
        null=True, blank=True, unique=True, editable=False
    )

    class Meta:
        permissions = [("post_return", "Can post refunds and purchase returns")]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(sale__isnull=False, purchase__isnull=True)
                    | models.Q(sale__isnull=True, purchase__isnull=False)
                ),
                name="return_exactly_one_source",
            )
        ]

    sale = models.ForeignKey(
        Sale, on_delete=models.PROTECT, null=True, blank=True, related_name="returns"
    )
    purchase = models.ForeignKey(
        Purchase,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="returns",
    )
    reason = models.TextField()
    payment_method = models.CharField(
        max_length=8, choices=Sale.Payment.choices, default=Sale.Payment.CASH
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    posted = models.BooleanField(default=False, editable=False)
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)
    document_number = models.CharField(
        max_length=20, unique=True, null=True, blank=True, editable=False
    )
    amount = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, editable=False
    )
    tax_amount = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, editable=False
    )
    profit_reversal = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, editable=False
    )
    valuation_variance = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, editable=False
    )

    def clean(self):
        if bool(self.sale_id) == bool(self.purchase_id):
            raise ValidationError("Choose exactly one sale or purchase.")
        if not self.reason.strip():
            raise ValidationError({"reason": "Explain the return or correction."})

    def __str__(self):
        return f"Return #{self.pk}"


class ReturnLine(models.Model):
    document = models.ForeignKey(
        ReturnDocument, on_delete=models.CASCADE, related_name="lines"
    )
    sale_line = models.ForeignKey(
        SaleLine,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="return_lines",
    )
    purchase_line = models.ForeignKey(
        PurchaseLine,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="return_lines",
    )
    quantity = models.DecimalField(
        max_digits=13, decimal_places=3, validators=[POSITIVE]
    )
    restock = models.BooleanField(
        default=False, help_text="Sales only: enable only for goods fit to sell again."
    )
    amount = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, editable=False
    )
    taxable_amount = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, editable=False
    )
    cgst = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, editable=False
    )
    sgst = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, editable=False
    )
    igst = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, editable=False
    )
    tax_amount = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, editable=False
    )
    cost_amount = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, editable=False
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="return_quantity_positive"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(sale_line__isnull=False, purchase_line__isnull=True)
                    | models.Q(sale_line__isnull=True, purchase_line__isnull=False)
                ),
                name="return_line_exactly_one_source",
            ),
        ]
        abstract = False

    objects = DocumentQuerySet.as_manager()

    def save(self, *args, **kwargs):
        with transaction.atomic():
            ids = {self.document_id}
            if self.pk:
                original = type(self).objects.get(pk=self.pk)
                ids.add(original.document_id)
            if any(
                x.posted
                for x in ReturnDocument.objects.select_for_update()
                .filter(pk__in=ids)
                .order_by("pk")
            ):
                raise ValidationError("Posted return lines cannot be edited")
            return super().save(*args, **kwargs)


class AuditEvent(models.Model):
    objects = DocumentQuerySet.as_manager()
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.CharField(max_length=150, default="system")
    action = models.CharField(max_length=50)
    reference = models.CharField(max_length=100)
    details = models.JSONField(default=dict)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Audit events cannot be edited")
        return super().save(*args, **kwargs)


pre_delete.connect(protect_posted_document, sender=ReturnDocument)


@receiver(pre_delete, sender=ReturnLine)
def protect_return_line(sender, instance, using, **kwargs):
    parent = (
        ReturnDocument.objects.using(using)
        .select_for_update()
        .get(pk=instance.document_id)
    )
    if parent.posted:
        raise ValidationError("Posted return lines cannot be deleted")


@receiver(pre_delete, sender=StockMovement)
@receiver(pre_delete, sender=AuditEvent)
def protect_audit(sender, instance, **kwargs):
    raise ValidationError("Audit events cannot be deleted")
