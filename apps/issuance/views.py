import csv
import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from django.http import StreamingHttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view, inline_serializer
from rest_framework import serializers as s
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit import services as audit
from apps.catalogue.models import Batch, Product
from apps.core.exceptions import ApiError
from apps.core.idempotency import run_idempotent
from apps.core.pagination import StandardPagination
from apps.core.tenancy import get_current_tenant_id
from apps.tenants.permissions import CanIssueCodes, HasActiveTenantMembership, IsOwnerOrAdmin

from .codes import find_code
from .models import Code, IssuanceJob
from .serializers import (
    CodeSerializer,
    DeactivateSerializer,
    IssuanceJobCreateSerializer,
    IssuanceJobSerializer,
)
from .tasks import generate_codes

logger = logging.getLogger(__name__)

_BASE_PERMS = [IsAuthenticated, HasActiveTenantMembership]
_WRITE_PERMS = [IsAuthenticated, HasActiveTenantMembership, IsOwnerOrAdmin]


# ---------------------------------------------------------------------------
# Issuance jobs
# ---------------------------------------------------------------------------

@extend_schema_view(
    get=extend_schema(
        responses={200: IssuanceJobSerializer(many=True)},
        tags=["Issuance — Jobs"],
        summary="List issuance jobs for the tenant",
        parameters=[
            OpenApiParameter("batch_id", OpenApiTypes.UUID, description="Only jobs for this batch"),
            OpenApiParameter(
                "status", OpenApiTypes.STR, many=True,
                enum=["pending", "processing", "partial", "done", "failed"],
                description="Filter by status (repeatable)",
            ),
        ],
    ),
    post=extend_schema(
        request=IssuanceJobCreateSerializer,
        responses={202: IssuanceJobSerializer},
        tags=["Issuance — Jobs"],
        summary="Submit a code-generation job for a batch",
        description=(
            "Starts an asynchronous job that generates the requested number of unique codes. "
            "Requires an `Idempotency-Key` header (8–128 URL-safe chars). "
            "Poll `GET /issuance/jobs/{id}` until `status` is `done` or `failed`."
        ),
        parameters=[
            OpenApiParameter(
                name="Idempotency-Key",
                location=OpenApiParameter.HEADER,
                required=True,
                type=str,
                description="Unique key (8–128 URL-safe chars) to make this request idempotent.",
            )
        ],
    ),
)
class IssuanceJobListCreateView(APIView):
    permission_classes = _BASE_PERMS

    def get(self, request):
        jobs = (
            IssuanceJob.objects.select_related("batch__product", "submitted_by")
            .order_by("-created_at")
        )
        batch_id = request.query_params.get("batch_id")
        if batch_id:
            try:
                jobs = jobs.filter(batch_id=batch_id)
            except (ValueError, DjangoValidationError):
                raise ValidationError({"batch_id": ["Must be a valid UUID."]}) from None
        statuses = request.query_params.getlist("status")
        if statuses:
            unknown = set(statuses) - set(IssuanceJob.Status.values)
            if unknown:
                raise ValidationError({"status": [f"Unknown status: {', '.join(sorted(unknown))}."]})
            jobs = jobs.filter(status__in=statuses)
        return Response(IssuanceJobSerializer(jobs, many=True).data)

    def post(self, request):
        _require_can_issue_codes(request, self)
        scope = f"{request.user.pk}:{request.active_tenant_id}:issuance.create"
        return run_idempotent(request, scope=scope, handler=lambda: self._create(request))

    def _create(self, request):
        serializer = IssuanceJobCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        batch = _get_batch(request.active_tenant_id, serializer.validated_data["batch_id"])
        _assert_issuable(batch)

        tenant = request.active_membership.tenant
        if tenant.is_pharma:
            _check_pharma_batch(batch)

        job = IssuanceJob(
            tenant=request.active_membership.tenant,
            batch=batch,
            quantity_requested=serializer.validated_data["quantity"],
            submitted_by=request.user,
        )
        job.save()

        audit.record(
            action="issuance.job.created",
            actor=request.user,
            tenant=request.active_membership.tenant,
            target=job,
            after={"batch_id": str(batch.pk), "quantity_requested": job.quantity_requested},
        )

        generate_codes.delay(str(job.pk))
        return Response(IssuanceJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


@extend_schema(
    responses={200: IssuanceJobSerializer},
    tags=["Issuance — Jobs"],
    summary="Retrieve job status and reconciliation",
)
class IssuanceJobDetailView(APIView):
    permission_classes = _BASE_PERMS

    def get(self, request, pk):
        job = _get_job(request.active_tenant_id, pk)
        return Response(IssuanceJobSerializer(job).data)


@extend_schema(
    request=None,
    responses={202: IssuanceJobSerializer},
    tags=["Issuance — Jobs"],
    summary="Resume a partial job: generate only the missing codes",
    description=(
        "Allowed when `status` is `partial`, or `failed` with some codes already generated "
        "(otherwise `409 job_not_resumable`). Never duplicates existing codes. Calling it while the "
        "job is already running returns the running job instead of starting a second run. "
        "`Idempotency-Key` is optional."
    ),
)
class IssuanceJobResumeView(APIView):
    permission_classes = [IsAuthenticated, HasActiveTenantMembership, CanIssueCodes]

    def post(self, request, pk):
        if "Idempotency-Key" in request.headers:
            scope = f"{request.user.pk}:{request.active_tenant_id}:issuance.resume:{pk}"
            return run_idempotent(request, scope=scope, handler=lambda: self._resume(request, pk))
        return self._resume(request, pk)

    def _resume(self, request, pk):
        with transaction.atomic():
            try:
                job = (
                    IssuanceJob.unscoped.select_related("batch__product", "submitted_by")
                    .select_for_update(of=("self",))  # lock the job row only (nullable join)
                    .get(pk=pk, tenant_id=request.active_tenant_id)
                )
            except IssuanceJob.DoesNotExist:
                raise NotFound("Issuance job not found.") from None

            if job.status in (IssuanceJob.Status.PROCESSING, IssuanceJob.Status.PENDING):
                # Already running (or about to): report it, never start a second run.
                return Response(IssuanceJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)

            generated = Code.unscoped.filter(issuance_job=job).count()
            resumable = job.status == IssuanceJob.Status.PARTIAL or (
                job.status == IssuanceJob.Status.FAILED and generated > 0
            )
            if not resumable:
                raise ApiError(
                    409, "job_not_resumable",
                    f"A job with status '{job.status}' cannot be resumed.",
                )
            _assert_issuable(job.batch)

            before = {"status": job.status, "generated": generated}
            job.status = IssuanceJob.Status.PROCESSING
            job.quantity_issued = generated
            job.save(update_fields=["status", "quantity_issued", "updated_at"])
            audit.record(
                action="issuance.job.resumed",
                actor=request.user,
                tenant=request.active_membership.tenant,
                target=job,
                before=before,
                after={"status": job.status, "remaining": job.quantity_requested - generated},
            )
            # Enqueue only once the status change is committed, so the worker sees PROCESSING.
            transaction.on_commit(lambda: generate_codes.delay(str(job.pk)))
        return Response(IssuanceJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


@extend_schema(
    responses={200: OpenApiTypes.BINARY},
    tags=["Issuance — Jobs"],
    summary="Download a CSV of all codes for a completed job",
    description=(
        "Streams a CSV file with columns: `code`, `batch_number`, `product_sku`, "
        "`product_name`, `issued_at`. Only available when `status` is `done`."
    ),
)
class IssuanceJobExportView(APIView):
    """Stream a CSV of all codes for a completed issuance job."""

    permission_classes = _BASE_PERMS

    def get(self, request, pk):
        job = _get_job(request.active_tenant_id, pk)
        if job.status != IssuanceJob.Status.DONE:
            raise ValidationError({"detail": "Export is only available for completed jobs."})

        return StreamingHttpResponse(
            _stream_csv(job),
            content_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="codes-{job.pk}.csv"',
            },
        )


def _stream_csv(job):
    header = ["code", "batch_number", "product_sku", "product_name", "issued_at"]
    yield _csv_row(header)
    qs = (
        Code.unscoped.filter(issuance_job=job)
        .select_related("batch", "product")
        .only("code", "batch__batch_number", "product__sku", "product__name", "created_at")
        .iterator(chunk_size=500)
    )
    for c in qs:
        yield _csv_row([
            c.code,
            c.batch.batch_number,
            c.product.sku,
            c.product.name,
            c.created_at.isoformat(),
        ])


def _csv_row(values: list) -> str:
    import io
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(values)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Codes
# ---------------------------------------------------------------------------

@extend_schema(
    parameters=[
        OpenApiParameter("batch_id", OpenApiTypes.UUID, description="Filter by batch UUID"),
        OpenApiParameter("job_id", OpenApiTypes.UUID, description="Filter by issuance job UUID"),
        OpenApiParameter(
            "status",
            OpenApiTypes.STR,
            enum=["active", "deactivated"],
            description="Filter by code status",
        ),
        OpenApiParameter(
            "q", OpenApiTypes.STR,
            description="Find a code by its string (full or partial, case-insensitive; dashes optional)",
        ),
        OpenApiParameter("page", OpenApiTypes.INT, description="Page number"),
        OpenApiParameter("page_size", OpenApiTypes.INT, description="Results per page (max 100)"),
    ],
    responses={200: CodeSerializer(many=True)},
    tags=["Issuance — Codes"],
    summary="List codes for the tenant",
)
class CodeListView(APIView):
    """List codes for the active tenant, with optional filters."""

    permission_classes = _BASE_PERMS

    def get(self, request):
        qs = Code.objects.select_related("batch", "product").order_by("-created_at")

        batch_id = request.query_params.get("batch_id")
        if batch_id:
            qs = qs.filter(batch_id=batch_id)

        code_status = request.query_params.get("status")
        if code_status:
            qs = qs.filter(status=code_status)

        job_id = request.query_params.get("job_id")
        if job_id:
            qs = qs.filter(issuance_job_id=job_id)

        q = request.query_params.get("q", "").strip()
        if q:
            # Codes can contain "-" themselves, so match the text as typed or without dashes.
            qs = qs.filter(Q(code__icontains=q) | Q(code__icontains=q.replace("-", "")))

        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(CodeSerializer(page, many=True).data)


@extend_schema(
    request=DeactivateSerializer,
    responses={200: CodeSerializer},
    tags=["Issuance — Codes"],
    summary="Deactivate a single code by its code string",
)
class CodeDeactivateView(APIView):
    """Deactivate a single code by its code string."""

    permission_classes = _WRITE_PERMS

    def post(self, request, code_str):
        code = find_code(code_str, Code.unscoped.filter(tenant_id=request.active_tenant_id))
        if code is None:
            raise NotFound("Code not found.")

        if code.status == Code.Status.DEACTIVATED:
            raise ValidationError({"detail": "Code is already deactivated."})

        serializer = DeactivateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        code.status = Code.Status.DEACTIVATED
        code.deactivated_at = timezone.now()
        code.deactivated_by = request.user
        code.deactivation_reason = serializer.validated_data["reason"]
        code.save(update_fields=["status", "deactivated_at", "deactivated_by", "deactivation_reason", "updated_at"])

        audit.record(
            action="issuance.code.deactivated",
            actor=request.user,
            tenant=request.active_membership.tenant,
            target=code,
            after={"reason": code.deactivation_reason},
        )
        return Response(CodeSerializer(code).data)


@extend_schema(
    request=DeactivateSerializer,
    responses={
        200: inline_serializer(
            "BatchDeactivateResponse",
            fields={"codes_deactivated": s.IntegerField()},
        )
    },
    tags=["Issuance — Codes"],
    summary="Bulk-deactivate all active codes for a batch",
)
class BatchCodeDeactivateView(APIView):
    """Bulk-deactivate all active codes for a batch."""

    permission_classes = _WRITE_PERMS

    def post(self, request, batch_pk):
        batch = _get_batch(request.active_tenant_id, batch_pk)

        serializer = DeactivateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data["reason"]

        now = timezone.now()
        updated = Code.unscoped.filter(
            tenant_id=request.active_tenant_id,
            batch=batch,
            status=Code.Status.ACTIVE,
        ).update(
            status=Code.Status.DEACTIVATED,
            deactivated_at=now,
            deactivated_by=request.user,
            deactivation_reason=reason,
            updated_at=now,
        )

        audit.record(
            action="issuance.batch.codes_deactivated",
            actor=request.user,
            tenant=request.active_membership.tenant,
            target=batch,
            after={"codes_deactivated": updated, "reason": reason},
        )
        return Response({"codes_deactivated": updated})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_can_issue_codes(request, view):
    perm = CanIssueCodes()
    if not perm.has_permission(request, view):
        raise PermissionDenied(perm.message)


def _get_job(tenant_id, pk):
    try:
        return IssuanceJob.unscoped.select_related("batch__product", "submitted_by").get(
            pk=pk, tenant_id=tenant_id
        )
    except IssuanceJob.DoesNotExist:
        raise NotFound("Issuance job not found.") from None


def _get_batch(tenant_id, batch_id):
    try:
        return Batch.unscoped.select_related("product").get(pk=batch_id, tenant_id=tenant_id)
    except Batch.DoesNotExist:
        raise NotFound("Batch not found.") from None


def _assert_issuable(batch) -> None:
    """Codes can only be issued for an active, unexpired batch of an approved product."""
    if batch.effective_status != Batch.Status.ACTIVE:
        raise ApiError(
            409, "batch_not_active",
            f"Codes cannot be issued for a {batch.effective_status} batch.",
        )
    if batch.product.status != Product.Status.APPROVED:
        raise ApiError(
            409, "product_not_approved",
            "Codes can only be issued once the product has been approved.",
        )


def _check_pharma_batch(batch) -> None:
    """Reject issuance for pharma tenants if the batch is expired or has no valid reg ref."""
    from datetime import date
    from django.db.models import Q as _Q

    today = date.today()
    if batch.expires_at and batch.expires_at < today:
        raise ValidationError(
            {"batch_id": "Pharma tenants cannot issue codes for an expired batch."}
        )

    has_ref = batch.product.regulatory_refs.filter(
        _Q(valid_until__isnull=True) | _Q(valid_until__gte=today)
    ).exists()
    if not has_ref:
        raise ValidationError(
            {"batch_id": "Pharma batches require at least one valid regulatory reference."}
        )
