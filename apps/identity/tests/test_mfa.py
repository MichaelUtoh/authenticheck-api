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
    return totp.token()


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
