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
    """Staff client with MFA done — force_authenticate bypasses the login flow, so the token claims
    (amr includes "otp") are injected manually."""
    client = APIClient()
    client.force_authenticate(user=staff, token={"amr": ["pwd", "otp"]})
    return client


REGISTRATION = {
    "legal_name": "Acme Cosmetics Ltd",
    "industry": "cosmetics",
    "rc_number": "RC1234567",
    "city": "Lagos",
}


@pytest.fixture
def registered_tenant(user):
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post("/api/v1/tenants/register", REGISTRATION)
    assert response.status_code == 201
    return Tenant.objects.get(pk=response.data["id"])


# --- Registration ---


def test_register_creates_tenant_and_owner_membership(user):
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post("/api/v1/tenants/register", REGISTRATION)
    assert response.status_code == 201
    assert response.data["status"] == Tenant.Status.PENDING
    assert response.data["industry"] == "cosmetics"
    assert response.data["rc_number"] == "RC1234567"
    assert response.data["city"] == "Lagos"
    membership = Membership.objects.get(user=user)
    assert membership.role == Membership.Role.OWNER


@pytest.mark.parametrize("missing", ["industry", "rc_number", "city"])
def test_register_requires_industry_rc_number_and_city(user, missing):
    client = APIClient()
    client.force_authenticate(user=user)
    body = {k: v for k, v in REGISTRATION.items() if k != missing}
    response = client.post("/api/v1/tenants/register", body)
    assert response.status_code == 400
    assert missing in response.data["error"]["details"]
    assert not Tenant.objects.exists()


def test_register_rejects_unknown_industry(user):
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post("/api/v1/tenants/register", {**REGISTRATION, "industry": "weapons"})
    assert response.status_code == 400
    assert "industry" in response.data["error"]["details"]


def test_register_normalises_industry_and_rc_number(user):
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post(
        "/api/v1/tenants/register",
        {**REGISTRATION, "industry": " Cosmetics ", "rc_number": "rc 123-4567", "city": " Abuja "},
    )
    assert response.status_code == 201
    assert response.data["industry"] == "cosmetics"
    assert response.data["rc_number"] == "RC1234567"
    assert response.data["city"] == "Abuja"


def test_registering_as_pharmaceuticals_does_not_unlock_pharma(user):
    client = APIClient()
    client.force_authenticate(user=user)
    response = client.post(
        "/api/v1/tenants/register", {**REGISTRATION, "industry": "pharmaceuticals"}
    )
    assert response.status_code == 201
    assert Tenant.objects.get(pk=response.data["id"]).is_pharma is False


def test_registration_industries_available_without_a_company(user):
    client = APIClient()
    client.force_authenticate(user=user)  # signed in, but no membership yet
    response = client.get("/api/v1/tenants/industries")
    assert response.status_code == 200
    keys = {row["key"] for row in response.data}
    assert {"cosmetics", "pharmaceuticals"} <= keys
    assert all({"key", "label"} <= set(row) for row in response.data)


def test_registration_industries_requires_auth():
    assert APIClient().get("/api/v1/tenants/industries").status_code == 401


def test_register_requires_auth():
    client = APIClient()
    response = client.post("/api/v1/tenants/register", {"legal_name": "Acme"})
    assert response.status_code == 401


# --- Staff status transitions ---


def test_staff_can_approve_pending_tenant(staff_client, registered_tenant):
    response = staff_client.post(
        f"/api/v1/tenants/{registered_tenant.pk}/approve", {"reason": "OK"}
    )
    assert response.status_code == 200
    registered_tenant.refresh_from_db()
    assert registered_tenant.status == Tenant.Status.APPROVED


def test_staff_can_reject_pending_tenant(staff_client, registered_tenant):
    response = staff_client.post(
        f"/api/v1/tenants/{registered_tenant.pk}/reject", {"reason": "CAC number does not match"}
    )
    assert response.status_code == 200
    registered_tenant.refresh_from_db()
    assert registered_tenant.status == Tenant.Status.REJECTED
    assert registered_tenant.decision_reason == "CAC number does not match"


@pytest.mark.parametrize("action", ["reject", "request-info"])
def test_reject_and_request_info_require_a_reason(staff_client, registered_tenant, action):
    for body in ({}, {"reason": "   "}):
        response = staff_client.post(f"/api/v1/tenants/{registered_tenant.pk}/{action}", body)
        assert response.status_code == 400
        assert "reason" in response.data["error"]["details"]
    registered_tenant.refresh_from_db()
    assert registered_tenant.status == Tenant.Status.PENDING


def test_request_info_reason_reaches_the_applicant_and_clears_on_approval(
    staff_client, registered_tenant
):
    staff_client.post(
        f"/api/v1/tenants/{registered_tenant.pk}/request-info", {"reason": "Upload your CAC certificate"}
    )
    registered_tenant.refresh_from_db()
    assert registered_tenant.status == Tenant.Status.INFO_REQUESTED
    assert registered_tenant.decision_reason == "Upload your CAC certificate"
    assert registered_tenant.decided_at is not None

    response = staff_client.post(f"/api/v1/tenants/{registered_tenant.pk}/approve", {})
    assert response.status_code == 200
    assert response.data["decision_reason"] == ""


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
    # no "otp" in amr
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
