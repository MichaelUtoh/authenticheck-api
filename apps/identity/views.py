"""Identity views — Phase 2: JWT login/logout/refresh/me + fixed register enumeration.

Session-based MFA setup/confirm are kept at their old URLs until Phase 6 replaces them
with challenge-token-based equivalents (mfa/enroll, mfa/enroll/confirm, mfa/verify).
"""

import logging
import time
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice
from drf_spectacular.utils import extend_schema, extend_schema_view, inline_serializer
from rest_framework import serializers as s
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit import services as audit
from apps.core.exceptions import LoginFailed
from apps.tenants.permissions import HasActiveTenantMembership, IsOwnerOrAdmin
from apps.identity.models import AuthSession, MFAChallenge
from apps.identity.permissions import IsAuthenticatedSession
from apps.identity.serializers import (
    LoginSerializer,
    MFATokenSerializer,
    RegisterSerializer,
    UserSerializer,
)
from apps.identity.services import session_service, token_service
from apps.identity.throttles import (
    LoginEmailIPThrottle,
    LoginIPThrottle,
    RefreshThrottle,
    RegisterIPThrottle,
)

logger = logging.getLogger(__name__)
User = get_user_model()


def _set_refresh_cookie(response: Response, raw_token: str, absolute_expires_at) -> None:
    max_age = max(0, int((absolute_expires_at - timezone.now()).total_seconds()))
    response.set_cookie(
        settings.REFRESH_COOKIE_NAME,
        raw_token,
        max_age=max_age,
        httponly=True,
        secure=settings.REFRESH_COOKIE_SECURE,
        samesite=settings.REFRESH_COOKIE_SAMESITE,
        domain=settings.REFRESH_COOKIE_DOMAIN,
        path="/api/v1/auth/refresh",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        settings.REFRESH_COOKIE_NAME,
        path="/api/v1/auth/refresh",
        samesite=settings.REFRESH_COOKIE_SAMESITE,
    )


def _require_requested_with(request) -> None:
    """Enforce the custom header required for cookie-endpoint CSRF protection [EC-052]."""
    if not request.headers.get("X-Requested-With"):
        raise PermissionDenied(
            {"code": "permission_denied", "message": "X-Requested-With header is required."}
        )


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


@extend_schema(
    request=RegisterSerializer,
    responses={
        202: inline_serializer(
            "RegisterResponse", fields={"detail": s.CharField()}
        )
    },
    tags=["Auth"],
    summary="Register a new user account",
    description=(
        "Creates a new account and sends a verification email. "
        "Returns the same response whether the email address already exists, "
        "to prevent account enumeration. [EC-025]"
    ),
)
class RegisterView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [RegisterIPThrottle]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]
        password = serializer.validated_data["password"]
        full_name = serializer.validated_data.get("full_name", "")

        try:
            User.objects.get(email=email)
            # Account exists: alert the real owner but reveal nothing to the caller.
            _send_registration_conflict_email(email)
        except User.DoesNotExist:
            user = User.objects.create_user(
                email=email, password=password, full_name=full_name
            )
            audit.record(action="auth.registered", actor=user)
            _send_verification_email(user)

        return Response(
            {"detail": "If that address is new, we've sent a verification email."},
            status=status.HTTP_202_ACCEPTED,
        )


def _send_verification_email(user) -> None:
    from django.core.mail import send_mail

    try:
        send_mail(
            subject="Verify your AuthentiCheck email address",
            message=(
                f"Hi {user.full_name or user.email},\n\n"
                "Please verify your email address to activate your account.\n\n"
                "(Email verification link coming in Phase 4.)"
            ),
            from_email=None,
            recipient_list=[user.email],
            fail_silently=True,
        )
    except Exception:
        pass


