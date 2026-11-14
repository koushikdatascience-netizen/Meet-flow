from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone


class AdminPasswordReset(models.Model):
    request_id = models.UUIDField(unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class Company(models.Model):
    name = models.CharField(max_length=160)
    active = models.BooleanField(default=True)
    is_primary = models.BooleanField(default=False, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["is_primary"],
                condition=models.Q(is_primary=True),
                name="inventory_single_primary_workspace",
            )
        ]

    def __str__(self):
        return self.name


class Membership(models.Model):
    ROLES = [
        ("admin", "Administrator"),
        ("manager", "Manager"),
        ("worker", "Worker"),
        ("support", "Support"),
        ("cook", "Cook"),
        ("server", "Server / serving staff"),
        ("operator", "Stock operator"),
        ("viewer", "Report viewer"),
    ]
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    role = models.CharField(max_length=12, choices=ROLES, default="viewer")
    must_change_password = models.BooleanField(default=True)
    features = models.JSONField(null=True, blank=True, default=None)

    @property
    def allowed_features(self):
        if self.role == "admin":
            return ["opening", "purchase", "sale", "delete", "reports"]
        if self.features is not None:
            return self.features
        return self.default_features(self.role)

    @staticmethod
    def default_features(role):
        return {
            "manager": ["opening", "purchase", "sale", "delete", "reports"],
            "worker": ["purchase", "sale"],
            "support": ["reports"],
            "cook": ["sale"],
            "server": ["sale"],
            "operator": ["opening", "purchase", "sale", "delete", "reports"],
            "viewer": ["reports"],
        }.get(role, [])


class Item(models.Model):
    class Category(models.TextChoices):
        CHICKEN = "Chicken", "Chicken"
        MUTTON = "Mutton", "Mutton"
        FISH = "Fish", "Fish"
        OTHERS = "Others", "Others"

    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    name = models.CharField(max_length=160)
    category = models.CharField(
        max_length=80, choices=Category.choices, default=Category.OTHERS
    )
    unit = models.CharField(max_length=2, choices=[("pc", "Pieces")], default="pc")
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(unit="pc"), name="inventory_item_pieces_only"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    category__in=["Chicken", "Mutton", "Fish", "Others"]
                ),
                name="inventory_item_category_allowed",
            ),
            models.UniqueConstraint(
                fields=["company", "name"], name="inventory_company_item_name"
            ),
        ]
        ordering = ["name"]

    def __str__(self):
        return self.name


class Entry(models.Model):
    KINDS = [("opening", "Opening stock"), ("purchase", "Purchase"), ("sale", "Sale")]
    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    kind = models.CharField(max_length=12, choices=KINDS)
    date = models.DateField(default=timezone.localdate)
    note = models.CharField(max_length=240, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    token = models.UUIDField(unique=True)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="+"
    )
    reversal_reason = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["-date", "-pk"]


class Line(models.Model):
    entry = models.ForeignKey(Entry, on_delete=models.PROTECT, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=13, decimal_places=3)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="inventory_positive_quantity"
            ),
            models.UniqueConstraint(
                fields=["entry", "item"], name="inventory_unique_entry_item"
            ),
        ]


def validate_timeline(item):
    balance = Decimal("0")
    for line in (
        Line.objects.filter(item=item, entry__reversed_at__isnull=True)
        .select_related("entry")
        .order_by("entry__date", "entry__pk")
    ):
        balance += -line.quantity if line.entry.kind == "sale" else line.quantity
        if balance < 0:
            raise ValidationError(
                f"{item.name}: insufficient stock on {line.entry.date}. This change would make stock negative."
            )


@transaction.atomic
def post_entry(company, actor, kind, date, note, token, rows):
    Company.objects.select_for_update().get(pk=company.pk)
    if kind not in dict(Entry.KINDS):
        raise ValidationError("Invalid entry type.")
    existing = Entry.objects.filter(token=token).first()
    if existing:
        if existing.company_id != company.pk or existing.created_by_id != actor.pk:
            raise ValidationError("Invalid submission token.")
        return existing
    if not rows:
        raise ValidationError("Add at least one item.")
    if date > timezone.localdate():
        raise ValidationError("Future stock entries are not allowed.")
    entry = Entry.objects.create(
        company=company, created_by=actor, kind=kind, date=date, note=note, token=token
    )
    seen = set()
    for item_id, quantity in rows:
        if (
            not quantity.is_finite()
            or quantity.as_tuple().exponent < -3
            or quantity > Decimal("9999999999.999")
        ):
            raise ValidationError(
                "Use finite quantities with at most three decimal places."
            )
        item = Item.objects.get(pk=item_id, company=company, active=True)
        if item.pk in seen:
            raise ValidationError("Each item can appear only once.")
        seen.add(item.pk)
        if quantity <= 0 or quantity != quantity.to_integral_value():
            raise ValidationError(
                "Use positive quantities and whole numbers for pieces."
            )
        if (
            kind == "opening"
            and Line.objects.filter(item=item, entry__reversed_at__isnull=True).exists()
        ):
            raise ValidationError(
                f"{item.name}: opening stock must be the first active entry. Use a purchase for later receipts."
            )
        Line.objects.create(entry=entry, item=item, quantity=quantity)
        validate_timeline(item)
    return entry


@transaction.atomic
def reverse_entry(company, actor, pk, reason):
    Company.objects.select_for_update().get(pk=company.pk)
    entry = Entry.objects.get(pk=pk, company=company)
    if entry.reversed_at:
        return
    if not reason.strip():
        raise ValidationError("Enter a reason for deletion.")
    if len(reason.strip()) > 240:
        raise ValidationError("Keep the deletion reason within 240 characters.")
    entry.reversed_at = timezone.now()
    entry.reversed_by = actor
    entry.reversal_reason = reason.strip()
    entry.save(update_fields=["reversed_at", "reversed_by", "reversal_reason"])
    for line in entry.lines.select_related("item"):
        validate_timeline(line.item)
