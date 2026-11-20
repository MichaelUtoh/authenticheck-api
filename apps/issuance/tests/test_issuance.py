"""Tests for Phase 3 – Issuance and export."""

import csv
import io

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.catalogue.models import Batch, Brand, Product
from apps.core.tenancy import tenant_context
from apps.issuance.models import Code, IssuanceJob
from apps.tenants.models import Membership, Tenant

User = get_user_model()
pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def owner():
    return User.objects.create_user("owner@issuance.test", "a-long-test-password")


@pytest.fixture
def tenant(owner):
    t = Tenant.objects.create(legal_name="IssuanceCo", status=Tenant.Status.APPROVED)
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
        brand = Brand.objects.create(tenant=tenant, name="IssueBrand", slug="issuebrand")
        product = Product.objects.create(
            tenant=tenant, brand=brand, name="Issue Product", sku="ISS-001",
            status=Product.Status.APPROVED, approved_version=1,
        )
        return Batch.objects.create(
            tenant=tenant, product=product, batch_number="BATCH-001", quantity=1000
        )


@pytest.fixture
def done_job(owner, tenant, batch):
    """A completed IssuanceJob with 5 codes already generated."""
    with tenant_context(tenant.pk):
        job = IssuanceJob.objects.create(
            tenant=tenant,
            batch=batch,
            quantity_requested=5,
            submitted_by=owner,
            status=IssuanceJob.Status.DONE,
            quantity_issued=5,
        )
        for i in range(5):
            Code.objects.create(
                tenant=tenant,
                code=f"testcode{i:04}",
                batch=batch,
                product=batch.product,
                issuance_job=job,
            )
    return job


# ---------------------------------------------------------------------------
# IssuanceJob create (POST /api/v1/issuance/jobs)
# ---------------------------------------------------------------------------


def test_create_job_returns_202(client, batch):
    response = client.post(
        "/api/v1/issuance/jobs",
        {"batch_id": str(batch.pk), "quantity": 10},
        HTTP_IDEMPOTENCY_KEY="test-key-001",
    )
    assert response.status_code == 202
    assert response.data["status"] == IssuanceJob.Status.PENDING
    assert response.data["quantity_requested"] == 10


def test_create_job_idempotency_replay(client, batch):
    key = "idem-key-replay"
    payload = {"batch_id": str(batch.pk), "quantity": 10}
    r1 = client.post("/api/v1/issuance/jobs", payload, HTTP_IDEMPOTENCY_KEY=key)
    r2 = client.post("/api/v1/issuance/jobs", payload, HTTP_IDEMPOTENCY_KEY=key)
    assert r1.status_code == 202
    assert r2.status_code == 202
    assert r2["Idempotent-Replayed"] == "true"
    assert r1.data["id"] == r2.data["id"]
    assert IssuanceJob.unscoped.filter(batch=batch).count() == 1


def test_create_job_requires_idempotency_key(client, batch):
    response = client.post(
        "/api/v1/issuance/jobs",
        {"batch_id": str(batch.pk), "quantity": 10},
    )
    assert response.status_code == 400


def test_create_job_rejects_inactive_batch(owner, tenant, batch):
    batch.status = Batch.Status.RECALLED
    batch.save(update_fields=["status", "updated_at"])

    c = APIClient()
    c.force_authenticate(user=owner)
    c.credentials(HTTP_X_TENANT_ID=str(tenant.pk))
    response = c.post(
        "/api/v1/issuance/jobs",
        {"batch_id": str(batch.pk), "quantity": 5},
        HTTP_IDEMPOTENCY_KEY="inactive-batch-key",
    )
    assert response.status_code == 409
    assert response.data["error"]["code"] == "batch_not_active"


@pytest.mark.parametrize(
    "role,expected",
    [
        (Membership.Role.VIEWER, 403),
        (Membership.Role.INVESTIGATOR, 403),
        (Membership.Role.PRODUCTION_QUALITY, 202),
    ],
)
def test_create_job_role_access(tenant, batch, role, expected):
    user = User.objects.create_user(f"{role}@issuance.test", "a-long-test-password")
    Membership.objects.create(user=user, tenant=tenant, role=role)
    c = APIClient()
    c.force_authenticate(user=user)
    c.credentials(HTTP_X_TENANT_ID=str(tenant.pk))
    response = c.post(
        "/api/v1/issuance/jobs",
        {"batch_id": str(batch.pk), "quantity": 5},
        HTTP_IDEMPOTENCY_KEY=f"{role}-key-001",
    )
    assert response.status_code == expected


# ---------------------------------------------------------------------------
# Celery task (runs eagerly via CELERY_TASK_ALWAYS_EAGER)
# ---------------------------------------------------------------------------


