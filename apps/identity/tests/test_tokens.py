"""Tests for token_service: JWT issuance, validation, key rotation, edge cases.

EC-085: alg:none rejected, exp/iat/iss/aud verified, 30s leeway.
"""

import time

import jwt
import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings

from apps.identity.services.token_service import (
    decode_access_token,
    decode_challenge_token,
    hash_token,
    issue_access_token,
    issue_challenge_token,
    issue_refresh_token,
)

User = get_user_model()
pytestmark = pytest.mark.django_db

SIGNING_KEY = "test-signing-key-that-is-at-least-32-bytes-long!"
OLD_KEY = "old-signing-key-that-is-at-least-32-bytes-long!!"

JWT_SETTINGS = {
    "JWT_SIGNING_KEY": SIGNING_KEY,
    "JWT_PREVIOUS_KEYS": [],
    "JWT_ISSUER": "authenticheck",
    "JWT_AUDIENCE": "authenticheck:api",
    "JWT_LEEWAY_SECONDS": 30,
    "ACCESS_TTL_MINUTES": 10,
    "ADMIN_ACCESS_TTL_MINUTES": 5,
}


@pytest.fixture
def user(db):
    return User.objects.create_user("token@example.com", "a-strong-test-passphrase")


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(
        "admin@example.com", "a-strong-test-passphrase", is_staff=True
    )


@override_settings(**JWT_SETTINGS)
def test_access_token_round_trips(user):
    token = issue_access_token(user, "session-1", ["pwd"], time.time())
    claims = decode_access_token(token)
    assert claims["sub"] == str(user.pk)
    assert claims["sid"] == "session-1"
    assert claims["amr"] == ["pwd"]
    assert "exp" in claims and "iat" in claims
    assert claims["iss"] == "authenticheck"
    assert claims["aud"] == "authenticheck:api"


@override_settings(**JWT_SETTINGS)
def test_access_token_includes_tenant_claims(user):
    token = issue_access_token(user, "s1", ["pwd"], time.time(), tid="t1", mid="m1")
    claims = decode_access_token(token)
    assert claims["tid"] == "t1"
    assert claims["mid"] == "m1"


@override_settings(**JWT_SETTINGS)
def test_admin_token_has_shorter_ttl(admin_user):
    before = int(time.time())
    token = issue_access_token(admin_user, "s1", ["pwd", "otp"], time.time())
    claims = decode_access_token(token)
    ttl = claims["exp"] - claims["iat"]
    assert ttl == 5 * 60  # ADMIN_ACCESS_TTL_MINUTES


@override_settings(**JWT_SETTINGS)
def test_regular_token_uses_standard_ttl(user):
    token = issue_access_token(user, "s1", ["pwd"], time.time())
    claims = decode_access_token(token)
    ttl = claims["exp"] - claims["iat"]
    assert ttl == 10 * 60  # ACCESS_TTL_MINUTES


@override_settings(**JWT_SETTINGS)
def test_expired_token_raises(user):
    token = issue_access_token(user, "s1", ["pwd"], time.time())
    claims_raw = jwt.decode(token, options={"verify_signature": False})
    expired_payload = {**claims_raw, "exp": int(time.time()) - 3600, "iat": int(time.time()) - 3700}
    tampered = jwt.encode(expired_payload, SIGNING_KEY, algorithm="HS256")
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(tampered)


@override_settings(**JWT_SETTINGS)
def test_tampered_signature_rejected(user):
    token = issue_access_token(user, "s1", ["pwd"], time.time())
    tampered = token[:-4] + "XXXX"
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(tampered)


@override_settings(**JWT_SETTINGS)
def test_wrong_issuer_rejected(user):
    token = issue_access_token(user, "s1", ["pwd"], time.time())
    raw = jwt.decode(token, options={"verify_signature": False})
    forged = jwt.encode({**raw, "iss": "evil"}, SIGNING_KEY, algorithm="HS256")
    with pytest.raises(jwt.InvalidIssuerError):
        decode_access_token(forged)


@override_settings(**JWT_SETTINGS)
def test_wrong_audience_rejected(user):
    token = issue_access_token(user, "s1", ["pwd"], time.time())
    raw = jwt.decode(token, options={"verify_signature": False})
    forged = jwt.encode({**raw, "aud": "wrong:audience"}, SIGNING_KEY, algorithm="HS256")
    with pytest.raises(jwt.InvalidAudienceError):
        decode_access_token(forged)


@override_settings(**JWT_SETTINGS)
def test_alg_none_rejected(user):
    token = issue_access_token(user, "s1", ["pwd"], time.time())
    raw = jwt.decode(token, options={"verify_signature": False})
    none_token = jwt.encode(raw, "", algorithm="none")
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(none_token)


@override_settings(**{**JWT_SETTINGS, "JWT_PREVIOUS_KEYS": [OLD_KEY]})
def test_key_rotation_previous_key_still_validates(user):
    # Token signed with OLD_KEY — should still verify after rotation
    old_token = jwt.encode(
        {
            "sub": str(user.pk),
            "sid": "s1",
            "amr": ["pwd"],
            "auth_time": int(time.time()),
            "jti": "test-jti",
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
            "iss": "authenticheck",
            "aud": "authenticheck:api",
        },
        OLD_KEY,
        algorithm="HS256",
    )
    claims = decode_access_token(old_token)
    assert claims["sub"] == str(user.pk)


@override_settings(**JWT_SETTINGS)
def test_challenge_token_round_trips(user):
    token = issue_challenge_token(user, "verify")
    claims = decode_challenge_token(token)
    assert claims["sub"] == str(user.pk)
    assert claims["kind"] == "verify"
    assert claims["aud"] == "authenticheck:mfa"


@override_settings(**JWT_SETTINGS)
def test_challenge_token_cannot_be_used_as_access_token(user):
    challenge = issue_challenge_token(user, "verify")
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(challenge)


@override_settings(**JWT_SETTINGS)
def test_access_token_cannot_be_used_as_challenge(user):
    access = issue_access_token(user, "s1", ["pwd"], time.time())
    with pytest.raises(jwt.PyJWTError):
        decode_challenge_token(access)


def test_refresh_token_is_urlsafe_and_hashed():
    raw, digest = issue_refresh_token()
    assert len(raw) > 40
    assert len(digest) == 64  # sha256 hex
    assert hash_token(raw) == digest


def test_refresh_tokens_are_unique():
    pairs = [issue_refresh_token() for _ in range(20)]
    raws = [p[0] for p in pairs]
    assert len(set(raws)) == 20
