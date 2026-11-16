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


def test_platform_tenant_list_accepts_repeated_status(staff_client):
    Tenant.objects.create(legal_name="A", status=Tenant.Status.PENDING)
    Tenant.objects.create(legal_name="B", status=Tenant.Status.INFO_REQUESTED)
    Tenant.objects.create(legal_name="C", status=Tenant.Status.APPROVED)
    response = staff_client.get("/api/v1/platform/tenants?status=pending&status=info_requested")
    assert response.status_code == 200
    assert {r["legal_name"] for r in response.data["results"]} == {"A", "B"}


def test_platform_tenant_rows_include_owner_and_brands(staff_client, tenant, owner):
    from apps.catalogue.models import Brand

    owner.full_name = "Ada Okafor"
    owner.save()
    with tenant_context(tenant.pk):
        Brand.objects.create(name="Zeta", slug="zeta")
        Brand.objects.create(name="Alpha", slug="alpha")
        Brand.objects.create(name="Old", slug="old", is_active=False)

    row = next(
        r for r in staff_client.get("/api/v1/platform/tenants").data["results"]
        if r["id"] == str(tenant.pk)
    )
    assert row["owner_email"] == "owner@platform.test"
    assert row["owner_name"] == "Ada Okafor"
    assert row["brands"] == [{"name": "Alpha"}, {"name": "Zeta"}]
    assert row["submitted_at"] == row["created_at"]


def test_platform_tenant_rows_include_registration_details(staff_client):
    t = Tenant.objects.create(
        legal_name="Reg Ltd", industry="cosmetics", rc_number="RC99", city="Kano"
    )
    row = next(
        r for r in staff_client.get("/api/v1/platform/tenants").data["results"]
        if r["id"] == str(t.pk)
    )
    assert (row["industry"], row["rc_number"], row["city"]) == ("cosmetics", "RC99", "Kano")
    detail = staff_client.get(f"/api/v1/platform/tenants/{t.pk}").data
    assert (detail["industry"], detail["rc_number"], detail["city"]) == ("cosmetics", "RC99", "Kano")


def test_platform_tenant_without_owner_has_blank_owner(staff_client):
    t = Tenant.objects.create(legal_name="No Owner Ltd")
    row = next(
        r for r in staff_client.get("/api/v1/platform/tenants").data["results"]
        if r["id"] == str(t.pk)
    )
    assert row["owner_email"] == "" and row["owner_name"] == "" and row["brands"] == []


def test_platform_tenant_detail_includes_owner_and_brands(staff_client, tenant, owner):
    response = staff_client.get(f"/api/v1/platform/tenants/{tenant.pk}")
    assert response.data["owner_email"] == "owner@platform.test"
    assert response.data["brands"] == []


def test_platform_tenant_list_rejects_unknown_status(staff_client):
    response = staff_client.get("/api/v1/platform/tenants?status=nope")
    assert response.status_code == 400


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


# ---------------------------------------------------------------------------
# Duplicate CAC numbers
# ---------------------------------------------------------------------------


def _tenant(name, rc, status=Tenant.Status.PENDING):
    return Tenant.objects.create(legal_name=name, rc_number=rc, status=status)


def test_rows_flag_other_tenants_with_the_same_rc_number(staff_client):
    a = _tenant("Alpha Ltd", "RC777")
    b = _tenant("Beta Ltd", "RC777")
    _tenant("Gamma Ltd", "RC777", Tenant.Status.REJECTED)  # rejected ones are ignored
    _tenant("Other Ltd", "RC888")

    rows = {r["id"]: r for r in staff_client.get("/api/v1/platform/tenants").data["results"]}
    assert [d["id"] for d in rows[str(a.pk)]["rc_duplicates"]] == [str(b.pk)]
    assert rows[str(b.pk)]["rc_duplicates"][0]["legal_name"] == "Alpha Ltd"
    detail = staff_client.get(f"/api/v1/platform/tenants/{a.pk}").data
    assert len(detail["rc_duplicates"]) == 1


