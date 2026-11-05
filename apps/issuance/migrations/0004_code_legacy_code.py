from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("issuance", "0003_code_first_checked_at")]

    operations = [
        migrations.AddField(
            model_name="code",
            name="legacy_code",
            field=models.CharField(blank=True, editable=False, max_length=22, null=True, unique=True),
        ),
    ]
