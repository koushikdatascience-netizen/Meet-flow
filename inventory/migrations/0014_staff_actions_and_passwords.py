from django.db import migrations, models


def update_memberships(apps, schema_editor):
    Membership = apps.get_model("inventory", "Membership")
    # Freeze the old-to-new mapping here so future permission changes cannot
    # change the meaning of this data migration.
    for member in Membership.objects.all().iterator():
        features = member.features
        member.must_change_password = False
        if features is not None:
            expanded = [f for f in features if f not in ["opening", "purchase", "sale", "edit", "delete", "reports"]]
            for module in ("opening", "purchase", "sale"):
                if module in features:
                    expanded.append(f"{module}_add")
                    for action in ("edit", "delete"):
                        if action in features:
                            expanded.append(f"{module}_{action}")
            if "reports" in features:
                expanded.extend(["reports_view", "reports_download"])
            member.features = list(dict.fromkeys(expanded))
        member.save(update_fields=["features", "must_change_password"])


class Migration(migrations.Migration):
    dependencies = [("inventory", "0013_stockclearbackup")]
    operations = [
        migrations.AlterField(model_name="membership", name="must_change_password", field=models.BooleanField(default=False)),
        migrations.RunPython(update_memberships, migrations.RunPython.noop),
    ]
