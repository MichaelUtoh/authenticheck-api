from rest_framework import serializers

from .models import Batch, Brand, BulkImportJob, Product, RegulatoryRef


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


class ProductSerializer(serializers.ModelSerializer):
    brand_id = serializers.UUIDField(source="brand.id", read_only=True)

    class Meta:
        model = Product
        fields = ["id", "brand_id", "name", "sku", "description", "status", "created_at", "updated_at"]
        read_only_fields = ["id", "brand_id", "created_at", "updated_at"]


class ProductWriteSerializer(serializers.ModelSerializer):
    brand_id = serializers.UUIDField(write_only=True)

    class Meta:
        model = Product
        fields = ["brand_id", "name", "sku", "description", "status"]

    def validate_sku(self, value):
        return value.strip().upper()


class RegulatoryRefSerializer(serializers.ModelSerializer):
    product_id = serializers.UUIDField(source="product.id", read_only=True)

    class Meta:
        model = RegulatoryRef
        fields = [
            "id", "product_id", "country_code", "authority",
            "reference_number", "valid_from", "valid_until",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "product_id", "created_at", "updated_at"]


class RegulatoryRefWriteSerializer(serializers.ModelSerializer):
    product_id = serializers.UUIDField(write_only=True)

    class Meta:
        model = RegulatoryRef
        fields = ["product_id", "country_code", "authority", "reference_number", "valid_from", "valid_until"]

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
        read_only_fields = fields
