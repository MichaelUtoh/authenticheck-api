import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel


class AuthSession(BaseModel):
    """Server-side record for a JWT-based login session. The refresh token is stored
    only as a SHA-256 hash. Session validity is checked on every authenticated request."""

    class RevocationReason(models.TextChoices):
        LOGOUT = "logout", "Logout"
        LOGOUT_ALL = "logout_all", "Logout all sessions"
        PASSWORD_RESET = "password_reset", "Password reset"
        ADMIN_REVOKE = "admin_revoke", "Revoked by admin"
        USER_DEACTIVATED = "user_deactivated", "User deactivated"
        MEMBERSHIP_REVOKED = "membership_revoked", "Membership revoked"
        REUSE_DETECTED = "reuse_detected", "Refresh token reuse detected"
        MFA_RESET = "mfa_reset", "MFA reset"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="auth_sessions",
    )
    refresh_hash = models.CharField(max_length=64, unique=True, db_index=True)
    previous_refresh_hash = models.CharField(max_length=64, blank=True, db_index=True)
    rotated_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(default=timezone.now)
    idle_expires_at = models.DateTimeField()
    absolute_expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(
        max_length=30, choices=RevocationReason.choices, blank=True
    )
    user_agent = models.CharField(max_length=200, blank=True)
    ip_prefix = models.CharField(max_length=50, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "revoked_at"]),
        ]

    def __str__(self):
        return f"Session {self.id} for {self.user_id}"

    @property
    def is_active(self):
        now = timezone.now()
        return (
            self.revoked_at is None
            and self.idle_expires_at > now
            and self.absolute_expires_at > now
        )


class MFAChallenge(BaseModel):
    """Short-lived, single-use token issued during platform admin login to gate the MFA step.
    Stored in DB for attempt counting (max 5 failures before the challenge is dead)."""

    class Kind(models.TextChoices):
        ENROLL = "enroll", "Enrollment"
        VERIFY = "verify", "Verification"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
    )
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    attempts = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"MFAChallenge({self.kind}) for {self.user_id}"

    @property
    def is_valid(self):
        return self.used_at is None and self.expires_at > timezone.now()


class PasswordResetToken(BaseModel):
    """Single-use, hashed, 30-minute token for password resets. A new request invalidates
    previous tokens for the same user."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
    )
    token_hash = models.CharField(max_length=64, db_index=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"PasswordResetToken for {self.user_id}"


class EmailVerificationToken(BaseModel):
    """Single-use, hashed token to verify a user's email address."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
    )
    token_hash = models.CharField(max_length=64, db_index=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"EmailVerificationToken for {self.user_id}"


class Invitation(BaseModel):
    """Invitation for a user to join a tenant with a given role. Token is stored hashed,
    single-use. One pending invitation per (tenant, email) via partial unique constraint."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REVOKED = "revoked", "Revoked"
        EXPIRED = "expired", "Expired"

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="invitations",
    )
    email = models.EmailField()
    role = models.CharField(max_length=20)
    token_hash = models.CharField(max_length=64, db_index=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    expires_at = models.DateTimeField()
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="+",
    )
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    accepted_at = models.DateTimeField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=128, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "email"],
                condition=models.Q(status="pending"),
                name="uniq_pending_invitation_tenant_email",
            )
        ]

    def __str__(self):
        return f"Invitation for {self.email} to {self.tenant_id} ({self.status})"


class SupportAccessGrant(BaseModel):
    """Temporary, read-only support access to a tenant. Must be approved by a different
    platform admin. Enforced on every request within the grant window."""

    SCOPE_READ_ONLY = "read_only"

    support_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
    )
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="support_grants",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    reason = models.TextField()
    scope = models.CharField(max_length=20, default=SCOPE_READ_ONLY)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"SupportGrant for {self.support_user_id} on {self.tenant_id}"

    @property
    def is_active(self):
        return (
            self.approved_by_id is not None
            and self.revoked_at is None
            and self.expires_at > timezone.now()
        )
