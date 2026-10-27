from django.urls import path

from .views import (
    APIKeyListCreateView,
    APIKeyRevokeView,
    LoginView,
    LogoutView,
    MeView,
    MFAConfirmView,
    MFASetupView,
    RefreshView,
    RegisterView,
)

urlpatterns = [
    path("auth/register", RegisterView.as_view(), name="auth-register"),
    path("auth/login", LoginView.as_view(), name="auth-login"),
    path("auth/logout", LogoutView.as_view(), name="auth-logout"),
    path("auth/refresh", RefreshView.as_view(), name="auth-refresh"),
    path("auth/me", MeView.as_view(), name="auth-me"),
    # Legacy session-based MFA setup — replaced in Phase 6 by challenge-token endpoints
    path("auth/mfa/setup", MFASetupView.as_view(), name="auth-mfa-setup"),
    path("auth/mfa/confirm", MFAConfirmView.as_view(), name="auth-mfa-confirm"),
    # API Keys
    path("api-keys", APIKeyListCreateView.as_view(), name="apikey-list-create"),
    path("api-keys/<uuid:pk>", APIKeyRevokeView.as_view(), name="apikey-revoke"),
]