def test_generate_codes_task_produces_correct_count(client, batch):
    response = client.post(
        "/api/v1/issuance/jobs",
        {"batch_id": str(batch.pk), "quantity": 20},
        HTTP_IDEMPOTENCY_KEY="gen-task-key",
    )
    assert response.status_code == 202
    job = IssuanceJob.unscoped.get(pk=response.data["id"])
    assert job.status == IssuanceJob.Status.DONE
    assert job.quantity_issued == 20
    assert Code.unscoped.filter(issuance_job=job).count() == 20


def test_generate_codes_codes_are_globally_unique(client, batch):
    client.post(
        "/api/v1/issuance/jobs",
        {"batch_id": str(batch.pk), "quantity": 50},
        HTTP_IDEMPOTENCY_KEY="unique-check-key",
    )
    codes = list(Code.unscoped.filter(batch=batch).values_list("code", flat=True))
    assert len(codes) == len(set(codes))


def test_generate_codes_task_is_idempotent_on_rerun(owner, tenant, batch):
    """Re-running the task on a DONE job is a no-op."""
    with tenant_context(tenant.pk):
        job = IssuanceJob.objects.create(
            tenant=tenant,
            batch=batch,
            quantity_requested=5,
            submitted_by=owner,
            status=IssuanceJob.Status.DONE,
            quantity_issued=5,
        )
    from apps.issuance.tasks import generate_codes
    generate_codes(str(job.pk))  # should not raise or create codes
    assert Code.unscoped.filter(issuance_job=job).count() == 0


# ---------------------------------------------------------------------------
# Job detail and reconciliation
# ---------------------------------------------------------------------------


def test_job_detail_returns_reconciliation(client, done_job):
    response = client.get(f"/api/v1/issuance/jobs/{done_job.pk}")
    assert response.status_code == 200
    assert response.data["is_reconciled"] is True
    assert response.data["quantity_issued"] == 5


def test_job_list_returns_tenant_jobs(client, done_job):
    response = client.get("/api/v1/issuance/jobs")
    assert response.status_code == 200
    assert len(response.data) == 1
    assert response.data[0]["id"] == str(done_job.pk)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def test_export_streams_csv(client, done_job):
    response = client.get(f"/api/v1/issuance/jobs/{done_job.pk}/export")
    assert response.status_code == 200
    assert "text/csv" in response["Content-Type"]
    assert f"codes-{done_job.pk}.csv" in response["Content-Disposition"]

    content = b"".join(response.streaming_content).decode()
    lines = [l for l in content.splitlines() if l]
    assert lines[0] == "code,batch_number,product_sku,product_name,issued_at,code_display,verify_url"
    assert len(lines) == 6  # header + 5 codes


def test_export_includes_display_code_and_verify_url(client, done_job, settings):
    settings.VERIFY_BASE_URL = "https://verify.example.com/"
    response = client.get(f"/api/v1/issuance/jobs/{done_job.pk}/export")
    rows = list(csv.DictReader(io.StringIO(b"".join(response.streaming_content).decode())))
    row = next(r for r in rows if r["code"] == "testcode0000")
    assert row["code_display"] == "test-code-0000"
    assert row["verify_url"] == "https://verify.example.com/v/testcode0000"


def test_display_code_and_verify_url_helpers(settings):
    from apps.issuance.codes import display_code, verify_url

    settings.VERIFY_BASE_URL = "https://verify.example.com"
    assert display_code("7KQ2M9XW4PRT") == "7KQ2-M9XW-4PRT"
    assert display_code("A" * 22) == "A" * 22  # old converted code: unchanged
    assert verify_url("7KQ2M9XW4PRT") == "https://verify.example.com/v/7KQ2M9XW4PRT"


def test_export_unavailable_for_pending_job(client, owner, tenant, batch):
    with tenant_context(tenant.pk):
        job = IssuanceJob.objects.create(
            tenant=tenant,
            batch=batch,
            quantity_requested=5,
            submitted_by=owner,
            status=IssuanceJob.Status.PENDING,
        )
    response = client.get(f"/api/v1/issuance/jobs/{job.pk}/export")
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Code list and filtering
# ---------------------------------------------------------------------------


def test_code_list_returns_tenant_codes(client, done_job):
    response = client.get("/api/v1/issuance/codes")
    assert response.status_code == 200
    assert response.data["count"] == 5
    assert len(response.data["results"]) == 5


def test_code_list_filters_by_batch(client, done_job, batch):
    response = client.get(f"/api/v1/issuance/codes?batch_id={batch.pk}")
    assert response.status_code == 200
    assert response.data["count"] == 5


def test_code_list_filters_by_status(client, done_job, tenant):
    # Deactivate one code
    code = Code.unscoped.filter(issuance_job=done_job).first()
    code.status = Code.Status.DEACTIVATED
    code.save(update_fields=["status", "updated_at"])

    response = client.get("/api/v1/issuance/codes?status=deactivated")
    assert response.status_code == 200
    assert response.data["count"] == 1


