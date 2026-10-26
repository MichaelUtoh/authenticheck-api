"""Tests for Phase 4 – Public verification."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.catalogue.models import Batch, Brand, Product
from apps.core.tenancy import tenant_context
from apps.issuance.models import Code, IssuanceJob
from apps.tenants.models import Membership, Tenant
from apps.verification.models import ScanEvent

User = get_user_model()
pytestmark = pytest.mark.django_db

URL = "/api/v1/verify/{code}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant():
    return Tenant.objects.create(legal_name="VerifyCo Ltd", status=Tenant.Status.APPROVED)


@pytest.fixture
def batch(tenant):
    with tenant_context(tenant.pk):
        brand = Brand.objects.create(tenant=tenant, name="VerifyBrand", slug="verifybrand")
        product = Product.objects.create(
            tenant=tenant, brand=brand, name="Verify Product", sku="VRF-001"
        )
        return Batch.objects.create(
            tenant=tenant,
            product=product,
            batch_number="VRF-BATCH-001",
            quantity=100,
            expires_at="2027-12-31",
        )


@pytest.fixture
def active_code(tenant, batch):
    with tenant_context(tenant.pk):
        owner = User.objects.create_user("owner@verify.test", "a-long-test-password")
        Membership.objects.create(user=owner, tenant=tenant, role=Membership.Role.OWNER)
        job = IssuanceJob.objects.create(
            tenant=tenant,
            batch=batch,
            quantity_requested=1,
            submitted_by=owner,
            status=IssuanceJob.Status.DONE,
            quantity_issued=1,
        )
        return Code.objects.create(
            tenant=tenant,
            code="VRFCODE0000001",
            batch=batch,
            product=batch.product,
            issuance_job=job,
        )


@pytest.fixture
def client():
    return APIClient()


# ---------------------------------------------------------------------------
# Genuine code
# ---------------------------------------------------------------------------


def test_genuine_code_returns_200(client, active_code, batch, tenant):
    response = client.get(URL.format(code=active_code.code))

    assert response.status_code == 200
    assert response.data["result"] == "genuine"
    assert response.data["code"] == active_code.code
    assert response.data["product"]["sku"] == "VRF-001"
    assert response.data["product"]["brand"] == "VerifyBrand"
    assert response.data["batch"]["batch_number"] == "VRF-BATCH-001"
    assert response.data["batch"]["expires_at"] == "2027-12-31"
    assert response.data["batch"]["status"] == "active"
    assert response.data["manufacturer"]["name"] == "VerifyCo Ltd"


def test_genuine_scan_records_event(client, active_code):
    client.get(URL.format(code=active_code.code))

    event = ScanEvent.objects.get(code_str=active_code.code)
    assert event.result == ScanEvent.Result.GENUINE
    assert event.code == active_code


# ---------------------------------------------------------------------------
# Recalled batch
# ---------------------------------------------------------------------------


def test_recalled_batch_returns_recalled(client, active_code, batch):
    batch.status = Batch.Status.RECALLED
    batch.save(update_fields=["status", "updated_at"])

    response = client.get(URL.format(code=active_code.code))

    assert response.status_code == 200
    assert response.data["result"] == "recalled"
    assert response.data["batch"]["status"] == "recalled"


def test_recalled_scan_records_event(client, active_code, batch):
    batch.status = Batch.Status.RECALLED
    batch.save(update_fields=["status", "updated_at"])

    client.get(URL.format(code=active_code.code))

    event = ScanEvent.objects.get(code_str=active_code.code)
    assert event.result == ScanEvent.Result.RECALLED


# ---------------------------------------------------------------------------
# Deactivated code
# ---------------------------------------------------------------------------


def test_deactivated_code_returns_deactivated(client, active_code):
    from django.utils import timezone

    active_code.status = Code.Status.DEACTIVATED
    active_code.deactivated_at = timezone.now()
    active_code.deactivation_reason = "Suspected counterfeit"
    active_code.save(update_fields=["status", "deactivated_at", "deactivation_reason", "updated_at"])

    response = client.get(URL.format(code=active_code.code))

    assert response.status_code == 200
    assert response.data["result"] == "deactivated"
    assert response.data["code"] == active_code.code
    assert response.data["deactivation_reason"] == "Suspected counterfeit"
    assert response.data["deactivated_at"] is not None


def test_deactivated_scan_records_event(client, active_code):
    from django.utils import timezone

    active_code.status = Code.Status.DEACTIVATED
    active_code.deactivated_at = timezone.now()
    active_code.save(update_fields=["status", "deactivated_at", "updated_at"])

    client.get(URL.format(code=active_code.code))

    event = ScanEvent.objects.get(code_str=active_code.code)
    assert event.result == ScanEvent.Result.DEACTIVATED


# ---------------------------------------------------------------------------
# Not found
# ---------------------------------------------------------------------------


def test_unknown_code_returns_404(client):
    response = client.get(URL.format(code="DOESNOTEXIST001"))

    assert response.status_code == 404
    assert response.data["result"] == "not_found"


def test_not_found_scan_records_event(client):
    client.get(URL.format(code="DOESNOTEXIST002"))

    event = ScanEvent.objects.get(code_str="DOESNOTEXIST002")
    assert event.result == ScanEvent.Result.NOT_FOUND
    assert event.code is None


# ---------------------------------------------------------------------------
# No authentication required
# ---------------------------------------------------------------------------


def test_verify_requires_no_auth(active_code):
    """Verify endpoint is public — unauthenticated requests succeed."""
    anon = APIClient()
    response = anon.get(URL.format(code=active_code.code))
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# IP and user-agent captured
# ---------------------------------------------------------------------------


def test_scan_event_captures_ip_and_ua(client, active_code):
    client.get(
        URL.format(code=active_code.code),
        REMOTE_ADDR="203.0.113.5",
        HTTP_USER_AGENT="ScannerApp/1.0",
    )

    event = ScanEvent.objects.get(code_str=active_code.code)
    assert event.ip_address == "203.0.113.5"
    assert event.user_agent == "ScannerApp/1.0"


# ---------------------------------------------------------------------------
# ScanEvent immutability
# ---------------------------------------------------------------------------


def test_scan_event_is_immutable(active_code):
    event = ScanEvent.objects.create(
        code_str=active_code.code,
        code=active_code,
        result=ScanEvent.Result.GENUINE,
    )
    with pytest.raises(ValueError):
        event.result = ScanEvent.Result.DEACTIVATED
        event.save()


def test_scan_event_cannot_be_deleted(active_code):
    event = ScanEvent.objects.create(
        code_str=active_code.code,
        code=active_code,
        result=ScanEvent.Result.GENUINE,
    )
    with pytest.raises(ValueError):
        event.delete()
