from rest_framework import serializers

from apps.audit.models import AuditEvent
from apps.risk.models import RiskFlag
from apps.tenants.models import Tenant


class _TenantExtrasMixin(serializers.Serializer):
    """owner and brands, filled in by attach_tenant_extras() so a page costs two queries."""

    owner_email = serializers.SerializerMethodField()
    owner_name = serializers.SerializerMethodField()
    brands = serializers.SerializerMethodField()
    submitted_at = serializers.DateTimeField(source="created_at", read_only=True)

    def get_owner_email(self, obj):
        return getattr(obj, "_owner_email", "")

    def get_owner_name(self, obj):
        return getattr(obj, "_owner_name", "")

    def get_brands(self, obj):
        return getattr(obj, "_brands", [])


class PlatformTenantSerializer(_TenantExtrasMixin, serializers.ModelSerializer):
    member_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Tenant
        fields = [
            "id", "legal_name", "trading_name", "industry", "rc_number", "city", "status",
            "decision_reason", "decided_at", "owner_email", "owner_name", "brands", "member_count", "submitted_at", "created_at",
        ]
        read_only_fields = fields


class PlatformTenantDetailSerializer(_TenantExtrasMixin, serializers.ModelSerializer):
    member_count = serializers.IntegerField(read_only=True)
    active_codes = serializers.IntegerField(read_only=True)
    open_flags = serializers.IntegerField(read_only=True)

    class Meta:
        model = Tenant
        fields = [
            "id", "legal_name", "trading_name", "industry", "rc_number", "city", "status",
            "decision_reason", "decided_at", "owner_email", "owner_name", "brands",
            "member_count", "active_codes",
            "open_flags", "submitted_at", "created_at",
        ]
        read_only_fields = fields


def attach_tenant_extras(tenants) -> None:
    """Set _owner_email, _owner_name and _brands on each tenant, in two queries.

    The owner is the earliest active member with the owner role; brands are the active ones."""
    from apps.catalogue.models import Brand
    from apps.tenants.models import Membership

    ids = [t.pk for t in tenants]
    owners = {}
    for m in (
        Membership.objects.filter(
            tenant_id__in=ids, role=Membership.Role.OWNER, status=Membership.Status.ACTIVE
        )
        .select_related("user")
        .order_by("-created_at")  # earliest wins: it overwrites later ones below
    ):
        owners[m.tenant_id] = m.user

    brands: dict = {}
    for tenant_id, name in (
        Brand.unscoped.filter(tenant_id__in=ids, is_active=True)
        .order_by("name")
        .values_list("tenant_id", "name")
    ):
        brands.setdefault(tenant_id, []).append({"name": name})

    for t in tenants:
        owner = owners.get(t.pk)
        t._owner_email = owner.email if owner else ""
        t._owner_name = owner.full_name if owner else ""
        t._brands = brands.get(t.pk, [])


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
