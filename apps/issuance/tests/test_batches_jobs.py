"""Tests for the batches & codes API requirements: cross-product batch list/detail, issuance counts,
partial jobs and resume, create-job validation, job list filters and code search."""

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

import apps.issuance.tasks as tasks
from apps.audit.models import AuditEvent
from apps.catalogue.models import Batch, Brand, Product
from apps.core.tenancy import tenant_context
from apps.issuance.models import Code, IssuanceJob
from apps.tenants.models import Membership, Tenant

User = get_user_model()
pytestmark = pytest.mark.django_db

API = "/api/v1"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _client(user, tenant):
    c = APIClient()
    c.force_authenticate(user=user)
    c.credentials(HTTP_X_TENANT_ID=str(tenant.pk))
    return c


@pytest.fixture
def tenant():
    return Tenant.objects.create(legal_name="Batch Co", status=Tenant.Status.APPROVED)


@pytest.fixture
def owner(tenant):
    u = User.objects.create_user("owner@batch.test", "a-long-test-password", full_name="Kemi Okoro")
    Membership.objects.create(user=u, tenant=tenant, role=Membership.Role.OWNER)
    return u


@pytest.fixture
def client(owner, tenant):
    return _client(owner, tenant)


@pytest.fixture
def brand(tenant):
    with tenant_context(tenant.pk):
        return Brand.objects.create(tenant=tenant, name="Glow", slug="glow")


def make_product(tenant, brand, sku="LOT-400", name="Shea Butter Body Lotion", approved=True, **kw):
    extra = {"status": Product.Status.APPROVED, "approved_version": 1} if approved else {}
    with tenant_context(tenant.pk):
        return Product.objects.create(
            tenant=tenant, brand=brand, name=name, sku=sku, variant=kw.pop("variant", "Unscented"),
            size_value=kw.pop("size_value", 400), size_unit=kw.pop("size_unit", "ml"), **extra, **kw,
        )


def make_batch(tenant, product, number="B-001", **kw):
    with tenant_context(tenant.pk):
        return Batch.objects.create(
            tenant=tenant, product=product, batch_number=number, quantity=kw.pop("quantity", 1000), **kw
        )


@pytest.fixture
def product(tenant, brand):
    return make_product(tenant, brand)


@pytest.fixture
def batch(tenant, product):
    return make_batch(tenant, product)


def issue(client, batch, quantity, key="job-key-0001"):
    return client.post(
        f"{API}/issuance/jobs", {"batch_id": str(batch.pk), "quantity": quantity},
        format="json", HTTP_IDEMPOTENCY_KEY=key,
    )


def make_codes(tenant, job, n, prefix="C", deactivated=0):
    with tenant_context(tenant.pk):
        for i in range(n):
            Code.objects.create(
                tenant=tenant, code=f"{prefix}{job.pk.hex[:6]}{i:05d}", batch=job.batch,
                product=job.batch.product, issuance_job=job,
                status=Code.Status.DEACTIVATED if i < deactivated else Code.Status.ACTIVE,
            )


def make_job(tenant, batch, requested, issued, status, owner=None):
    with tenant_context(tenant.pk):
        return IssuanceJob.objects.create(
            tenant=tenant, batch=batch, quantity_requested=requested, quantity_issued=issued,
            status=status, submitted_by=owner,
        )


@pytest.fixture
def flaky_generation(monkeypatch):
    """Make the third chunk of generation fail once (500 + 500 + failure on the last 200)."""
    real = tasks._generate_chunk
    calls = {"n": 0}

    def flaky(job, product, size):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("storage unavailable")
        return real(job, product, size)

    monkeypatch.setattr(tasks, "_generate_chunk", flaky)
    return calls


# ---------------------------------------------------------------------------
# Batches across products
# ---------------------------------------------------------------------------


