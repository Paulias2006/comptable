from django.db import migrations, models
from django.utils import timezone


def create_installation_state(apps, schema_editor):
    InstallationState = apps.get_model("ledger", "InstallationState")
    Membership = apps.get_model("ledger", "Membership")
    already_claimed = Membership.objects.exists()
    InstallationState.objects.get_or_create(
        key="primary",
        defaults={"claimed": already_claimed, "claimed_at": timezone.now() if already_claimed else None},
    )


class Migration(migrations.Migration):
    dependencies = [("ledger", "0004_company_last_backup_at")]

    operations = [
        migrations.CreateModel(
            name="InstallationState",
            fields=[
                ("key", models.CharField(default="primary", max_length=16, primary_key=True, serialize=False)),
                ("claimed", models.BooleanField(default=False)),
                ("claimed_at", models.DateTimeField(blank=True, null=True)),
            ],
        ),
        migrations.RunPython(create_installation_state, migrations.RunPython.noop),
    ]
