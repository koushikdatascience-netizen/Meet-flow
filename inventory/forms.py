from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError

from .models import Item, Membership


class ItemForm(forms.ModelForm):
    class Meta:
        model = Item
        fields = ["name", "category", "unit", "active"]


class UserForm(forms.Form):
    username = forms.CharField(max_length=150)
    temporary_password = forms.CharField(widget=forms.PasswordInput, required=False)
    role = forms.ChoiceField(choices=Membership.ROLES)
    active = forms.BooleanField(required=False, initial=True)
    features = forms.MultipleChoiceField(
        required=False,
        choices=[
            ("opening", "Opening stock"),
            ("purchase", "Purchases"),
            ("sale", "Sales"),
            ("delete", "Delete entries by reversal"),
            ("reports", "Reports and sheet export"),
        ],
        widget=forms.CheckboxSelectMultiple,
        help_text="Choose exactly what this staff member can do. Administrators always have full access. Other roles cannot manage users or item setup.",
    )

    def clean_username(self):
        value = self.cleaned_data["username"]
        get_user_model()._meta.get_field("username").run_validators(value)
        return value

    def clean(self):
        data = super().clean()
        if data.get("role") == "viewer" and any(
            feature != "reports" for feature in data.get("features", [])
        ):
            raise ValidationError(
                "Report viewers can only have report access. Select Stock operator for stock entry features."
            )
        if data.get("temporary_password"):
            password_validation.validate_password(
                data["temporary_password"],
                get_user_model()(username=data.get("username", "")),
            )
        return data
