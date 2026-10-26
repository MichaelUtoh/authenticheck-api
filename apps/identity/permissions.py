"""Reusable DRF permission classes for JWT-based authentication.

Compose these on views — no inline role checks anywhere.
    class MyView(APIView):
        permission_classes = [IsAuthenticatedSession, HasActiveTenantContext, HasRole("owner")]
"""

import time

from django.conf import settings
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission


class IsAuthenticatedSession(BasePermission):
    """Valid JWT + live session. Replaces IsAuthenticated for JWT-auth views."""

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and getattr(request, "auth_context", None)
        )


class HasActiveTenantContext(BasePermission):
    """Requires tid in the token (set by JWTAuthentication) resolving to an approved tenant
    with an active membership. The tenant context is set in the authentication class."""

    message = "An active tenant context is required."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and getattr(request, "active_membership", None) is not None
        )


class HasRole(BasePermission):
    """Check the membership role loaded from the database. Pass one or more allowed roles.

    Usage:
        permission_classes = [IsAuthenticatedSession, HasActiveTenantContext, HasRole("owner", "admin")]
    """

    def __init__(self, *roles):
        self.roles = roles

    def has_permission(self, request, view):
        membership = getattr(request, "active_membership", None)
        if membership is None:
            return False
        return membership.role in self.roles


class IsPlatformAdmin(BasePermission):
    """is_staff AND otp in amr. Combine with RequiresRecentAuth for privileged mutations."""

    message = "Platform admin access with MFA verification is required."

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated and request.user.is_staff):
            return False
        auth_context = getattr(request, "auth_context", None) or {}
        return "otp" in auth_context.get("amr", [])


class RequiresRecentAuth(BasePermission):
    """Checks that the password/MFA was proved within max_age_seconds. Returns 403 with
    reauth_required if stale, so the client can redirect to POST /auth/reauth. [EC-044]"""

    def __init__(self, max_age_seconds: int | None = None):
        self.max_age_seconds = max_age_seconds or getattr(settings, "RECENT_AUTH_SECONDS", 300)

    def has_permission(self, request, view):
        auth_context = getattr(request, "auth_context", None) or {}
        auth_time = auth_context.get("auth_time", 0)
        if (time.time() - auth_time) > self.max_age_seconds:
            raise PermissionDenied(
                {"code": "reauth_required", "message": "Re-authentication is required."}
            )
        return True


class HasSupportGrant(BasePermission):
    """Requires a valid, approved, unexpired, unrevoked SupportAccessGrant for the
    authenticated support user and the active tenant."""

    message = "A valid support access grant is required."

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        tid = getattr(request, "active_tenant_id", None)
        if not tid:
            return False

        from django.utils import timezone

        from apps.identity.models import SupportAccessGrant

        return SupportAccessGrant.objects.filter(
            support_user=request.user,
            tenant_id=tid,
            approved_by__isnull=False,
            expires_at__gt=timezone.now(),
            revoked_at__isnull=True,
        ).exists()
