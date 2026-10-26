import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.tenants.models import Membership, Tenant

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def user(db):
    return User.objects.create_user("owner@example.com", "a-long-test-password")


@pytest.fixture
def staff(db):
    return User.objects.create_user("staff@example.com", "a-long-test-password", is_staff=True)


@pytest.fixture
def staff_client(staff):
    """Staff client with _mfa_verified set — force_authenticate bypasses the login flow so the
    session flag must be injected manually."""
    client = APIClient()
    client.force_authenticate(user=staff)
    session = client.session
    session["_mfa_verified"] = True
    session.save()
    return client


@pytest.fixture
def registered_tenant(user):
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post("/api/v1/tenants/register", {"legal_name": "Acme Cosmetics Ltd"})
    assert response.status_code == 201
    return Tenant.objects.get(pk=response.data["id"])


# --- Registration ---


def test_register_creates_tenant_and_owner_membership(user):
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post("/api/v1/tenants/register", {"legal_name": "Acme Cosmetics Ltd"})
    assert response.status_code == 201
    assert response.data["status"] == Tenant.Status.PENDING
    membership = Membership.objects.get(user=user)
    assert membership.role == Membership.Role.OWNER


def test_register_requires_auth():
    client = APIClient()
    response = client.post("/api/v1/tenants/register", {"legal_name": "Acme"})
    assert response.status_code == 403


# --- Staff status transitions ---


def test_staff_can_approve_pending_tenant(staff_client, registered_tenant):
    response = staff_client.post(
        f"/api/v1/tenants/{registered_tenant.pk}/approve", {"reason": "OK"}
    )
    assert response.status_code == 200
    registered_tenant.refresh_from_db()
    assert registered_tenant.status == Tenant.Status.APPROVED


def test_staff_can_reject_pending_tenant(staff_client, registered_tenant):
    response = staff_client.post(f"/api/v1/tenants/{registered_tenant.pk}/reject", {})
    assert response.status_code == 200
    registered_tenant.refresh_from_db()
    assert registered_tenant.status == Tenant.Status.REJECTED


def test_invalid_transition_is_rejected(staff_client, registered_tenant):
    # Cannot suspend a PENDING tenant
    response = staff_client.post(f"/api/v1/tenants/{registered_tenant.pk}/suspend", {})
    assert response.status_code == 403


def test_non_staff_cannot_approve(user, registered_tenant):
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post(f"/api/v1/tenants/{registered_tenant.pk}/approve", {})
    assert response.status_code == 403


def test_staff_without_mfa_verified_cannot_approve(staff, registered_tenant):
    client = APIClient()
    client.force_authenticate(user=staff)
    # No _mfa_verified in session
    response = client.post(f"/api/v1/tenants/{registered_tenant.pk}/approve", {})
    assert response.status_code == 403


# --- Tenant detail (requires approved membership) ---


def test_detail_requires_approved_tenant(user, registered_tenant):
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get("/api/v1/tenants/me", HTTP_X_TENANT_ID=str(registered_tenant.pk))
    # Tenant is PENDING, not APPROVED — permission denied
    assert response.status_code == 403


def test_detail_returns_tenant_for_approved_member(user, staff_client, registered_tenant):
    staff_client.post(f"/api/v1/tenants/{registered_tenant.pk}/approve", {})

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get("/api/v1/tenants/me", HTTP_X_TENANT_ID=str(registered_tenant.pk))
    assert response.status_code == 200
    assert response.data["id"] == str(registered_tenant.pk)


# --- Members list ---


def test_owner_can_list_members(user, staff_client, registered_tenant):
    staff_client.post(f"/api/v1/tenants/{registered_tenant.pk}/approve", {})

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get("/api/v1/tenants/me/members", HTTP_X_TENANT_ID=str(registered_tenant.pk))
    assert response.status_code == 200
    assert len(response.data) == 1
    assert response.data[0]["role"] == Membership.Role.OWNER
