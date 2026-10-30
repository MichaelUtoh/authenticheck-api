from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import Code, IssuanceJob


class IssuanceJobSerializer(serializers.ModelSerializer):
    batch_id = serializers.UUIDField(source="batch.id", read_only=True)
    batch_number = serializers.CharField(source="batch.batch_number", read_only=True)
    product_name = serializers.CharField(source="batch.product.display_name", read_only=True)
    is_reconciled = serializers.BooleanField(read_only=True)
    quantity_generated = serializers.IntegerField(read_only=True)
    quantity_failed = serializers.IntegerField(read_only=True)
    started_at = serializers.DateTimeField(source="created_at", read_only=True)
    started_by = serializers.SerializerMethodField()

    class Meta:
        model = IssuanceJob
        fields = [
            "id", "batch_id", "batch_number", "product_name",
            "quantity_requested", "quantity_issued", "quantity_generated", "quantity_failed",
            "status", "is_reconciled", "errors",
            "started_at", "started_by", "created_at", "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_started_by(self, obj):
        user = obj.submitted_by
        if user is None:
            return None
        return {"id": str(user.pk), "name": user.full_name or user.email}


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
