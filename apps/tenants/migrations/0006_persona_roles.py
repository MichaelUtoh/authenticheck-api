from django.db import migrations, models

LEGACY = ["editor", "member"]


def to_persona_roles(apps, schema_editor):
    Membership = apps.get_model("tenants", "Membership")
    Membership.objects.filter(role__in=LEGACY).update(role="production_quality")


def to_legacy_roles(apps, schema_editor):
    Membership = apps.get_model("tenants", "Membership")
    Membership.objects.filter(role="production_quality").update(role="editor")
    Membership.objects.filter(role="investigator").update(role="viewer")


class Migration(migrations.Migration):
    dependencies = [("tenants", "0005_product_workflow_decisions")]

    operations = [
        migrations.AlterField(
            model_name="membership",
            name="role",
            field=models.CharField(
                choices=[
                    ("owner", "Owner"),
                    ("admin", "Admin"),
                    ("production_quality", "Production/Quality"),
                    ("investigator", "Investigator"),
                    ("viewer", "Viewer"),
                ],
                max_length=20,
            ),
        ),
        migrations.RunPython(to_persona_roles, to_legacy_roles),
    ]
