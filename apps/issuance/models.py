from django.conf import settings
from django.db import models

from apps.core.tenancy import TenantScopedModel


class IssuanceJob(TenantScopedModel):
    """A request to generate unique codes for a production batch.

    Processed asynchronously. The task is idempotent: if it is redelivered after
    a crash it resumes from PROCESSING and skips if already DONE.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    batch = models.ForeignKey(
        "catalogue.Batch", on_delete=models.PROTECT, related_name="issuance_jobs"
    )
    quantity_requested = models.PositiveIntegerField()
    quantity_issued = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    errors = models.JSONField(default=list)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="+",
    )

    def __str__(self):
        return f"IssuanceJob {self.pk} [{self.status}] qty={self.quantity_requested}"

    @property
    def is_reconciled(self) -> bool:
        return self.quantity_issued == self.quantity_requested


class Code(TenantScopedModel):
    """An individual unique authentication code tied to a production batch.

    `code` is globally unique (not just per-tenant) because Phase 4 looks it up
    without knowing the tenant upfront. Generated with 128 bits of entropy so
    collisions are negligible even at 10 M+ codes.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DEACTIVATED = "deactivated", "Deactivated"

    code = models.CharField(max_length=22, unique=True, db_index=True)
    batch = models.ForeignKey(
        "catalogue.Batch", on_delete=models.PROTECT, related_name="codes"
    )
    product = models.ForeignKey(
        "catalogue.Product", on_delete=models.PROTECT, related_name="codes"
    )
    issuance_job = models.ForeignKey(
        IssuanceJob, on_delete=models.PROTECT, related_name="codes"
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deactivated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    deactivation_reason = models.TextField(blank=True)

    def __str__(self):
        return f"{self.code} [{self.status}]"
