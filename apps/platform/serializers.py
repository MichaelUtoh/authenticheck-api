from rest_framework import serializers

from apps.audit.models import AuditEvent
from apps.risk.models import RiskFlag
from apps.tenants.models import Tenant


class PlatformTenantSerializer(serializers.ModelSerializer):
    member_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Tenant
        fields = ["id", "legal_name", "trading_name", "status", "member_count", "created_at"]
        read_only_fields = fields


class PlatformTenantDetailSerializer(serializers.ModelSerializer):
    member_count = serializers.IntegerField(read_only=True)
    active_codes = serializers.IntegerField(read_only=True)
    open_flags = serializers.IntegerField(read_only=True)

    class Meta:
        model = Tenant
        fields = [
            "id", "legal_name", "trading_name", "status",
            "member_count", "active_codes", "open_flags", "created_at",
        ]
        read_only_fields = fields


class PlatformRiskFlagSerializer(serializers.ModelSerializer):
    tenant_name = serializers.CharField(source="tenant.legal_name", read_only=True)
    code_str = serializers.CharField(source="code.code", read_only=True, allow_null=True)
    batch_number = serializers.CharField(
        source="batch.batch_number", read_only=True, allow_null=True
    )

    class Meta:
        model = RiskFlag
        fields = [
            "id", "tenant_name", "kind", "severity", "code_str", "batch_number",
            "detail", "is_reviewed", "created_at",
        ]
        read_only_fields = fields


class PlatformAuditEventSerializer(serializers.ModelSerializer):
    actor_email = serializers.EmailField(source="actor.email", read_only=True, allow_null=True)
    tenant_name = serializers.CharField(source="tenant.legal_name", read_only=True, allow_null=True)

    class Meta:
        model = AuditEvent
        fields = [
            "id", "occurred_at", "actor_email", "tenant_name",
            "action", "target_type", "target_id", "reason",
            "before", "after", "request_id",
        ]
        read_only_fields = fields