def test_batch_list_spans_products_and_names_them(client, tenant, brand, product, batch):
    other = make_product(tenant, brand, sku="SOAP-1", name="Black Soap", variant="", size_value=100, size_unit="g")
    make_batch(tenant, other, "B-002")
    resp = client.get(f"{API}/catalogue/batches")
    assert resp.status_code == 200
    assert resp.data["count"] == 2
    names = {r["batch_number"]: r["product_name"] for r in resp.data["results"]}
    assert names == {
        "B-001": "Shea Butter Body Lotion · Unscented · 400 ml",
        "B-002": "Black Soap · 100 g",
    }


def test_batch_detail_by_id_alone(client, batch, product):
    resp = client.get(f"{API}/catalogue/batches/{batch.pk}")
    assert resp.status_code == 200
    assert resp.data["id"] == str(batch.pk)
    assert resp.data["product_id"] == str(product.pk)
    assert resp.data["declared_quantity"] == 1000


def test_batches_are_tenant_scoped(client, batch):
    other = Tenant.objects.create(legal_name="Rival", status=Tenant.Status.APPROVED)
    rival_brand = Brand.unscoped.create(tenant=other, name="R", slug="r")
    rival_batch = make_batch(other, make_product(other, rival_brand, sku="R-1"), "RB-1")
    assert client.get(f"{API}/catalogue/batches/{rival_batch.pk}").status_code == 404
    numbers = [r["batch_number"] for r in client.get(f"{API}/catalogue/batches").data["results"]]
    assert numbers == ["B-001"]


def test_batch_search_by_number_and_product_name(client, tenant, brand, batch):
    other = make_product(tenant, brand, sku="SOAP-1", name="Black Soap")
    make_batch(tenant, other, "XYZ-9")
    by_number = client.get(f"{API}/catalogue/batches?q=xyz").data["results"]
    by_product = client.get(f"{API}/catalogue/batches?q=black").data["results"]
    assert [r["batch_number"] for r in by_number] == ["XYZ-9"]
    assert [r["batch_number"] for r in by_product] == ["XYZ-9"]


def test_batch_status_filters_including_computed_expired(client, tenant, product):
    yesterday = timezone.localdate() - timedelta(days=1)
    make_batch(tenant, product, "ACTIVE-1")
    make_batch(tenant, product, "RECALLED-1", status=Batch.Status.RECALLED)
    make_batch(tenant, product, "PAST-1", expires_at=yesterday)  # active in the DB, expired by date
    make_batch(tenant, product, "STORED-EXP", status=Batch.Status.EXPIRED)

    def numbers(qs):
        return sorted(r["batch_number"] for r in client.get(f"{API}/catalogue/batches?{qs}").data["results"])

    assert numbers("status=active") == ["ACTIVE-1"]
    assert numbers("status=recalled") == ["RECALLED-1"]
    assert numbers("status=expired") == ["PAST-1", "STORED-EXP"]
    assert numbers("status=active&status=recalled") == ["ACTIVE-1", "RECALLED-1"]
    past = next(r for r in client.get(f"{API}/catalogue/batches?status=expired").data["results"] if r["batch_number"] == "PAST-1")
    assert past["status"] == "expired"


def test_batch_list_rejects_unknown_status_and_ordering(client):
    assert client.get(f"{API}/catalogue/batches?status=banana").status_code == 400
    assert client.get(f"{API}/catalogue/batches?ordering=banana").status_code == 400
    assert client.get(f"{API}/catalogue/batches?product_id=not-a-uuid").status_code == 400


def test_batch_ordering_and_product_filter(client, tenant, brand, product):
    other = make_product(tenant, brand, sku="SOAP-1", name="Black Soap")
    make_batch(tenant, product, "B-LATE", expires_at=date(2030, 1, 1))
    make_batch(tenant, product, "B-EARLY", expires_at=date(2028, 1, 1))
    make_batch(tenant, other, "B-OTHER")
    ordered = client.get(f"{API}/catalogue/batches?product_id={product.pk}&ordering=best_before").data["results"]
    assert [r["batch_number"] for r in ordered] == ["B-EARLY", "B-LATE"]
    by_number = client.get(f"{API}/catalogue/batches?ordering=-batch_number").data["results"]
    assert [r["batch_number"] for r in by_number] == ["B-OTHER", "B-LATE", "B-EARLY"]


