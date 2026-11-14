from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError

from .models import Item, Membership, ItemCategory, Company


class ItemForm(forms.ModelForm):
    category = forms.ChoiceField(choices=())

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        company = company or (
            self.instance.company
            if self.instance.pk
            else Company.objects.filter(is_primary=True).first()
        )
        choices = (
            list(
                ItemCategory.objects.filter(company=company, active=True).values_list(
                    "name", "name"
                )
            )
            if company
            else []
        )
        self.fields["category"].choices = [("", "Choose category"), *choices]

    class Meta:
        model = Item
        fields = ["name", "category", "active"]


class CategoryForm(forms.Form):
    name = forms.CharField(label="Category name", max_length=80)


class UserForm(forms.Form):
    username = forms.CharField(max_length=150)
    temporary_password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput,
        required=False,
        help_text="Leave blank to keep the current password.",
    )
    role = forms.ChoiceField(choices=Membership.ROLES)
    active = forms.BooleanField(required=False, initial=True)
    features = forms.MultipleChoiceField(
        required=False,
        choices=[
            ("opening", "Opening stock"),
            ("purchase", "Purchases"),
            ("sale", "Sales"),
            ("edit", "Edit entries"),
            ("delete", "Delete entries"),
            ("reports", "Reports"),
        ],
        widget=forms.CheckboxSelectMultiple,
        label="Allowed actions",
        help_text="Tick the actions this person can use.",
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