def test_approving_a_duplicate_rc_number_needs_a_resolution(staff_client):
    a = _tenant("Alpha Ltd", "RC777")
    _tenant("Beta Ltd", "RC777")

    blocked = staff_client.post(f"/api/v1/tenants/{a.pk}/approve", {})
    assert blocked.status_code == 409
    assert blocked.data["error"]["code"] == "rc_number_conflict"
    assert blocked.data["error"]["details"]["duplicates"][0]["legal_name"] == "Beta Ltd"
    a.refresh_from_db()
    assert a.status == Tenant.Status.PENDING

    ok = staff_client.post(
        f"/api/v1/tenants/{a.pk}/approve", {"rc_conflict_resolution": "Subsidiary of Beta; CAC confirmed"}
    )
    assert ok.status_code == 200
    a.refresh_from_db()
    assert a.status == Tenant.Status.APPROVED
    assert a.rc_conflict_resolution.startswith("Subsidiary")


def test_approving_without_a_clash_needs_no_resolution(staff_client):
    a = _tenant("Solo Ltd", "RC999")
    assert staff_client.post(f"/api/v1/tenants/{a.pk}/approve", {}).status_code == 200


def test_registration_does_not_reveal_a_duplicate_but_audits_it(staff, owner):
    existing = _tenant("Alpha Ltd", "RC555", Tenant.Status.APPROVED)
    newcomer = User.objects.create_user("new@platform.test", "a-long-test-password")
    c = APIClient()
    c.force_authenticate(user=newcomer)
    response = c.post(
        "/api/v1/tenants/register",
        {"legal_name": "Copycat Ltd", "industry": "cosmetics", "rc_number": "RC555", "city": "Lagos"},
    )
    assert response.status_code == 201
    assert "duplicate" not in str(response.data).lower()
    event = AuditEvent.objects.get(action="tenant.registered", target_id=response.data["id"])
    assert event.metadata["rc_duplicate_of"] == [str(existing.pk)]


# ---------------------------------------------------------------------------
# Evidence checklist
# ---------------------------------------------------------------------------


def _checklist(staff_client, tenant):
    detail = staff_client.get(f"/api/v1/platform/tenants/{tenant.pk}").data
    return {row["key"]: row for row in detail["evidence"]}


def test_evidence_is_derived_from_tenant_data(staff_client, tenant, owner):
    tenant.rc_number = "RC123"
    tenant.support_email = "help@acme.test"
    tenant.save()
    owner.full_name = "Ada Okafor"
    owner.save()

    items = _checklist(staff_client, tenant)
    assert set(items) == {
        "cac_certificate", "brand_ownership", "nafdac_registrations", "signatory", "support_contact",
    }
    assert items["cac_certificate"]["status"] == "manufacturer_provided"
    assert items["signatory"]["status"] == "manufacturer_provided"
    assert items["support_contact"]["detail"] == "help@acme.test"
    assert items["brand_ownership"]["status"] == "missing"
    assert items["nafdac_registrations"]["status"] == "missing"


def test_staff_can_check_and_uncheck_an_item(staff_client, tenant, staff):
    tenant.rc_number = "RC123"
    tenant.save()
    url = f"/api/v1/platform/tenants/{tenant.pk}/evidence/cac_certificate"

    response = staff_client.put(url, {"checked": True, "note": "Matches CAC portal"}, format="json")
    assert response.status_code == 200
    row = next(r for r in response.data if r["key"] == "cac_certificate")
    assert row["status"] == "checked"
    assert row["checked_by"] == staff.email
    assert row["note"] == "Matches CAC portal"
    assert AuditEvent.objects.filter(action="tenant.evidence_checked").exists()

    response = staff_client.put(url, {"checked": False}, format="json")
    row = next(r for r in response.data if r["key"] == "cac_certificate")
    assert row["status"] == "manufacturer_provided"


def test_evidence_rejects_unknown_key_and_missing_flag(staff_client, tenant):
    base = f"/api/v1/platform/tenants/{tenant.pk}/evidence"
    assert staff_client.put(f"{base}/nope", {"checked": True}, format="json").status_code == 404
    assert staff_client.put(f"{base}/signatory", {}, format="json").status_code == 400


def test_evidence_requires_staff(owner, tenant):
    c = APIClient()
    c.force_authenticate(user=owner)
    assert c.put(
        f"/api/v1/platform/tenants/{tenant.pk}/evidence/signatory", {"checked": True}, format="json"
    ).status_code == 403


# ---------------------------------------------------------------------------
# Operations at a glance
# ---------------------------------------------------------------------------


