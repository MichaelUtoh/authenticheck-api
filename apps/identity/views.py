import logging

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django_otp.plugins.otp_totp.models import TOTPDevice
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.core.exceptions import LoginFailed
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit import services as audit
from apps.identity.serializers import (
    LoginSerializer,
    MFATokenSerializer,
    RegisterSerializer,
    UserSerializer,
)
from apps.identity.throttles import LoginRateThrottle, MFARateThrottle

logger = logging.getLogger(__name__)
User = get_user_model()

_PENDING_USER_KEY = "_pending_user_id"


class RegisterView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        audit.record(action="auth.registered", actor=user)
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


class LoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [LoginRateThrottle]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = authenticate(
            request,
            email=serializer.validated_data["email"].lower(),
            password=serializer.validated_data["password"],
        )
        if user is None or not user.is_active:
            raise LoginFailed()

        confirmed_device = TOTPDevice.objects.filter(user=user, confirmed=True).first()
        if confirmed_device:
            request.session[_PENDING_USER_KEY] = str(user.pk)
            return Response({"mfa_required": True})

        auth_login(request, user)
        audit.record(action="auth.login", actor=user, metadata={"method": "password"})
        return Response(UserSerializer(user).data)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        audit.record(action="auth.logout", actor=request.user)
        auth_logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.tenants.models import Membership
        from apps.tenants.serializers import MembershipSerializer

        data = UserSerializer(request.user).data
        memberships = Membership.objects.filter(user=request.user, is_active=True).select_related(
            "tenant"
        )
        data["memberships"] = MembershipSerializer(memberships, many=True).data
        return Response(data)


class MFASetupView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        TOTPDevice.objects.filter(user=request.user, confirmed=False).delete()
        device = TOTPDevice.objects.create(user=request.user, name="default", confirmed=False)
        return Response({"provisioning_uri": device.config_url}, status=status.HTTP_201_CREATED)


class MFAConfirmView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = MFATokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        device = TOTPDevice.objects.filter(user=request.user, confirmed=False).first()
        if device is None:
            raise ValidationError("No pending MFA setup found. Call /auth/mfa/setup first.")

        if not device.verify_token(serializer.validated_data["token"]):
            raise ValidationError({"token": "Invalid or expired token."})

        device.confirmed = True
        device.save(update_fields=["confirmed"])
        audit.record(action="auth.mfa.enabled", actor=request.user)
        return Response({"mfa_enabled": True})


class MFAVerifyView(APIView):
    """Step-2 of login for accounts that have a confirmed TOTP device."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [MFARateThrottle]

    def post(self, request):
        serializer = MFATokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user_id = request.session.get(_PENDING_USER_KEY)
        if not user_id:
            raise PermissionDenied("No pending MFA session.")

        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            raise PermissionDenied("No pending MFA session.") from None

        device = TOTPDevice.objects.filter(user=user, confirmed=True).first()
        if device is None or not device.verify_token(serializer.validated_data["token"]):
            raise LoginFailed("Invalid token.")

        del request.session[_PENDING_USER_KEY]
        auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        request.session["_mfa_verified"] = True
        audit.record(action="auth.login", actor=user, metadata={"method": "totp"})
        return Response(UserSerializer(user).data)
