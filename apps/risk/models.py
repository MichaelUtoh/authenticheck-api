import uuid

from django.conf import settings
from django.db import models

from apps.core.tenancy import TenantScopedModel


class ConsumerReport(models.Model):
    """Unauthenticated tip from a consumer who suspects a counterfeit."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    code_str = models.CharField(max_length=22, db_index=True)
    code = models.ForeignKey(
        "issuance.Code",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="consumer_reports",
    )
    description = models.TextField()
    contact_email = models.EmailField(null=True, blank=True)
    country_code = models.CharField(max_length=3, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    def __str__(self):
        return f"Report {self.pk} [{self.code_str}]"


class Case(TenantScopedModel):
    """An open investigation into suspected counterfeiting activity."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        UNDER_REVIEW = "under_review", "Under Review"
        CLOSED = "closed", "Closed"

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    batch = models.ForeignKey(
        "catalogue.Batch",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cases",
    )
    product = models.ForeignKey(
        "catalogue.Product",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cases",
    )
    notes = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.title} [{self.status}]"


class RiskFlag(TenantScopedModel):
    """System-generated signal of suspicious activity, linked to a code, batch, or scan."""

    class Kind(models.TextChoices):
        SCAN_AFTER_DEACTIVATION = "scan_after_deactivation", "Scan After Deactivation"
        REPEATED_SCAN = "repeated_scan", "Repeated Scan"
        CONSUMER_REPORT = "consumer_report", "Consumer Report"

    class Severity(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    kind = models.CharField(max_length=30, choices=Kind.choices, db_index=True)
    severity = models.CharField(max_length=10, choices=Severity.choices, db_index=True)
    code = models.ForeignKey(
        "issuance.Code",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="risk_flags",
    )
    batch = models.ForeignKey(
        "catalogue.Batch",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="risk_flags",
    )
    detail = models.JSONField(default=dict)
    is_reviewed = models.BooleanField(default=False, db_index=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    case = models.ForeignKey(
        Case,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="flags",
    )

    def __str__(self):
        return f"{self.kind} [{self.severity}] reviewed={self.is_reviewed}"
