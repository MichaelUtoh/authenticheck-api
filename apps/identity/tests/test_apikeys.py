"""Tests for Phase 7 — API key authentication."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.identity.models import APIKey
from apps.tenants.models import Membership, Tenant

User = get_user_model()
pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def owner():
    return User.objects.create_user("owner@apikey.test", "a-long-test-password")


@pytest.fixture
def tenant(owner):
    t = Tenant.objects.create(legal_name="APIKeyCo", status=Tenant.Status.APPROVED)
    Membership.objects.create(user=owner, tenant=t, role=Membership.Role.OWNER)
    return t


@pytest.fixture
def membership(owner, tenant):
    return Membership.objects.get(user=owner, tenant=tenant)


@pytest.fixture
def jwt_client(owner, tenant):
    c = APIClient()
    c.force_authenticate(user=owner)
    c.credentials(HTTP_X_TENANT_ID=str(tenant.pk))
    return c


# ---------------------------------------------------------------------------
# Key creation
# ---------------------------------------------------------------------------


def test_create_api_key_returns_raw_key(jwt_client, tenant):
    response = jwt_client.post("/api/v1/api-keys", {"name": "CI pipeline"})
    assert response.status_code == 201
    key = response.data["key"]
    assert key.startswith("ac_")
    assert len(key) > 40


def test_create_api_key_saves_hash_not_raw(jwt_client, tenant):
    response = jwt_client.post("/api/v1/api-keys", {"name": "CI pipeline"})
    raw_key = response.data["key"]
    pk = response.data["id"]

    api_key = APIKey.unscoped.get(pk=pk)
    assert api_key.key_hash != raw_key
    assert len(api_key.key_hash) == 64


def test_create_api_key_requires_name(jwt_client):
    response = jwt_client.post("/api/v1/api-keys", {})
    assert response.status_code == 400


def test_list_api_keys(jwt_client, owner, tenant, membership):
    APIKey.create_key(
        name="test key", tenant=tenant, membership=membership, created_by=owner
    )
    response = jwt_client.get("/api/v1/api-keys")
    assert response.status_code == 200
    assert len(response.data) >= 1
    assert "key_prefix" in response.data[0]
    assert "key" not in response.data[0]  # raw key never returned in list


# ---------------------------------------------------------------------------
# APIKeyAuthentication
# ---------------------------------------------------------------------------


def test_api_key_authenticates_request(owner, tenant, membership):
    api_key, raw_key = APIKey.create_key(
        name="test", tenant=tenant, membership=membership, created_by=owner
    )

    c = APIClient()
    c.credentials(HTTP_X_API_KEY=raw_key)
    response = c.get("/api/v1/auth/me")
    assert response.status_code == 200


def test_invalid_api_key_returns_401(tenant):
    c = APIClient()
    c.credentials(HTTP_X_API_KEY="ac_invalid_key_that_does_not_exist")
    response = c.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_revoked_api_key_is_rejected(owner, tenant, membership):
    from django.utils import timezone

    api_key, raw_key = APIKey.create_key(
        name="revoked", tenant=tenant, membership=membership, created_by=owner
    )
    api_key.revoked_at = timezone.now()
    api_key.save(update_fields=["revoked_at"])

    c = APIClient()
    c.credentials(HTTP_X_API_KEY=raw_key)
    response = c.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_api_key_tenant_context_set(owner, tenant, membership):
    api_key, raw_key = APIKey.create_key(
        name="ctx", tenant=tenant, membership=membership, created_by=owner
    )

    c = APIClient()
    c.credentials(HTTP_X_API_KEY=raw_key)
    response = c.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.data["active_tenant"]["id"] == str(tenant.pk)


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------


def test_revoke_api_key(jwt_client, owner, tenant, membership):
    api_key, raw_key = APIKey.create_key(
        name="to-revoke", tenant=tenant, membership=membership, created_by=owner
    )

    response = jwt_client.delete(f"/api/v1/api-keys/{api_key.pk}")
    assert response.status_code == 204

    api_key.refresh_from_db()
    assert api_key.revoked_at is not None


def test_revoke_nonexistent_key_returns_404(jwt_client):
    import uuid
    response = jwt_client.delete(f"/api/v1/api-keys/{uuid.uuid4()}")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Pharma issuance gating
# ---------------------------------------------------------------------------


def test_pharma_tenant_blocks_expired_batch(jwt_client, owner, tenant, membership):
    import datetime
    from apps.catalogue.models import Batch, Brand, Product
    from apps.core.tenancy import tenant_context

    tenant.is_pharma = True
    tenant.save(update_fields=["is_pharma"])

    with tenant_context(tenant.pk):
        brand = Brand.objects.create(tenant=tenant, name="Pharma B", slug="pharma-b")
        product = Product.objects.create(tenant=tenant, brand=brand, name="Drug A", sku="DRUG-001")
        batch = Batch.objects.create(
            tenant=tenant, product=product, batch_number="RX-001", quantity=100,
            expires_at=datetime.date(2020, 1, 1),  # expired
        )

    response = jwt_client.post(
        "/api/v1/issuance/jobs",
        {"batch_id": str(batch.pk), "quantity": 10},
        headers={"Idempotency-Key": "pharma-test-001"},
    )
    assert response.status_code == 400
