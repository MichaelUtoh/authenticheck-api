from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0003_membership_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="is_pharma",
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
