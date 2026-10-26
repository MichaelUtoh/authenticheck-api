from rest_framework import serializers

from .models import Code, IssuanceJob


class IssuanceJobSerializer(serializers.ModelSerializer):
    batch_id = serializers.UUIDField(source="batch.id", read_only=True)
    is_reconciled = serializers.BooleanField(read_only=True)

    class Meta:
        model = IssuanceJob
        fields = [
            "id", "batch_id", "quantity_requested", "quantity_issued",
            "status", "is_reconciled", "errors", "created_at", "updated_at",
        ]
        read_only_fields = fields


class IssuanceJobCreateSerializer(serializers.Serializer):
    batch_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1, max_value=1_000_000)


class CodeSerializer(serializers.ModelSerializer):
    batch_id = serializers.UUIDField(source="batch.id", read_only=True)
    product_id = serializers.UUIDField(source="product.id", read_only=True)

    class Meta:
        model = Code
        fields = [
            "id", "code", "batch_id", "product_id",
            "status", "deactivated_at", "deactivation_reason",
            "created_at", "updated_at",
        ]
        read_only_fields = fields


class DeactivateSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500, required=False, default="")
