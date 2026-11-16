from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class Tenant(BaseModel):
    """A manufacturer: the isolation boundary. Phase 1 adds onboarding workflow, evidence, contacts,
    memberships and roles. Status changes must always be audited."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        INFO_REQUESTED = "info_requested", "More information requested"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        SUSPENDED = "suspended", "Suspended"

    legal_name = models.CharField(max_length=255)
    trading_name = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    is_pharma = models.BooleanField(default=False, db_index=True)
    # Collected at registration so platform staff can review the application. Blank on tenants
    # registered before these were required.
    industry = models.CharField(max_length=40, blank=True, db_index=True)  # Industry.key
    rc_number = models.CharField(max_length=32, blank=True)  # CAC registration number
    city = models.CharField(max_length=120, blank=True)
    # Latest platform decision that needs the applicant's attention (rejected / info requested).
    # Shown to the tenant; cleared when the tenant is approved or suspended.
    decision_reason = models.TextField(blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    # Default consumer-facing support contact; a product may override it.
    support_email = models.EmailField(blank=True)
    support_phone = models.CharField(max_length=32, blank=True)
    support_url = models.URLField(blank=True)

    def __str__(self):
        return self.trading_name or self.legal_name


# Role names used before the persona-based roles; still accepted by the API and mapped on input.
LEGACY_ROLE_ALIASES = {"editor": "production_quality", "member": "production_quality"}


class Membership(BaseModel):
    """A user's role within a tenant. One person can belong to many tenants (EC-039/EC-040).
    The active tenant is set per-request via the token tid claim, validated against this table."""

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        PRODUCTION_QUALITY = "production_quality", "Production/Quality"
        INVESTIGATOR = "investigator", "Investigator"
        VIEWER = "viewer", "Viewer"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        REVOKED = "revoked", "Revoked"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=Role.choices)
    # Production/quality staff may submit products for review only when an owner/admin enables it.
    can_submit_products = models.BooleanField(default=False)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    joined_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "tenant"], name="uniq_membership_user_tenant")
        ]

    def __str__(self):
        return f"{self.user} @ {self.tenant} ({self.role})"
