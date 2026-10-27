from django.db.models import Count, Q
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from drf_spectacular.types import OpenApiTypes
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import AuditEvent
from apps.core.pagination import StandardPagination
from apps.issuance.models import Code
from apps.risk.models import RiskFlag
from apps.tenants.models import Membership, Tenant
from apps.tenants.permissions import IsMFAVerified

from .serializers import (
    PlatformAuditEventSerializer,
    PlatformRiskFlagSerializer,
    PlatformTenantDetailSerializer,
    PlatformTenantSerializer,
)

_STAFF_PERMS = [IsAuthenticated, IsAdminUser, IsMFAVerified]


@extend_schema_view(
    get=extend_schema(
        parameters=[
            OpenApiParameter(
                "status",
                OpenApiTypes.STR,
                description="Filter by tenant status",
            )
        ],
        responses={200: PlatformTenantSerializer(many=True)},
        tags=["Platform — Tenants"],
        summary="List all tenants (staff)",
    )
)
class PlatformTenantListView(APIView):
    permission_classes = _STAFF_PERMS

    def get(self, request):
        qs = (
            Tenant.objects
            .annotate(
                member_count=Count(
                    "memberships__id",
                    filter=Q(memberships__status=Membership.Status.ACTIVE),
                )
            )
            .order_by("-created_at")
        )
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(PlatformTenantSerializer(page, many=True).data)


@extend_schema(
    responses={200: PlatformTenantDetailSerializer},
    tags=["Platform — Tenants"],
    summary="Retrieve a tenant with usage stats (staff)",
)
class PlatformTenantDetailView(APIView):
    permission_classes = _STAFF_PERMS

    def get(self, request, pk):
        try:
            tenant = (
                Tenant.objects
                .annotate(
                    member_count=Count(
                        "memberships__id",
                        filter=Q(memberships__status=Membership.Status.ACTIVE),
                    )
                )
                .get(pk=pk)
            )
        except Tenant.DoesNotExist:
            raise NotFound("Tenant not found.") from None

        tenant.active_codes = Code.unscoped.filter(
            tenant_id=pk, status=Code.Status.ACTIVE
        ).count()
        tenant.open_flags = RiskFlag.unscoped.filter(
            tenant_id=pk, is_reviewed=False
        ).count()

        return Response(PlatformTenantDetailSerializer(tenant).data)


@extend_schema(
    parameters=[
        OpenApiParameter("severity", OpenApiTypes.STR, description="Filter by severity"),
        OpenApiParameter("kind", OpenApiTypes.STR, description="Filter by flag kind"),
    ],
    responses={200: PlatformRiskFlagSerializer(many=True)},
    tags=["Platform — Risk"],
    summary="List unreviewed risk flags across all tenants (staff)",
)
class PlatformFlagListView(APIView):
    permission_classes = _STAFF_PERMS

    def get(self, request):
        qs = (
            RiskFlag.unscoped
            .filter(is_reviewed=False)
            .select_related("tenant", "code", "batch")
            .order_by("-created_at")
        )
        severity = request.query_params.get("severity")
        if severity:
            qs = qs.filter(severity=severity)
        kind = request.query_params.get("kind")
        if kind:
            qs = qs.filter(kind=kind)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(
            PlatformRiskFlagSerializer(page, many=True).data
        )


@extend_schema(
    parameters=[
        OpenApiParameter("action", OpenApiTypes.STR, description="Filter by action prefix"),
        OpenApiParameter("tenant_id", OpenApiTypes.UUID, description="Filter by tenant UUID"),
    ],
    responses={200: PlatformAuditEventSerializer(many=True)},
    tags=["Platform — Audit"],
    summary="Paginated audit log across all tenants (staff)",
)
class PlatformAuditLogView(APIView):
    permission_classes = _STAFF_PERMS

    def get(self, request):
        qs = AuditEvent.objects.select_related("actor", "tenant").order_by("-occurred_at")

        action = request.query_params.get("action")
        if action:
            qs = qs.filter(action__startswith=action)

        tenant_id = request.query_params.get("tenant_id")
        if tenant_id:
            qs = qs.filter(tenant_id=tenant_id)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(
            PlatformAuditEventSerializer(page, many=True).data
        )
