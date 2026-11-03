import logging

from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit import services as audit

from .models import Membership, Tenant
from .permissions import HasActiveTenantMembership, IsMFAVerified, IsOwnerOrAdmin
from .serializers import (
    MembershipSerializer,
    MembershipUpdateSerializer,
    SupportContactSerializer,
    TenantRegisterSerializer,
    TenantSerializer,
    TenantStatusUpdateSerializer,
)

logger = logging.getLogger(__name__)

_ALLOWED_TRANSITIONS = {
    Tenant.Status.PENDING: {
        "approve": Tenant.Status.APPROVED,
        "reject": Tenant.Status.REJECTED,
        "request_info": Tenant.Status.INFO_REQUESTED,
    },
    Tenant.Status.INFO_REQUESTED: {
        "approve": Tenant.Status.APPROVED,
        "reject": Tenant.Status.REJECTED,
    },
    Tenant.Status.APPROVED: {
        "suspend": Tenant.Status.SUSPENDED,
    },
    Tenant.Status.SUSPENDED: {
        "approve": Tenant.Status.APPROVED,
    },
}


@extend_schema(
    request=TenantRegisterSerializer,
    responses={201: TenantSerializer},
    tags=["Tenants"],
    summary="Register a new manufacturer tenant",
    description="Creates the tenant in `pending` status and makes the requesting user its owner.",
)
class TenantRegisterView(APIView):
    """Create a new tenant and make the requesting user its OWNER."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = TenantRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tenant = serializer.save()
        Membership.objects.create(
            user=request.user,
            tenant=tenant,
            role=Membership.Role.OWNER,
            joined_at=timezone.now(),
        )
        audit.record(
            action="tenant.registered",
            actor=request.user,
            tenant=tenant,
            target=tenant,
            after={"legal_name": tenant.legal_name, "status": tenant.status},
        )
        return Response(TenantSerializer(tenant).data, status=status.HTTP_201_CREATED)


@extend_schema(
    responses={200: TenantSerializer},
    tags=["Tenants"],
    summary="Retrieve the active tenant's details",
)
class TenantDetailView(APIView):
    """Retrieve a tenant. Requires active membership and approved tenant."""

    permission_classes = [IsAuthenticated, HasActiveTenantMembership]

    def get(self, request):
        return Response(TenantSerializer(request.active_membership.tenant).data)


@extend_schema(
    request=TenantStatusUpdateSerializer,
    responses={200: TenantSerializer},
    tags=["Tenants — Staff"],
)
class _TenantStatusTransitionView(APIView):
    """Base for staff-only status transitions."""

    permission_classes = [IsAuthenticated, IsAdminUser, IsMFAVerified]
    _action: str = ""

    def _get_tenant(self, pk):
        try:
            return Tenant.objects.get(pk=pk)
        except Tenant.DoesNotExist:
            raise NotFound("Tenant not found.") from None

    def post(self, request, pk):
        tenant = self._get_tenant(pk)
        allowed = _ALLOWED_TRANSITIONS.get(tenant.status, {})
        if self._action not in allowed:
            raise PermissionDenied(
                f"Cannot perform '{self._action}' from status '{tenant.status}'."
            )

        serializer = TenantStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        before = {"status": tenant.status}
        tenant.status = allowed[self._action]
        tenant.save(update_fields=["status", "updated_at"])

        audit.record(
            action=f"tenant.{self._action}",
            actor=request.user,
            tenant=tenant,
            target=tenant,
            before=before,
            after={"status": tenant.status},
            reason=serializer.validated_data.get("reason", ""),
        )
        return Response(TenantSerializer(tenant).data)


@extend_schema(summary="Approve a pending or info-requested tenant")
class TenantApproveView(_TenantStatusTransitionView):
    _action = "approve"


@extend_schema(summary="Reject a pending or info-requested tenant")
class TenantRejectView(_TenantStatusTransitionView):
    _action = "reject"


@extend_schema(summary="Request further information from a pending tenant")
class TenantRequestInfoView(_TenantStatusTransitionView):
    _action = "request_info"


@extend_schema(summary="Suspend an approved tenant")
class TenantSuspendView(_TenantStatusTransitionView):
    _action = "suspend"


@extend_schema(
    responses={200: MembershipSerializer(many=True)},
    tags=["Tenants"],
    summary="List active members of the current tenant",
)
class TenantMembersView(APIView):
    """List members of a tenant. Requires owner or admin role."""

    permission_classes = [IsAuthenticated, HasActiveTenantMembership, IsOwnerOrAdmin]

    def get(self, request):
        members = Membership.objects.filter(
            tenant_id=request.active_tenant_id, status=Membership.Status.ACTIVE
        ).select_related("user", "tenant")
        return Response(MembershipSerializer(members, many=True).data)


@extend_schema(
    responses={200: None},
    tags=["Tenants"],
    summary="Industry release gate states for the active tenant",
)
class ReleaseGatesView(APIView):
    permission_classes = [IsAuthenticated, HasActiveTenantMembership]

    def get(self, request):
        return Response({
            "cosmetics": {"status": "live"},
            "pharmaceuticals": {
                "status": "locked",
                "items": [
                    {
                        "key": "nafdac_gs1_review",
                        "label": "Qualified NAFDAC / GS1 review",
                        "state": "in_progress",
                    },
                    {
                        "key": "identifier_decision",
                        "label": "Identifier & data-carrier decision",
                        "state": "not_started",
                    },
                ],
            },
        })


@extend_schema_view(
    get=extend_schema(
        responses={200: SupportContactSerializer},
        tags=["Tenants"],
        summary="Get the tenant's default support contact",
    ),
    patch=extend_schema(
        request=SupportContactSerializer,
        responses={200: SupportContactSerializer},
        tags=["Tenants"],
        summary="Update the tenant's default support contact",
    ),
)
class SupportContactView(APIView):
    """Support contact lives in tenant settings; a product may override it."""

    permission_classes = [IsAuthenticated, HasActiveTenantMembership]

    def get(self, request):
        return Response(SupportContactSerializer(request.active_membership.tenant).data)

    def patch(self, request):
        if not IsOwnerOrAdmin().has_permission(request, self):
            raise PermissionDenied(IsOwnerOrAdmin.message)
        tenant = request.active_membership.tenant
        before = SupportContactSerializer(tenant).data
        serializer = SupportContactSerializer(tenant, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        audit.record(
            action="tenant.support_contact.updated",
            actor=request.user,
            tenant=tenant,
            target=tenant,
            before=dict(before),
            after=dict(serializer.data),
        )
        return Response(serializer.data)


@extend_schema(
    request=MembershipUpdateSerializer,
    responses={200: MembershipSerializer},
    tags=["Tenants"],
    summary="Change a member's role or product-submission permission",
)
class TenantMemberDetailView(APIView):
    """Owner/admin may enable submission for production/quality staff; only the owner may change roles."""

    permission_classes = [IsAuthenticated, HasActiveTenantMembership, IsOwnerOrAdmin]

    def patch(self, request, pk):
        try:
            member = Membership.objects.select_related("user", "tenant").get(
                pk=pk, tenant_id=request.active_tenant_id, status=Membership.Status.ACTIVE
            )
        except Membership.DoesNotExist:
            raise NotFound("Member not found.") from None
        serializer = MembershipUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if member.role == Membership.Role.OWNER:
            raise PermissionDenied("The owner's role cannot be changed.")
        if "role" in data and request.active_membership.role != Membership.Role.OWNER:
            raise PermissionDenied("Only the owner can change roles.")

        before = {"role": member.role, "can_submit_products": member.can_submit_products}
        for attr, val in data.items():
            setattr(member, attr, val)
        member.save()
        audit.record(
            action="tenant.member.updated",
            actor=request.user,
            tenant=request.active_membership.tenant,
            target=member,
            before=before,
            after={"role": member.role, "can_submit_products": member.can_submit_products},
        )
        return Response(MembershipSerializer(member).data)
