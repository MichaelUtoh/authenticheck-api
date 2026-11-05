"""Tests for Phase 5 – Risk, cases, recall/deactivation, consumer reports."""

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.catalogue.models import Batch, Brand, Product
from apps.core.tenancy import tenant_context
from apps.issuance.models import Code, IssuanceJob
from apps.risk.models import Case, ConsumerReport, RiskFlag
from apps.tenants.models import Membership, Tenant
from apps.verification.models import ScanEvent

User = get_user_model()
pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def owner():
    return User.objects.create_user("owner@risk.test", "a-long-test-password")


@pytest.fixture
def tenant(owner):
    t = Tenant.objects.create(legal_name="RiskCo Ltd", status=Tenant.Status.APPROVED)
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
        brand = Brand.objects.create(tenant=tenant, name="RiskBrand", slug="riskbrand")
        product = Product.objects.create(
            tenant=tenant, brand=brand, name="Risk Product", sku="RSK-001"
        )
        return Batch.objects.create(
            tenant=tenant, product=product, batch_number="RSK-BATCH-001", quantity=50
        )


@pytest.fixture
def active_code(tenant, batch):
    with tenant_context(tenant.pk):
        owner = User.objects.create_user("code-owner@risk.test", "a-long-test-password")
        job = IssuanceJob.objects.create(
            tenant=tenant,
            batch=batch,
            quantity_requested=3,
            submitted_by=owner,
            status=IssuanceJob.Status.DONE,
            quantity_issued=3,
        )
        return Code.objects.create(
            tenant=tenant,
            code="RSKCODE0000001",
            batch=batch,
            product=batch.product,
            issuance_job=job,
        )


# ---------------------------------------------------------------------------
# Batch recall
# ---------------------------------------------------------------------------


def test_recall_active_batch_returns_200(client, tenant, batch):
    url = f"/api/v1/catalogue/products/{batch.product_id}/batches/{batch.pk}/recall"
    response = client.post(url, {"reason": "Quality issue detected"})

    assert response.status_code == 200
    assert response.data["batch"]["status"] == "recalled"


def test_recall_sets_batch_recalled(client, batch):
    url = f"/api/v1/catalogue/products/{batch.product_id}/batches/{batch.pk}/recall"
    client.post(url, {"reason": "Contamination"})

    batch.refresh_from_db()
    assert batch.status == Batch.Status.RECALLED


def test_recall_deactivates_active_codes(client, tenant, batch, active_code):
    with tenant_context(tenant.pk):
        owner2 = User.objects.create_user("owner2@risk.test", "a-long-test-password")
        job = IssuanceJob.objects.create(
            tenant=tenant, batch=batch, quantity_requested=1,
            submitted_by=owner2, status=IssuanceJob.Status.DONE, quantity_issued=1,
        )
        code2 = Code.objects.create(
            tenant=tenant, code="RSKCODE0000002", batch=batch,
            product=batch.product, issuance_job=job,
        )

    url = f"/api/v1/catalogue/products/{batch.product_id}/batches/{batch.pk}/recall"
    response = client.post(url, {"reason": "Safety recall"})

    assert response.data["codes_deactivated"] == 2

    active_code.refresh_from_db()
    code2.refresh_from_db()
    assert active_code.status == Code.Status.DEACTIVATED
    assert code2.status == Code.Status.DEACTIVATED


def test_recall_already_recalled_returns_400(client, batch):
    batch.status = Batch.Status.RECALLED
    batch.save(update_fields=["status", "updated_at"])

    url = f"/api/v1/catalogue/products/{batch.product_id}/batches/{batch.pk}/recall"
    response = client.post(url, {"reason": "Duplicate"})
    assert response.status_code == 400


def test_recall_requires_owner_or_admin(tenant, batch):
    viewer = User.objects.create_user("viewer@risk.test", "a-long-test-password")
    Membership.objects.create(user=viewer, tenant=tenant, role=Membership.Role.VIEWER)
    c = APIClient()
    c.force_authenticate(user=viewer)
    c.credentials(HTTP_X_TENANT_ID=str(tenant.pk))

    url = f"/api/v1/catalogue/products/{batch.product_id}/batches/{batch.pk}/recall"
    response = c.post(url, {"reason": "Try"})
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Consumer reports
# ---------------------------------------------------------------------------


