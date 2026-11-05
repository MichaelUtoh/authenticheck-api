import uuid

from django.db import models


class ScanEvent(models.Model):
    """Immutable record of every public code lookup. High-volume; no updated_at."""

    class Result(models.TextChoices):
        GENUINE = "genuine", "Genuine"
        NOT_FOUND = "not_found", "Not Found"
        DEACTIVATED = "deactivated", "Deactivated"
        RECALLED = "recalled", "Recalled"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)
    code_str = models.CharField(max_length=22, db_index=True)
    code = models.ForeignKey(
        "issuance.Code",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="scan_events",
    )
    result = models.CharField(max_length=20, choices=Result.choices, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["code_str", "occurred_at"], name="scanevent_code_time_idx"),
        ]

    def __str__(self):
        return f"{self.code_str} [{self.result}]"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("ScanEvents are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("ScanEvents cannot be deleted.")
