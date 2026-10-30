from django.db.models import Count, Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
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
