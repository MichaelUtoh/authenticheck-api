from django.db.models import Count, Q
from django.utils import timezone
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
from drf_spectacular.types import OpenApiTypes
from rest_framework import serializers as s
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit import services as audit
from apps.audit.models import AuditEvent
from apps.catalogue import workflow
from apps.catalogue.models import Category, Industry, Product, RegulatoryRef
from apps.catalogue.serializers import ProductSerializer, RegulatoryRefSerializer
from apps.core.exceptions import ApiError
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
    attach_tenant_extras,
)

_STAFF_PERMS = [IsAuthenticated, IsAdminUser, IsMFAVerified]


@extend_schema_view(
    get=extend_schema(
        parameters=[
            OpenApiParameter(
                "status",
                OpenApiTypes.STR,
                many=True,
                description="Filter by tenant status. Repeatable: ?status=pending&status=info_requested",
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
        statuses = request.query_params.getlist("status")
        if statuses:
            valid = {value for value, _ in Tenant.Status.choices}
            unknown = set(statuses) - valid
            if unknown:
                raise ValidationError({"status": [f"Unknown status: {', '.join(sorted(unknown))}."]})
            qs = qs.filter(status__in=statuses)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        attach_tenant_extras(page)
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

        attach_tenant_extras([tenant])
        return Response(PlatformTenantDetailSerializer(tenant).data)


@extend_schema(
    request=inline_serializer(
        "TenantEvidenceCheckRequest",
        fields={"checked": s.BooleanField(), "note": s.CharField(required=False, allow_blank=True)},
    ),
    responses={200: s.ListField()},
    tags=["Platform — Tenants"],
    summary="Tick or untick one item of a tenant's evidence checklist (staff)",
    description=(
        "`key` is one of cac_certificate, brand_ownership, nafdac_registrations, signatory, "
        "support_contact. Returns the full checklist. Audited."
    ),
)
class PlatformTenantEvidenceView(APIView):
    permission_classes = _STAFF_PERMS

    def put(self, request, pk, key):
        from apps.tenants.models import TenantEvidenceCheck

        from .evidence import KEYS, evidence_checklist

        if key not in KEYS:
            raise NotFound("Unknown checklist item.")
        try:
            tenant = Tenant.objects.get(pk=pk)
        except Tenant.DoesNotExist:
            raise NotFound("Tenant not found.") from None
        if "checked" not in request.data:
            raise ValidationError({"checked": ["This field is required."]})
        checked = request.data["checked"]
        if isinstance(checked, str):
            checked = checked.lower() in ("true", "1")
        note = str(request.data.get("note", "")).strip()

        if checked:
            TenantEvidenceCheck.objects.update_or_create(
                tenant=tenant, key=key,
                defaults={"checked_by": request.user, "checked_at": timezone.now(), "note": note},
            )
        else:
            TenantEvidenceCheck.objects.filter(tenant=tenant, key=key).delete()
        audit.record(
            action="tenant.evidence_checked" if checked else "tenant.evidence_unchecked",
            actor=request.user, tenant=tenant, target=tenant,
            metadata={"item": key}, reason=note,
        )
        return Response(evidence_checklist(tenant))


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


@extend_schema(
    request=s.Serializer,
    responses={200: RegulatoryRefSerializer},
    tags=["Platform — Catalogue"],
    summary="Mark a regulatory reference as verified (staff)",
)
class RegulatoryRefVerifyView(APIView):
    permission_classes = _STAFF_PERMS

    def post(self, request, product_id, ref_id):
        try:
            product = Product.unscoped.get(pk=product_id)
        except Product.DoesNotExist:
            raise NotFound("Product not found.")
        try:
            ref = RegulatoryRef.unscoped.get(pk=ref_id, product=product)
        except RegulatoryRef.DoesNotExist:
            raise NotFound("Regulatory reference not found.")

        method = request.data.get("method", "")
        note = request.data.get("note", "")
        if not method:
            raise ValidationError({"method": "This field is required."})

        now = timezone.now()
        ref.verification_status = RegulatoryRef.VerificationStatus.VERIFIED
        ref.verified_by = request.user
        ref.verified_at = now
        ref.verification_method = method
        ref.verification_note = note
        ref.save(update_fields=[
            "verification_status", "verified_by", "verified_at",
            "verification_method", "verification_note", "updated_at",
        ])

        audit.record(
            action="catalogue.regulatory_ref.verified",
            actor=request.user,
            tenant=None,
            target=ref,
            after={
                "verification_status": ref.verification_status,
                "method": method,
                "verified_at": now.isoformat(),
            },
        )
        return Response(RegulatoryRefSerializer(ref).data)


# ---------------------------------------------------------------------------
# Product review (AuthentiCheck platform reviewers approve products)
# ---------------------------------------------------------------------------

def _get_any_product(product_id):
    try:
        return Product.unscoped.select_related("brand", "tenant").get(pk=product_id)
    except Product.DoesNotExist:
        raise NotFound("Product not found.") from None


def _required_text(request, field):
    value = str(request.data.get(field, "")).strip()
    if not value:
        raise ValidationError({field: ["This field is required."]})
    return value


@extend_schema(
    parameters=[
        OpenApiParameter("status", OpenApiTypes.STR, description="Default: in_review"),
        OpenApiParameter("tenant_id", OpenApiTypes.UUID),
    ],
    responses={200: ProductSerializer(many=True)},
    tags=["Platform — Catalogue"],
    summary="Product review queue (staff)",
)
class PlatformProductListView(APIView):
    permission_classes = _STAFF_PERMS

    def get(self, request):
        qs = Product.unscoped.select_related("brand", "tenant").order_by("submitted_at")
        qs = qs.filter(status=request.query_params.get("status", Product.Status.IN_REVIEW))
        if request.query_params.get("tenant_id"):
            qs = qs.filter(tenant_id=request.query_params["tenant_id"])
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        rows = ProductSerializer(page, many=True).data
        for row, product in zip(rows, page, strict=True):
            row["tenant_id"] = str(product.tenant_id)
            row["tenant_name"] = str(product.tenant)
        return paginator.get_paginated_response(rows)


@extend_schema(
    request=s.Serializer,
    responses={200: ProductSerializer},
    tags=["Platform — Catalogue"],
    summary="Approve a product in review (staff)",
    description=(
        "Applies any pending revision as a new approved version. Returns 409 `gtin_conflict` if the "
        "GTIN is registered against another product, unless `gtin_conflict_resolution` is supplied. "
        "Approval does not unlock pharmaceuticals, batch approval or code issuance."
    ),
)
class PlatformProductApproveView(APIView):
    permission_classes = _STAFF_PERMS

    def post(self, request, product_id):
        product = _get_any_product(product_id)
        resolution = str(request.data.get("gtin_conflict_resolution", "")).strip()
        workflow.approve(product, request.user, resolution)
        return Response(ProductSerializer(product).data)


@extend_schema(
    request=s.Serializer,
    responses={200: ProductSerializer},
    tags=["Platform — Catalogue"],
    summary="Send a product back with requested changes (staff)",
)
class PlatformProductRequestChangesView(APIView):
    permission_classes = _STAFF_PERMS

    def post(self, request, product_id):
        product = _get_any_product(product_id)
        workflow.decline(product, request.user, _required_text(request, "review_note"), final=False)
        return Response(ProductSerializer(product).data)


@extend_schema(
    request=s.Serializer,
    responses={200: ProductSerializer},
    tags=["Platform — Catalogue"],
    summary="Reject a product in review (staff)",
)
class PlatformProductRejectView(APIView):
    permission_classes = _STAFF_PERMS

    def post(self, request, product_id):
        product = _get_any_product(product_id)
        workflow.decline(product, request.user, _required_text(request, "review_note"), final=True)
        return Response(ProductSerializer(product).data)


class _SuspensionView(APIView):
    permission_classes = _STAFF_PERMS
    suspend = True

    def post(self, request, product_id):
        product = _get_any_product(product_id)
        reason = _required_text(request, "reason")
        product.public_suspended = self.suspend
        product.suspended_reason = reason if self.suspend else ""
        product.save(update_fields=["public_suspended", "suspended_reason", "updated_at"])
        audit.record(
            action="catalogue.product.suspended" if self.suspend else "catalogue.product.unsuspended",
            actor=request.user,
            tenant=product.tenant,
            target=product,
            after={"public_suspended": product.public_suspended},
            reason=reason,
        )
        return Response(ProductSerializer(product).data)


@extend_schema(
    request=s.Serializer, responses={200: ProductSerializer}, tags=["Platform — Catalogue"],
    summary="Suspend public verification of a product (staff)",
)
class PlatformProductSuspendView(_SuspensionView):
    suspend = True


@extend_schema(
    request=s.Serializer, responses={200: ProductSerializer}, tags=["Platform — Catalogue"],
    summary="Restore public verification of a suspended product (staff)",
)
class PlatformProductUnsuspendView(_SuspensionView):
    suspend = False


@extend_schema(
    request=s.Serializer,
    responses={200: ProductSerializer},
    tags=["Platform — Catalogue"],
    summary="Controlled correction of a locked SKU or GTIN (staff)",
    description="Re-runs uniqueness checks, requires a reason and audits old and new values.",
)
class PlatformProductCorrectView(APIView):
    permission_classes = _STAFF_PERMS

    def post(self, request, product_id):
        from apps.catalogue.serializers import ProductWriteSerializer

        product = _get_any_product(product_id)
        reason = _required_text(request, "reason")
        changes = {k: request.data[k] for k in workflow.LOCKED_AFTER_APPROVAL if k in request.data}
        if not changes:
            raise ValidationError({"detail": ["Provide sku and/or gtin."]})
        validated = ProductWriteSerializer(
            product, data=changes, partial=True
        )
        validated.is_valid(raise_exception=True)
        data = validated.validated_data

        before = {k: getattr(product, k) for k in data}
        if "sku" in data:
            workflow.check_sku_conflict(product.tenant_id, data["sku"], exclude_pk=product.pk)
        for attr, val in data.items():
            setattr(product, attr, val)
        if workflow.gtin_conflicts(product).exists():
            # Uniqueness is global; the correction must not create a clash. Staff resolve first.
            raise ApiError(409, "gtin_conflict", "This GTIN is registered against another product.")
        product.correction_request = None
        workflow.save_product(product)
        audit.record(
            action="catalogue.product.corrected",
            actor=request.user,
            tenant=product.tenant,
            target=product,
            before=before,
            after=dict(data),
            reason=reason,
        )
        return Response(ProductSerializer(product).data)


# ---------------------------------------------------------------------------
# Taxonomy (platform admins only)
# ---------------------------------------------------------------------------

class IndustrySerializer(s.ModelSerializer):
    class Meta:
        model = Industry
        fields = ["id", "key", "label", "description", "available", "required_fields",
                  "is_active", "sort_order"]
        read_only_fields = ["id"]


class CategorySerializer(s.ModelSerializer):
    industry = s.SlugRelatedField(slug_field="key", queryset=Industry.objects.all())
    parent = s.SlugRelatedField(
        slug_field="key", queryset=Category.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = Category
        fields = ["id", "industry", "key", "label", "parent", "allowed_units",
                  "required_fields", "is_active"]
        read_only_fields = ["id"]


class _TaxonomyListCreateView(APIView):
    permission_classes = _STAFF_PERMS
    serializer_class = None
    model = None
    audit_prefix = ""

    def get(self, request):
        return Response(self.serializer_class(self.model.objects.all(), many=True).data)

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        obj = serializer.save()
        audit.record(
            action=f"{self.audit_prefix}.created", actor=request.user, tenant=None, target=obj,
            after=serializer.data,
        )
        return Response(serializer.data, status=201)


class _TaxonomyDetailView(APIView):
    """Categories and industries are deactivated (is_active=false), never deleted, so existing
    products keep their historical references."""

    permission_classes = _STAFF_PERMS
    serializer_class = None
    model = None
    audit_prefix = ""

    def patch(self, request, pk):
        try:
            obj = self.model.objects.get(pk=pk)
        except self.model.DoesNotExist:
            raise NotFound("Not found.") from None
        before = self.serializer_class(obj).data
        serializer = self.serializer_class(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        audit.record(
            action=f"{self.audit_prefix}.updated", actor=request.user, tenant=None, target=obj,
            before=dict(before), after=dict(serializer.data),
        )
        return Response(serializer.data)


@extend_schema(responses={200: IndustrySerializer(many=True)}, request=IndustrySerializer,
               tags=["Platform — Taxonomy"], summary="List / create industries (staff)")
class PlatformIndustryListView(_TaxonomyListCreateView):
    serializer_class, model, audit_prefix = IndustrySerializer, Industry, "catalogue.industry"


@extend_schema(request=IndustrySerializer, responses={200: IndustrySerializer},
               tags=["Platform — Taxonomy"], summary="Update an industry (staff)")
class PlatformIndustryDetailView(_TaxonomyDetailView):
    serializer_class, model, audit_prefix = IndustrySerializer, Industry, "catalogue.industry"


@extend_schema(responses={200: CategorySerializer(many=True)}, request=CategorySerializer,
               tags=["Platform — Taxonomy"], summary="List / create categories (staff)")
class PlatformCategoryListView(_TaxonomyListCreateView):
    serializer_class, model, audit_prefix = CategorySerializer, Category, "catalogue.category"


@extend_schema(request=CategorySerializer, responses={200: CategorySerializer},
               tags=["Platform — Taxonomy"], summary="Update or deactivate a category (staff)")
class PlatformCategoryDetailView(_TaxonomyDetailView):
    serializer_class, model, audit_prefix = CategorySerializer, Category, "catalogue.category"


# ---------------------------------------------------------------------------
# Operations at a glance
# ---------------------------------------------------------------------------


def _iso_date(value: str):
    """Parse an optional ISO 8601 date or datetime setting; None when unset or malformed."""
    from datetime import datetime, time as dtime

    from django.utils.dateparse import parse_date, parse_datetime

    if not value:
        return None
    try:
        parsed = parse_datetime(value)
        if parsed is None:
            day = parse_date(value)
            parsed = datetime.combine(day, dtime.min) if day else None
    except ValueError:
        return None
    if parsed is None:
        return None
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed


@extend_schema(
    responses={200: s.DictField()},
    tags=["Platform — Operations"],
    summary="Operations at a glance (staff)",
    description=(
        "Verification latency and error rate over the last hour (measured by middleware; `p95_ms` is "
        "the upper bound of a histogram bucket), failed and queued issuance jobs, scans in the last "
        "24 hours, and the TLS certificate expiry and last restore test, which are set by the "
        "deployment (`TLS_CERT_EXPIRES_AT`, `LAST_RESTORE_TEST_AT`) and are null when not set."
    ),
)
class PlatformOpsView(APIView):
    permission_classes = _STAFF_PERMS

    def get(self, request):
        from datetime import timedelta

        from django.conf import settings

        from apps.core.metrics import verify_metrics
        from apps.issuance.models import IssuanceJob
        from apps.verification.models import ScanEvent

        now = timezone.now()
        jobs = IssuanceJob.unscoped
        tls = _iso_date(settings.TLS_CERT_EXPIRES_AT)
        restore = _iso_date(settings.LAST_RESTORE_TEST_AT)
        return Response(
            {
                "generated_at": now,
                "verification": {
                    **verify_metrics(60),
                    "scans_24h": ScanEvent.objects.filter(
                        occurred_at__gte=now - timedelta(hours=24)
                    ).count(),
                },
                "issuance": {
                    "queue_backlog": jobs.filter(
                        status__in=[IssuanceJob.Status.PENDING, IssuanceJob.Status.PROCESSING]
                    ).count(),
                    "failed_jobs": jobs.filter(
                        status__in=[IssuanceJob.Status.FAILED, IssuanceJob.Status.PARTIAL]
                    ).count(),
                },
                "tls_cert_expires_at": tls,
                "tls_days_remaining": (tls - now).days if tls else None,
                "last_restore_test_at": restore,
                "days_since_restore_test": (now - restore).days if restore else None,
            }
        )


# ---------------------------------------------------------------------------
# Staff authenticator reset
# ---------------------------------------------------------------------------


@extend_schema(
    responses={200: s.ListField()},
    tags=["Platform — Staff"],
    summary="List platform staff and whether each has an authenticator (staff)",
)
class PlatformStaffListView(APIView):
    permission_classes = _STAFF_PERMS

    def get(self, request):
        from django.contrib.auth import get_user_model
        from django_otp.plugins.otp_totp.models import TOTPDevice

        User = get_user_model()
        enrolled = set(
            TOTPDevice.objects.filter(confirmed=True).values_list("user_id", flat=True)
        )
        rows = [
            {
                "id": str(u.pk),
                "email": u.email,
                "full_name": u.full_name,
                "is_active": u.is_active,
                "mfa_enabled": u.pk in enrolled,
                "is_you": u.pk == request.user.pk,
            }
            for u in User.objects.filter(is_staff=True).order_by("email")
        ]
        return Response(rows)


@extend_schema(
    request=inline_serializer("ResetMfaRequest", fields={"reason": s.CharField()}),
    responses={200: s.DictField()},
    tags=["Platform — Staff"],
    summary="Reset another staff member's authenticator (staff)",
    description=(
        "For a colleague who lost their phone. Removes their authenticators and signs them out "
        "everywhere; at next login they enrol a new one. `reason` is required and audited. You "
        "cannot reset your own authenticator. If no other staff user is available, an operator "
        "runs `python manage.py reset_staff_mfa <email>`."
    ),
)
class PlatformStaffResetMFAView(APIView):
    permission_classes = _STAFF_PERMS

    def post(self, request, user_id):
        from django.contrib.auth import get_user_model
        from django_otp.plugins.otp_totp.models import TOTPDevice

        from apps.identity.models import AuthSession
        from apps.identity.services import session_service

        reason = _required_text(request, "reason")
        User = get_user_model()
        try:
            target = User.objects.get(pk=user_id, is_staff=True)
        except User.DoesNotExist:
            raise NotFound("Staff user not found.") from None
        if target.pk == request.user.pk:
            raise ApiError(
                403, "cannot_reset_own_mfa",
                "Ask another staff member to reset your authenticator.",
            )

        removed = TOTPDevice.objects.filter(user=target).delete()[0]
        revoked = session_service.revoke_all_sessions(
            target, AuthSession.RevocationReason.MFA_RESET
        )
        audit.record(
            action="auth.mfa.reset",
            actor=request.user,
            target=target,
            reason=reason,
            metadata={"via": "api", "devices_removed": removed, "sessions_revoked": revoked},
        )
        return Response(
            {"id": str(target.pk), "email": target.email, "mfa_enabled": False,
             "devices_removed": removed, "sessions_revoked": revoked}
        )
