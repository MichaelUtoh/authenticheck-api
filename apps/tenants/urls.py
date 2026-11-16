from django.urls import path

from .invitations import (
    InvitationAcceptView,
    InvitationDetailView,
    InvitationListCreateView,
    InvitationPreviewView,
    InvitationResendView,
)
from .views import (
    RegistrationIndustriesView,
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
    path("tenants/industries", RegistrationIndustriesView.as_view(), name="tenant-industries"),
    path("tenants/register", TenantRegisterView.as_view(), name="tenant-register"),
    path("tenants/me", TenantDetailView.as_view(), name="tenant-detail"),
    path("tenants/me/members", TenantMembersView.as_view(), name="tenant-members"),
    path("tenants/me/invitations", InvitationListCreateView.as_view(), name="invitation-list"),
    path("tenants/me/invitations/<uuid:pk>", InvitationDetailView.as_view(), name="invitation-detail"),
    path("tenants/me/invitations/<uuid:pk>/resend", InvitationResendView.as_view(), name="invitation-resend"),
    path("invitations/preview", InvitationPreviewView.as_view(), name="invitation-preview"),
    path("invitations/accept", InvitationAcceptView.as_view(), name="invitation-accept"),
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