def test_batch_list_is_paginated(client, tenant, product):
    for i in range(5):
        make_batch(tenant, product, f"P-{i}")
    resp = client.get(f"{API}/catalogue/batches?page_size=2")
    assert resp.data["count"] == 5
    assert len(resp.data["results"]) == 2
    assert resp.data["next"]


def test_active_batch_past_best_before_reads_expired_everywhere(client, tenant, product):
    old = make_batch(tenant, product, "OLD-1", expires_at=timezone.localdate() - timedelta(days=3))
    assert client.get(f"{API}/catalogue/batches/{old.pk}").data["status"] == "expired"
    assert client.get(f"{API}/catalogue/products/{product.pk}/batches/{old.pk}").data["status"] == "expired"


def test_declared_quantity_alias_on_write(client, product):
    resp = client.post(
        f"{API}/catalogue/products/{product.pk}/batches",
        {"product_id": str(product.pk), "batch_number": "DQ-1", "declared_quantity": 2500},
        format="json",
    )
    assert resp.status_code == 201
    assert resp.data["declared_quantity"] == 2500 and resp.data["quantity"] == 2500
    bad = client.post(
        f"{API}/catalogue/products/{product.pk}/batches",
        {"product_id": str(product.pk), "batch_number": "DQ-2", "declared_quantity": 0},
        format="json",
    )
    assert bad.status_code == 400


# ---------------------------------------------------------------------------
# Issuance counts
# ---------------------------------------------------------------------------


def test_issuance_counts_on_detail_and_list(client, tenant, batch, owner):
    done = make_job(tenant, batch, 5, 5, IssuanceJob.Status.DONE, owner)
    partial = make_job(tenant, batch, 10, 6, IssuanceJob.Status.PARTIAL, owner)
    make_codes(tenant, done, 5, prefix="D", deactivated=2)
    make_codes(tenant, partial, 6, prefix="P")

    expected = {"requested": 15, "generated": 11, "failed": 4, "deactivated": 2}
    assert client.get(f"{API}/catalogue/batches/{batch.pk}").data["issuance"] == expected
    listed = client.get(f"{API}/catalogue/batches").data["results"][0]
    assert listed["issuance"] == expected
    scoped = client.get(f"{API}/catalogue/products/{batch.product_id}/batches").data[0]
    assert scoped["issuance"] == expected


def test_issuance_counts_are_zero_for_untouched_batch(client, batch):
    assert client.get(f"{API}/catalogue/batches/{batch.pk}").data["issuance"] == {
        "requested": 0, "generated": 0, "failed": 0, "deactivated": 0,
    }


def test_counts_do_not_leak_between_batches(client, tenant, product, owner):
    a, b = make_batch(tenant, product, "A"), make_batch(tenant, product, "B")
    make_codes(tenant, make_job(tenant, a, 3, 3, IssuanceJob.Status.DONE, owner), 3, prefix="A")
    rows = {r["batch_number"]: r["issuance"] for r in client.get(f"{API}/catalogue/batches").data["results"]}
    assert rows["A"]["generated"] == 3
    assert rows["B"]["generated"] == 0


# ---------------------------------------------------------------------------
# Create-job validation
# ---------------------------------------------------------------------------


def test_cannot_issue_for_unapproved_product(client, tenant, brand):
    draft = make_product(tenant, brand, sku="DRAFT-1", approved=False)
    resp = issue(client, make_batch(tenant, draft, "D-1"), 10)
    assert resp.status_code == 409
    assert resp.data["error"]["code"] == "product_not_approved"