def test_code_list_filters_by_job(client, done_job):
    response = client.get(f"/api/v1/issuance/codes?job_id={done_job.pk}")
    assert response.status_code == 200
    assert response.data["count"] == 5


# ---------------------------------------------------------------------------
# Single code deactivation
# ---------------------------------------------------------------------------


def test_deactivate_code(client, done_job):
    code = Code.unscoped.filter(issuance_job=done_job).first()
    response = client.post(
        f"/api/v1/issuance/codes/{code.code}/deactivate",
        {"reason": "defective"},
    )
    assert response.status_code == 200
    assert response.data["status"] == Code.Status.DEACTIVATED
    assert response.data["deactivation_reason"] == "defective"
    code.refresh_from_db()
    assert code.deactivated_at is not None


def test_deactivate_already_deactivated_code(client, done_job):
    code = Code.unscoped.filter(issuance_job=done_job).first()
    code.status = Code.Status.DEACTIVATED
    code.save(update_fields=["status", "updated_at"])

    response = client.post(f"/api/v1/issuance/codes/{code.code}/deactivate", {})
    assert response.status_code == 400


def test_deactivate_code_wrong_tenant(client, done_job):
    other_tenant = Tenant.objects.create(
        legal_name="OtherCo", status=Tenant.Status.APPROVED
    )
    other_user = User.objects.create_user("other@issuance.test", "a-long-test-password")
    Membership.objects.create(user=other_user, tenant=other_tenant, role=Membership.Role.OWNER)

    code = Code.unscoped.filter(issuance_job=done_job).first()
    c = APIClient()
    c.force_authenticate(user=other_user)
    c.credentials(HTTP_X_TENANT_ID=str(other_tenant.pk))
    response = c.post(f"/api/v1/issuance/codes/{code.code}/deactivate", {})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Batch code deactivation
# ---------------------------------------------------------------------------


def test_batch_deactivate_all_active_codes(client, done_job, batch):
    response = client.post(
        f"/api/v1/issuance/batches/{batch.pk}/deactivate",
        {"reason": "recall"},
    )
    assert response.status_code == 200
    assert response.data["codes_deactivated"] == 5
    assert Code.unscoped.filter(issuance_job=done_job, status=Code.Status.ACTIVE).count() == 0


def test_batch_deactivate_skips_already_deactivated(client, done_job, batch):
    # Pre-deactivate 2
    pks = list(Code.unscoped.filter(issuance_job=done_job).order_by("code").values_list("pk", flat=True)[:2])
    Code.unscoped.filter(pk__in=pks).update(status=Code.Status.DEACTIVATED)
    response = client.post(
        f"/api/v1/issuance/batches/{batch.pk}/deactivate", {"reason": "recall"}
    )
    assert response.status_code == 200
    assert response.data["codes_deactivated"] == 3


def test_batch_deactivate_requires_owner_or_admin(tenant, batch):
    viewer = User.objects.create_user("v2@issuance.test", "a-long-test-password")
    Membership.objects.create(user=viewer, tenant=tenant, role=Membership.Role.VIEWER)
    c = APIClient()
    c.force_authenticate(user=viewer)
    c.credentials(HTTP_X_TENANT_ID=str(tenant.pk))
    response = c.post(f"/api/v1/issuance/batches/{batch.pk}/deactivate", {})
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


def test_codes_from_other_tenant_not_visible(client, done_job):
    other_tenant = Tenant.objects.create(
        legal_name="OtherIssuanceCo", status=Tenant.Status.APPROVED
    )
    other_user = User.objects.create_user("other2@issuance.test", "a-long-test-password")
    Membership.objects.create(user=other_user, tenant=other_tenant, role=Membership.Role.OWNER)
    with tenant_context(other_tenant.pk):
        other_brand = Brand.objects.create(
            tenant=other_tenant, name="OtherBrand", slug="otherbrand"
        )
        other_product = Product.objects.create(
            tenant=other_tenant, brand=other_brand, name="OP", sku="OP-001"
        )
        other_batch = Batch.objects.create(
            tenant=other_tenant, product=other_product, batch_number="OB-001", quantity=10
        )
        other_job = IssuanceJob.objects.create(
            tenant=other_tenant,
            batch=other_batch,
            quantity_requested=3,
            submitted_by=other_user,
            status=IssuanceJob.Status.DONE,
            quantity_issued=3,
        )
        for i in range(3):
            Code.objects.create(
                tenant=other_tenant,
                code=f"othercod{i:04}",
                batch=other_batch,
                product=other_product,
                issuance_job=other_job,
            )

    response = client.get("/api/v1/issuance/codes")
    assert response.status_code == 200
    codes = [c["code"] for c in response.data["results"]]
    assert not any(c.startswith("othercode") for c in codes)
