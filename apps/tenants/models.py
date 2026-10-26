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

    def __str__(self):
        return self.trading_name or self.legal_name


class Membership(BaseModel):
    """A user's role within a tenant. One person can belong to many tenants (EC-039/EC-040).
    The active tenant is set per-request via X-Tenant-ID, validated against this table."""

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"
        VIEWER = "viewer", "Viewer"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=Role.choices)
    is_active = models.BooleanField(default=True, db_index=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    joined_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "tenant"], name="uniq_membership_user_tenant")
        ]

    def __str__(self):
        return f"{self.user} @ {self.tenant} ({self.role})"