@pytest.mark.parametrize("kwargs", [
    {"status": Batch.Status.RECALLED},
    {"status": Batch.Status.EXPIRED},
    {"expires_at": date(2020, 1, 1)},
])
def test_cannot_issue_for_inactive_or_expired_batch(client, tenant, product, kwargs):
    resp = issue(client, make_batch(tenant, product, "X-1", **kwargs), 10)
    assert resp.status_code == 409
    assert resp.data["error"]["code"] == "batch_not_active"


# ---------------------------------------------------------------------------
# Job fields and list
# ---------------------------------------------------------------------------


def test_job_exposes_names_started_by_and_counts(client, tenant, batch, owner):
    job = make_job(tenant, batch, 10, 6, IssuanceJob.Status.PARTIAL, owner)
    data = client.get(f"{API}/issuance/jobs/{job.pk}").data
    assert data["batch_number"] == "B-001"
    assert data["product_name"] == "Shea Butter Body Lotion · Unscented · 400 ml"
    assert data["started_by"] == {"id": str(owner.pk), "name": "Kemi Okoro"}
    assert data["started_at"] == data["created_at"]
    assert (data["quantity_generated"], data["quantity_failed"]) == (6, 4)
    assert data["status"] == "partial"


def test_started_by_falls_back_to_email_and_handles_missing_user(client, tenant, batch):
    nameless = User.objects.create_user("nameless@batch.test", "a-long-test-password")
    with_user = make_job(tenant, batch, 1, 1, IssuanceJob.Status.DONE, nameless)
    without = make_job(tenant, batch, 1, 1, IssuanceJob.Status.DONE, None)
    assert client.get(f"{API}/issuance/jobs/{with_user.pk}").data["started_by"]["name"] == "nameless@batch.test"
    assert client.get(f"{API}/issuance/jobs/{without.pk}").data["started_by"] is None


def test_quantity_failed_is_zero_unless_stopped(client, tenant, batch):
    running = make_job(tenant, batch, 10, 3, IssuanceJob.Status.PROCESSING)
    done = make_job(tenant, batch, 10, 10, IssuanceJob.Status.DONE)
    assert client.get(f"{API}/issuance/jobs/{running.pk}").data["quantity_failed"] == 0
    assert client.get(f"{API}/issuance/jobs/{done.pk}").data["quantity_failed"] == 0


def test_job_list_filters_by_batch_and_status(client, tenant, product, batch, owner):
    other = make_batch(tenant, product, "B-002")
    a = make_job(tenant, batch, 5, 5, IssuanceJob.Status.DONE, owner)
    b = make_job(tenant, batch, 5, 2, IssuanceJob.Status.PARTIAL, owner)
    c = make_job(tenant, other, 5, 5, IssuanceJob.Status.DONE, owner)

    def ids(qs):
        return {r["id"] for r in client.get(f"{API}/issuance/jobs?{qs}").data}

    assert ids(f"batch_id={batch.pk}") == {str(a.pk), str(b.pk)}
    assert ids("status=partial") == {str(b.pk)}
    assert ids("status=partial&status=done") == {str(a.pk), str(b.pk), str(c.pk)}
    assert ids(f"batch_id={other.pk}&status=done") == {str(c.pk)}
    assert client.get(f"{API}/issuance/jobs?status=nope").status_code == 400
    assert client.get(f"{API}/issuance/jobs?batch_id=nope").status_code == 400


# ---------------------------------------------------------------------------
# Partial jobs and resume
# ---------------------------------------------------------------------------


def test_failure_mid_run_leaves_partial_job_with_codes_kept(client, batch, flaky_generation):
    resp = issue(client, batch, 1200)
    job = IssuanceJob.unscoped.get(pk=resp.data["id"])
    assert job.status == IssuanceJob.Status.PARTIAL
    assert job.quantity_issued == 1000
    assert Code.unscoped.filter(issuance_job=job).count() == 1000
    assert "storage unavailable" in job.errors[0]["error"]
    data = client.get(f"{API}/issuance/jobs/{job.pk}").data
    assert (data["quantity_generated"], data["quantity_failed"]) == (1000, 200)


