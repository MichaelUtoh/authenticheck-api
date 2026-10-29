import csv
import io
import logging
import uuid as _uuid
from io import BytesIO

from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import IntegrityError
from django.db.models import Count, Q
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
from apps.audit.models import AuditEvent
from apps.core.pagination import StandardPagination
from apps.core.tenancy import tenant_context
from apps.issuance.models import Code
from apps.tenants.permissions import HasActiveTenantMembership, IsOwnerOrAdmin

from .models import Batch, Brand, BulkImportJob, Product, ProductImage, RegulatoryRef
from .serializers import (
    BatchSerializer,
    BatchWriteSerializer,
    BrandSerializer,
    BrandWriteSerializer,
    BulkImportJobSerializer,
    ProductImageSerializer,
    ProductSerializer,
    ProductWriteSerializer,
    RegulatoryRefSerializer,
    RegulatoryRefWriteSerializer,
)
from .tasks import process_bulk_import

logger = logging.getLogger(__name__)

_BASE_PERMS = [IsAuthenticated, HasActiveTenantMembership]
_WRITE_PERMS = [IsAuthenticated, HasActiveTenantMembership, IsOwnerOrAdmin]

# Magic bytes for image type detection
_PNG_MAGIC = b"\x89PNG"
_JPEG_MAGIC = b"\xff\xd8\xff"
_MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB


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
        raise PermissionDenied(IsOwnerOrAdmin.message)


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

