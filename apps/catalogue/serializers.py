import re

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator, validate_email
from rest_framework import serializers

from apps.core.exceptions import ApiError

from .models import Batch, Brand, BulkImportJob, Product, ProductImage, RegulatoryRef
from .workflow import guard_industry, validate_taxonomy


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
    gtin_conflict = serializers.SerializerMethodField()

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
            "size_value",
            "size_unit",
            "pack_configuration",
            "dosage_form",
            "strength",
            "category",
            "gtin",
            "gtin_conflict",
            "status",
            "approved_version",
            "pending_revision",
            "public_suspended",
            "correction_request",
            "is_active",
            "consumer_visibility",
            "support_contact",
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
            "size", "approved_version", "pending_revision", "public_suspended",
            "correction_request", "gtin_conflict",
            "created_at", "updated_at",
        ]

    def get_gtin_conflict(self, obj):
        # Only computed on detail responses (set by the view); None on list rows.
        return getattr(obj, "_gtin_conflict", None)

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


_VISIBILITY_KEYS = {
    "brand", "variant_size", "batch_number", "manufacture_date", "expiry",
    "regulatory_reference", "product_image", "support_contact",
}


class ProductWriteSerializer(serializers.ModelSerializer):
    brand_id = serializers.UUIDField(write_only=True)
    reference_image_id = serializers.UUIDField(write_only=True, allow_null=True, required=False)
    regulatory_reference = serializers.DictField(required=False, allow_null=True, write_only=True)
    confirm_not_duplicate = serializers.BooleanField(write_only=True, required=False, default=False)
    size_value = serializers.DecimalField(
        max_digits=10, decimal_places=3, required=False, allow_null=True
    )

    class Meta:
        model = Product
        fields = [
            "brand_id",
            "name",
            "sku",
            "description",
            "industry",
            "variant",
            "size_value",
            "size_unit",
            "pack_configuration",
            "dosage_form",
            "strength",
            "category",
            "gtin",
            "consumer_visibility",
            "support_contact",
            "reference_image_id",
            "regulatory_reference",
            "confirm_not_duplicate",
        ]

    def validate_industry(self, value):
        current = getattr(self.instance, "industry", "")
        if current and value != current:
            raise ApiError(
                409, "field_locked",
                "Industry cannot be changed after creation. Create a new product or ask "
                "AuthentiCheck for a controlled migration.",
                {"field": "industry"},
            )
        guard_industry(value)  # pharmaceuticals: 403 industry_locked until the gate opens
        return value

    def validate_name(self, value):
        value = value.strip()
        if not 2 <= len(value) <= 120:
            raise serializers.ValidationError("Name must be 2-120 characters.")
        return value

    def validate_sku(self, value):
        value = value.strip().upper()
        if not re.fullmatch(r"[A-Z0-9_-]{3,64}", value):
            raise serializers.ValidationError(
                "SKU must be 3-64 characters: letters, digits, hyphen or underscore."
            )
        return value

    def validate_gtin(self, value):
        return _validate_gtin(value) if value else ""

    def validate_size_value(self, value):
        if value is not None and value <= 0:
            raise serializers.ValidationError("Size must be a positive number.")
        return value

    def validate_pack_configuration(self, value):
        if value:
            for key in ("packs", "per_pack"):
                if key in value and not (isinstance(value[key], int) and value[key] > 0):
                    raise serializers.ValidationError({key: "Must be a positive whole number."})
        return value

    def validate_consumer_visibility(self, value):
        unknown = set(value) - _VISIBILITY_KEYS
        if unknown or not all(isinstance(v, bool) for v in value.values()):
            raise serializers.ValidationError("Unknown key or non-boolean toggle.")
        base = self.instance.consumer_visibility if self.instance else {}
        return {**{k: True for k in _VISIBILITY_KEYS}, **base, **value}

    def validate_support_contact(self, value):
        if value is None:
            return None
        unknown = set(value) - {"email", "phone", "url"}
        if unknown:
            raise serializers.ValidationError(f"Unknown keys: {', '.join(sorted(unknown))}.")
        cleaned = {}
        if value.get("email"):
            try:
                validate_email(value["email"])
            except DjangoValidationError:
                raise serializers.ValidationError({"email": "Enter a valid email."}) from None
            cleaned["email"] = value["email"].strip()
        if value.get("phone"):
            digits = value["phone"].strip().removeprefix("+")
            if not (digits.isdigit() and 7 <= len(digits) <= 15):
                raise serializers.ValidationError({"phone": "Enter a valid phone number."})
            cleaned["phone"] = value["phone"].strip()
        if value.get("url"):
            try:
                URLValidator(schemes=["http", "https"])(value["url"])
            except DjangoValidationError:
                raise serializers.ValidationError({"url": "Enter a valid URL."}) from None
            cleaned["url"] = value["url"].strip()
        return cleaned or None

    def validate(self, attrs):
        # size_value and size_unit travel together (structural check, valid on drafts).
        value = attrs.get("size_value", getattr(self.instance, "size_value", None))
        unit = attrs.get("size_unit", getattr(self.instance, "size_unit", ""))
        if ("size_value" in attrs or "size_unit" in attrs) and bool(value) != bool(unit):
            raise serializers.ValidationError(
                {"size_unit" if value else "size_value": ["Provide size_value and size_unit together."]}
            )
        industry = attrs.get("industry", getattr(self.instance, "industry", ""))
        category = attrs.get("category", getattr(self.instance, "category", ""))
        if "category" in attrs or "size_unit" in attrs or "industry" in attrs:
            errors = validate_taxonomy(industry, category, unit)
            if errors:
                raise serializers.ValidationError(errors)
        return attrs


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
