from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils.html import format_html
from .models import (
    Product,
    Purchase,
    PurchaseLine,
    Sale,
    SaleLine,
    StockMovement,
    StockAdjustment,
    Expense,
    ReturnDocument,
    ReturnLine,
    ShopSettings,
    AuditEvent,
)
from .services import post_purchase, post_sale, post_adjustment, post_return, audit

admin.site.site_header = "Chatterjee Flow Shop Management"
admin.site.site_title = "Chatterjee Flow"
admin.site.index_title = "Products, Purchases, Sales and Reports"


class AuditedAdmin(admin.ModelAdmin):
    def save_model(self, request, obj, form, change):
        fields = [f for f in obj._meta.fields if not f.primary_key]
        before = None
        if change:
            old = type(obj).objects.get(pk=obj.pk)
            before = {f.name: str(getattr(old, f.attname)) for f in fields}
        super().save_model(request, obj, form, change)
        audit(
            getattr(request, "user", None),
            "record.saved",
            f"{obj._meta.label} #{obj.pk}",
            {
                "before": before,
                "after": {f.name: str(getattr(obj, f.attname)) for f in fields},
            },
        )

    def log_addition(self, request, obj, message):
        audit(
            request.user,
            "record.created",
            f"{obj._meta.label} #{obj.pk}",
            {"changes": str(message)},
        )
        return super().log_addition(request, obj, message)

    def log_change(self, request, obj, message):
        audit(
            request.user,
            "record.changed",
            f"{obj._meta.label} #{obj.pk}",
            {"changes": str(message)},
        )
        return super().log_change(request, obj, message)

    def log_deletions(self, request, queryset):
        for obj in queryset:
            audit(
                request.user,
                "record.deleted",
                f"{obj._meta.label} #{obj.pk}",
                {"description": str(obj)},
            )
        return super().log_deletions(request, queryset)