def test_failure_before_any_code_is_failed_not_partial(client, batch, monkeypatch):
    def boom(job, product, size):
        raise RuntimeError("nope")

    monkeypatch.setattr(tasks, "_generate_chunk", boom)
    job = IssuanceJob.unscoped.get(pk=issue(client, batch, 50).data["id"])
    assert job.status == IssuanceJob.Status.FAILED
    assert job.quantity_issued == 0


def test_resume_generates_only_the_missing_codes(client, batch, flaky_generation, django_capture_on_commit_callbacks):
    job_id = issue(client, batch, 1200).data["id"]
    before = set(Code.unscoped.filter(issuance_job_id=job_id).values_list("code", flat=True))
    assert len(before) == 1000

    with django_capture_on_commit_callbacks(execute=True):
        resp = client.post(f"{API}/issuance/jobs/{job_id}/resume")
    assert resp.status_code == 202

    job = IssuanceJob.unscoped.get(pk=job_id)
    after = set(Code.unscoped.filter(issuance_job_id=job_id).values_list("code", flat=True))
    assert job.status == IssuanceJob.Status.DONE
    assert job.quantity_issued == 1200 and len(after) == 1200
    assert before <= after  # nothing regenerated or replaced
    assert job.errors == []
    event = AuditEvent.objects.get(action="issuance.job.resumed", target_id=str(job.pk))
    assert event.before == {"status": "partial", "generated": 1000}
    assert event.after["remaining"] == 200


def test_resume_a_failed_job_that_has_some_codes(client, tenant, batch, owner, django_capture_on_commit_callbacks):
    job = make_job(tenant, batch, 30, 10, IssuanceJob.Status.FAILED, owner)
    make_codes(tenant, job, 10)
    with django_capture_on_commit_callbacks(execute=True):
        assert client.post(f"{API}/issuance/jobs/{job.pk}/resume").status_code == 202
    job.refresh_from_db()
    assert job.status == IssuanceJob.Status.DONE
    assert Code.unscoped.filter(issuance_job=job).count() == 30


def test_resume_refuses_jobs_that_are_not_resumable(client, tenant, batch, owner):
    done = make_job(tenant, batch, 5, 5, IssuanceJob.Status.DONE, owner)
    empty_failed = make_job(tenant, batch, 5, 0, IssuanceJob.Status.FAILED, owner)
    for job in (done, empty_failed):
        resp = client.post(f"{API}/issuance/jobs/{job.pk}/resume")
        assert resp.status_code == 409
        assert resp.data["error"]["code"] == "job_not_resumable"


def test_resume_on_a_running_job_does_not_start_a_second_run(client, tenant, batch, owner):
    running = make_job(tenant, batch, 5, 2, IssuanceJob.Status.PROCESSING, owner)
    with patch("apps.issuance.views.generate_codes") as task:
        resp = client.post(f"{API}/issuance/jobs/{running.pk}/resume")
    assert resp.status_code == 202
    assert resp.data["status"] == "processing"
    task.delay.assert_not_called()


def test_repeated_resume_runs_once(client, tenant, batch, owner, django_capture_on_commit_callbacks):
    job = make_job(tenant, batch, 20, 5, IssuanceJob.Status.PARTIAL, owner)
    make_codes(tenant, job, 5)
    with patch("apps.issuance.views.generate_codes") as task:
        with django_capture_on_commit_callbacks(execute=True):
            first = client.post(f"{API}/issuance/jobs/{job.pk}/resume")
        second = client.post(f"{API}/issuance/jobs/{job.pk}/resume")  # job is now processing
    assert first.status_code == second.status_code == 202
    assert task.delay.call_count == 1


