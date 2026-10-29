from rest_framework import serializers

from .models import Batch, Brand, BulkImportJob, Product, ProductImage, RegulatoryRef


class BrandSerializer(serializers.ModelSerializer):
    class Meta:
        model = Brand
        fields = ["id", "name", "slug", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class BrandWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Brand
        fields = ["name", "slug", "is_active"]

    def validate_slug(self, value):
        return value.lower()


class ProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ["id", "url", "width", "height", "original_filename", "created_at"]
        read_only_fields = ["id", "url", "width", "height", "original_filename", "created_at"]


class ProductSerializer(serializers.ModelSerializer):
    brand_id = serializers.UUIDField(source="brand.id", read_only=True)
    brand_name = serializers.SerializerMethodField()
    reference_image_id = serializers.SerializerMethodField()
    batch_count = serializers.SerializerMethodField()
    active_batch_count = serializers.SerializerMethodField()
    codes_issued = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "brand_id",
            "brand_name",
            "name",
            "sku",
            "description",
            "industry",
            "variant",
            "size",
            "category",
            "gtin",
            "status",
            "is_active",
            "consumer_visibility",
            "review_note",
            "submitted_at",
            "reviewed_at",
            "reference_image_id",
            "batch_count",
            "active_batch_count",
            "codes_issued",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id", "brand_id", "brand_name", "status", "is_active", "review_note",
            "submitted_at", "reviewed_at", "reference_image_id",
            "batch_count", "active_batch_count", "codes_issued",
            "created_at", "updated_at",
        ]

    def get_brand_name(self, obj):
        return obj.brand.name if obj.brand_id else None

    def get_reference_image_id(self, obj):
        return str(obj.reference_image_id) if obj.reference_image_id else None

    def get_batch_count(self, obj):
        return getattr(obj, "_batch_count", None)

    def get_active_batch_count(self, obj):
        return getattr(obj, "_active_batch_count", None)

    def get_codes_issued(self, obj):
        return getattr(obj, "_codes_issued", None)


def _validate_gtin(value):
    """Validate GTIN-13 or GTIN-14 using GS1 mod-10 check digit."""
    if not value:
        return value
    if not value.isdigit() or len(value) not in (13, 14):
        raise serializers.ValidationError("GTIN must be 13 or 14 digits.")
    digits = [int(d) for d in value]
    total = 0
    for i, d in enumerate(digits[:-1]):
        # GS1: multiply by 3 if position from right (excluding check digit) is odd
        if (len(digits) - 2 - i) % 2 == 0:
            total += d * 3
        else:
            total += d
    calculated = (10 - (total % 10)) % 10
    if calculated != digits[-1]:
        raise serializers.ValidationError("Invalid GTIN check digit.")
    return value


class ProductWriteSerializer(serializers.ModelSerializer):
    brand_id = serializers.UUIDField(write_only=True)
    regulatory_reference = serializers.DictField(required=False, allow_null=True, write_only=True)

    class Meta:
        model = Product
        fields = [
            "brand_id",
            "name",
            "sku",
            "description",
            "industry",
            "variant",
            "size",
            "category",
            "gtin",
            "consumer_visibility",
            "regulatory_reference",
        ]

    def validate_sku(self, value):
        return value.strip().upper()

    def validate_gtin(self, value):
        return _validate_gtin(value) if value else ""

    def validate_industry(self, value):
        if value == Product.Industry.PHARMACEUTICALS:
            raise serializers.ValidationError(
                detail="Pharmaceuticals is not available yet.",
                code="industry_locked",
            )
        return value


class RegulatoryRefSerializer(serializers.ModelSerializer):
    product_id = serializers.UUIDField(source="product.id", read_only=True)
    verified_by = serializers.SerializerMethodField()

    class Meta:
        model = RegulatoryRef
        fields = [
            "id",
            "product_id",
            "country_code",
            "authority",
            "reference_number",
            "valid_from",
            "valid_until",
            "source",
            "verification_status",
            "verified_by",
            "verified_at",
            "verification_method",
            "verification_note",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id", "product_id", "verification_status", "verified_by",
            "verified_at", "verification_method", "verification_note",
            "created_at", "updated_at",
        ]

    def get_verified_by(self, obj):
        return str(obj.verified_by_id) if obj.verified_by_id else None


class RegulatoryRefWriteSerializer(serializers.ModelSerializer):
    product_id = serializers.UUIDField(write_only=True)

    class Meta:
        model = RegulatoryRef
        fields = [
            "product_id",
            "country_code",
            "authority",
            "reference_number",
            "valid_from",
            "valid_until",
            "source",
        ]

    def validate_country_code(self, value):
        return value.upper()


class BatchSerializer(serializers.ModelSerializer):
    product_id = serializers.UUIDField(source="product.id", read_only=True)

    class Meta:
        model = Batch
        fields = [
            "id", "product_id", "batch_number", "manufactured_at",
            "expires_at", "quantity", "status", "notes",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "product_id", "created_at", "updated_at"]


class BatchWriteSerializer(serializers.ModelSerializer):
    product_id = serializers.UUIDField(write_only=True)

    class Meta:
        model = Batch
        fields = ["product_id", "batch_number", "manufactured_at", "expires_at", "quantity", "status", "notes"]


class BulkImportJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = BulkImportJob
        fields = [
            "id", "import_type", "status", "file_name",
            "rows_total", "rows_imported", "rows_failed", "errors",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "import_type", "status", "file_name",
            "rows_total", "rows_imported", "rows_failed", "errors",
            "created_at", "updated_at",
        ]
