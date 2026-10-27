from django.db import models

from apps.core.tenancy import TenantScopedModel


class WebhookEndpoint(TenantScopedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DISABLED = "disabled", "Disabled"

    url = models.URLField(max_length=500)
    events = models.JSONField(default=list)  # e.g. ["code.verified", "batch.recalled"]
    secret = models.CharField(max_length=64)  # hex HMAC signing secret, shown only on creation
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    description = models.CharField(max_length=200, blank=True)
    failure_count = models.PositiveSmallIntegerField(default=0)
    last_success_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Webhook {self.url} ({self.tenant_id})"


class WebhookDelivery(TenantScopedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    endpoint = models.ForeignKey(
        WebhookEndpoint, on_delete=models.CASCADE, related_name="deliveries"
    )
    event_type = models.CharField(max_length=50, db_index=True)
    payload = models.JSONField()
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    attempt_count = models.PositiveSmallIntegerField(default=0)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_body = models.TextField(blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["endpoint", "status"]),
        ]

    def __str__(self):
        return f"Delivery {self.id} [{self.event_type}] → {self.status}"
