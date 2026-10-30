from django.urls import path

from .views import (
    ReleaseGatesView,
    SupportContactView,
    TenantMemberDetailView,
    TenantApproveView,
    TenantDetailView,
    TenantMembersView,
    TenantRegisterView,
    TenantRejectView,
    TenantRequestInfoView,
    TenantSuspendView,
)

urlpatterns = [
    path("tenants/register", TenantRegisterView.as_view(), name="tenant-register"),
    path("tenants/me", TenantDetailView.as_view(), name="tenant-detail"),
    path("tenants/me/members", TenantMembersView.as_view(), name="tenant-members"),
    path("tenants/me/members/<uuid:pk>", TenantMemberDetailView.as_view(), name="tenant-member-detail"),
    path("tenants/me/support-contact", SupportContactView.as_view(), name="tenant-support-contact"),
    path("tenants/me/release-gates", ReleaseGatesView.as_view(), name="tenant-release-gates"),
    path("tenants/<uuid:pk>/approve", TenantApproveView.as_view(), name="tenant-approve"),
    path("tenants/<uuid:pk>/reject", TenantRejectView.as_view(), name="tenant-reject"),
    path(
        "tenants/<uuid:pk>/request-info",
        TenantRequestInfoView.as_view(),
        name="tenant-request-info",
    ),
    path("tenants/<uuid:pk>/suspend", TenantSuspendView.as_view(), name="tenant-suspend"),
]