@extend_schema_view(
    get=extend_schema(
        responses={200: ProductSerializer(many=True)},
        tags=["Catalogue — Products"],
        summary="List all products for the tenant",
        parameters=[
            OpenApiParameter("page", OpenApiTypes.INT, description="Page number"),
            OpenApiParameter("page_size", OpenApiTypes.INT, description="Results per page (max 100, default 25)"),
            OpenApiParameter("q", OpenApiTypes.STR, description="Search name, SKU, brand name"),
            OpenApiParameter("status", OpenApiTypes.STR, description="Filter by status (repeatable)"),
            OpenApiParameter("brand_id", OpenApiTypes.UUID, description="Filter by brand"),
            OpenApiParameter("industry", OpenApiTypes.STR, description="Filter by industry"),
            OpenApiParameter("is_active", OpenApiTypes.BOOL, description="Include archived (default: true only)"),
            OpenApiParameter("ordering", OpenApiTypes.STR, description="-updated_at | name | -name | sku | -sku | -created_at"),
        ],
    ),
    post=extend_schema(
        request=ProductWriteSerializer,
        responses={201: ProductSerializer},
        tags=["Catalogue — Products"],
        summary="Create a product (starts as draft)",
    ),
)
class ProductListCreateView(APIView):
    permission_classes = _BASE_PERMS

    def get(self, request):
        qs = Product.objects.select_related("brand").order_by("-updated_at")

        q = request.query_params.get("q")
        if q:
            qs = qs.filter(
                Q(name__icontains=q) | Q(sku__icontains=q) | Q(brand__name__icontains=q)
            )

        status_filter = request.query_params.getlist("status")
        if status_filter:
            qs = qs.filter(status__in=status_filter)

        brand_id = request.query_params.get("brand_id")
        if brand_id:
            qs = qs.filter(brand_id=brand_id)

        industry = request.query_params.get("industry")
        if industry:
            qs = qs.filter(industry=industry)

        is_active_param = request.query_params.get("is_active")
        if is_active_param is not None:
            qs = qs.filter(is_active=is_active_param.lower() != "false")
        else:
            qs = qs.filter(is_active=True)

        allowed_orderings = {"name", "-name", "sku", "-sku", "-updated_at", "-created_at"}
        ordering = request.query_params.get("ordering", "-updated_at")
        if ordering in allowed_orderings:
            qs = qs.order_by(ordering)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(ProductSerializer(page, many=True).data)

    def post(self, request):
        _require_owner_or_admin(request, self)
        serializer = ProductWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        reg_ref_data = serializer.validated_data.pop("regulatory_reference", None)
        brand_id = serializer.validated_data.pop("brand_id")
        brand = _get_brand(request.active_tenant_id, brand_id)

        product = Product(
            tenant=request.active_membership.tenant,
            brand=brand,
            status=Product.Status.DRAFT,
            **serializer.validated_data,
        )
        try:
            product.save()
        except IntegrityError:
            raise ValidationError({"sku": "A product with this SKU already exists."})

        if reg_ref_data:
            _create_regulatory_ref(product, reg_ref_data, request)

        audit.record(
            action="catalogue.product.created",
            actor=request.user,
            tenant=request.active_membership.tenant,
            target=product,
            after={"sku": product.sku, "name": product.name, "status": product.status},
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
        product = _get_product(request.active_tenant_id, pk)
        product._batch_count = product.batches.count()
        product._active_batch_count = product.batches.filter(status=Batch.Status.ACTIVE).count()
        product._codes_issued = Code.unscoped.filter(batch__product=product).count()
        return Response(ProductSerializer(product).data)

    def patch(self, request, pk):
        _require_owner_or_admin(request, self)
        product = _get_product(request.active_tenant_id, pk)
        serializer = ProductWriteSerializer(product, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        reg_ref_data = serializer.validated_data.pop("regulatory_reference", None)

        if "brand_id" in serializer.validated_data:
            brand_id = serializer.validated_data.pop("brand_id")
            serializer.validated_data["brand"] = _get_brand(request.active_tenant_id, brand_id)

        before = {
            "sku": product.sku, "name": product.name, "status": product.status,
            "industry": product.industry,
        }
        for attr, val in serializer.validated_data.items():
            setattr(product, attr, val)
        product.save()

        if reg_ref_data:
            _create_regulatory_ref(product, reg_ref_data, request)

        audit.record(
            action="catalogue.product.updated",
            actor=request.user,
            tenant=request.active_membership.tenant,
            target=product,
            before=before,
            after={"sku": product.sku, "name": product.name, "status": product.status},
        )
        return Response(ProductSerializer(product).data)


@extend_schema(
    responses={200: ProductSerializer},
    tags=["Catalogue — Products"],
    summary="Submit a product for review",
    description="Transitions a draft or rejected product to in_review. Validates all required fields.",
)
class ProductSubmitView(APIView):
    permission_classes = _BASE_PERMS

    def post(self, request, pk):
        _require_owner_or_admin(request, self)
        product = _get_product(request.active_tenant_id, pk)

        if product.status not in (Product.Status.DRAFT, Product.Status.REJECTED):
            raise ValidationError(
                {"status": f"Cannot submit a product with status '{product.status}'."},
                code="invalid_status_transition",
            )

        errors = {}
        if not product.name:
            errors["name"] = ["This field is required."]
        if not product.sku:
            errors["sku"] = ["This field is required."]
        if not product.industry:
            errors["industry"] = ["This field is required."]
        if errors:
            raise ValidationError(errors)

        now = timezone.now()
        product.status = Product.Status.IN_REVIEW
        product.submitted_by = request.user
        product.submitted_at = now
        product.review_note = ""
        product.save(update_fields=["status", "submitted_by", "submitted_at", "review_note", "updated_at"])

        audit.record(
            action="catalogue.product.submitted",
            actor=request.user,
            tenant=request.active_membership.tenant,
            target=product,
            after={
                "status": product.status,
                "name": product.name,
                "sku": product.sku,
                "industry": product.industry,
                "variant": product.variant,
                "size": product.size,
                "category": product.category,
                "gtin": product.gtin,
                "brand_id": str(product.brand_id),
                "consumer_visibility": product.consumer_visibility,
                "submitted_at": now.isoformat(),
            },
        )
        return Response(ProductSerializer(product).data)


@extend_schema(
    responses={200: ProductSerializer},
    tags=["Catalogue — Products"],
    summary="Preview what the verify endpoint would return for this product",
)
class ProductConsumerPreviewView(APIView):
    permission_classes = _BASE_PERMS

    def get(self, request, pk):
        product = _get_product(request.active_tenant_id, pk)

        batch = (
            product.batches
            .filter(status=Batch.Status.ACTIVE)
            .order_by("-created_at")
            .first()
        ) or product.batches.order_by("-created_at").first()

        payload = {
            "result": "genuine",
            "product": {
                "name": product.name,
                "sku": product.sku,
                "brand": product.brand.name,
                "industry": product.industry,
                "variant": product.variant,
                "size": product.size,
            },
            "batch": {
                "batch_number": batch.batch_number if batch else None,
                "manufactured_at": str(batch.manufactured_at) if batch and batch.manufactured_at else None,
                "expires_at": str(batch.expires_at) if batch and batch.expires_at else None,
                "status": batch.status if batch else None,
            },
            "manufacturer": {"name": product.tenant.legal_name},
        }

        vis = product.consumer_visibility or {}
        if not vis.get("brand", True):
            payload["product"].pop("brand", None)
        if not vis.get("variant_size", True):
            payload["product"].pop("variant", None)
            payload["product"].pop("size", None)
        if batch:
            if not vis.get("batch_number", True):
                payload["batch"].pop("batch_number", None)
            if not vis.get("manufacture_date", True):
                payload["batch"].pop("manufactured_at", None)
            if not vis.get("expiry", True):
                payload["batch"].pop("expires_at", None)

        return Response(payload)


@extend_schema(
    responses={200: inline_serializer("ActivityResponse", fields={"results": s.ListField()})},
    tags=["Catalogue — Products"],
    summary="Per-product audit activity log",
)
class ProductActivityView(APIView):
    permission_classes = _BASE_PERMS

    def get(self, request, pk):
        product = _get_product(request.active_tenant_id, pk)
        events = (
            AuditEvent.objects
            .filter(target_id=str(product.pk))
            .select_related("actor")
            .order_by("-occurred_at")[:100]
        )
        results = []
        for event in events:
            actor = None
            if event.actor:
                actor = {
                    "id": str(event.actor_id),
                    "name": event.actor.get_full_name() or event.actor.email,
                }
            results.append({
                "at": event.occurred_at,
                "actor": actor,
                "action": event.action,
                "before": event.before,
                "after": event.after,
            })
        return Response({"results": results})


# ---------------------------------------------------------------------------
# Product images
# ---------------------------------------------------------------------------

@extend_schema(
    request=inline_serializer("ImageUploadRequest", fields={"file": s.ImageField()}),
    responses={201: ProductImageSerializer},
    tags=["Catalogue — Images"],
    summary="Upload a reference image for a product",
)
class ProductImageUploadView(APIView):
    permission_classes = _BASE_PERMS
    parser_classes = [MultiPartParser]

    def post(self, request):
        _require_owner_or_admin(request, self)

        try:
            from PIL import Image as PilImage
        except ImportError:
            raise ImproperlyConfigured("Pillow is required for image upload. Add Pillow to requirements.")

        uploaded = request.FILES.get("file")
        if not uploaded:
            raise ValidationError({"file": "An image file is required."})

        if uploaded.size > _MAX_IMAGE_BYTES:
            return Response(
                {"error": {"code": "file_too_large", "message": "Image must be under 5 MB."}},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        header = uploaded.read(16)
        uploaded.seek(0)
        if header[:4] == _PNG_MAGIC:
            fmt = "PNG"
            ext = "png"
        elif header[:3] == _JPEG_MAGIC:
            fmt = "JPEG"
            ext = "jpg"
        else:
            return Response(
                {"error": {"code": "unsupported_media_type", "message": "Only PNG and JPEG images are accepted."}},
                status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            )

        try:
            img = PilImage.open(uploaded)
            img.verify()
            uploaded.seek(0)
            img = PilImage.open(uploaded)
            width, height = img.size
            if min(width, height) < 400 or max(width, height) > 4000:
                return Response(
                    {"error": {"code": "image_invalid", "message": "Image must be between 400 and 4000 pixels on each side."}},
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            # Strip EXIF by re-encoding
            buf = BytesIO()
            img = img.convert("RGB")
            img.save(buf, "JPEG", quality=90)
            buf.seek(0)
            width, height = img.size
            ext = "jpg"
        except Exception:
            return Response(
                {"error": {"code": "image_invalid", "message": "Could not process image."}},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        filename = f"catalogue/images/{_uuid.uuid4().hex}.{ext}"
        saved_path = default_storage.save(filename, ContentFile(buf.read()))
        image_url = default_storage.url(saved_path)

        image = ProductImage.objects.create(
            tenant=request.active_membership.tenant,
            url=image_url,
            width=width,
            height=height,
            original_filename=uploaded.name,
            uploaded_by=request.user,
        )
        return Response(ProductImageSerializer(image).data, status=status.HTTP_201_CREATED)


@extend_schema(
    responses={204: None},
    tags=["Catalogue — Images"],
    summary="Delete a reference image",
)
class ProductImageDeleteView(APIView):
    permission_classes = _BASE_PERMS

    def delete(self, request, pk):
        _require_owner_or_admin(request, self)
        try:
            image = ProductImage.objects.get(pk=pk)
        except ProductImage.DoesNotExist:
            raise NotFound("Image not found.")
        image.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


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
        return Product.unscoped.select_related("brand", "tenant").get(pk=product_id, tenant_id=tenant_id)
    except Product.DoesNotExist:
        raise NotFound("Product not found.") from None


def _get_batch(tenant_id, product_id, batch_id):
    try:
        return Batch.unscoped.get(pk=batch_id, tenant_id=tenant_id, product_id=product_id)
    except Batch.DoesNotExist:
        raise NotFound("Batch not found.") from None


def _create_regulatory_ref(product, reg_ref_data, request):
    """Create a RegulatoryRef from nested data on product create/update."""
    from .serializers import RegulatoryRefWriteSerializer
    reg_ref_data.setdefault("product_id", str(product.pk))
    ref_serializer = RegulatoryRefWriteSerializer(data=reg_ref_data)
    ref_serializer.is_valid(raise_exception=True)
    ref_serializer.validated_data.pop("product_id", None)
    RegulatoryRef.objects.create(
        tenant=request.active_membership.tenant,
        product=product,
        **ref_serializer.validated_data,
    )
