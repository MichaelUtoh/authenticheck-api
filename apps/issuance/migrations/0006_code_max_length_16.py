from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("issuance", "0005_convert_codes_to_12_char")]

    operations = [
        migrations.AlterField(
            model_name="code",
            name="code",
            field=models.CharField(db_index=True, max_length=16, unique=True),
        ),
    ]
