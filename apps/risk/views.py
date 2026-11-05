import logging

from django.db import models as django_models
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from drf_spectacular.types import OpenApiTypes
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit import services as audit
from apps.issuance.codes import find_code
from apps.issuance.models import Code
from apps.tenants.permissions import HasActiveTenantMembership, IsOwnerOrAdmin

from . import services as risk_services
from .models import Case, ConsumerReport, RiskFlag
from .serializers import (
    CaseCreateSerializer,
    CaseSerializer,
    CaseUpdateSerializer,
    ConsumerReportCreateSerializer,
    ConsumerReportSerializer,
    RiskFlagSerializer,
)
from .throttles import ReportIPThrottle

logger = logging.getLogger(__name__)

_BASE_PERMS = [IsAuthenticated, HasActiveTenantMembership]
_WRITE_PERMS = [IsAuthenticated, HasActiveTenantMembership, IsOwnerOrAdmin]


# ---------------------------------------------------------------------------
# Consumer reports
# ---------------------------------------------------------------------------


@extend_schema(
    request=ConsumerReportCreateSerializer,
    responses={201: ConsumerReportSerializer},
    tags=["Risk — Reports"],
    summary="Submit a consumer counterfeit report",
    description=(
        "Public endpoint. No authentication required. "
        "Rate-limited to 5 per hour per IP. "
        "If the code exists in the system, a risk flag is automatically raised."
    ),
)
class ConsumerReportView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ReportIPThrottle]

    def post(self, request):
        serializer = ConsumerReportCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        ip = _get_client_ip(request)
        code_str = data["code_str"]

        code = find_code(code_str, Code.unscoped.select_related("batch"))

        report = ConsumerReport.objects.create(
            code_str=code_str,
            code=code,
            description=data["description"],
            contact_email=data["contact_email"] or None,
            country_code=data["country_code"],
            ip_address=ip,
        )

        if code is not None:
            try:
                risk_services.create_report_flag(code)
            except Exception:
                logger.exception("Failed to create report risk flag for code=%s", code_str)

        return Response(ConsumerReportSerializer(report).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Risk flags
# ---------------------------------------------------------------------------


@extend_schema(
    parameters=[
        OpenApiParameter("kind", OpenApiTypes.STR, description="Filter by flag kind"),
        OpenApiParameter("severity", OpenApiTypes.STR, description="Filter by severity"),
        OpenApiParameter(
            "is_reviewed", OpenApiTypes.BOOL, description="Filter by review status"
        ),
    ],
    responses={200: RiskFlagSerializer(many=True)},
    tags=["Risk — Flags"],
    summary="List risk flags for the tenant",
)
class RiskFlagListView(APIView):
    permission_classes = _BASE_PERMS

    def get(self, request):
        qs = RiskFlag.objects.select_related("code", "batch", "case").order_by("-created_at")

        kind = request.query_params.get("kind")
        if kind:
            qs = qs.filter(kind=kind)

        severity = request.query_params.get("severity")
        if severity:
            qs = qs.filter(severity=severity)

        is_reviewed = request.query_params.get("is_reviewed")
        if is_reviewed is not None:
            qs = qs.filter(is_reviewed=is_reviewed.lower() == "true")

        return Response(RiskFlagSerializer(qs, many=True).data)


@extend_schema(
    responses={200: RiskFlagSerializer},
    tags=["Risk — Flags"],
    summary="Mark a risk flag as reviewed",
)
class RiskFlagReviewView(APIView):
    permission_classes = _WRITE_PERMS

    def post(self, request, pk):
        flag = _get_flag(request.active_tenant_id, pk)

        if flag.is_reviewed:
            raise ValidationError({"detail": "Flag has already been reviewed."})

        flag.is_reviewed = True
        flag.reviewed_at = timezone.now()
        flag.reviewed_by = request.user
        flag.save(update_fields=["is_reviewed", "reviewed_at", "reviewed_by", "updated_at"])

        audit.record(
            action="risk.flag.reviewed",
            actor=request.user,
            tenant=request.active_membership.tenant,
            target=flag,
            after={"kind": flag.kind, "severity": flag.severity},
        )
        return Response(RiskFlagSerializer(flag).data)


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


@extend_schema_view(
    get=extend_schema(
        responses={200: CaseSerializer(many=True)},
        tags=["Risk — Cases"],
        summary="List cases for the tenant",
    ),
    post=extend_schema(
        request=CaseCreateSerializer,
        responses={201: CaseSerializer},
        tags=["Risk — Cases"],
        summary="Open a new case",
    ),
)
class CaseListCreateView(APIView):
    permission_classes = _BASE_PERMS

    def get(self, request):
        qs = (
            Case.objects
            .annotate(flags_count=django_models.Count("flags"))
            .select_related("batch", "product")
            .order_by("-created_at")
        )
        return Response(CaseSerializer(qs, many=True).data)

    def post(self, request):
        _require_owner_or_admin(request, self)
        serializer = CaseCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        batch = None
        if data.get("batch_id"):
            batch = _get_batch(request.active_tenant_id, data["batch_id"])

        product = None
        if data.get("product_id"):
            product = _get_product(request.active_tenant_id, data["product_id"])

        case = Case(
            tenant=request.active_membership.tenant,
            title=data["title"],
            description=data["description"],
            notes=data["notes"],
            batch=batch,
            product=product,
        )
        case.save()

        audit.record(
            action="risk.case.created",
            actor=request.user,
            tenant=request.active_membership.tenant,
            target=case,
            after={"title": case.title, "status": case.status},
        )

        case.flags_count = 0
        return Response(CaseSerializer(case).data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(
        responses={200: CaseSerializer},
        tags=["Risk — Cases"],
        summary="Retrieve a case",
    ),
    patch=extend_schema(
        request=CaseUpdateSerializer,
        responses={200: CaseSerializer},
        tags=["Risk — Cases"],
        summary="Update a case (status, title, notes)",
    ),
)
class CaseDetailView(APIView):
    permission_classes = _BASE_PERMS

    def get(self, request, pk):
        case = _get_case_annotated(request.active_tenant_id, pk)
        return Response(CaseSerializer(case).data)

    def patch(self, request, pk):
        _require_owner_or_admin(request, self)
        case = _get_case_annotated(request.active_tenant_id, pk)

        serializer = CaseUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        before = {"status": case.status, "title": case.title}
        update_fields = ["updated_at"]

        if "title" in data:
            case.title = data["title"]
            update_fields.append("title")

        if "notes" in data:
            case.notes = data["notes"]
            update_fields.append("notes")

        if "status" in data:
            new_status = data["status"]
            if new_status == Case.Status.CLOSED and case.status != Case.Status.CLOSED:
                case.resolved_at = timezone.now()
                update_fields.append("resolved_at")
            case.status = new_status
            update_fields.append("status")

        case.save(update_fields=update_fields)

        audit.record(
            action="risk.case.updated",
            actor=request.user,
            tenant=request.active_membership.tenant,
            target=case,
            before=before,
            after={"status": case.status, "title": case.title},
        )
        return Response(CaseSerializer(case).data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_owner_or_admin(request, view):
    perm = IsOwnerOrAdmin()
    if not perm.has_permission(request, view):
        raise PermissionDenied(perm.message)


def _get_flag(tenant_id, pk):
    try:
        return RiskFlag.unscoped.get(pk=pk, tenant_id=tenant_id)
    except RiskFlag.DoesNotExist:
        raise NotFound("Risk flag not found.") from None


def _get_case_annotated(tenant_id, pk):
    try:
        return (
            Case.unscoped
            .annotate(flags_count=django_models.Count("flags"))
            .select_related("batch", "product")
            .get(pk=pk, tenant_id=tenant_id)
        )
    except Case.DoesNotExist:
        raise NotFound("Case not found.") from None


def _get_batch(tenant_id, batch_id):
    from apps.catalogue.models import Batch
    try:
        return Batch.unscoped.get(pk=batch_id, tenant_id=tenant_id)
    except Batch.DoesNotExist:
        raise NotFound("Batch not found.") from None


def _get_product(tenant_id, product_id):
    from apps.catalogue.models import Product
    try:
        return Product.unscoped.get(pk=product_id, tenant_id=tenant_id)
    except Product.DoesNotExist:
        raise NotFound("Product not found.") from None


def _get_client_ip(request) -> str | None:
    from django.conf import settings as dj_settings
    proxy_count = getattr(dj_settings, "TRUSTED_PROXY_COUNT", 0)
    if proxy_count > 0:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            ips = [ip.strip() for ip in forwarded.split(",")]
            if len(ips) >= proxy_count:
                return ips[-proxy_count]
    return request.META.get("REMOTE_ADDR")
