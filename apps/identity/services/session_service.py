"""Session lifecycle: creation, refresh rotation, revocation, cache management.

Sessions are tracked in AuthSession. Refresh tokens are opaque; only their SHA-256
hash is stored. Per-request lookups are cached in Redis with a 30-second TTL;
cache entries are explicitly deleted on any revocation or status change."""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from apps.identity.models import AuthSession
from apps.identity.services.token_service import issue_refresh_token

logger = logging.getLogger(__name__)

_CACHE_TTL = 30  # seconds — fallback only; explicit invalidation on revoke
_SESSION_KEY = "auth:session:{sid}"
_USER_KEY = "auth:user:{uid}"
_MEMBERSHIP_KEY = "auth:membership:{uid}:{tid}"
_MISS = "__miss__"


def _sk(sid) -> str:
    return _SESSION_KEY.format(sid=sid)


def _uk(uid) -> str:
    return _USER_KEY.format(uid=uid)


def _mk(uid, tid) -> str:
    return _MEMBERSHIP_KEY.format(uid=uid, tid=tid)


def create_session(user, request, is_admin: bool = False) -> tuple[AuthSession, str]:
    """Create a new AuthSession and return (session, raw_refresh_token)."""
    raw, refresh_hash = issue_refresh_token()
    now = timezone.now()

    if is_admin:
        idle_delta = timedelta(minutes=settings.ADMIN_REFRESH_IDLE_MINUTES)
        absolute_delta = timedelta(hours=settings.ADMIN_REFRESH_ABSOLUTE_HOURS)
    else:
        idle_delta = timedelta(days=settings.REFRESH_IDLE_DAYS)
        absolute_delta = timedelta(days=settings.REFRESH_ABSOLUTE_DAYS)

    session = AuthSession.objects.create(
        user=user,
        refresh_hash=refresh_hash,
        last_used_at=now,
        idle_expires_at=now + idle_delta,
        absolute_expires_at=now + absolute_delta,
        user_agent=request.headers.get("User-Agent", "")[:200],
        ip_prefix=_ip_prefix(request),
    )
    return session, raw


def resolve_session_membership(session: AuthSession):
    """Membership whose tenant a refreshed access token should carry (`tid`/`mid`).

    Uses the session's stored active membership while it is still active and its tenant
    approved; otherwise falls back to the user's first active membership in an approved
    tenant, and stores that choice. Returns None when the user has no usable membership."""
    from apps.tenants.models import Membership, Tenant

    usable = Membership.objects.filter(
        user_id=session.user_id,
        status=Membership.Status.ACTIVE,
        tenant__status=Tenant.Status.APPROVED,
    ).select_related("tenant")

    membership = None
    if session.active_membership_id:
        membership = usable.filter(pk=session.active_membership_id).first()
    if membership is None:
        membership = usable.order_by("created_at").first()
    if membership is not None and membership.pk != session.active_membership_id:
        session.active_membership = membership
        session.save(update_fields=["active_membership", "updated_at"])
    return membership


def rotate_refresh(session: AuthSession, is_admin: bool = False) -> str:
    """Issue a new refresh token, archive the old hash for grace-window reuse detection.
    Returns the raw new refresh token. Must be called inside select_for_update()."""
    raw, new_hash = issue_refresh_token()
    now = timezone.now()

    if is_admin:
        idle_delta = timedelta(minutes=settings.ADMIN_REFRESH_IDLE_MINUTES)
    else:
        idle_delta = timedelta(days=settings.REFRESH_IDLE_DAYS)

    session.previous_refresh_hash = session.refresh_hash
    session.refresh_hash = new_hash
    session.rotated_at = now
    session.last_used_at = now
    session.idle_expires_at = now + idle_delta
    session.save(
        update_fields=[
            "refresh_hash",
            "previous_refresh_hash",
            "rotated_at",
            "last_used_at",
            "idle_expires_at",
        ]
    )
    cache.delete(_sk(str(session.id)))
    return raw


def get_active_session(sid: str) -> AuthSession | None:
    """Fetch a non-revoked, non-expired session. Cached for 30 s."""
    cache_key = _sk(sid)
    cached = cache.get(cache_key)
    if cached == _MISS:
        return None
    if cached is not None:
        return cached

    try:
        session = (
            AuthSession.objects.select_related("user").get(pk=sid, revoked_at__isnull=True)
        )
    except AuthSession.DoesNotExist:
        cache.set(cache_key, _MISS, _CACHE_TTL)
        return None

    now = timezone.now()
    if session.idle_expires_at <= now or session.absolute_expires_at <= now:
        cache.set(cache_key, _MISS, _CACHE_TTL)
        return None

    cache.set(cache_key, session, _CACHE_TTL)
    return session


def revoke_session(session: AuthSession, reason: str) -> None:
    session.revoked_at = timezone.now()
    session.revoked_reason = reason
    session.save(update_fields=["revoked_at", "revoked_reason"])
    cache.delete(_sk(str(session.id)))


def revoke_all_sessions(user, reason: str, exclude_sid: str | None = None) -> int:
    """Revoke all active sessions for a user. Returns count revoked."""
    now = timezone.now()
    qs = AuthSession.objects.filter(user=user, revoked_at__isnull=True)
    if exclude_sid:
        qs = qs.exclude(pk=exclude_sid)
    ids = list(qs.values_list("id", flat=True))
    qs.update(revoked_at=now, revoked_reason=reason)
    cache.delete_many([_sk(str(sid)) for sid in ids])
    return len(ids)


def invalidate_user_cache(user_id) -> None:
    cache.delete(_uk(str(user_id)))


def invalidate_membership_cache(user_id, tenant_id) -> None:
    cache.delete(_mk(str(user_id), str(tenant_id)))


def _ip_prefix(request) -> str:
    """Store only a coarse IP prefix for privacy. Honours X-Forwarded-For from trusted proxies."""
    trusted = getattr(settings, "TRUSTED_PROXY_COUNT", 0)
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if trusted and xff:
        parts = [p.strip() for p in xff.split(",")]
        raw = parts[max(0, len(parts) - trusted)]
    else:
        raw = request.META.get("REMOTE_ADDR", "")

    if ":" in raw:
        segments = raw.split(":")
        return ":".join(segments[:3]) + "::/48"
    octets = raw.split(".")
    if len(octets) >= 2:
        return f"{octets[0]}.{octets[1]}.0.0/16"
    return ""
