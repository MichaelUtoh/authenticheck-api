from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import StandardPagination
from apps.tenants.permissions import HasActiveTenantMembership, IsOwnerOrAdmin

from .models import WebhookDelivery, WebhookEndpoint
from .serializers import (
    WebhookDeliverySerializer,
    WebhookEndpointCreateResponseSerializer,
    WebhookEndpointCreateSerializer,
    WebhookEndpointSerializer,
)
from .services import generate_secret

_BASE_PERMS = [IsAuthenticated, HasActiveTenantMembership]
_WRITE_PERMS = [IsAuthenticated, HasActiveTenantMembership, IsOwnerOrAdmin]


@extend_schema_view(
    get=extend_schema(
        responses={200: WebhookEndpointSerializer(many=True)},
        tags=["Webhooks"],
        summary="List webhook endpoints",
    ),
    post=extend_schema(
        request=WebhookEndpointCreateSerializer,
        responses={201: WebhookEndpointCreateResponseSerializer},
        tags=["Webhooks"],
        summary="Register a webhook endpoint",
        description=(
            "Creates a webhook endpoint for the tenant. The `secret` field is returned "
            "only in this response — store it securely to verify incoming signatures. "
            "Supported events: `code.verified`, `batch.recalled`, `risk.flag.created`."
        ),
    ),
)
class WebhookEndpointListCreateView(APIView):
    permission_classes = _BASE_PERMS

    def get(self, request):
        qs = WebhookEndpoint.objects.order_by("-created_at")
        return Response(WebhookEndpointSerializer(qs, many=True).data)

    def post(self, request):
        self.permission_classes = _WRITE_PERMS
        self.check_permissions(request)

        serializer = WebhookEndpointCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        endpoint = WebhookEndpoint(
            tenant=request.active_membership.tenant,
            url=d["url"],
            events=d["events"],
            secret=generate_secret(),
            description=d.get("description", ""),
        )
        endpoint.save()
        return Response(
            WebhookEndpointCreateResponseSerializer(endpoint).data,
            status=201,
        )


@extend_schema_view(
    get=extend_schema(
        responses={200: WebhookEndpointSerializer},
        tags=["Webhooks"],
        summary="Retrieve a webhook endpoint",
    ),
    patch=extend_schema(
        request=WebhookEndpointCreateSerializer,
        responses={200: WebhookEndpointSerializer},
        tags=["Webhooks"],
        summary="Update a webhook endpoint",
    ),
    delete=extend_schema(
        responses={204: None},
        tags=["Webhooks"],
        summary="Delete a webhook endpoint",
    ),
)
class WebhookEndpointDetailView(APIView):
    permission_classes = _BASE_PERMS

    def _get_endpoint(self, request, pk):
        try:
            return WebhookEndpoint.objects.get(pk=pk)
        except WebhookEndpoint.DoesNotExist:
            raise NotFound("Webhook endpoint not found.")

    def get(self, request, pk):
        return Response(WebhookEndpointSerializer(self._get_endpoint(request, pk)).data)

    def patch(self, request, pk):
        self.permission_classes = _WRITE_PERMS
        self.check_permissions(request)

        endpoint = self._get_endpoint(request, pk)
        serializer = WebhookEndpointCreateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        if "url" in d:
            endpoint.url = d["url"]
        if "events" in d:
            endpoint.events = d["events"]
        if "description" in d:
            endpoint.description = d["description"]
        endpoint.save(update_fields=["url", "events", "description", "updated_at"])
        return Response(WebhookEndpointSerializer(endpoint).data)

    def delete(self, request, pk):
        self.permission_classes = _WRITE_PERMS
        self.check_permissions(request)
        self._get_endpoint(request, pk).delete()
        return Response(status=204)


@extend_schema(
    responses={200: WebhookDeliverySerializer(many=True)},
    tags=["Webhooks"],
    summary="List recent deliveries for a webhook endpoint",
)
class WebhookDeliveryListView(APIView):
    permission_classes = _BASE_PERMS

    def get(self, request, pk):
        try:
            endpoint = WebhookEndpoint.objects.get(pk=pk)
        except WebhookEndpoint.DoesNotExist:
            raise NotFound("Webhook endpoint not found.")

        qs = WebhookDelivery.objects.filter(endpoint=endpoint).order_by("-created_at")
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(WebhookDeliverySerializer(page, many=True).data)
