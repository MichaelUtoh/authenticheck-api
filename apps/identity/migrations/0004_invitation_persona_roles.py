from django.db import migrations


def to_persona_roles(apps, schema_editor):
    Invitation = apps.get_model("identity", "Invitation")
    Invitation.objects.filter(role__in=["editor", "member"]).update(role="production_quality")


def to_legacy_roles(apps, schema_editor):
    Invitation = apps.get_model("identity", "Invitation")
    Invitation.objects.filter(role="production_quality").update(role="editor")
    Invitation.objects.filter(role="investigator").update(role="viewer")


class Migration(migrations.Migration):
    dependencies = [("identity", "0003_invitation_can_submit_products")]

    operations = [migrations.RunPython(to_persona_roles, to_legacy_roles)]
