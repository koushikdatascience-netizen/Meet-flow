from decimal import Decimal
from django import forms
from django.forms import formset_factory
from .models import Product, Sale, Purchase


class CustomerStateField(forms.ChoiceField):
    def to_python(self, value):
        from .states import normalize_state

        try:
            return normalize_state(value)
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc


class BillingForm(forms.ModelForm):
    token = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, tax_mode="receipt", **kwargs):
        super().__init__(*args, **kwargs)
        if tax_mode == "receipt":
            self.fields.pop("customer_state")
            self.fields.pop("customer_gstin")
        else:
            from .states import STATE_CHOICES

            self.fields["customer_state"] = CustomerStateField(
                label="Customer state",
                required=False,
                choices=[("", "Same as shop / use customer GSTIN")]
                + list(STATE_CHOICES),
                help_text="Choose a state for an out-of-state sale. A customer GSTIN fills its state automatically.",
            )

    def clean_customer_gstin(self):
        return self.cleaned_data.get("customer_gstin", "").strip().upper()

    class Meta:
        model = Sale
        fields = [
            "customer_name",
            "customer_address",
            "customer_gstin",
            "customer_state",
            "payment_method",
            "discount",
            "notes",
        ]
        widgets = {
            "payment_method": forms.RadioSelect,
            "customer_address": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }


class BillingLineForm(forms.Form):
    product = forms.ModelChoiceField(
        queryset=Product.objects.filter(active=True).order_by("name")
    )
    quantity = forms.DecimalField(
        max_digits=13,
        decimal_places=3,
        min_value=Decimal("0.001"),
        initial=1,
        widget=forms.NumberInput(attrs={"step": "0.001", "min": "0.001"}),
    )

    def clean(self):
        cleaned = super().clean()
        product, quantity = cleaned.get("product"), cleaned.get("quantity")
        if (
            product
            and quantity
            and product.unit == Product.Unit.PIECE
            and quantity != quantity.to_integral_value()
        ):
            raise forms.ValidationError("Piece quantities must be whole numbers.")
        return cleaned


BillingLineFormSet = formset_factory(
    BillingLineForm,
    extra=0,
    can_delete=True,
    min_num=1,
    validate_min=True,
    max_num=100,
    validate_max=True,
)


class ReturnForm(forms.Form):
    token = forms.UUIDField(widget=forms.HiddenInput)
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), max_length=2000)
    payment_method = forms.ChoiceField(choices=Sale.Payment.choices)


class ReturnQuantityForm(forms.Form):
    source_line = forms.IntegerField(widget=forms.HiddenInput)
    quantity = forms.DecimalField(
        max_digits=13,
        decimal_places=3,
        min_value=Decimal("0"),
        initial=0,
        widget=forms.NumberInput(attrs={"step": "0.001", "min": "0"}),
    )
    restock = forms.BooleanField(required=False, label="Fit for resale: restore stock")


ReturnQuantityFormSet = formset_factory(
    ReturnQuantityForm, extra=0, max_num=1000, validate_max=True
)


class PurchaseEntryForm(forms.ModelForm):
    token = forms.UUIDField(widget=forms.HiddenInput)

    class Meta:
        model = Purchase
        fields = ["supplier", "notes"]
        widgets = {
            "supplier": forms.TextInput(
                attrs={
                    "list": "supplier-suggestions",
                    "placeholder": "Supplier name (optional)",
                }
            ),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }


class PurchaseEntryLineForm(BillingLineForm):
    product = forms.ModelChoiceField(
        queryset=Product.objects.filter(active=True)
        .with_purchase_price()
        .order_by("name")
    )
    unit_cost = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0"),
        label="Buying price",
        required=False,
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
    )

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        if (
            product
            and cleaned.get("unit_cost") is None
            and "unit_cost" not in self.errors
        ):
            cleaned["unit_cost"] = product.suggested_purchase_price
        return cleaned


PurchaseEntryLineFormSet = formset_factory(
    PurchaseEntryLineForm,
    extra=0,
    can_delete=True,
    min_num=1,
    validate_min=True,
    max_num=100,
    validate_max=True,
)


class ItemForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            "name",
            "unit",
            "default_purchase_price",
            "sale_price",
            "category",
            "low_stock_at",
            "hsn",
            "tax_rate",
        ]
        labels = {
            "name": "Item name",
            "default_purchase_price": "Default buying price",
            "sale_price": "Selling price",
            "low_stock_at": "Low-stock alert at",
            "hsn": "HSN (if needed)",
            "tax_rate": "GST rate (%)",
        }
        widgets = {
            "name": forms.TextInput(
                attrs={"autofocus": True, "placeholder": "e.g. Chicken breast"}
            )
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["low_stock_at"].required = False
        self.fields["tax_rate"].required = False

    def clean_low_stock_at(self):
        value = self.cleaned_data.get("low_stock_at")
        return self.instance.low_stock_at if value is None else value

    def clean_tax_rate(self):
        value = self.cleaned_data.get("tax_rate")
        return self.instance.tax_rate if value is None else value
