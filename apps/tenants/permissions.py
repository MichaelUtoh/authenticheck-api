import uuid

from rest_framework.permissions import BasePermission

from apps.core.tenancy import _current_tenant_id

from .models import Membership, Tenant


class HasActiveTenantMembership(BasePermission):
    """Validate X-Tenant-ID against the authenticated user's active memberships and
    write the tenant into the request-scoped context so TenantScopedModel queries filter
    automatically. TenantContextMiddleware resets the context after the response."""

    message = "A valid X-Tenant-ID header with an active membership is required."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        # APIKeyAuthentication already resolved and validated the membership
        if getattr(request, "active_membership", None) is not None:
            return True

        raw = request.headers.get("X-Tenant-ID", "")
        try:
            tenant_id = uuid.UUID(raw)
        except ValueError:
            return False

        membership = (
            Membership.objects.filter(
                user=request.user, tenant_id=tenant_id, status=Membership.Status.ACTIVE
            )
            .select_related("tenant")
            .first()
        )

        if membership is None or membership.tenant.status != Tenant.Status.APPROVED:
            return False

        _current_tenant_id.set(tenant_id)
        request.active_tenant_id = tenant_id
        request.active_membership = membership
        return True


class IsMFAVerified(BasePermission):
    """Platform admins (is_staff=True) must complete TOTP before accessing staff endpoints.
    Non-staff always pass. Used in combination with IsAuthenticated."""

    message = "MFA verification is required for platform admin access."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if not request.user.is_staff:
            return True
        # JWT login: the access token's `amr` claim lists "otp" once TOTP was completed.
        auth = request.auth if isinstance(request.auth, dict) else {}
        return "otp" in auth.get("amr", [])


class IsOwnerOrAdmin(BasePermission):
    """Requires HasActiveTenantMembership to have run first (sets request.active_membership)."""

    message = "Owner or admin role required."

    def has_permission(self, request, view):
        membership = getattr(request, "active_membership", None)
        if membership is None:
            return False
        return membership.role in (Membership.Role.OWNER, Membership.Role.ADMIN)


class CanEditCatalogue(BasePermission):
    """Owner, admin and production/quality staff may create and edit drafts and enter batch data."""

    message = "Owner, admin or production/quality role required."

    def has_permission(self, request, view):
        membership = getattr(request, "active_membership", None)
        if membership is None:
            return False
        return membership.role in (
            Membership.Role.OWNER,
            Membership.Role.ADMIN,
            Membership.Role.PRODUCTION_QUALITY,
        )


class CanIssueCodes(BasePermission):
    """Owner, admin and production/quality staff may issue codes and resume issuance jobs."""

    message = "Owner, admin or production/quality role required to issue codes."

    def has_permission(self, request, view):
        membership = getattr(request, "active_membership", None)
        if membership is None:
            return False
        return membership.role in (
            Membership.Role.OWNER,
            Membership.Role.ADMIN,
            Membership.Role.PRODUCTION_QUALITY,
        )


class CanSubmitProducts(BasePermission):
    """Owner and admin always; production/quality staff only when can_submit_products is enabled."""

    message = "You do not have permission to submit products for review."

    def has_permission(self, request, view):
        membership = getattr(request, "active_membership", None)
        if membership is None:
            return False
        if membership.role in (Membership.Role.OWNER, Membership.Role.ADMIN):
            return True
        return (
            membership.role == Membership.Role.PRODUCTION_QUALITY and membership.can_submit_products
        )
