"""Tests for Phase 6 – Platform dashboard (staff endpoints)."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.audit import services as audit_service
from apps.audit.models import AuditEvent
from apps.core.tenancy import tenant_context
from apps.issuance.models import Code, IssuanceJob
from apps.catalogue.models import Batch, Brand, Product
from apps.risk.models import RiskFlag
from apps.tenants.models import Membership, Tenant

User = get_user_model()
pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def staff():
    return User.objects.create_user(
        "staff@platform.test", "a-long-test-password", is_staff=True
    )


@pytest.fixture
def staff_client(staff):
    c = APIClient()
    c.force_authenticate(user=staff, token={"amr": ["pwd", "otp"]})
    return c


@pytest.fixture
def tenant():
    return Tenant.objects.create(
        legal_name="Platform Tenant Ltd", status=Tenant.Status.APPROVED
    )


@pytest.fixture
def owner(tenant):
    u = User.objects.create_user("owner@platform.test", "a-long-test-password")
    Membership.objects.create(user=u, tenant=tenant, role=Membership.Role.OWNER)
    return u


# ---------------------------------------------------------------------------
# Auth guards
# ---------------------------------------------------------------------------


def test_platform_requires_staff():
    regular = User.objects.create_user("regular@platform.test", "a-long-test-password")
    c = APIClient()
    c.force_authenticate(user=regular)
    response = c.get("/api/v1/platform/tenants")
    assert response.status_code == 403


def test_platform_requires_mfa(staff):
    c = APIClient()
    c.force_authenticate(user=staff)
    response = c.get("/api/v1/platform/tenants")
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Tenant list
# ---------------------------------------------------------------------------


def test_platform_tenant_list_returns_200(staff_client, tenant):
    response = staff_client.get("/api/v1/platform/tenants")
    assert response.status_code == 200
    assert response.data["count"] >= 1


def test_platform_tenant_list_includes_member_count(staff_client, tenant, owner):
    response = staff_client.get("/api/v1/platform/tenants")
    result = next(t for t in response.data["results"] if str(t["id"]) == str(tenant.pk))
    assert result["member_count"] == 1


def test_platform_tenant_list_filter_by_status(staff_client, tenant):
    Tenant.objects.create(legal_name="Pending Co", status=Tenant.Status.PENDING)

    response = staff_client.get("/api/v1/platform/tenants?status=pending")
    assert all(t["status"] == "pending" for t in response.data["results"])

    response = staff_client.get("/api/v1/platform/tenants?status=approved")
    assert all(t["status"] == "approved" for t in response.data["results"])


# ---------------------------------------------------------------------------
# Tenant detail
# ---------------------------------------------------------------------------


def test_platform_tenant_detail_returns_stats(staff_client, tenant, owner):
    with tenant_context(tenant.pk):
        brand = Brand.objects.create(tenant=tenant, name="B", slug="b")
        product = Product.objects.create(
            tenant=tenant, brand=brand, name="P", sku="P-001"
        )
        batch = Batch.objects.create(
            tenant=tenant, product=product, batch_number="B-001", quantity=5
        )
        job = IssuanceJob.objects.create(
            tenant=tenant, batch=batch, quantity_requested=2,
            submitted_by=owner, status=IssuanceJob.Status.DONE, quantity_issued=2,
        )
        for i in range(2):
            Code.objects.create(
                tenant=tenant, code=f"PLATCODE{i:06}", batch=batch,
                product=product, issuance_job=job,
            )

    response = staff_client.get(f"/api/v1/platform/tenants/{tenant.pk}")
    assert response.status_code == 200
    assert response.data["active_codes"] == 2
    assert response.data["member_count"] == 1


def test_platform_tenant_detail_404_for_unknown(staff_client):
    import uuid
    response = staff_client.get(f"/api/v1/platform/tenants/{uuid.uuid4()}")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Platform flag list
# ---------------------------------------------------------------------------


def test_platform_flags_shows_all_tenants(staff_client, tenant):
    other = Tenant.objects.create(legal_name="Other", status=Tenant.Status.APPROVED)
    RiskFlag.unscoped.create(
        tenant=tenant, kind=RiskFlag.Kind.CONSUMER_REPORT, severity=RiskFlag.Severity.HIGH
    )
    RiskFlag.unscoped.create(
        tenant=other, kind=RiskFlag.Kind.CONSUMER_REPORT, severity=RiskFlag.Severity.HIGH
    )

    response = staff_client.get("/api/v1/platform/flags")
    assert response.data["count"] == 2


def test_platform_flags_excludes_reviewed(staff_client, tenant):
    from django.utils import timezone
    RiskFlag.unscoped.create(
        tenant=tenant, kind=RiskFlag.Kind.CONSUMER_REPORT, severity=RiskFlag.Severity.HIGH,
        is_reviewed=True, reviewed_at=timezone.now(),
    )

    response = staff_client.get("/api/v1/platform/flags")
    assert response.data["count"] == 0


def test_platform_flags_filter_by_severity(staff_client, tenant):
    RiskFlag.unscoped.create(
        tenant=tenant, kind=RiskFlag.Kind.CONSUMER_REPORT, severity=RiskFlag.Severity.HIGH
    )
    RiskFlag.unscoped.create(
        tenant=tenant, kind=RiskFlag.Kind.CONSUMER_REPORT, severity=RiskFlag.Severity.LOW
    )

    response = staff_client.get("/api/v1/platform/flags?severity=high")
    assert response.data["count"] == 1


# ---------------------------------------------------------------------------
# Platform audit log
# ---------------------------------------------------------------------------


def test_platform_audit_log_returns_200(staff_client, tenant, owner):
    with tenant_context(tenant.pk):
        audit_service.record(action="test.event", actor=owner, tenant=tenant)

    response = staff_client.get("/api/v1/platform/audit")
    assert response.status_code == 200
    assert response.data["count"] >= 1


def test_platform_audit_log_filter_by_action(staff_client, tenant, owner):
    with tenant_context(tenant.pk):
        audit_service.record(action="catalogue.brand.created", actor=owner, tenant=tenant)
        audit_service.record(action="risk.case.created", actor=owner, tenant=tenant)

    response = staff_client.get("/api/v1/platform/audit?action=catalogue")
    actions = [e["action"] for e in response.data["results"]]
    assert all(a.startswith("catalogue") for a in actions)


def test_platform_audit_log_filter_by_tenant(staff_client, tenant, owner):
    other = Tenant.objects.create(legal_name="OtherAudit", status=Tenant.Status.APPROVED)
    with tenant_context(tenant.pk):
        audit_service.record(action="test.a", actor=owner, tenant=tenant)
    with tenant_context(other.pk):
        u2 = User.objects.create_user("u2@platform.test", "a-long-test-password")
        audit_service.record(action="test.b", actor=u2, tenant=other)

    response = staff_client.get(f"/api/v1/platform/audit?tenant_id={tenant.pk}")
    assert all(e["tenant_name"] == "Platform Tenant Ltd" for e in response.data["results"])


# ---------------------------------------------------------------------------
# Audit hardening – hash chain
# ---------------------------------------------------------------------------


def test_audit_event_hash_is_set(tenant, owner):
    with tenant_context(tenant.pk):
        event = audit_service.record(action="test.hash", actor=owner, tenant=tenant)
    assert len(event.event_hash) == 64


def test_audit_event_chain_links(tenant, owner):
    with tenant_context(tenant.pk):
        e1 = audit_service.record(action="test.first", actor=owner, tenant=tenant)
        e2 = audit_service.record(action="test.second", actor=owner, tenant=tenant)

    assert e2.prev_hash == e1.event_hash


def test_audit_db_trigger_blocks_raw_delete(tenant, owner):
    from django.db import DatabaseError, connection, transaction

    with tenant_context(tenant.pk):
        event = audit_service.record(action="test.trigger", actor=owner, tenant=tenant)

    with pytest.raises(DatabaseError):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM audit_auditevent WHERE id = %s", [str(event.pk)]
                )
