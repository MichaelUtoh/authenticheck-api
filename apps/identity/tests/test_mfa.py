"""Tests for MFA setup/confirm (legacy session-based, replaced in Phase 6)
and for the platform-admin challenge at login."""

import time

import pytest
from django.contrib.auth import get_user_model
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice
from rest_framework.test import APIClient

User = get_user_model()
pytestmark = pytest.mark.django_db


def _current_token(device):
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits)
    totp.time = time.time()
    return f"{totp.token():06d}"  # keep leading zeros


@pytest.fixture
def user(db):
    return User.objects.create_user("mfa@example.com", "a-long-test-password")


@pytest.fixture
def admin_user(db):
    return User.objects.create_user("admin@example.com", "a-long-test-password", is_staff=True)


@pytest.fixture
def authed_client(user):
    """force_authenticate works here because MFASetupView/MFAConfirmView use IsAuthenticated."""
    c = APIClient()
    c.force_authenticate(user=user)
    return c


# ---------------------------------------------------------------------------
# Legacy TOTP setup / confirm (will move to mfa/enroll in Phase 6)
# ---------------------------------------------------------------------------


def test_setup_returns_provisioning_uri(authed_client):
    response = authed_client.post("/api/v1/auth/mfa/setup")
    assert response.status_code == 201
    assert response.data["provisioning_uri"].startswith("otpauth://totp/")


def test_setup_replaces_previous_unconfirmed_device(authed_client, user):
    authed_client.post("/api/v1/auth/mfa/setup")
    authed_client.post("/api/v1/auth/mfa/setup")
    assert TOTPDevice.objects.filter(user=user, confirmed=False).count() == 1


def test_confirm_rejects_bad_token(authed_client):
    authed_client.post("/api/v1/auth/mfa/setup")
    response = authed_client.post("/api/v1/auth/mfa/confirm", {"token": "000000"})
    assert response.status_code == 400


def test_confirm_requires_setup_first(authed_client):
    response = authed_client.post("/api/v1/auth/mfa/confirm", {"token": "123456"})
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Login behaviour for platform admins vs regular users
# ---------------------------------------------------------------------------


def test_regular_user_with_totp_device_gets_full_tokens(user):
    """MFA is only required for platform admins in the MVP. Regular users are not gated."""
    TOTPDevice.objects.create(user=user, name="default", confirmed=True)
    response = APIClient().post(
        "/api/v1/auth/login", {"email": "mfa@example.com", "password": "a-long-test-password"}
    )
    assert response.status_code == 200
    assert "access_token" in response.data
    assert "mfa_required" not in response.data


def test_platform_admin_with_device_gets_mfa_challenge(admin_user):
    TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)
    response = APIClient().post(
        "/api/v1/auth/login", {"email": "admin@example.com", "password": "a-long-test-password"}
    )
    assert response.status_code == 200
    assert response.data["mfa_required"] is True
    assert "challenge_token" in response.data
    assert response.data.get("mfa_enrollment_required") is not True


def test_platform_admin_without_device_gets_enrollment_challenge(admin_user):
    response = APIClient().post(
        "/api/v1/auth/login", {"email": "admin@example.com", "password": "a-long-test-password"}
    )
    assert response.status_code == 200
    assert response.data["mfa_required"] is True
    assert response.data["mfa_enrollment_required"] is True
    assert "challenge_token" in response.data


def test_challenge_token_is_stored_as_hash(admin_user):
    """The raw challenge JWT must not be stored in the DB."""
    from apps.identity.models import MFAChallenge

    response = APIClient().post(
        "/api/v1/auth/login", {"email": "admin@example.com", "password": "a-long-test-password"}
    )
    raw_challenge = response.data["challenge_token"]
    challenge = MFAChallenge.objects.get(user=admin_user)
    # The stored hash must not equal the raw token
    assert challenge.token_hash != raw_challenge
    assert len(challenge.token_hash) == 64  # SHA-256 hex


def test_repeated_login_invalidates_prior_challenge(admin_user):
    """Each new login creates a fresh challenge; old ones are expired."""
    from apps.identity.models import MFAChallenge

    APIClient().post(
        "/api/v1/auth/login", {"email": "admin@example.com", "password": "a-long-test-password"}
    )
    APIClient().post(
        "/api/v1/auth/login", {"email": "admin@example.com", "password": "a-long-test-password"}
    )
    active = MFAChallenge.objects.filter(user=admin_user, used_at__isnull=True)
    assert active.count() == 1


# ---------------------------------------------------------------------------
# Platform staff MFA: enroll -> enroll/confirm -> verify
# ---------------------------------------------------------------------------

LOGIN = {"email": "admin@example.com", "password": "a-long-test-password"}
XHR = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


def _claims(access_token):
    import jwt
    from django.conf import settings

    return jwt.decode(
        access_token,
        settings.JWT_SIGNING_KEY,
        algorithms=["HS256"],
        audience=settings.JWT_AUDIENCE,
        issuer=settings.JWT_ISSUER,
        options={"verify_exp": False},
    )


def _login(client=None):
    client = client or APIClient()
    return client, client.post("/api/v1/auth/login", LOGIN).data["challenge_token"]


