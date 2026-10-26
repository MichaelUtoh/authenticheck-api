from django.urls import path

from .views import (
    LoginView,
    LogoutView,
    MeView,
    MFAConfirmView,
    MFASetupView,
    MFAVerifyView,
    RegisterView,
)

urlpatterns = [
    path("auth/register", RegisterView.as_view(), name="auth-register"),
    path("auth/login", LoginView.as_view(), name="auth-login"),
    path("auth/logout", LogoutView.as_view(), name="auth-logout"),
    path("auth/me", MeView.as_view(), name="auth-me"),
    path("auth/mfa/setup", MFASetupView.as_view(), name="auth-mfa-setup"),
    path("auth/mfa/confirm", MFAConfirmView.as_view(), name="auth-mfa-confirm"),
    path("auth/mfa/verify", MFAVerifyView.as_view(), name="auth-mfa-verify"),
]
