import csv
import io
import logging

from django.db import IntegrityError
from django.utils import timezone
from django.utils.text import slugify
from drf_spectacular.utils import extend_schema, extend_schema_view, inline_serializer, OpenApiParameter
from drf_spectacular.types import OpenApiTypes
from rest_framework import serializers as s
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit import services as audit
from apps.core.tenancy import tenant_context
from apps.issuance.models import Code
from apps.tenants.permissions import HasActiveTenantMembership, IsOwnerOrAdmin

from .models import Batch, Brand, BulkImportJob, Product, RegulatoryRef
from .serializers import (
    BatchSerializer,
    BatchWriteSerializer,
    BrandSerializer,
    BrandWriteSerializer,
    BulkImportJobSerializer,
    ProductSerializer,
    ProductWriteSerializer,
    RegulatoryRefSerializer,
    RegulatoryRefWriteSerializer,
)
from .tasks import process_bulk_import

logger = logging.getLogger(__name__)

_BASE_PERMS = [IsAuthenticated, HasActiveTenantMembership]
_WRITE_PERMS = [IsAuthenticated, HasActiveTenantMembership, IsOwnerOrAdmin]


# ---------------------------------------------------------------------------
# Brands
# ---------------------------------------------------------------------------

@extend_schema_view(
    get=extend_schema(
        responses={200: BrandSerializer(many=True)},
        tags=["Catalogue — Brands"],
        summary="List active brands for the tenant",
    ),
    post=extend_schema(
        request=BrandWriteSerializer,
        responses={201: BrandSerializer},
        tags=["Catalogue — Brands"],
        summary="Create a brand",
    ),
)
class BrandListCreateView(APIView):
    permission_classes = _BASE_PERMS

    def get(self, request):
        brands = Brand.objects.filter(is_active=True).order_by("name")
        return Response(BrandSerializer(brands, many=True).data)

    def post(self, request):
        self._check_write(request)
        serializer = BrandWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        brand = Brand(tenant=request.active_membership.tenant, **serializer.validated_data)
        try:
            brand.save()
        except IntegrityError:
            raise ValidationError({"slug": "A brand with this slug already exists."})
        audit.record(
            action="catalogue.brand.created",
            actor=request.user,
            tenant=request.active_membership.tenant,
            target=brand,
            after={"name": brand.name, "slug": brand.slug},
        )
        return Response(BrandSerializer(brand).data, status=status.HTTP_201_CREATED)

    def _check_write(self, request):
        perm = IsOwnerOrAdmin()
        if not perm.has_permission(request, self):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied(perm.message)


