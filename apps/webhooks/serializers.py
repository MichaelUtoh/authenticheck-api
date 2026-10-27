from rest_framework import serializers

from .models import WebhookDelivery, WebhookEndpoint
from .services import SUPPORTED_EVENTS


class WebhookEndpointSerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookEndpoint
        fields = [
            "id", "url", "events", "status", "description",
            "failure_count", "last_success_at", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "failure_count", "last_success_at", "created_at", "updated_at"]


class WebhookEndpointCreateSerializer(serializers.Serializer):
    url = serializers.URLField(max_length=500)
    events = serializers.ListField(
        child=serializers.ChoiceField(choices=sorted(SUPPORTED_EVENTS)),
        min_length=1,
    )
    description = serializers.CharField(max_length=200, required=False, allow_blank=True)

    def validate_events(self, value):
        return list(set(value))  # deduplicate


class WebhookEndpointCreateResponseSerializer(WebhookEndpointSerializer):
    """Includes the secret — returned only on creation."""
    secret = serializers.CharField()

    class Meta(WebhookEndpointSerializer.Meta):
        fields = WebhookEndpointSerializer.Meta.fields + ["secret"]


class WebhookDeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookDelivery
        fields = [
            "id", "event_type", "status", "attempt_count",
            "response_status", "delivered_at", "created_at",
        ]
        read_only_fields = fields
