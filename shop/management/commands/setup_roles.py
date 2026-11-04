from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create/update Cashier, Inventory Manager and Owner groups. Does not grant staff status or assign users."

    def handle(self, *args, **options):
        permissions = Permission.objects.filter(content_type__app_label="shop")
        cashier = {"add_sale", "view_sale", "post_sale"}
        inventory = {
            f"{verb}_{model}"
            for model in ["product", "purchase", "purchaseline", "stockadjustment"]
            for verb in ["add", "view", "change"]
        }
        inventory |= {"post_purchase", "post_stockadjustment", "view_stockmovement"}
        for name, selected in [
            ("Cashier", cashier),
            ("Inventory Manager", inventory),
            ("Owner", None),
        ]:
            group, _ = Group.objects.get_or_create(name=name)
            group.permissions.set(
                permissions
                if selected is None
                else permissions.filter(codename__in=selected)
            )
        self.stdout.write(
            self.style.SUCCESS(
                "Roles updated. Assign groups and staff status through Users admin."
            )
        )
