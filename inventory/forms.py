from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from .models import Item, Membership, ItemCategory, Company
from .permissions import FEATURE_GROUPS, expand_features


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
        if not self.instance.pk:
            self.fields.pop("active")

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
        choices=[choice for _, choices in FEATURE_GROUPS for choice in choices],
        widget=forms.CheckboxSelectMultiple,
        label="Allowed actions",
        help_text="Tick the actions this person can use.",
    )

    def clean_username(self):
        value = self.cleaned_data["username"]
        get_user_model()._meta.get_field("username").run_validators(value)
        existing = get_user_model().objects.filter(username=value)
        if self.target_user:
            existing = existing.exclude(pk=self.target_user.pk)
        duplicate = existing.first()
        if duplicate:
            if not duplicate.is_active:
                raise ValidationError(
                    "This username belongs to a disabled or deleted account and is reserved for its history. "
                    "Choose a different username, or use Edit to reactivate it if it is still listed in Users."
                )
            raise ValidationError("This username is already in use. Edit the existing user or choose a different username.")
        return value

    def __init__(self, *args, target_user=None, **kwargs):
        self.target_user = target_user
        original = args[0] if args else kwargs.get("data")
        if original is not None:
            data = original.copy()
            selected = data.getlist("features") if hasattr(data, "getlist") else data.get("features", [])
            legacy = {"opening", "purchase", "sale", "edit", "delete", "reports"}
            values = list(dict.fromkeys(expand_features(selected) + [f for f in selected if f not in legacy]))
            if hasattr(data, "setlist"):
                data.setlist("features", values)
            else:
                data["features"] = values
            if args:
                args = (data, *args[1:])
            else:
                kwargs["data"] = data
        super().__init__(*args, **kwargs)

    @property
    def permission_groups(self):
        fields = {widget.data["value"]: widget for widget in self["features"]}
        return [(label, [fields[value] for value, _ in choices]) for label, choices in FEATURE_GROUPS]

    def clean(self):
        data = super().clean()
        password = data.get("temporary_password")
        admin = data.get("role") == "admin" or bool(self.target_user and self.target_user.is_superuser)
        if password and len(password) < (8 if admin else 4):
            self.add_error("temporary_password", "Use at least 8 characters for an administrator." if admin else "Use at least 4 characters.")
        return data
