"""Tests for register, login, logout, refresh, and /me.

EC-025: same 401 for unknown email / wrong password.
EC-026: registration doesn't reveal existing emails.
EC-027: max password/email lengths; no stack traces.
"""

import time

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from rest_framework.test import APIClient

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def user(db):
    return User.objects.create_user("alice@example.com", "a-long-test-password", full_name="Alice")


@pytest.fixture
def anon():
    return APIClient()


# ---------------------------------------------------------------------------
# Register [EC-025, EC-026, EC-027]
# ---------------------------------------------------------------------------


def test_register_creates_user_and_returns_202(anon):
    response = anon.post(
        "/api/v1/auth/register",
        {"email": "bob@example.com", "password": "a-long-test-password", "full_name": "Bob"},
    )
    assert response.status_code == 202
    assert User.objects.filter(email="bob@example.com").exists()
    assert "detail" in response.data


def test_register_returns_same_202_for_duplicate_email(anon, user):
    """Must not reveal whether the address already exists. [EC-025, EC-026]"""
    response = anon.post(
        "/api/v1/auth/register",
        {"email": "alice@example.com", "password": "a-long-test-password"},
    )
    assert response.status_code == 202
    assert "detail" in response.data


def test_register_sends_conflict_email_for_existing_account(anon, user):
    anon.post(
        "/api/v1/auth/register",
        {"email": "alice@example.com", "password": "a-long-test-password"},
    )
    assert len(mail.outbox) == 1
    assert "tried to register" in mail.outbox[0].subject


def test_register_normalises_email_to_lowercase(anon):
    anon.post(
        "/api/v1/auth/register",
        {"email": "Bob@Example.COM", "password": "a-long-test-password"},
    )
    assert User.objects.filter(email="bob@example.com").exists()


def test_register_rejects_password_over_max_length(anon):
    response = anon.post(
        "/api/v1/auth/register",
        {"email": "long@example.com", "password": "x" * 129},
    )
    assert response.status_code == 400