@admin.register(Product)
class ProductAdmin(AuditedAdmin):
    list_display = (
        "name",
        "category",
        "unit",
        "default_purchase_price",
        "sale_price",
        "stock",
        "average_cost",
        "active",
    )
    list_filter = ("category", "active", "unit")
    search_fields = ("name",)
    readonly_fields = ("stock", "average_cost")
    fieldsets = (
        (None, {"fields": ("name", "unit", "default_purchase_price", "sale_price")}),
        (
            "Stock and valuation",
            {"fields": ("stock", "average_cost"), "classes": ("collapse",)},
        ),
        (
            "More details",
            {
                "fields": (
                    "category",
                    "active",
                    "low_stock_at",
                    "image",
                    "details",
                    "hsn",
                    "tax_rate",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    def save_model(self, request, obj, form, change):
        # A stale product form must never overwrite stock updated by a posting.
        with transaction.atomic():
            if change:
                current = Product.objects.select_for_update().get(pk=obj.pk)
                obj.stock = current.stock
                obj.average_cost = current.average_cost
            super().save_model(request, obj, form, change)


class PurchaseLineInline(admin.TabularInline):
    model = PurchaseLine
    extra = 1


class SaleLineInline(admin.TabularInline):
    model = SaleLine
    extra = 1


class PostedDocumentAdmin(AuditedAdmin):
    def has_post_permission(self, request):
        codename = (
            "post_return"
            if self.model is ReturnDocument
            else f"post_{self.model._meta.model_name}"
        )
        return request.user.has_perm(f"shop.{codename}")

    def has_delete_permission(self, request, obj=None):
        return bool(obj is None or not obj.posted) and super().has_delete_permission(
            request, obj
        )

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.posted:
            return tuple(f.name for f in self.model._meta.fields)
        return tuple(f.name for f in self.model._meta.fields if not f.editable)

    def get_inlines(self, request, obj=None):
        return [] if obj and obj.posted else super().get_inlines(request, obj)

    def save_formset(self, request, form, formset, change):
        if form.instance.posted:
            raise ValidationError("Posted documents cannot be edited")
        return super().save_formset(request, form, formset, change)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        with transaction.atomic():
            if object_id and request.method == "POST":
                from django.contrib.admin.utils import unquote

                obj = (
                    self.get_queryset(request)
                    .select_for_update()
                    .filter(pk=unquote(object_id))
                    .first()
                )
                if obj and obj.posted:
                    self.message_user(
                        request, "Posted records cannot be edited", messages.ERROR
                    )
                    from django.http import HttpResponseRedirect

                    return HttpResponseRedirect(request.path)
            return super().changeform_view(request, object_id, form_url, extra_context)


@admin.register(Purchase)
class PurchaseAdmin(PostedDocumentAdmin):
    list_display = (
        "id",
        "supplier",
        "created_at",
        "posted",
        "total_display",
        "return_link",
    )

    def return_link(self, obj):
        return (
            format_html(
                '<a href="{}">Return / correct</a>',
                reverse("create_return", args=["purchase", obj.pk]),
            )
            if obj.posted
            else "Draft"
        )

    inlines = [PurchaseLineInline]
    actions = ["post_selected"]

    def total_display(self, obj):
        return obj.total

    @admin.action(
        description="Post selected purchases (increase stock)", permissions=["post"]
    )
    def post_selected(self, request, queryset):
        for obj in queryset:
            try:
                post_purchase(obj.pk, actor=request.user)
                self.message_user(
                    request, f"Purchase #{obj.pk} posted", messages.SUCCESS
                )
            except ValidationError as e:
                self.message_user(request, f"Purchase #{obj.pk}: {e}", messages.ERROR)


@admin.register(Sale)
class SaleAdmin(PostedDocumentAdmin):
    list_display = (
        "id",
        "invoice_number",
        "created_at",
        "customer_name",
        "payment_method",
        "posted",
        "total_display",
        "bill_link",
    )
    search_fields = ("invoice_number", "customer_name")
    readonly_fields = ("posted", "created_at")
    inlines = [SaleLineInline]
    actions = ["post_selected"]

    def total_display(self, obj):
        return obj.total

    def bill_link(self, obj):
        return (
            format_html(
                '<a href="{}" target="_blank">Print bill</a>',
                reverse("invoice", args=[obj.pk]),
            )
            if obj.posted
            else "Post sale first"
        )

    @admin.action(
        description="Post selected sales (deduct stock)", permissions=["post"]
    )
    def post_selected(self, request, queryset):
        for obj in queryset:
            try:
                post_sale(obj.pk, actor=request.user)
                self.message_user(request, f"Bill #{obj.pk} posted", messages.SUCCESS)
            except ValidationError as e:
                self.message_user(request, f"Bill #{obj.pk}: {e}", messages.ERROR)


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("product", "quantity_delta", "reason", "reference", "created_at")
    list_filter = ("reason", "created_at")
    search_fields = ("product__name", "reference")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StockAdjustment)
class StockAdjustmentAdmin(PostedDocumentAdmin):
    list_display = ("id", "product", "quantity_delta", "reason", "created_at", "posted")
    list_filter = ("reason", "posted")
    actions = ["post_selected"]

    def get_readonly_fields(self, request, obj=None):
        return tuple(super().get_readonly_fields(request, obj)) + ("cost_at_posting",)

    @admin.action(description="Post selected stock adjustments", permissions=["post"])
    def post_selected(self, request, queryset):
        for obj in queryset:
            try:
                post_adjustment(obj.pk, actor=request.user)
                self.message_user(
                    request, f"Adjustment #{obj.pk} posted", messages.SUCCESS
                )
            except ValidationError as exc:
                self.message_user(request, str(exc), messages.ERROR)


@admin.register(Expense)
class ExpenseAdmin(AuditedAdmin):
    list_display = ("date", "category", "description", "amount")
    list_filter = ("category", "date")
    search_fields = ("description",)
    readonly_fields = ("created_at",)


class ReturnLineInline(admin.TabularInline):
    model = ReturnLine
    extra = 1
    readonly_fields = ("amount", "tax_amount", "cost_amount")


@admin.register(ReturnDocument)
class ReturnDocumentAdmin(PostedDocumentAdmin):
    list_display = (
        "id",
        "document_number",
        "sale",
        "purchase",
        "created_at",
        "amount",
        "posted",
        "receipt_link",
    )
    list_filter = ("posted", "payment_method")
    inlines = [ReturnLineInline]
    actions = ["post_selected"]

    def receipt_link(self, obj):
        return (
            format_html(
                '<a href="{}">Print return</a>',
                reverse("return_receipt", args=[obj.pk]),
            )
            if obj.posted
            else "Draft"
        )

    @admin.action(description="Post selected returns / refunds", permissions=["post"])
    def post_selected(self, request, queryset):
        for obj in queryset:
            try:
                post_return(obj.pk, actor=request.user)
                self.message_user(request, f"Return #{obj.pk} posted", messages.SUCCESS)
            except ValidationError as exc:
                self.message_user(request, "; ".join(exc.messages), messages.ERROR)


@admin.register(ShopSettings)
class ShopSettingsAdmin(AuditedAdmin):
    def has_add_permission(self, request):
        return not ShopSettings.objects.exists() and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "action", "reference")
    list_filter = ("action", "created_at")
    search_fields = ("actor", "reference")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
