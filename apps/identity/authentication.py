"""DRF authentication class for JWT Bearer tokens.

On every authenticated request:
1. Decode and validate the JWT (sig, exp, iat, iss, aud; rejects alg:none).
2. Confirm the session (sid) is live and not past idle/absolute expiry.
3. Confirm the user is active.
4. If tid is present, confirm the membership is active and the tenant is approved.
5. Attach request.session_id, request.active_membership, request.auth_context.

Session and user lookups are cached in Redis for 30 s. Cache entries are explicitly
deleted on revocation, deactivation, membership change, and tenant status change."""

import logging
import uuid

import jwt
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from apps.identity.services import session_service, token_service

logger = logging.getLogger(__name__)
User = get_user_model()

_USER_CACHE_TTL = 30
_MISS = "__miss__"


class JWTAuthentication(BaseAuthentication):
    def authenticate(self, request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header.startswith("Bearer "):
            return None

        raw_token = header[7:].strip()
        if not raw_token:
            return None

        try:
            claims = token_service.decode_access_token(raw_token)
        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed({"code": "token_expired", "message": "Token has expired."})
        except jwt.PyJWTError:
            raise AuthenticationFailed({"code": "token_invalid", "message": "Token is invalid."})

        session = session_service.get_active_session(claims["sid"])
        if session is None:
            raise AuthenticationFailed(
                {"code": "session_revoked", "message": "Session has been revoked or expired."}
            )

        user = _get_user(claims["sub"])
        if user is None or not user.is_active:
            raise AuthenticationFailed(
                {"code": "token_invalid", "message": "User account is inactive or not found."}
            )

        active_membership = None
        tid = claims.get("tid")
        mid = claims.get("mid")
        if tid:
            active_membership = _resolve_membership(user, tid, request)

        _touch_session(session)

        auth_context = {
            "session_id": claims["sid"],
            "session": session,
            "amr": claims.get("amr", []),
            "auth_time": claims.get("auth_time", 0),
            "tid": tid,
            "mid": mid,
        }
        request.session_id = claims["sid"]
        request.active_membership = active_membership
        request.auth_context = auth_context

        return user, auth_context

    def authenticate_header(self, request):
        return "Bearer"


def _get_user(user_id: str):
    cache_key = f"auth:user:{user_id}"
    cached = cache.get(cache_key)
    if cached == _MISS:
        return None
    if cached is not None:
        return cached
    try:
        user = User.objects.get(pk=user_id)
        cache.set(cache_key, user, _USER_CACHE_TTL)
        return user
    except User.DoesNotExist:
        cache.set(cache_key, _MISS, _USER_CACHE_TTL)
        return None


def _resolve_membership(user, tid: str, request):
    from apps.core.tenancy import _current_tenant_id
    from apps.tenants.models import Membership, Tenant

    cache_key = f"auth:membership:{user.pk}:{tid}"
    cached = cache.get(cache_key)

    if cached == _MISS:
        raise AuthenticationFailed(
            {"code": "membership_inactive", "message": "Membership is not active."}
        )
    if cached is not None:
        membership = cached
    else:
        membership = (
            Membership.objects.filter(
                user=user, tenant_id=tid, status=Membership.Status.ACTIVE
            )
            .select_related("tenant")
            .first()
        )
        if membership is None:
            cache.set(cache_key, _MISS, _USER_CACHE_TTL)
            raise AuthenticationFailed(
                {"code": "membership_inactive", "message": "Membership is not active."}
            )
        cache.set(cache_key, membership, _USER_CACHE_TTL)

    if membership.tenant.status != Tenant.Status.APPROVED:
        raise AuthenticationFailed(
            {"code": "tenant_suspended", "message": "Tenant is not in an active state."}
        )

    try:
        tid_uuid = uuid.UUID(tid)
    except ValueError:
        raise AuthenticationFailed({"code": "token_invalid", "message": "Token is invalid."})

    _current_tenant_id.set(tid_uuid)
    request.active_tenant_id = tid_uuid
    return membership


def _touch_session(session) -> None:
    """Best-effort last_used_at update — not critical to the request."""
    from django.utils import timezone

    try:
        AuthSession = session.__class__
        AuthSession.objects.filter(pk=session.pk).update(last_used_at=timezone.now())
    except Exception:
        pass
