from django.conf import settings
from django.db import migrations
from django.db.models import Count


def configure_workspace(apps, schema_editor):
    Company = apps.get_model("inventory", "Company")
    Membership = apps.get_model("inventory", "Membership")
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    owner = (
        User.objects.filter(is_superuser=True, is_active=True).order_by("pk").first()
    )
    membership = Membership.objects.filter(user=owner).first() if owner else None
    primary = (
        Company.objects.filter(pk=membership.company_id).first()
        if membership
        else Company.objects.annotate(
            stock_entries=Count("entry", distinct=True),
            stock_items=Count("item", distinct=True),
        )
        .order_by("-stock_entries", "-stock_items", "pk")
        .first()
    )
    if not primary:
        primary = Company.objects.create(name="My shop")
    primary.is_primary = True
    primary.active = True
    primary.save(update_fields=["is_primary", "active"])
    # Existing staff become one internal team. Historical stock documents retain
    # their original company references; no unrelated stock is silently merged.
    Membership.objects.exclude(company=primary).update(company=primary)
    for admin in User.objects.filter(is_superuser=True, is_active=True):
        Membership.objects.get_or_create(
            user=admin,
            defaults={
                "company": primary,
                "role": "admin",
                "must_change_password": False,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("inventory", "0005_company_is_primary_and_more")]
    operations = [migrations.RunPython(configure_workspace)]
