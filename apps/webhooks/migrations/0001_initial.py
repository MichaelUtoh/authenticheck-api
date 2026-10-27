import django.db.models.deletion
import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("tenants", "0003_membership_status"),
    ]

    operations = [
        migrations.CreateModel(
            name="WebhookEndpoint",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("url", models.URLField(max_length=500)),
                ("events", models.JSONField(default=list)),
                ("secret", models.CharField(max_length=64)),
                ("status", models.CharField(
                    choices=[("active", "Active"), ("disabled", "Disabled")],
                    db_index=True,
                    default="active",
                    max_length=10,
                )),
                ("description", models.CharField(blank=True, max_length=200)),
                ("failure_count", models.PositiveSmallIntegerField(default=0)),
                ("last_success_at", models.DateTimeField(blank=True, null=True)),
                ("tenant", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="+",
                    to="tenants.tenant",
                )),
            ],
            options={"abstract": False},
        ),
        migrations.CreateModel(
            name="WebhookDelivery",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("event_type", models.CharField(db_index=True, max_length=50)),
                ("payload", models.JSONField()),
                ("status", models.CharField(
                    choices=[("pending", "Pending"), ("success", "Success"), ("failed", "Failed")],
                    db_index=True,
                    default="pending",
                    max_length=10,
                )),
                ("attempt_count", models.PositiveSmallIntegerField(default=0)),
                ("response_status", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("response_body", models.TextField(blank=True)),
                ("delivered_at", models.DateTimeField(blank=True, null=True)),
                ("tenant", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="+",
                    to="tenants.tenant",
                )),
                ("endpoint", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="deliveries",
                    to="webhooks.webhookendpoint",
                )),
            ],
            options={"abstract": False},
        ),
        migrations.AddIndex(
            model_name="webhookdelivery",
            index=models.Index(fields=["endpoint", "status"], name="webhook_delivery_endpoint_status_idx"),
        ),
    ]
