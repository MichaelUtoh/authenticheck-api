"""JWT and opaque token issuance / verification.

Access tokens: short-lived JWT with HS256.
Refresh tokens: opaque secrets.token_urlsafe(48), stored as SHA-256 hash.
Challenge tokens: single-use JWT scoped to the MFA audience only.
"""

import hashlib
import secrets
import time
import uuid

import jwt
from django.conf import settings


def _signing_key() -> str:
    return settings.JWT_SIGNING_KEY


def _all_keys() -> list[str]:
    """Current key first, then previous keys for rotation verification."""
    return [settings.JWT_SIGNING_KEY, *list(settings.JWT_PREVIOUS_KEYS)]


def issue_access_token(
    user,
    session_id,
    amr: list[str],
    auth_time: float,
    tid: str | None = None,
    mid: str | None = None,
) -> str:
    ttl_minutes = (
        settings.ADMIN_ACCESS_TTL_MINUTES if user.is_staff else settings.ACCESS_TTL_MINUTES
    )
    now = int(time.time())
    payload = {
        "sub": str(user.pk),
        "sid": str(session_id),
        "amr": amr,
        "auth_time": int(auth_time),
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + ttl_minutes * 60,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
    }
    if tid is not None:
        payload["tid"] = str(tid)
    if mid is not None:
        payload["mid"] = str(mid)
    return jwt.encode(payload, _signing_key(), algorithm="HS256", headers={"kid": "current"})


def decode_access_token(token: str) -> dict:
    """Decode and fully validate an access token. Raises jwt.PyJWTError on failure.
    Tries each signing key in order to support key rotation."""
    last_exc: Exception | None = None
    for key in _all_keys():
        try:
            return jwt.decode(
                token,
                key,
                algorithms=["HS256"],
                issuer=settings.JWT_ISSUER,
                audience=settings.JWT_AUDIENCE,
                leeway=settings.JWT_LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "sid", "jti"]},
            )
        except jwt.ExpiredSignatureError:
            raise
        except jwt.InvalidSignatureError as exc:
            last_exc = exc
            continue
        except jwt.PyJWTError:
            raise
    raise last_exc or jwt.InvalidSignatureError("No valid signing key")


def issue_challenge_token(user, kind: str) -> str:
    """5-minute single-use token for the MFA enroll/verify step. Audience is distinct
    from the API audience so it cannot be used as an access token."""
    now = int(time.time())
    payload = {
        "sub": str(user.pk),
        "kind": kind,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + 5 * 60,
        "iss": settings.JWT_ISSUER,
        "aud": "authenticheck:mfa",
    }
    return jwt.encode(payload, _signing_key(), algorithm="HS256")


def decode_challenge_token(token: str) -> dict:
    last_exc: Exception | None = None
    for key in _all_keys():
        try:
            return jwt.decode(
                token,
                key,
                algorithms=["HS256"],
                issuer=settings.JWT_ISSUER,
                audience="authenticheck:mfa",
                leeway=settings.JWT_LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "kind", "jti"]},
            )
        except jwt.ExpiredSignatureError:
            raise
        except jwt.InvalidSignatureError as exc:
            last_exc = exc
            continue
        except jwt.PyJWTError:
            raise
    raise last_exc or jwt.InvalidSignatureError("No valid signing key")


def issue_refresh_token() -> tuple[str, str]:
    """Return (raw_token, sha256_hex). Raw token is delivered to the client only;
    only the hash is persisted."""
    raw = secrets.token_urlsafe(48)
    digest = _sha256(raw)
    return raw, digest


def hash_token(raw: str) -> str:
    return _sha256(raw)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
