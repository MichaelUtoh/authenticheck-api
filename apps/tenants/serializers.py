from rest_framework import serializers

from .models import LEGACY_ROLE_ALIASES, Membership, Tenant


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
    email = serializers.EmailField(source="user.email", read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)

    class Meta:
        model = Membership
        fields = [
            "id",
            "email",
            "full_name",
            "tenant_id",
            "tenant_name",
            "tenant_legal_name",
            "tenant_status",
            "role",
            "can_submit_products",
            "status",
            "joined_at",
        ]
        read_only_fields = fields


class SupportContactSerializer(serializers.ModelSerializer):
    """Tenant-level support contact shown on the verification page."""

    class Meta:
        model = Tenant
        fields = ["support_email", "support_phone", "support_url"]

    def validate_support_phone(self, value):
        value = value.strip()
        digits = value[1:] if value.startswith("+") else value
        if value and not (digits.isdigit() and 7 <= len(digits) <= 15):
            raise serializers.ValidationError("Enter a valid phone number, e.g. +2348012345678.")
        return value


class MembershipUpdateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(
        choices=["admin", "production_quality", "investigator", "viewer", *LEGACY_ROLE_ALIASES],
        required=False,
    )
    can_submit_products = serializers.BooleanField(required=False)

    def validate_role(self, value):
        return LEGACY_ROLE_ALIASES.get(value, value)
