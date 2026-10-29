from django.urls import path

from .views import (
    PlatformAuditLogView,
    PlatformFlagListView,
    PlatformTenantDetailView,
    PlatformTenantListView,
    RegulatoryRefVerifyView,
)

urlpatterns = [
    path("platform/tenants", PlatformTenantListView.as_view(), name="platform-tenant-list"),
    path(
        "platform/tenants/<uuid:pk>",
        PlatformTenantDetailView.as_view(),
        name="platform-tenant-detail",
    ),
    path("platform/flags", PlatformFlagListView.as_view(), name="platform-flag-list"),
    path("platform/audit", PlatformAuditLogView.as_view(), name="platform-audit-log"),
    path(
        "platform/products/<uuid:product_id>/regulatory-refs/<uuid:ref_id>/verify",
        RegulatoryRefVerifyView.as_view(),
        name="platform-regulatory-ref-verify",
    ),
]
