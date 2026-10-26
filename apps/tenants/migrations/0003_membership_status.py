import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def copy_is_active_to_status(apps, schema_editor):
    Membership = apps.get_model("tenants", "Membership")
    Membership.objects.filter(is_active=False).update(status="revoked")


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0002_membership"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 1. Add status first (default active), then migrate data, then drop is_active.
        migrations.AddField(
            model_name="membership",
            name="status",
            field=models.CharField(
                choices=[("active", "Active"), ("revoked", "Revoked")],
                db_index=True,
                default="active",
                max_length=10,
            ),
        ),
        migrations.RunPython(copy_is_active_to_status, migrations.RunPython.noop),
        migrations.RemoveField(model_name="membership", name="is_active"),
        migrations.AddField(
            model_name="membership",
            name="revoked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="membership",
            name="revoked_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