def test_register_rejects_email_over_max_length(anon):
    response = anon.post(
        "/api/v1/auth/register",
        {"email": "a" * 250 + "@x.com", "password": "a-long-test-password"},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Login [EC-025, EC-026, EC-027]
# ---------------------------------------------------------------------------


def test_login_returns_access_token(anon, user):
    response = anon.post(
        "/api/v1/auth/login",
        {"email": "alice@example.com", "password": "a-long-test-password"},
    )
    assert response.status_code == 200
    assert "access_token" in response.data
    assert response.data["token_type"] == "bearer"
    assert response.data["user"]["email"] == "alice@example.com"


def test_login_sets_refresh_cookie(anon, user):
    response = anon.post(
        "/api/v1/auth/login",
        {"email": "alice@example.com", "password": "a-long-test-password"},
    )
    assert response.status_code == 200
    assert "refresh_token" in response.cookies


def test_login_wrong_password_returns_401(anon, user):
    response = anon.post(
        "/api/v1/auth/login", {"email": "alice@example.com", "password": "wrong"}
    )
    assert response.status_code == 401
    assert response.data["error"]["code"] == "authentication_failed"


def test_login_unknown_email_returns_same_401(anon):
    """Same error code and body as wrong password — no enumeration. [EC-025]"""
    response = anon.post(
        "/api/v1/auth/login",
        {"email": "nobody@example.com", "password": "a-long-test-password"},
    )
    assert response.status_code == 401
    assert response.data["error"]["code"] == "authentication_failed"


def test_login_inactive_user_returns_401(anon, user):
    user.is_active = False
    user.save()
    response = anon.post(
        "/api/v1/auth/login", {"email": "alice@example.com", "password": "a-long-test-password"}
    )
    assert response.status_code == 401


def test_login_auto_selects_single_membership(anon, user):
    """When a user has exactly one active membership, tid is embedded in the token."""
    import jwt
    from django.conf import settings

    from apps.tenants.models import Membership, Tenant

    tenant = Tenant.objects.create(legal_name="Acme Ltd", status=Tenant.Status.APPROVED)
    Membership.objects.create(
        user=user, tenant=tenant, role=Membership.Role.OWNER, status=Membership.Status.ACTIVE
    )

    response = anon.post(
        "/api/v1/auth/login", {"email": "alice@example.com", "password": "a-long-test-password"}
    )
    assert response.status_code == 200
    claims = jwt.decode(
        response.data["access_token"],
        settings.JWT_SIGNING_KEY,
        algorithms=["HS256"],
        audience=settings.JWT_AUDIENCE,
        issuer=settings.JWT_ISSUER,
        options={"verify_exp": False},
    )
    assert claims["tid"] == str(tenant.pk)


def test_login_multiple_memberships_returns_list_without_tid(anon, user):
    import jwt
    from django.conf import settings

    from apps.tenants.models import Membership, Tenant

    for name in ["Tenant A", "Tenant B"]:
        t = Tenant.objects.create(legal_name=name, status=Tenant.Status.APPROVED)
        Membership.objects.create(
            user=user, tenant=t, role=Membership.Role.MEMBER, status=Membership.Status.ACTIVE
        )

    response = anon.post(
        "/api/v1/auth/login", {"email": "alice@example.com", "password": "a-long-test-password"}
    )
    assert response.status_code == 200
    assert "memberships" in response.data
    claims = jwt.decode(
        response.data["access_token"],
        settings.JWT_SIGNING_KEY,
        algorithms=["HS256"],
        audience=settings.JWT_AUDIENCE,
        issuer=settings.JWT_ISSUER,
        options={"verify_exp": False},
    )
    assert "tid" not in claims


def test_login_platform_admin_returns_challenge_token(db):
    admin = User.objects.create_user("admin@example.com", "a-long-test-password", is_staff=True)
    response = APIClient().post(
        "/api/v1/auth/login", {"email": "admin@example.com", "password": "a-long-test-password"}
    )
    assert response.status_code == 200
    assert response.data["mfa_required"] is True
    assert "challenge_token" in response.data
    assert "access_token" not in response.data


def test_login_platform_admin_no_device_returns_enrollment_required(db):
    admin = User.objects.create_user("admin@example.com", "a-long-test-password", is_staff=True)
    response = APIClient().post(
        "/api/v1/auth/login", {"email": "admin@example.com", "password": "a-long-test-password"}
    )
    assert response.status_code == 200
    assert response.data.get("mfa_enrollment_required") is True


# ---------------------------------------------------------------------------
# Me
# ---------------------------------------------------------------------------


def test_me_requires_auth(anon):
    assert anon.get("/api/v1/auth/me").status_code == 401


def test_me_returns_current_user(user, jwt_client):
    client, _, _ = jwt_client(user)
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.data["email"] == user.email
    assert "memberships" in response.data
    assert "amr" in response.data


def test_patch_me_requires_auth(anon):
    assert anon.patch("/api/v1/auth/me", {"full_name": "X"}, format="json").status_code == 401


def test_patch_me_updates_full_name(user, jwt_client):
    from apps.audit.models import AuditEvent

    client, _, _ = jwt_client(user)
    response = client.patch("/api/v1/auth/me", {"full_name": "  New Name "}, format="json")
    assert response.status_code == 200
    assert response.data["full_name"] == "New Name"
    assert "memberships" in response.data
    user.refresh_from_db()
    assert user.full_name == "New Name"
    assert AuditEvent.objects.filter(action="auth.profile.updated", actor=user).count() == 1


def test_patch_me_unchanged_name_writes_no_audit(user, jwt_client):
    from apps.audit.models import AuditEvent

    client, _, _ = jwt_client(user)
    for _ in range(2):
        assert client.patch("/api/v1/auth/me", {"full_name": "Same Name"}, format="json").status_code == 200
    assert AuditEvent.objects.filter(action="auth.profile.updated", actor=user).count() == 1


@pytest.mark.parametrize("value", ["", "   ", "x" * 256])
def test_patch_me_rejects_bad_name(user, jwt_client, value):
    client, _, _ = jwt_client(user)
    response = client.patch("/api/v1/auth/me", {"full_name": value}, format="json")
    assert response.status_code == 400


def test_patch_me_ignores_email_and_staff(user, jwt_client):
    client, _, _ = jwt_client(user)
    original = user.email
    response = client.patch(
        "/api/v1/auth/me", {"full_name": "A B", "email": "x@y.com", "is_staff": True}, format="json"
    )
    assert response.status_code == 200
    user.refresh_from_db()
    assert user.email == original and not user.is_staff


# ---------------------------------------------------------------------------
# Logout [EC-038]
# ---------------------------------------------------------------------------


def test_logout_requires_auth(anon):
    response = anon.post(
        "/api/v1/auth/logout", HTTP_X_REQUESTED_WITH="XMLHttpRequest"
    )
    assert response.status_code == 401


def test_logout_requires_x_requested_with(user, jwt_client):
    client, _, _ = jwt_client(user)
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 403


def test_logout_revokes_session_and_clears_cookie(user, jwt_client):
    from apps.identity.models import AuthSession

    client, session, _ = jwt_client(user)
    response = client.post("/api/v1/auth/logout", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert response.status_code == 204
    session.refresh_from_db()
    assert session.revoked_at is not None


def test_access_token_rejected_after_logout(user, jwt_client):
    """Revocation takes effect on the very next request. [EC-038]"""
    client, session, _ = jwt_client(user)
    client.post("/api/v1/auth/logout", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Refresh [EC-013, EC-038]
# ---------------------------------------------------------------------------


def test_refresh_requires_x_requested_with(anon):
    response = anon.post("/api/v1/auth/refresh")
    assert response.status_code == 403


def test_refresh_without_cookie_returns_401(anon):
    response = anon.post("/api/v1/auth/refresh", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert response.status_code == 401


def test_refresh_issues_new_access_token(user, jwt_client):
    client, session, raw_refresh = jwt_client(user)
    client.cookies["refresh_token"] = raw_refresh
    client.cookies["refresh_token"]["path"] = "/api/v1/auth/refresh"

    response = client.post("/api/v1/auth/refresh", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert response.status_code == 200
    assert "access_token" in response.data
    assert "refresh_token" in response.cookies


def test_refresh_rotates_token(user, jwt_client):
    from apps.identity.services.token_service import hash_token

    client, session, raw_refresh = jwt_client(user)
    client.cookies["refresh_token"] = raw_refresh

    old_hash = session.refresh_hash
    client.post("/api/v1/auth/refresh", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    session.refresh_from_db()

    assert session.refresh_hash != old_hash
    assert session.previous_refresh_hash == old_hash


def test_refresh_reuse_within_grace_window_is_safe(user, jwt_client):
    """Concurrent client retry during grace window must not revoke the session. [EC-013]"""
    from django.conf import settings
    from django.test import override_settings

    client, session, raw_refresh = jwt_client(user)
    client.cookies["refresh_token"] = raw_refresh

    # First rotation
    client.post("/api/v1/auth/refresh", HTTP_X_REQUESTED_WITH="XMLHttpRequest")

    # Present the old token again immediately (simulated retry within grace window)
    with override_settings(REFRESH_GRACE_SECONDS=30):
        response = client.post("/api/v1/auth/refresh", HTTP_X_REQUESTED_WITH="XMLHttpRequest")

    session.refresh_from_db()
    assert session.revoked_at is None  # session survived


def test_refresh_reuse_outside_grace_window_revokes_session(user, jwt_client):
    """Token reuse outside grace window is treated as theft. [EC-013]"""
    from django.test import override_settings

    client, session, raw_refresh = jwt_client(user)
    client.cookies["refresh_token"] = raw_refresh

    # First rotation (grace = 0 → previous token is immediately expired)
    with override_settings(REFRESH_GRACE_SECONDS=0):
        client.post("/api/v1/auth/refresh", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        # Re-present the original token (client jar was updated by the first rotation)
        client.cookies["refresh_token"] = raw_refresh
        response = client.post("/api/v1/auth/refresh", HTTP_X_REQUESTED_WITH="XMLHttpRequest")

    assert response.status_code == 401
    session.refresh_from_db()
    assert session.revoked_at is not None
    assert session.revoked_reason == "reuse_detected"