def test_submit_report_creates_consumer_report(active_code):
    c = APIClient()
    response = c.post(
        "/api/v1/reports",
        {"code_str": active_code.code, "description": "Packaging looks fake."},
    )
    assert response.status_code == 201
    assert ConsumerReport.objects.filter(code_str=active_code.code).exists()


def test_submit_report_links_code_if_found(active_code):
    c = APIClient()
    c.post("/api/v1/reports", {"code_str": active_code.code, "description": "Suspicious."})

    report = ConsumerReport.objects.get(code_str=active_code.code)
    assert report.code == active_code


def test_submit_report_for_unknown_code_still_creates():
    c = APIClient()
    response = c.post(
        "/api/v1/reports",
        {"code_str": "UNKNOWNCODE9999", "description": "Fake product."},
    )
    assert response.status_code == 201
    report = ConsumerReport.objects.get(code_str="UNKNOWNCODE9999")
    assert report.code is None


def test_submit_report_creates_risk_flag_for_known_code(active_code):
    c = APIClient()
    c.post("/api/v1/reports", {"code_str": active_code.code, "description": "Suspicious."})

    assert RiskFlag.unscoped.filter(
        code=active_code, kind=RiskFlag.Kind.CONSUMER_REPORT
    ).exists()


def test_submit_report_no_flag_for_unknown_code():
    c = APIClient()
    c.post("/api/v1/reports", {"code_str": "GHOST0000000001", "description": "Suspicious."})
    assert not RiskFlag.unscoped.filter(kind=RiskFlag.Kind.CONSUMER_REPORT).exists()


def test_submit_report_requires_no_auth(active_code):
    c = APIClient()
    response = c.post(
        "/api/v1/reports",
        {"code_str": active_code.code, "description": "Counterfeit."},
    )
    assert response.status_code == 201


# ---------------------------------------------------------------------------
# Risk flag generation from scans
# ---------------------------------------------------------------------------


def test_deactivated_scan_creates_risk_flag(active_code):
    active_code.status = Code.Status.DEACTIVATED
    active_code.deactivated_at = timezone.now()
    active_code.save(update_fields=["status", "deactivated_at", "updated_at"])

    c = APIClient()
    c.get(f"/api/v1/verify/{active_code.code}")

    assert RiskFlag.unscoped.filter(
        code=active_code, kind=RiskFlag.Kind.SCAN_AFTER_DEACTIVATION, severity=RiskFlag.Severity.HIGH
    ).exists()


def test_repeated_scan_creates_flag_at_threshold(active_code):
    from apps.risk import services as risk_services

    threshold = 10
    for i in range(threshold - 1):
        ScanEvent.objects.create(
            code_str=active_code.code,
            code=active_code,
            result=ScanEvent.Result.GENUINE,
        )

    assert not RiskFlag.unscoped.filter(kind=RiskFlag.Kind.REPEATED_SCAN).exists()

    scan = ScanEvent.objects.create(
        code_str=active_code.code,
        code=active_code,
        result=ScanEvent.Result.GENUINE,
    )
    risk_services.evaluate_scan(scan)

    assert RiskFlag.unscoped.filter(
        code=active_code, kind=RiskFlag.Kind.REPEATED_SCAN, severity=RiskFlag.Severity.MEDIUM
    ).exists()


def test_repeated_scan_flag_created_only_once(active_code):
    from apps.risk import services as risk_services

    threshold = 10
    for i in range(threshold):
        scan = ScanEvent.objects.create(
            code_str=active_code.code,
            code=active_code,
            result=ScanEvent.Result.GENUINE,
        )
        risk_services.evaluate_scan(scan)

    # One more scan past the threshold — should NOT create a second flag.
    scan = ScanEvent.objects.create(
        code_str=active_code.code,
        code=active_code,
        result=ScanEvent.Result.GENUINE,
    )
    risk_services.evaluate_scan(scan)

    assert RiskFlag.unscoped.filter(kind=RiskFlag.Kind.REPEATED_SCAN).count() == 1


# ---------------------------------------------------------------------------
# Risk flag list / review
# ---------------------------------------------------------------------------


def test_list_flags_returns_tenant_flags_only(client, tenant, active_code):
    other_tenant = Tenant.objects.create(
        legal_name="OtherCo", status=Tenant.Status.APPROVED
    )
    RiskFlag.unscoped.create(
        tenant=tenant,
        kind=RiskFlag.Kind.CONSUMER_REPORT,
        severity=RiskFlag.Severity.LOW,
        code=active_code,
    )
    RiskFlag.unscoped.create(
        tenant=other_tenant,
        kind=RiskFlag.Kind.CONSUMER_REPORT,
        severity=RiskFlag.Severity.LOW,
    )

    response = client.get("/api/v1/risk/flags")
    assert response.status_code == 200
    assert len(response.data) == 1


