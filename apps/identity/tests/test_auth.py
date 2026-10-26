import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def user(db):
    return User.objects.create_user("alice@example.com", "a-long-test-password", full_name="Alice")


@pytest.fixture
def client():
    return APIClient()


# --- Registration ---


def test_register_creates_user_and_returns_profile(client):
    response = client.post(
        "/api/v1/auth/register",
        {"email": "bob@example.com", "password": "a-long-test-password", "full_name": "Bob"},
    )
    assert response.status_code == 201
    assert response.data["email"] == "bob@example.com"
    assert User.objects.filter(email="bob@example.com").exists()


def test_register_rejects_duplicate_email(client, user):
    response = client.post(
        "/api/v1/auth/register",
        {"email": "alice@example.com", "password": "a-long-test-password"},
    )
    assert response.status_code == 400


def test_register_normalises_email_to_lowercase(client):
    client.post(
        "/api/v1/auth/register",
        {"email": "Bob@Example.COM", "password": "a-long-test-password"},
    )
    assert User.objects.filter(email="bob@example.com").exists()


# --- Login ---


def test_login_returns_user_data(client, user):
    response = client.post(
        "/api/v1/auth/login", {"email": "alice@example.com", "password": "a-long-test-password"}
    )
    assert response.status_code == 200
    assert response.data["email"] == "alice@example.com"
    assert "mfa_enabled" in response.data


def test_login_wrong_password_returns_401(client, user):
    response = client.post(
        "/api/v1/auth/login", {"email": "alice@example.com", "password": "wrong"}
    )
    assert response.status_code == 401


def test_login_unknown_email_returns_401(client):
    response = client.post(
        "/api/v1/auth/login", {"email": "nobody@example.com", "password": "a-long-test-password"}
    )
    assert response.status_code == 401


# --- Me ---


def test_me_requires_auth(client):
    assert client.get("/api/v1/auth/me").status_code == 403


def test_me_returns_current_user(client, user):
    client.force_authenticate(user=user)
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.data["email"] == user.email
    assert "memberships" in response.data


# --- Logout ---


def test_logout_clears_session(client, user):
    client.force_authenticate(user=user)
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 204
