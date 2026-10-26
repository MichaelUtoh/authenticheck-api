from rest_framework import serializers


class ProductInfoSerializer(serializers.Serializer):
    name = serializers.CharField()
    sku = serializers.CharField()
    brand = serializers.CharField()


class BatchInfoSerializer(serializers.Serializer):
    batch_number = serializers.CharField()
    expires_at = serializers.DateField(allow_null=True)
    status = serializers.CharField()


class ManufacturerInfoSerializer(serializers.Serializer):
    name = serializers.CharField()


class VerifyGenuineSerializer(serializers.Serializer):
    """Response shape for `genuine` and `recalled` results."""

    result = serializers.CharField()
    code = serializers.CharField()
    product = ProductInfoSerializer()
    batch = BatchInfoSerializer()
    manufacturer = ManufacturerInfoSerializer()


class VerifyDeactivatedSerializer(serializers.Serializer):
    result = serializers.CharField()
    code = serializers.CharField()
    deactivated_at = serializers.DateTimeField()
    deactivation_reason = serializers.CharField()


class VerifyNotFoundSerializer(serializers.Serializer):
    result = serializers.CharField()
    detail = serializers.CharField()