def test_ops_reports_verify_latency_errors_and_queue(staff_client, settings):
    from django.core.cache import cache

    cache.clear()
    settings.TLS_CERT_EXPIRES_AT = "2099-01-01"
    settings.LAST_RESTORE_TEST_AT = ""
    anon = APIClient()
    for _ in range(3):
        anon.get("/api/v1/verify/ABCDEFGHJKMN")  # well-formed, unknown: 404, still measured

    data = staff_client.get("/api/v1/platform/ops").data
    assert data["verification"]["requests"] == 3
    assert data["verification"]["errors"] == 0
    assert data["verification"]["error_rate"] == 0.0
    assert data["verification"]["p95_ms"] in (50, 100, 200, 300, 500, 1000, 2000, 5000)
    assert data["verification"]["scans_24h"] >= 0
    assert data["issuance"] == {"queue_backlog": 0, "failed_jobs": 0}
    assert data["tls_days_remaining"] > 365
    assert data["last_restore_test_at"] is None and data["days_since_restore_test"] is None


def test_ops_is_empty_but_valid_with_no_traffic(staff_client, settings):
    from django.core.cache import cache

    cache.clear()
    settings.TLS_CERT_EXPIRES_AT = "not a date"
    data = staff_client.get("/api/v1/platform/ops").data
    assert data["verification"]["requests"] == 0
    assert data["verification"]["p95_ms"] is None and data["verification"]["error_rate"] is None
    assert data["tls_cert_expires_at"] is None


def test_ops_requires_staff(owner):
    c = APIClient()
    c.force_authenticate(user=owner)
    assert c.get("/api/v1/platform/ops").status_code == 403


# ---------------------------------------------------------------------------
# Staff authenticator reset
# ---------------------------------------------------------------------------


@pytest.fixture
def colleague():
    return User.objects.create_user("colleague@platform.test", "a-long-test-password", is_staff=True)


def _enrol(user):
    from django_otp.plugins.otp_totp.models import TOTPDevice

    return TOTPDevice.objects.create(user=user, name="default", confirmed=True)


def test_staff_list_shows_mfa_state(staff_client, staff, colleague):
    _enrol(colleague)
    rows = {r["email"]: r for r in staff_client.get("/api/v1/platform/staff").data}
    assert rows[colleague.email]["mfa_enabled"] is True
    assert rows[staff.email]["is_you"] is True


def test_staff_can_reset_a_colleagues_authenticator(staff_client, colleague):
    from django_otp.plugins.otp_totp.models import TOTPDevice

    from apps.identity.models import AuthSession
    from apps.identity.services import session_service

    _enrol(colleague)
    session, _ = session_service.create_session(colleague, type("R", (), {
        "headers": {}, "META": {"REMOTE_ADDR": "127.0.0.1"}})())

    response = staff_client.post(
        f"/api/v1/platform/staff/{colleague.pk}/reset-mfa", {"reason": "Lost phone"}, format="json"
    )
    assert response.status_code == 200
    assert response.data["devices_removed"] == 1 and response.data["sessions_revoked"] == 1
    assert not TOTPDevice.objects.filter(user=colleague).exists()
    session.refresh_from_db()
    assert session.revoked_reason == AuthSession.RevocationReason.MFA_RESET
    assert AuditEvent.objects.filter(action="auth.mfa.reset", reason="Lost phone").exists()

    # next login asks them to enrol a new authenticator
    login = APIClient().post(
        "/api/v1/auth/login", {"email": colleague.email, "password": "a-long-test-password"}
    )
    assert login.data["mfa_enrollment_required"] is True


def test_staff_cannot_reset_their_own_authenticator(staff_client, staff):
    response = staff_client.post(
        f"/api/v1/platform/staff/{staff.pk}/reset-mfa", {"reason": "me"}, format="json"
    )
    assert response.status_code == 403
    assert response.data["error"]["code"] == "cannot_reset_own_mfa"


def test_reset_requires_a_reason_and_a_staff_target(staff_client, owner):
    assert staff_client.post(
        f"/api/v1/platform/staff/{owner.pk}/reset-mfa", {"reason": "x"}, format="json"
    ).status_code == 404  # not a staff user
    assert staff_client.post(
        f"/api/v1/platform/staff/{owner.pk}/reset-mfa", {}, format="json"
    ).status_code == 400


def test_reset_staff_mfa_management_command(colleague):
    from django.core.management import call_command
    from django_otp.plugins.otp_totp.models import TOTPDevice

    _enrol(colleague)
    call_command("reset_staff_mfa", colleague.email.upper())
    assert not TOTPDevice.objects.filter(user=colleague).exists()