def _enrolled(admin_user, client):
    """Run the full first-time flow; returns the confirmed device."""
    _, challenge = _login(client)
    client.post("/api/v1/auth/mfa/enroll", {"challenge_token": challenge})
    device = TOTPDevice.objects.get(user=admin_user)
    response = client.post(
        "/api/v1/auth/mfa/enroll/confirm",
        {"challenge_token": challenge, "token": _current_token(device)},
    )
    assert response.status_code == 200, response.data
    return device


def test_enroll_returns_uri_and_secret(admin_user):
    client, challenge = _login()
    response = client.post("/api/v1/auth/mfa/enroll", {"challenge_token": challenge})
    assert response.status_code == 201
    assert response.data["provisioning_uri"].startswith("otpauth://totp/")
    assert response.data["secret"]
    assert TOTPDevice.objects.filter(user=admin_user, confirmed=False).count() == 1


def test_enroll_confirm_issues_session_with_otp(admin_user):
    client = APIClient()
    _, challenge = _login(client)
    client.post("/api/v1/auth/mfa/enroll", {"challenge_token": challenge})
    device = TOTPDevice.objects.get(user=admin_user)

    response = client.post(
        "/api/v1/auth/mfa/enroll/confirm",
        {"challenge_token": challenge, "token": _current_token(device)},
    )
    assert response.status_code == 200
    assert _claims(response.data["access_token"])["amr"] == ["pwd", "otp"]
    assert "refresh_token" in response.cookies
    device.refresh_from_db()
    assert device.confirmed is True


def test_enroll_confirm_wrong_code_rejected(admin_user):
    client, challenge = _login()
    client.post("/api/v1/auth/mfa/enroll", {"challenge_token": challenge})
    response = client.post(
        "/api/v1/auth/mfa/enroll/confirm", {"challenge_token": challenge, "token": "000000"}
    )
    assert response.status_code == 401
    assert response.data["error"]["code"] == "invalid_mfa_code"
    assert TOTPDevice.objects.get(user=admin_user).confirmed is False


def test_enroll_confirm_before_enroll_is_409(admin_user):
    client, challenge = _login()
    response = client.post(
        "/api/v1/auth/mfa/enroll/confirm", {"challenge_token": challenge, "token": "123456"}
    )
    assert response.status_code == 409


def test_challenge_is_single_use(admin_user):
    client = APIClient()
    _, challenge = _login(client)
    client.post("/api/v1/auth/mfa/enroll", {"challenge_token": challenge})
    device = TOTPDevice.objects.get(user=admin_user)
    body = {"challenge_token": challenge, "token": _current_token(device)}
    assert client.post("/api/v1/auth/mfa/enroll/confirm", body).status_code == 200
    again = client.post("/api/v1/auth/mfa/enroll/confirm", body)
    assert again.status_code == 401
    assert again.data["error"]["code"] == "challenge_invalid"


def test_verify_issues_session_for_enrolled_staff(admin_user):
    device = _enrolled(admin_user, APIClient())
    # a fresh login now yields a verify challenge; the same-step code is replay-protected,
    # so move the device's last used step back.
    device.last_t = -1
    device.save(update_fields=["last_t"])

    client, challenge = _login()
    response = client.post(
        "/api/v1/auth/mfa/verify", {"challenge_token": challenge, "token": _current_token(device)}
    )
    assert response.status_code == 200, response.data
    assert _claims(response.data["access_token"])["amr"] == ["pwd", "otp"]


def test_verify_rejects_enroll_challenge_and_vice_versa(admin_user):
    client, enroll_challenge = _login()
    response = client.post(
        "/api/v1/auth/mfa/verify", {"challenge_token": enroll_challenge, "token": "123456"}
    )
    assert response.status_code == 401
    assert response.data["error"]["code"] == "challenge_invalid"


def test_verify_wrong_code_rejected_and_challenge_burns_after_five(admin_user):
    _enrolled(admin_user, APIClient())
    client, challenge = _login()
    body = {"challenge_token": challenge, "token": "000000"}
    for _ in range(4):
        response = client.post("/api/v1/auth/mfa/verify", body)
        assert response.data["error"]["code"] == "invalid_mfa_code"
    last = client.post("/api/v1/auth/mfa/verify", body)
    assert last.data["error"]["code"] == "challenge_invalid"


def test_garbage_challenge_token_rejected():
    response = APIClient().post(
        "/api/v1/auth/mfa/verify", {"challenge_token": "nope", "token": "123456"}
    )
    assert response.status_code == 401


def test_challenge_token_is_not_an_access_token(admin_user):
    client, challenge = _login()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {challenge}")
    assert client.get("/api/v1/auth/me").status_code in (401, 403)


def test_staff_refresh_keeps_otp(admin_user):
    client = APIClient()
    _enrolled(admin_user, client)
    # the enroll/confirm response set the refresh cookie on this client
    refreshed = client.post("/api/v1/auth/refresh", **XHR)
    assert refreshed.status_code == 200
    assert _claims(refreshed.data["access_token"])["amr"] == ["pwd", "otp"]


def test_staff_can_reach_platform_endpoint_after_mfa(admin_user):
    client = APIClient()
    _, challenge = _login(client)
    client.post("/api/v1/auth/mfa/enroll", {"challenge_token": challenge})
    device = TOTPDevice.objects.get(user=admin_user)
    token = client.post(
        "/api/v1/auth/mfa/enroll/confirm",
        {"challenge_token": challenge, "token": _current_token(device)},
    ).data["access_token"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    assert client.get("/api/v1/platform/products").status_code == 200