def test_resume_with_idempotency_key_replays(client, tenant, batch, owner, django_capture_on_commit_callbacks):
    job = make_job(tenant, batch, 20, 5, IssuanceJob.Status.PARTIAL, owner)
    make_codes(tenant, job, 5)
    with patch("apps.issuance.views.generate_codes") as task:
        with django_capture_on_commit_callbacks(execute=True):
            one = client.post(f"{API}/issuance/jobs/{job.pk}/resume", HTTP_IDEMPOTENCY_KEY="resume-key-001")
        two = client.post(f"{API}/issuance/jobs/{job.pk}/resume", HTTP_IDEMPOTENCY_KEY="resume-key-001")
    assert one.status_code == two.status_code == 202
    assert two["Idempotent-Replayed"] == "true"
    assert task.delay.call_count == 1


def test_resume_rejected_when_batch_is_no_longer_active(client, tenant, batch, owner):
    job = make_job(tenant, batch, 20, 5, IssuanceJob.Status.PARTIAL, owner)
    make_codes(tenant, job, 5)
    batch.status = Batch.Status.RECALLED
    batch.save(update_fields=["status"])
    resp = client.post(f"{API}/issuance/jobs/{job.pk}/resume")
    assert resp.status_code == 409
    assert resp.data["error"]["code"] == "batch_not_active"
    job.refresh_from_db()
    assert job.status == IssuanceJob.Status.PARTIAL  # untouched


def test_resume_denied_for_investigator_and_viewer(tenant, batch, owner):
    job = make_job(tenant, batch, 20, 5, IssuanceJob.Status.PARTIAL, owner)
    for role in (Membership.Role.INVESTIGATOR, Membership.Role.VIEWER):
        user = User.objects.create_user(f"{role}@batch.test", "a-long-test-password")
        Membership.objects.create(user=user, tenant=tenant, role=role)
        assert _client(user, tenant).post(f"{API}/issuance/jobs/{job.pk}/resume").status_code == 403


def test_resume_other_tenants_job_is_not_found(client, owner):
    other = Tenant.objects.create(legal_name="Rival", status=Tenant.Status.APPROVED)
    rival_brand = Brand.unscoped.create(tenant=other, name="R", slug="r")
    rival_batch = make_batch(other, make_product(other, rival_brand, sku="R-1"), "RB-1")
    job = make_job(other, rival_batch, 5, 2, IssuanceJob.Status.PARTIAL)
    assert client.post(f"{API}/issuance/jobs/{job.pk}/resume").status_code == 404


# ---------------------------------------------------------------------------
# Code search
# ---------------------------------------------------------------------------


def test_code_search_partial_case_insensitive_and_dash_tolerant(client, tenant, batch, owner):
    job = make_job(tenant, batch, 2, 2, IssuanceJob.Status.DONE, owner)
    with tenant_context(tenant.pk):
        for text in ("7KQ2M9XW4PRTAAAA", "ab-cd_EFGH12345"):
            Code.objects.create(
                tenant=tenant, code=text, batch=batch, product=batch.product, issuance_job=job
            )

    def found(q):
        return [r["code"] for r in client.get(f"{API}/issuance/codes", {"q": q}).data["results"]]

    assert found("m9xw") == ["7KQ2M9XW4PRTAAAA"]  # partial, any case
    assert found("7KQ2-M9XW-4PRT") == ["7KQ2M9XW4PRTAAAA"]  # typed with dashes
    assert found("ab-cd_") == ["ab-cd_EFGH12345"]  # dashes that belong to the code still match
    assert found("zzzz") == []
    assert len(client.get(f"{API}/issuance/codes").data["results"]) == 2


# ---------------------------------------------------------------------------
# Regression: product activity log uses the user's full_name
# ---------------------------------------------------------------------------


def test_product_activity_shows_actor_name(client, product):
    client.patch(f"{API}/catalogue/products/{product.pk}", {"description": "notes"}, format="json")
    resp = client.get(f"{API}/catalogue/products/{product.pk}/activity")
    assert resp.status_code == 200
    assert resp.data["results"][0]["actor"]["name"] == "Kemi Okoro"
