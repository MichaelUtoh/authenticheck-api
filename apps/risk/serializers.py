from rest_framework import serializers

from .models import Case, ConsumerReport, RiskFlag


class ConsumerReportCreateSerializer(serializers.Serializer):
    code_str = serializers.CharField(max_length=22)
    description = serializers.CharField(max_length=2000)
    contact_email = serializers.EmailField(required=False, allow_blank=True, default="")
    country_code = serializers.CharField(max_length=3, required=False, allow_blank=True, default="")

    def validate_country_code(self, value):
        return value.upper()


class ConsumerReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsumerReport
        fields = ["id", "code_str", "description", "contact_email", "country_code", "created_at"]
        read_only_fields = fields


class RiskFlagSerializer(serializers.ModelSerializer):
    code_id = serializers.UUIDField(source="code.id", read_only=True, allow_null=True)
    batch_id = serializers.UUIDField(source="batch.id", read_only=True, allow_null=True)
    case_id = serializers.UUIDField(source="case.id", read_only=True, allow_null=True)

    class Meta:
        model = RiskFlag
        fields = [
            "id", "kind", "severity", "code_id", "batch_id", "case_id",
            "detail", "is_reviewed", "reviewed_at", "created_at",
        ]
        read_only_fields = fields


class CaseSerializer(serializers.ModelSerializer):
    batch_id = serializers.UUIDField(source="batch.id", read_only=True, allow_null=True)
    product_id = serializers.UUIDField(source="product.id", read_only=True, allow_null=True)
    flags_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Case
        fields = [
            "id", "status", "title", "description", "batch_id", "product_id",
            "notes", "resolved_at", "flags_count", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "flags_count", "resolved_at"]


class CaseCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    batch_id = serializers.UUIDField(required=False, allow_null=True)
    product_id = serializers.UUIDField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class CaseUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Case.Status.choices, required=False)
    title = serializers.CharField(max_length=255, required=False)
    notes = serializers.CharField(required=False, allow_blank=True)
