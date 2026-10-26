from rest_framework import serializers

from .models import Membership, Tenant


class TenantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = ["id", "legal_name", "trading_name", "status", "created_at"]
        read_only_fields = ["id", "status", "created_at"]


class TenantRegisterSerializer(serializers.Serializer):
    legal_name = serializers.CharField(max_length=255)
    trading_name = serializers.CharField(max_length=255, default="", allow_blank=True)

    def create(self, validated_data):
        return Tenant.objects.create(
            legal_name=validated_data["legal_name"],
            trading_name=validated_data.get("trading_name", ""),
        )


class TenantStatusUpdateSerializer(serializers.Serializer):
    reason = serializers.CharField(allow_blank=True, default="")


class MembershipSerializer(serializers.ModelSerializer):
    tenant_id = serializers.UUIDField(source="tenant.id", read_only=True)
    tenant_name = serializers.CharField(source="tenant.trading_name", read_only=True)
    tenant_legal_name = serializers.CharField(source="tenant.legal_name", read_only=True)
    tenant_status = serializers.CharField(source="tenant.status", read_only=True)

    class Meta:
        model = Membership
        fields = [
            "id",
            "tenant_id",
            "tenant_name",
            "tenant_legal_name",
            "tenant_status",
            "role",
            "is_active",
            "joined_at",
        ]
        read_only_fields = fields
