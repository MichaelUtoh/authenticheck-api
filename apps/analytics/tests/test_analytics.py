"""Tests for Phase 6 – Analytics endpoints."""

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.catalogue.models import Batch, Brand, Product
from apps.core.tenancy import tenant_context
from apps.issuance.models import Code, IssuanceJob
from apps.risk.models import Case, RiskFlag
from apps.tenants.models import Membership, Tenant
from apps.verification.models import ScanEvent

User = get_user_model()
pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def owner():
    return User.objects.create_user("owner@analytics.test", "a-long-test-password")


@pytest.fixture
def tenant(owner):
    t = Tenant.objects.create(legal_name="AnalyticsCo", status=Tenant.Status.APPROVED)
    Membership.objects.create(user=owner, tenant=t, role=Membership.Role.OWNER)
    return t


@pytest.fixture
def client(owner, tenant):
    c = APIClient()
    c.force_authenticate(user=owner)
    c.credentials(HTTP_X_TENANT_ID=str(tenant.pk))
    return c


@pytest.fixture
def batch(tenant):
    with tenant_context(tenant.pk):
        brand = Brand.objects.create(tenant=tenant, name="ABrand", slug="abrand")
        product = Product.objects.create(
            tenant=tenant, brand=brand, name="AProduct", sku="ANA-001"
        )
        return Batch.objects.create(
            tenant=tenant, product=product, batch_number="ANA-BATCH-001", quantity=100
        )


@pytest.fixture
def code(tenant, batch):
    with tenant_context(tenant.pk):
        u = User.objects.create_user("codeuser@analytics.test", "a-long-test-password")
        job = IssuanceJob.objects.create(
            tenant=tenant, batch=batch, quantity_requested=1,
            submitted_by=u, status=IssuanceJob.Status.DONE, quantity_issued=1,
        )
        return Code.objects.create(
            tenant=tenant, code="ANACODE000001", batch=batch,
            product=batch.product, issuance_job=job,
        )


# ---------------------------------------------------------------------------
# Scan analytics
# ---------------------------------------------------------------------------


def test_scan_analytics_returns_200(client):
    response = client.get("/api/v1/analytics/scans")
    assert response.status_code == 200
    assert "total_scans" in response.data
    assert "by_result" in response.data
    assert "by_day" in response.data


def test_scan_analytics_counts_tenant_scans(client, code, tenant):
    ScanEvent.objects.create(
        code_str=code.code, code=code, result=ScanEvent.Result.GENUINE
    )
    ScanEvent.objects.create(
        code_str=code.code, code=code, result=ScanEvent.Result.GENUINE
    )

    response = client.get("/api/v1/analytics/scans")
    assert response.data["total_scans"] == 2
    assert response.data["by_result"].get("genuine") == 2


def test_scan_analytics_excludes_other_tenant_scans(client, code, tenant):
    other_tenant = Tenant.objects.create(
        legal_name="OtherCo", status=Tenant.Status.APPROVED
    )
    with tenant_context(other_tenant.pk):
        other_brand = Brand.objects.create(tenant=other_tenant, name="OB", slug="ob")
        other_product = Product.objects.create(
            tenant=other_tenant, brand=other_brand, name="OP", sku="O-001"
        )
        other_batch = Batch.objects.create(
            tenant=other_tenant, product=other_product,
            batch_number="O-B-001", quantity=10,
        )
        u2 = User.objects.create_user("u2@analytics.test", "a-long-test-password")
        job2 = IssuanceJob.objects.create(
            tenant=other_tenant, batch=other_batch, quantity_requested=1,
            submitted_by=u2, status=IssuanceJob.Status.DONE, quantity_issued=1,
        )
        other_code = Code.objects.create(
            tenant=other_tenant, code="OTHERANA00001", batch=other_batch,
            product=other_product, issuance_job=job2,
        )

    ScanEvent.objects.create(
        code_str=code.code, code=code, result=ScanEvent.Result.GENUINE
    )
    ScanEvent.objects.create(
        code_str=other_code.code, code=other_code, result=ScanEvent.Result.GENUINE
    )

    response = client.get("/api/v1/analytics/scans")
    assert response.data["total_scans"] == 1


def test_scan_analytics_by_day_filled(client):
    response = client.get("/api/v1/analytics/scans?days=7")
    assert response.data["period_days"] == 7
    assert len(response.data["by_day"]) == 8  # 7 days + today


def test_scan_analytics_rejects_invalid_days(client):
    response = client.get("/api/v1/analytics/scans?days=0")
    assert response.status_code == 400

    response = client.get("/api/v1/analytics/scans?days=91")
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Code analytics
# ---------------------------------------------------------------------------


def test_code_analytics_returns_snapshot(client, code):
    response = client.get("/api/v1/analytics/codes")
    assert response.status_code == 200
    assert response.data["total_issued"] == 1
    assert response.data["active"] == 1
    assert response.data["deactivated"] == 0


def test_code_analytics_counts_deactivated(client, code):
    code.status = Code.Status.DEACTIVATED
    code.deactivated_at = timezone.now()
    code.save(update_fields=["status", "deactivated_at", "updated_at"])

    response = client.get("/api/v1/analytics/codes")
    assert response.data["active"] == 0
    assert response.data["deactivated"] == 1


# ---------------------------------------------------------------------------
# Risk analytics
# ---------------------------------------------------------------------------


def test_risk_analytics_returns_200(client):
    response = client.get("/api/v1/analytics/risk")
    assert response.status_code == 200
    assert "open_flags" in response.data
    assert "cases" in response.data
    assert "flags_last_30_days" in response.data


def test_risk_analytics_counts_open_flags(client, tenant, code):
    RiskFlag.unscoped.create(
        tenant=tenant, kind=RiskFlag.Kind.CONSUMER_REPORT,
        severity=RiskFlag.Severity.HIGH, code=code,
    )
    RiskFlag.unscoped.create(
        tenant=tenant, kind=RiskFlag.Kind.REPEATED_SCAN,
        severity=RiskFlag.Severity.MEDIUM, code=code,
    )

    response = client.get("/api/v1/analytics/risk")
    assert response.data["open_flags"]["high"] == 1
    assert response.data["open_flags"]["medium"] == 1
    assert response.data["open_flags"]["low"] == 0


def test_risk_analytics_counts_cases_by_status(client, tenant):
    with tenant_context(tenant.pk):
        Case.objects.create(tenant=tenant, title="Open")
        Case.objects.create(tenant=tenant, title="Closed", status=Case.Status.CLOSED)

    response = client.get("/api/v1/analytics/risk")
    assert response.data["cases"]["open"] == 1
    assert response.data["cases"]["closed"] == 1