def _send_registration_conflict_email(email: str) -> None:
    from django.core.mail import send_mail

    try:
        send_mail(
            subject="Someone tried to register with your AuthentiCheck email",
            message=(
                "Someone tried to create an AuthentiCheck account using your email address.\n\n"
                "If this was you, please log in instead. If not, you can ignore this message."
            ),
            from_email=None,
            recipient_list=[email],
            fail_silently=True,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


@extend_schema(
    request=LoginSerializer,
    responses={
        200: inline_serializer(
            "LoginResponse",
            fields={
                "access_token": s.CharField(required=False),
                "token_type": s.CharField(required=False),
                "user": s.DictField(required=False),
                "memberships": s.ListField(child=s.DictField(), required=False),
                "mfa_required": s.BooleanField(required=False),
                "mfa_enrollment_required": s.BooleanField(required=False),
                "challenge_token": s.CharField(required=False),
            },
        )
    },
    tags=["Auth"],
    summary="Authenticate with email and password",
    description=(
        "Regular users receive an access token (JWT) and a refresh token cookie. "
        "Platform admins receive a challenge token and must complete MFA via POST /auth/mfa/verify. "
        "Unknown email, wrong password, and inactive account all return the same 401. [EC-025, EC-026]"
    ),
)
class LoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [LoginIPThrottle, LoginEmailIPThrottle]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"].lower().strip()
        password = serializer.validated_data["password"]

        try:
            user = User.objects.get(email=email)
            password_ok = user.check_password(password)
        except User.DoesNotExist:
            User().check_password(password)  # timing equalization — always hash [EC-025]
            raise LoginFailed()

        if not password_ok or not user.is_active:
            raise LoginFailed()

        if user.is_staff:
            return self._admin_challenge_response(user)

        return self._issue_jwt_response(request, user)

    def _admin_challenge_response(self, user):
        """Platform admins never get tokens from the password step alone."""
        has_device = TOTPDevice.objects.filter(user=user, confirmed=True).exists()
        kind = MFAChallenge.Kind.VERIFY if has_device else MFAChallenge.Kind.ENROLL

        # Invalidate any prior unused challenges for this user+kind
        MFAChallenge.objects.filter(user=user, kind=kind, used_at__isnull=True).update(
            used_at=timezone.now()
        )

        challenge_jwt = token_service.issue_challenge_token(user, kind)
        challenge_hash = token_service.hash_token(challenge_jwt)

        MFAChallenge.objects.create(
            user=user,
            token_hash=challenge_hash,
            kind=kind,
            expires_at=timezone.now() + timedelta(minutes=5),
        )

        body = {"mfa_required": True, "challenge_token": challenge_jwt}
        if kind == MFAChallenge.Kind.ENROLL:
            body["mfa_enrollment_required"] = True

        return Response(body)

    def _issue_jwt_response(self, request, user):
        from apps.tenants.models import Membership
        from apps.tenants.serializers import MembershipSerializer

        session, raw_refresh = session_service.create_session(user, request)

        memberships = list(
            Membership.objects.filter(user=user, status=Membership.Status.ACTIVE)
            .select_related("tenant")
        )

        tid = mid = None
        if len(memberships) == 1:
            m = memberships[0]
            tid = str(m.tenant_id)
            mid = str(m.id)

        access_token = token_service.issue_access_token(
            user, session.id, ["pwd"], time.time(), tid=tid, mid=mid
        )
        audit.record(action="auth.login", actor=user, metadata={"method": "password"})

        body = {
            "access_token": access_token,
            "token_type": "bearer",
            "user": UserSerializer(user).data,
        }
        if len(memberships) > 1:
            body["memberships"] = MembershipSerializer(memberships, many=True).data

        response = Response(body)
        _set_refresh_cookie(response, raw_refresh, session.absolute_expires_at)
        return response


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


@extend_schema(
    request=None,
    responses={204: None},
    tags=["Auth"],
    summary="Revoke the current session and clear the refresh cookie",
)
class LogoutView(APIView):
    permission_classes = [IsAuthenticatedSession]

    def post(self, request):
        _require_requested_with(request)
        auth_context = request.auth_context
        session = auth_context["session"]
        audit.record(action="auth.logout", actor=request.user)
        session_service.revoke_session(session, AuthSession.RevocationReason.LOGOUT)
        response = Response(status=status.HTTP_204_NO_CONTENT)
        _clear_refresh_cookie(response)
        return response


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


@extend_schema(
    request=None,
    responses={
        200: inline_serializer(
            "RefreshResponse", fields={"access_token": s.CharField()}
        )
    },
    tags=["Auth"],
    summary="Rotate the refresh token and issue a new access token",
    description=(
        "Reads the refresh token from the HttpOnly cookie. "
        "Rotation uses reuse-detection: presenting an old token outside the grace window "
        "revokes the entire session (theft assumed). [EC-013]"
    ),
)
class RefreshView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [RefreshThrottle]

    def get_authenticate_header(self, request):
        return "Bearer"

    def post(self, request):
        _require_requested_with(request)

        raw = request.COOKIES.get(settings.REFRESH_COOKIE_NAME)
        if not raw:
            raise AuthenticationFailed(
                {"code": "token_invalid", "message": "No refresh token cookie."}
            )

        token_hash = token_service.hash_token(raw)

        with transaction.atomic():
            # select_for_update ensures exactly one concurrent rotation wins [EC-011]
            session = (
                AuthSession.objects.select_for_update()
                .filter(
                    Q(refresh_hash=token_hash) | Q(previous_refresh_hash=token_hash),
                    revoked_at__isnull=True,
                )
                .select_related("user")
                .first()
            )

            if session is None:
                raise AuthenticationFailed(
                    {"code": "token_invalid", "message": "Invalid or expired refresh token."}
                )

            now = timezone.now()
            if session.idle_expires_at <= now or session.absolute_expires_at <= now:
                raise AuthenticationFailed(
                    {"code": "token_expired", "message": "Session has expired."}
                )

            if token_hash == session.previous_refresh_hash:
                grace = timedelta(seconds=settings.REFRESH_GRACE_SECONDS)
                if session.rotated_at and (now - session.rotated_at) <= grace:
                    # Client retry within grace window — re-issue without revoking [EC-013]
                    logger.info(
                        "refresh_grace_window_reuse",
                        extra={"session_id": str(session.id), "user_id": str(session.user_id)},
                    )
                    new_raw = session_service.rotate_refresh(
                        session, is_admin=session.user.is_staff
                    )
                else:
                    # Token reuse outside grace window → assume theft [EC-013]
                    audit.record(
                        action="auth.refresh_reuse_detected",
                        actor=session.user,
                        metadata={"session_id": str(session.id)},
                    )
                    session_service.revoke_session(
                        session, AuthSession.RevocationReason.REUSE_DETECTED
                    )
                    response = Response(
                        {"error": {"code": "session_revoked", "message": "Session revoked due to token reuse."}},
                        status=status.HTTP_401_UNAUTHORIZED,
                    )
                    _clear_refresh_cookie(response)
                    return response
            else:
                new_raw = session_service.rotate_refresh(session, is_admin=session.user.is_staff)

        user = session.user
        access_token = token_service.issue_access_token(
            user,
            session.id,
            ["pwd"],  # amr preserved in Phase 6 when we store it on the session
            time.time(),
        )

        response = Response({"access_token": access_token})
        _set_refresh_cookie(response, new_raw, session.absolute_expires_at)
        return response


# ---------------------------------------------------------------------------
# Me
# ---------------------------------------------------------------------------


@extend_schema(
    responses={
        200: inline_serializer(
            "MeResponse",
            fields={
                "id": s.UUIDField(),
                "email": s.EmailField(),
                "full_name": s.CharField(),
                "is_staff": s.BooleanField(),
                "mfa_enabled": s.BooleanField(),
                "amr": s.ListField(child=s.CharField()),
                "memberships": s.ListField(child=s.DictField()),
                "active_tenant": s.DictField(required=False),
            },
        )
    },
    tags=["Auth"],
    summary="Retrieve the authenticated user's profile, memberships and auth context",
)
class MeView(APIView):
    permission_classes = [IsAuthenticatedSession]

    def get(self, request):
        from apps.tenants.models import Membership
        from apps.tenants.serializers import MembershipSerializer, TenantSerializer

        user = request.user
        auth_context = request.auth_context

        memberships = Membership.objects.filter(
            user=user, status=Membership.Status.ACTIVE
        ).select_related("tenant")

        data = {
            **UserSerializer(user).data,
            "amr": auth_context.get("amr", []),
            "memberships": MembershipSerializer(memberships, many=True).data,
        }

        if request.active_membership:
            data["active_tenant"] = TenantSerializer(request.active_membership.tenant).data

        return Response(data)


# ---------------------------------------------------------------------------
# MFA setup / confirm (legacy session-based; replaced in Phase 6)
# ---------------------------------------------------------------------------


@extend_schema(
    request=None,
    responses={
        201: inline_serializer(
            "MFASetupResponse",
            fields={"provisioning_uri": s.CharField()},
        )
    },
    tags=["Auth"],
    summary="Begin TOTP enrolment — returns a provisioning URI for the authenticator app",
)
class MFASetupView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        TOTPDevice.objects.filter(user=request.user, confirmed=False).delete()
        device = TOTPDevice.objects.create(user=request.user, name="default", confirmed=False)
        return Response({"provisioning_uri": device.config_url}, status=status.HTTP_201_CREATED)


@extend_schema(
    request=MFATokenSerializer,
    responses={
        200: inline_serializer(
            "MFAConfirmResponse",
            fields={"mfa_enabled": s.BooleanField()},
        )
    },
    tags=["Auth"],
    summary="Confirm TOTP enrolment with a valid token from the authenticator app",
)
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


# ---------------------------------------------------------------------------
# API Keys
# ---------------------------------------------------------------------------


@extend_schema_view(
    get=extend_schema(
        responses={
            200: inline_serializer(
                "APIKeyListItem",
                fields={
                    "id": s.UUIDField(),
                    "name": s.CharField(),
                    "key_prefix": s.CharField(),
                    "last_used_at": s.DateTimeField(allow_null=True),
                    "expires_at": s.DateTimeField(allow_null=True),
                    "revoked_at": s.DateTimeField(allow_null=True),
                    "created_at": s.DateTimeField(),
                },
                many=True,
            )
        },
        tags=["API Keys"],
        summary="List API keys for the active tenant",
    ),
    post=extend_schema(
        request=inline_serializer(
            "APIKeyCreateRequest",
            fields={
                "name": s.CharField(),
                "expires_at": s.DateTimeField(required=False, allow_null=True),
            },
        ),
        responses={
            201: inline_serializer(
                "APIKeyCreateResponse",
                fields={
                    "id": s.UUIDField(),
                    "name": s.CharField(),
                    "key": s.CharField(
                        help_text="Raw API key — shown once, store it securely."
                    ),
                    "key_prefix": s.CharField(),
                    "created_at": s.DateTimeField(),
                },
            )
        },
        tags=["API Keys"],
        summary="Create an API key",
        description=(
            "Returns the raw `key` once. Store it securely — it cannot be retrieved again. "
            "Send it in the `X-API-Key` header on subsequent requests."
        ),
    ),
)
class APIKeyListCreateView(APIView):
    permission_classes = [IsAuthenticated, HasActiveTenantMembership, IsOwnerOrAdmin]

    def get(self, request):
        from apps.identity.models import APIKey
        keys = APIKey.objects.order_by("-created_at")
        data = [
            {
                "id": str(k.pk),
                "name": k.name,
                "key_prefix": k.key_prefix,
                "last_used_at": k.last_used_at,
                "expires_at": k.expires_at,
                "revoked_at": k.revoked_at,
                "created_at": k.created_at,
            }
            for k in keys
        ]
        return Response(data)

    def post(self, request):
        from apps.identity.models import APIKey

        name = request.data.get("name", "").strip()
        if not name:
            raise ValidationError({"name": "This field is required."})
        if len(name) > 100:
            raise ValidationError({"name": "Name must be 100 characters or fewer."})

        expires_at = request.data.get("expires_at")

        api_key, raw_key = APIKey.create_key(
            name=name,
            tenant=request.active_membership.tenant,
            membership=request.active_membership,
            created_by=request.user,
        )
        if expires_at:
            api_key.expires_at = expires_at
            api_key.save(update_fields=["expires_at"])

        audit.record(
            action="apikey.created",
            actor=request.user,
            tenant=request.active_membership.tenant,
            target=api_key,
            after={"name": name, "key_prefix": api_key.key_prefix},
        )
        return Response(
            {
                "id": str(api_key.pk),
                "name": api_key.name,
                "key": raw_key,
                "key_prefix": api_key.key_prefix,
                "created_at": api_key.created_at,
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(
    responses={204: None},
    tags=["API Keys"],
    summary="Revoke an API key",
)
class APIKeyRevokeView(APIView):
    permission_classes = [IsAuthenticated, HasActiveTenantMembership, IsOwnerOrAdmin]

    def delete(self, request, pk):
        from apps.identity.models import APIKey
        from rest_framework.exceptions import NotFound as _NotFound

        try:
            api_key = APIKey.objects.get(pk=pk)
        except APIKey.DoesNotExist:
            raise _NotFound("API key not found.")

        if api_key.revoked_at is not None:
            return Response(status=status.HTTP_204_NO_CONTENT)

        api_key.revoked_at = timezone.now()
        api_key.save(update_fields=["revoked_at", "updated_at"])
        audit.record(
            action="apikey.revoked",
            actor=request.user,
            tenant=request.active_membership.tenant,
            target=api_key,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
