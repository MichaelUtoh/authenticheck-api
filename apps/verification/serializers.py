from rest_framework import serializers


class ProductInfoSerializer(serializers.Serializer):
    name = serializers.CharField()
    sku = serializers.CharField()
    brand = serializers.CharField(required=False)
    industry = serializers.CharField(required=False, allow_blank=True)
    variant = serializers.CharField(required=False, allow_blank=True)
    size = serializers.CharField(required=False, allow_blank=True)


class BatchInfoSerializer(serializers.Serializer):
    batch_number = serializers.CharField(required=False)
    manufactured_at = serializers.DateField(allow_null=True, required=False)
    expires_at = serializers.DateField(allow_null=True, required=False)
    status = serializers.CharField()


class ManufacturerInfoSerializer(serializers.Serializer):
    name = serializers.CharField()


class ProductImageInfoSerializer(serializers.Serializer):
    url = serializers.CharField()
    thumbnail_url = serializers.CharField(required=False)


class VerifyGenuineSerializer(serializers.Serializer):
    """Response shape for `genuine` and `recalled` results."""

    result = serializers.CharField()
    code = serializers.CharField()
    is_first_check = serializers.BooleanField()
    checked_at = serializers.DateTimeField()
    first_checked_at = serializers.DateTimeField(required=False)
    product = ProductInfoSerializer()
    batch = BatchInfoSerializer()
    manufacturer = ManufacturerInfoSerializer()
    product_image = ProductImageInfoSerializer(required=False)
    support_contact = serializers.DictField(child=serializers.CharField(), required=False)
    regulatory_refs = serializers.ListField(child=serializers.DictField(), required=False)


class VerifyDeactivatedSerializer(serializers.Serializer):
    result = serializers.CharField()
    code = serializers.CharField()
    deactivated_at = serializers.DateTimeField()
    deactivation_reason = serializers.CharField()


class VerifyNotFoundSerializer(serializers.Serializer):
    result = serializers.CharField()
    detail = serializers.CharField()