@extend_schema_view(
    get=extend_schema(
        responses={200: BrandSerializer},
        tags=["Catalogue — Brands"],
        summary="Retrieve a brand",
    ),
    patch=extend_schema(
        request=BrandWriteSerializer,
        responses={200: BrandSerializer},
        tags=["Catalogue — Brands"],
        summary="Partially update a brand",
    ),
    delete=extend_schema(
        responses={204: None},
        tags=["Catalogue — Brands"],
        summary="Soft-deactivate a brand",
    ),
)
class BrandDetailView(APIView):
    permission_classes = _BASE_PERMS

    def _get_brand(self, request, pk):
        try:
            return Brand.objects.get(pk=pk)
        except Brand.DoesNotExist:
            raise NotFound("Brand not found.") from None

    def get(self, request, pk):
        return Response(BrandSerializer(self._get_brand(request, pk)).data)

    def patch(self, request, pk):
        IsOwnerOrAdmin().has_permission(request, self) or self._deny()
        brand = self._get_brand(request, pk)
        serializer = BrandWriteSerializer(brand, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        before = {"name": brand.name, "slug": brand.slug, "is_active": brand.is_active}
        serializer.save()
        audit.record(
            action="catalogue.brand.updated",
            actor=request.user,
            tenant=request.active_membership.tenant,
            target=brand,
            before=before,
            after={"name": brand.name, "slug": brand.slug, "is_active": brand.is_active},
        )
        return Response(BrandSerializer(brand).data)

    def delete(self, request, pk):
        IsOwnerOrAdmin().has_permission(request, self) or self._deny()
        brand = self._get_brand(request, pk)
        brand.is_active = False
        brand.save(update_fields=["is_active", "updated_at"])
        audit.record(
            action="catalogue.brand.deactivated",
            actor=request.user,
            tenant=request.active_membership.tenant,
            target=brand,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _deny(self):
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied(IsOwnerOrAdmin.message)


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

@extend_schema_view(
    get=extend_schema(
        responses={200: ProductSerializer(many=True)},
        tags=["Catalogue — Products"],
        summary="List all products for the tenant",
    ),
    post=extend_schema(
        request=ProductWriteSerializer,
        responses={201: ProductSerializer},
        tags=["Catalogue — Products"],
        summary="Create a product",
    ),
)
class ProductListCreateView(APIView):
    permission_classes = _BASE_PERMS

    def get(self, request):
        products = Product.objects.select_related("brand").order_by("sku")
        return Response(ProductSerializer(products, many=True).data)

    def post(self, request):
        _require_owner_or_admin(request, self)
        serializer = ProductWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        brand_id = serializer.validated_data.pop("brand_id")
        brand = _get_brand(request.active_tenant_id, brand_id)

        product = Product(
            tenant=request.active_membership.tenant,
            brand=brand,
            **serializer.validated_data,
        )
        try:
            product.save()
        except IntegrityError:
            raise ValidationError({"sku": "A product with this SKU already exists."})
        audit.record(
            action="catalogue.product.created",
            actor=request.user,
            tenant=request.active_membership.tenant,
            target=product,
            after={"sku": product.sku, "name": product.name},
        )
        return Response(ProductSerializer(product).data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(
        responses={200: ProductSerializer},
        tags=["Catalogue — Products"],
        summary="Retrieve a product",
    ),
    patch=extend_schema(
        request=ProductWriteSerializer,
        responses={200: ProductSerializer},
        tags=["Catalogue — Products"],
        summary="Partially update a product",
    ),
)
class ProductDetailView(APIView):
    permission_classes = _BASE_PERMS

    def get(self, request, pk):
        return Response(ProductSerializer(_get_product(request.active_tenant_id, pk)).data)

    def patch(self, request, pk):
        _require_owner_or_admin(request, self)
        product = _get_product(request.active_tenant_id, pk)
        serializer = ProductWriteSerializer(product, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        if "brand_id" in serializer.validated_data:
            brand_id = serializer.validated_data.pop("brand_id")
            serializer.validated_data["brand"] = _get_brand(request.active_tenant_id, brand_id)

        before = {"sku": product.sku, "name": product.name, "status": product.status}
        for attr, val in serializer.validated_data.items():
            setattr(product, attr, val)
        product.save()
        audit.record(
            action="catalogue.product.updated",
            actor=request.user,
            tenant=request.active_membership.tenant,
            target=product,
            before=before,
            after={"sku": product.sku, "name": product.name, "status": product.status},
        )
        return Response(ProductSerializer(product).data)


# ---------------------------------------------------------------------------
# Regulatory refs
# ---------------------------------------------------------------------------

@extend_schema_view(
    get=extend_schema(
        responses={200: RegulatoryRefSerializer(many=True)},
        tags=["Catalogue — Regulatory Refs"],
        summary="List regulatory references for a product",
    ),
    post=extend_schema(
        request=RegulatoryRefWriteSerializer,
        responses={201: RegulatoryRefSerializer},
        tags=["Catalogue — Regulatory Refs"],
        summary="Create a regulatory reference",
    ),
)
class RegulatoryRefListCreateView(APIView):
    permission_classes = _BASE_PERMS

    def get(self, request, product_pk):
        product = _get_product(request.active_tenant_id, product_pk)
        refs = RegulatoryRef.objects.filter(product=product).order_by("country_code")
        return Response(RegulatoryRefSerializer(refs, many=True).data)

    def post(self, request, product_pk):
        _require_owner_or_admin(request, self)
        product = _get_product(request.active_tenant_id, product_pk)
        serializer = RegulatoryRefWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.validated_data.pop("product_id", None)

        ref = RegulatoryRef(
            tenant=request.active_membership.tenant,
            product=product,
            **serializer.validated_data,
        )
        ref.save()
        audit.record(
            action="catalogue.regulatory_ref.created",
            actor=request.user,
            tenant=request.active_membership.tenant,
            target=ref,
            after={"reference_number": ref.reference_number, "country_code": ref.country_code},
        )
        return Response(RegulatoryRefSerializer(ref).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Batches
# ---------------------------------------------------------------------------

@extend_schema_view(
    get=extend_schema(
        responses={200: BatchSerializer(many=True)},
        tags=["Catalogue — Batches"],
        summary="List batches for a product",
    ),
    post=extend_schema(
        request=BatchWriteSerializer,
        responses={201: BatchSerializer},
        tags=["Catalogue — Batches"],
        summary="Create a production batch",
    ),
)
class BatchListCreateView(APIView):
    permission_classes = _BASE_PERMS

    def get(self, request, product_pk):
        product = _get_product(request.active_tenant_id, product_pk)
        batches = Batch.objects.filter(product=product).order_by("-created_at")
        return Response(BatchSerializer(batches, many=True).data)

    def post(self, request, product_pk):
        _require_owner_or_admin(request, self)
        product = _get_product(request.active_tenant_id, product_pk)
        serializer = BatchWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.validated_data.pop("product_id", None)

        batch = Batch(
            tenant=request.active_membership.tenant,
            product=product,
            **serializer.validated_data,
        )
        batch.save()
        audit.record(
            action="catalogue.batch.created",
            actor=request.user,
            tenant=request.active_membership.tenant,
            target=batch,
            after={"batch_number": batch.batch_number, "quantity": batch.quantity},
        )
        return Response(BatchSerializer(batch).data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(
        responses={200: BatchSerializer},
        tags=["Catalogue — Batches"],
        summary="Retrieve a batch",
    ),
    patch=extend_schema(
        request=BatchWriteSerializer,
        responses={200: BatchSerializer},
        tags=["Catalogue — Batches"],
        summary="Partially update a batch",
    ),
)
class BatchDetailView(APIView):
    permission_classes = _BASE_PERMS

    def get(self, request, product_pk, pk):
        batch = _get_batch(request.active_tenant_id, product_pk, pk)
        return Response(BatchSerializer(batch).data)

    def patch(self, request, product_pk, pk):
        _require_owner_or_admin(request, self)
        batch = _get_batch(request.active_tenant_id, product_pk, pk)
        serializer = BatchWriteSerializer(batch, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.validated_data.pop("product_id", None)
        before = {"batch_number": batch.batch_number, "status": batch.status, "quantity": batch.quantity}
        for attr, val in serializer.validated_data.items():
            setattr(batch, attr, val)
        batch.save()
        audit.record(
            action="catalogue.batch.updated",
            actor=request.user,
            tenant=request.active_membership.tenant,
            target=batch,
            before=before,
            after={"batch_number": batch.batch_number, "status": batch.status, "quantity": batch.quantity},
        )
        return Response(BatchSerializer(batch).data)


# ---------------------------------------------------------------------------
# Bulk import
# ---------------------------------------------------------------------------

@extend_schema(
    request=inline_serializer(
        "BulkImportRequest",
        fields={
            "import_type": s.ChoiceField(choices=BulkImportJob.ImportType.choices),
            "file": s.FileField(),
        },
    ),
    responses={202: BulkImportJobSerializer},
    tags=["Catalogue — Import"],
    summary="Upload a CSV to asynchronously import brands, products, or batches",
)
class BulkImportView(APIView):
    permission_classes = _WRITE_PERMS
    parser_classes = [MultiPartParser]

    def post(self, request):
        import_type = request.data.get("import_type", "")
        if import_type not in BulkImportJob.ImportType.values:
            raise ValidationError({"import_type": f"Must be one of: {BulkImportJob.ImportType.values}"})

        uploaded = request.FILES.get("file")
        if not uploaded:
            raise ValidationError({"file": "A CSV file is required."})
        if not uploaded.name.lower().endswith(".csv"):
            raise ValidationError({"file": "Only CSV files are supported."})

        content = uploaded.read().decode("utf-8-sig")

        with tenant_context(request.active_tenant_id):
            job = BulkImportJob.objects.create(
                tenant=request.active_membership.tenant,
                import_type=import_type,
                file_name=uploaded.name,
                submitted_by=request.user,
            )

        process_bulk_import.delay(str(job.pk), content)

        audit.record(
            action="catalogue.bulk_import.submitted",
            actor=request.user,
            tenant=request.active_membership.tenant,
            target=job,
            after={"import_type": import_type, "file_name": uploaded.name},
        )
        return Response(BulkImportJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


@extend_schema(
    responses={200: BulkImportJobSerializer},
    tags=["Catalogue — Import"],
    summary="Poll the status of a bulk import job",
)
class BulkImportJobDetailView(APIView):
    permission_classes = _BASE_PERMS

    def get(self, request, pk):
        try:
            job = BulkImportJob.objects.get(pk=pk)
        except BulkImportJob.DoesNotExist:
            raise NotFound("Import job not found.")
        return Response(BulkImportJobSerializer(job).data)


# ---------------------------------------------------------------------------
# Batch recall
# ---------------------------------------------------------------------------


@extend_schema(
    request=inline_serializer("BatchRecallRequest", fields={"reason": s.CharField(required=False)}),
    responses={
        200: inline_serializer(
            "BatchRecallResponse",
            fields={"batch": BatchSerializer(), "codes_deactivated": s.IntegerField()},
        )
    },
    tags=["Catalogue — Batches"],
    summary="Recall a batch and deactivate all its active codes",
    description=(
        "Sets the batch status to `recalled` and bulk-deactivates every active code "
        "that belongs to it. Idempotent on code deactivation; raises 400 if the batch "
        "is already recalled."
    ),
)
class BatchRecallView(APIView):
    permission_classes = [IsAuthenticated, HasActiveTenantMembership, IsOwnerOrAdmin]

    def post(self, request, product_pk, pk):
        batch = _get_batch(request.active_tenant_id, product_pk, pk)

        if batch.status == Batch.Status.RECALLED:
            raise ValidationError({"detail": "Batch is already recalled."})

        reason = (request.data.get("reason") or "")[:500]
        now = timezone.now()

        batch.status = Batch.Status.RECALLED
        batch.save(update_fields=["status", "updated_at"])

        codes_deactivated = Code.unscoped.filter(
            tenant_id=request.active_tenant_id,
            batch=batch,
            status=Code.Status.ACTIVE,
        ).update(
            status=Code.Status.DEACTIVATED,
            deactivated_at=now,
            deactivated_by=request.user,
            deactivation_reason=reason or "Batch recalled.",
            updated_at=now,
        )

        audit.record(
            action="catalogue.batch.recalled",
            actor=request.user,
            tenant=request.active_membership.tenant,
            target=batch,
            after={"reason": reason, "codes_deactivated": codes_deactivated},
        )
        return Response({"batch": BatchSerializer(batch).data, "codes_deactivated": codes_deactivated})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_owner_or_admin(request, view):
    perm = IsOwnerOrAdmin()
    if not perm.has_permission(request, view):
        raise PermissionDenied(perm.message)


def _get_brand(tenant_id, brand_id):
    try:
        return Brand.unscoped.get(pk=brand_id, tenant_id=tenant_id)
    except Brand.DoesNotExist:
        raise NotFound("Brand not found.") from None


def _get_product(tenant_id, product_id):
    try:
        return Product.unscoped.get(pk=product_id, tenant_id=tenant_id)
    except Product.DoesNotExist:
        raise NotFound("Product not found.") from None


def _get_batch(tenant_id, product_id, batch_id):
    try:
        return Batch.unscoped.get(pk=batch_id, tenant_id=tenant_id, product_id=product_id)
    except Batch.DoesNotExist:
        raise NotFound("Batch not found.") from None
