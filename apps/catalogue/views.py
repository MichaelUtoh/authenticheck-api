import csv
import io
import logging

from django.db import IntegrityError
from django.utils.text import slugify
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit import services as audit
from apps.core.tenancy import tenant_context
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


class BulkImportJobDetailView(APIView):
    permission_classes = _BASE_PERMS

    def get(self, request, pk):
        try:
            job = BulkImportJob.objects.get(pk=pk)
        except BulkImportJob.DoesNotExist:
            raise NotFound("Import job not found.")
        return Response(BulkImportJobSerializer(job).data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_owner_or_admin(request, view):
    perm = IsOwnerOrAdmin()
    if not perm.has_permission(request, view):
        from rest_framework.exceptions import PermissionDenied
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