def test_mark_flag_reviewed(client, tenant, active_code, owner):
    flag = RiskFlag.unscoped.create(
        tenant=tenant,
        kind=RiskFlag.Kind.CONSUMER_REPORT,
        severity=RiskFlag.Severity.MEDIUM,
        code=active_code,
    )
    response = client.post(f"/api/v1/risk/flags/{flag.pk}/review")

    assert response.status_code == 200
    flag.refresh_from_db()
    assert flag.is_reviewed is True
    assert flag.reviewed_by == owner
    assert flag.reviewed_at is not None


def test_mark_flag_reviewed_twice_returns_400(client, tenant, active_code):
    flag = RiskFlag.unscoped.create(
        tenant=tenant,
        kind=RiskFlag.Kind.CONSUMER_REPORT,
        severity=RiskFlag.Severity.LOW,
        code=active_code,
        is_reviewed=True,
        reviewed_at=timezone.now(),
    )
    response = client.post(f"/api/v1/risk/flags/{flag.pk}/review")
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


def test_list_cases_empty(client):
    response = client.get("/api/v1/risk/cases")
    assert response.status_code == 200
    assert response.data == []


def test_create_case(client):
    response = client.post(
        "/api/v1/risk/cases",
        {"title": "Counterfeit outbreak in Lagos", "description": "Multiple reports received."},
    )
    assert response.status_code == 201
    assert response.data["status"] == "open"
    assert response.data["title"] == "Counterfeit outbreak in Lagos"
    assert response.data["flags_count"] == 0


def test_create_case_with_batch(client, batch):
    response = client.post(
        "/api/v1/risk/cases",
        {"title": "Batch recall investigation", "batch_id": str(batch.pk)},
    )
    assert response.status_code == 201
    assert response.data["batch_id"] == str(batch.pk)


def test_get_case_detail(client, tenant):
    with tenant_context(tenant.pk):
        case = Case.objects.create(tenant=tenant, title="Test Case")

    response = client.get(f"/api/v1/risk/cases/{case.pk}")
    assert response.status_code == 200
    assert response.data["title"] == "Test Case"


def test_update_case_status_to_closed_sets_resolved_at(client, tenant):
    with tenant_context(tenant.pk):
        case = Case.objects.create(tenant=tenant, title="Investigation")

    response = client.patch(
        f"/api/v1/risk/cases/{case.pk}",
        {"status": "closed"},
    )
    assert response.status_code == 200
    assert response.data["status"] == "closed"
    assert response.data["resolved_at"] is not None


def test_update_case_notes(client, tenant):
    with tenant_context(tenant.pk):
        case = Case.objects.create(tenant=tenant, title="Investigation")

    response = client.patch(
        f"/api/v1/risk/cases/{case.pk}",
        {"notes": "Confirmed counterfeits in Kano region."},
    )
    assert response.status_code == 200
    case.refresh_from_db()
    assert case.notes == "Confirmed counterfeits in Kano region."


def test_cases_scoped_to_tenant(client, tenant):
    other_tenant = Tenant.objects.create(
        legal_name="OtherCo2", status=Tenant.Status.APPROVED
    )
    with tenant_context(tenant.pk):
        Case.objects.create(tenant=tenant, title="Our Case")
    with tenant_context(other_tenant.pk):
        Case.objects.create(tenant=other_tenant, title="Their Case")

    response = client.get("/api/v1/risk/cases")
    assert response.status_code == 200
    assert len(response.data) == 1
    assert response.data[0]["title"] == "Our Case"


def test_submit_report_keeps_case_and_links_mixed_case_code(tenant, batch, active_code):
    with tenant_context(tenant.pk):
        Code.objects.create(
            tenant=tenant, code="aB3_x-Qz9", batch=batch,
            product=batch.product, issuance_job=active_code.issuance_job,
        )
    c = APIClient()
    resp = c.post("/api/v1/reports", {"code_str": " aB3_x-Qz9 ", "description": "Odd packaging."})
    assert resp.status_code == 201
    report = ConsumerReport.objects.get(pk=resp.json()["id"])
    assert report.code_str == "aB3_x-Qz9"
    assert report.code is not None and report.code.code == "aB3_x-Qz9"
