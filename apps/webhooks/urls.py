from django.urls import path

from .views import WebhookDeliveryListView, WebhookEndpointDetailView, WebhookEndpointListCreateView

urlpatterns = [
    path("webhooks", WebhookEndpointListCreateView.as_view(), name="webhook-list-create"),
    path("webhooks/<uuid:pk>", WebhookEndpointDetailView.as_view(), name="webhook-detail"),
    path("webhooks/<uuid:pk>/deliveries", WebhookDeliveryListView.as_view(), name="webhook-deliveries"),
]
