import uuid

from django.db import models


class BaseModel(models.Model):
    """UUID primary key + created/updated timestamps. Never expose sequential IDs."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class IdempotencyRecord(models.Model):
    """Claim on an (scope, Idempotency-Key) pair, holding the response for safe replay."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope = models.CharField(max_length=255)  # e.g. "<user_id>:<tenant_id>:issuance.create"
    key = models.CharField(max_length=128)
    request_hash = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_body = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["scope", "key"], name="uniq_idempotency_scope_key")
        ]

    def __str__(self):
        return f"{self.scope}:{self.key}"
