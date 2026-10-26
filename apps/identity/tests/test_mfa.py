import time

import pytest
from django.contrib.auth import get_user_model
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice
from rest_framework.test import APIClient


def _current_token(device):
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits)
    totp.time = time.time()
    return totp.token()

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def user(db):
    return User.objects.create_user("mfa@example.com", "a-long-test-password")


@pytest.fixture
def authed_client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


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


def test_login_triggers_mfa_step_for_confirmed_device(user):
    TOTPDevice.objects.create(user=user, name="default", confirmed=True)

    client = APIClient()
    response = client.post(
        "/api/v1/auth/login", {"email": "mfa@example.com", "password": "a-long-test-password"}
    )
    assert response.status_code == 200
    assert response.data == {"mfa_required": True}


def test_mfa_verify_completes_login(user):
    device = TOTPDevice.objects.create(user=user, name="default", confirmed=True)

    client = APIClient()
    # Step 1
    client.post(
        "/api/v1/auth/login", {"email": "mfa@example.com", "password": "a-long-test-password"}
    )
    # Step 2
    response = client.post("/api/v1/auth/mfa/verify", {"token": _current_token(device)})
    assert response.status_code == 200
    assert response.data["email"] == user.email
    assert client.session.get("_mfa_verified") is True


def test_mfa_verify_without_pending_session_is_rejected():
    client = APIClient()
    response = client.post("/api/v1/auth/mfa/verify", {"token": "123456"})
    assert response.status_code == 403


def test_mfa_verify_wrong_token_is_rejected(user):
    TOTPDevice.objects.create(user=user, name="default", confirmed=True)

    client = APIClient()
    client.post(
        "/api/v1/auth/login", {"email": "mfa@example.com", "password": "a-long-test-password"}
    )
    response = client.post("/api/v1/auth/mfa/verify", {"token": "000000"})
    assert response.status_code == 401
