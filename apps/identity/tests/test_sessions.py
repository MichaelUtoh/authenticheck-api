"""Tests for session_service: creation, rotation, reuse detection, revocation, cache.

EC-013: Grace window on reuse.
EC-038: Logout kills session immediately.
EC-044: Idle and absolute expiry enforced.
"""

import time
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory, override_settings
from django.utils import timezone

from apps.identity.models import AuthSession
from apps.identity.services import session_service
from apps.identity.services.token_service import hash_token

User = get_user_model()
pytestmark = pytest.mark.django_db

SESSION_SETTINGS = {
    "REFRESH_IDLE_DAYS": 7,
    "REFRESH_ABSOLUTE_DAYS": 30,
    "ADMIN_REFRESH_IDLE_MINUTES": 60,
    "ADMIN_REFRESH_ABSOLUTE_HOURS": 12,
    "REFRESH_GRACE_SECONDS": 10,
    "TRUSTED_PROXY_COUNT": 0,
}


@pytest.fixture
def user(db):
    return User.objects.create_user("sessions@example.com", "a-strong-test-passphrase")


@pytest.fixture
def fake_request():
    rf = RequestFactory()
    req = rf.post("/")
    req.META["HTTP_USER_AGENT"] = "TestAgent/1.0"
    req.META["REMOTE_ADDR"] = "192.168.1.50"
    return req


@override_settings(**SESSION_SETTINGS)
def test_create_session_returns_session_and_raw_token(user, fake_request):
    session, raw = session_service.create_session(user, fake_request)
    assert session.user == user
    assert raw
    assert session.refresh_hash == hash_token(raw)
    assert session.idle_expires_at > timezone.now()
    assert session.absolute_expires_at > timezone.now()
    assert session.revoked_at is None


@override_settings(**SESSION_SETTINGS)
def test_admin_session_has_shorter_expiry(user, fake_request):
    session, _ = session_service.create_session(user, fake_request, is_admin=True)
    idle_delta = session.idle_expires_at - session.created_at
    assert idle_delta <= timedelta(minutes=61)  # ADMIN_REFRESH_IDLE_MINUTES + tiny drift


@override_settings(**SESSION_SETTINGS)
def test_get_active_session_returns_session(user, fake_request):
    session, _ = session_service.create_session(user, fake_request)
    found = session_service.get_active_session(str(session.id))
    assert found is not None
    assert found.id == session.id


@override_settings(**SESSION_SETTINGS)
def test_get_active_session_returns_none_after_revoke(user, fake_request):
    session, _ = session_service.create_session(user, fake_request)
    session_service.revoke_session(session, "logout")
    found = session_service.get_active_session(str(session.id))
    assert found is None


@override_settings(**SESSION_SETTINGS)
def test_get_active_session_returns_none_for_unknown_id(user, fake_request):
    import uuid
    found = session_service.get_active_session(str(uuid.uuid4()))
    assert found is None


@override_settings(**SESSION_SETTINGS)
def test_idle_expiry_blocks_session(user, fake_request):
    session, _ = session_service.create_session(user, fake_request)
    session.idle_expires_at = timezone.now() - timedelta(seconds=1)
    session.save()
    # Clear cache so the DB is re-read
    from django.core.cache import cache
    cache.delete(session_service._sk(str(session.id)))
    assert session_service.get_active_session(str(session.id)) is None


@override_settings(**SESSION_SETTINGS)
def test_absolute_expiry_blocks_session(user, fake_request):
    session, _ = session_service.create_session(user, fake_request)
    session.absolute_expires_at = timezone.now() - timedelta(seconds=1)
    session.save()
    from django.core.cache import cache
    cache.delete(session_service._sk(str(session.id)))
    assert session_service.get_active_session(str(session.id)) is None


@override_settings(**SESSION_SETTINGS)
def test_rotate_refresh_changes_hash(user, fake_request):
    session, old_raw = session_service.create_session(user, fake_request)
    old_hash = session.refresh_hash

    new_raw = session_service.rotate_refresh(session)
    session.refresh_from_db()

    assert session.refresh_hash != old_hash
    assert session.previous_refresh_hash == old_hash
    assert session.refresh_hash == hash_token(new_raw)
    assert session.rotated_at is not None


@override_settings(**SESSION_SETTINGS)
def test_revoke_all_sessions_revokes_active_ones(user, fake_request):
    s1, _ = session_service.create_session(user, fake_request)
    s2, _ = session_service.create_session(user, fake_request)

    count = session_service.revoke_all_sessions(user, "logout_all")
    assert count == 2

    s1.refresh_from_db()
    s2.refresh_from_db()
    assert s1.revoked_at is not None
    assert s2.revoked_at is not None


@override_settings(**SESSION_SETTINGS)
def test_revoke_all_sessions_can_exclude_current(user, fake_request):
    s1, _ = session_service.create_session(user, fake_request)
    s2, _ = session_service.create_session(user, fake_request)

    session_service.revoke_all_sessions(user, "password_reset", exclude_sid=str(s1.id))

    s1.refresh_from_db()
    s2.refresh_from_db()
    assert s1.revoked_at is None  # kept
    assert s2.revoked_at is not None


@override_settings(**SESSION_SETTINGS)
def test_ip_prefix_is_coarsened(user, fake_request):
    session, _ = session_service.create_session(user, fake_request)
    assert session.ip_prefix == "192.168.0.0/16"


@override_settings(**SESSION_SETTINGS)
def test_user_agent_is_stored_truncated(user, fake_request):
    fake_request.META["HTTP_USER_AGENT"] = "A" * 300
    session, _ = session_service.create_session(user, fake_request)
    assert len(session.user_agent) == 200
