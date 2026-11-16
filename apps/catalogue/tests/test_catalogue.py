"""Tests for catalogue Phase 2 endpoints.

Fixture setup: one approved tenant + owner. Each test family exercises one resource.
"""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.catalogue.models import Batch, Brand, BulkImportJob, Product, RegulatoryRef
from apps.core.tenancy import tenant_context
from apps.tenants.models import Membership, Tenant

User = get_user_model()
pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def owner():
    return User.objects.create_user("owner@catalogue.test", "a-long-test-password")


@pytest.fixture
def tenant(owner):
    t = Tenant.objects.create(legal_name="Catalogue Corp", status=Tenant.Status.APPROVED)
    Membership.objects.create(user=owner, tenant=t, role=Membership.Role.OWNER)
    return t


@pytest.fixture
def client(owner, tenant):
    c = APIClient()
    c.force_authenticate(user=owner)
    c.credentials(HTTP_X_TENANT_ID=str(tenant.pk))
    return c


@pytest.fixture
def brand(owner, tenant):
    with tenant_context(tenant.pk):
        return Brand.objects.create(tenant=tenant, name="AcmeCare", slug="acmecare")


@pytest.fixture
def product(tenant, brand):
    with tenant_context(tenant.pk):
        return Product.objects.create(
            tenant=tenant, brand=brand, name="AcmeCare 50ml", sku="ACM-50ML"
        )


@pytest.fixture
def batch(tenant, product):
    with tenant_context(tenant.pk):
        return Batch.objects.create(
            tenant=tenant, product=product, batch_number="B001", quantity=1000
        )


# ---------------------------------------------------------------------------
# Brands
# ---------------------------------------------------------------------------


def test_list_brands_empty(client):
    response = client.get("/api/v1/catalogue/brands")
    assert response.status_code == 200
    assert response.data == []


def test_create_brand(client):
    response = client.post("/api/v1/catalogue/brands", {"name": "PureSkin", "slug": "pureskin"})
    assert response.status_code == 201
    assert response.data["slug"] == "pureskin"
    assert Brand.unscoped.filter(slug="pureskin").exists()


def test_create_brand_slug_normalised_to_lowercase(client):
    response = client.post("/api/v1/catalogue/brands", {"name": "PureSkin", "slug": "PureSKIN"})
    assert response.status_code == 201
    assert response.data["slug"] == "pureskin"


def test_create_brand_duplicate_slug_fails(client, brand):
    response = client.post(
        "/api/v1/catalogue/brands", {"name": "Another", "slug": "acmecare"}
    )
    assert response.status_code == 400


def test_get_brand(client, brand):
    response = client.get(f"/api/v1/catalogue/brands/{brand.pk}")
    assert response.status_code == 200
    assert response.data["name"] == "AcmeCare"


def test_patch_brand(client, brand):
    response = client.patch(f"/api/v1/catalogue/brands/{brand.pk}", {"name": "AcmeCare Pro"})
    assert response.status_code == 200
    assert response.data["name"] == "AcmeCare Pro"


def test_delete_brand_soft_deactivates(client, brand):
    response = client.delete(f"/api/v1/catalogue/brands/{brand.pk}")
    assert response.status_code == 204
    brand.refresh_from_db()
    assert brand.is_active is False


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------


def test_create_product(client, brand):
    response = client.post(
        "/api/v1/catalogue/products",
        {"brand_id": str(brand.pk), "name": "AcmeCare 100ml", "sku": "ACM-100ML"},
    )
    assert response.status_code == 201
    assert response.data["sku"] == "ACM-100ML"


def test_create_product_sku_uppercased(client, brand):
    response = client.post(
        "/api/v1/catalogue/products",
        {"brand_id": str(brand.pk), "name": "Mini", "sku": "acm-mini"},
    )
    assert response.status_code == 201
    assert response.data["sku"] == "ACM-MINI"


def test_get_product(client, product):
    response = client.get(f"/api/v1/catalogue/products/{product.pk}")
    assert response.status_code == 200
    assert response.data["sku"] == "ACM-50ML"


def test_patch_cannot_set_status_directly(client, product):
    """Status only moves through submit / review / withdraw, never via PATCH."""
    response = client.patch(
        f"/api/v1/catalogue/products/{product.pk}", {"status": Product.Status.APPROVED}
    )
    assert response.status_code == 200
    product.refresh_from_db()
    assert product.status == Product.Status.DRAFT


# ---------------------------------------------------------------------------
# Regulatory refs
# ---------------------------------------------------------------------------


def test_create_regulatory_ref(client, product):
    response = client.post(
        f"/api/v1/catalogue/products/{product.pk}/regulatory-refs",
        {
            "product_id": str(product.pk),
            "country_code": "NGA",
            "reference_number": "NAFDAC-12345",
        },
    )
    assert response.status_code == 201
    assert response.data["reference_number"] == "NAFDAC-12345"
    assert response.data["country_code"] == "NGA"


def test_list_regulatory_refs(client, product, tenant):
    with tenant_context(tenant.pk):
        RegulatoryRef.objects.create(
            tenant=tenant,
            product=product,
            country_code="NGA",
            reference_number="NAFDAC-001",
        )
    response = client.get(f"/api/v1/catalogue/products/{product.pk}/regulatory-refs")
    assert response.status_code == 200
    assert len(response.data) == 1


# ---------------------------------------------------------------------------
# Batches
# ---------------------------------------------------------------------------


def test_create_batch(client, product):
    response = client.post(
        f"/api/v1/catalogue/products/{product.pk}/batches",
        {"product_id": str(product.pk), "batch_number": "B100", "quantity": 500},
    )
    assert response.status_code == 201
    assert response.data["batch_number"] == "B100"
    assert response.data["quantity"] == 500


def test_create_batch_with_frontend_field_names(client, product):
    response = client.post(
        f"/api/v1/catalogue/products/{product.pk}/batches",
        {
            "product_id": str(product.pk),
            "batch_number": "B200",
            "manufacture_date": "2026-01-15",
            "expiry": "2028-01-14",
            "declared_quantity": 1200,
            "pack_format": "5 x 12 bottle",
        },
    )
    assert response.status_code == 201, response.data
    # both naming styles are returned
    assert response.data["manufacture_date"] == response.data["manufactured_at"] == "2026-01-15"
    assert response.data["expiry"] == response.data["expires_at"] == "2028-01-14"
    assert response.data["declared_quantity"] == 1200
    assert response.data["pack_format"] == "5 x 12 bottle"
    assert response.data["id"]


def test_create_batch_still_accepts_original_field_names(client, product):
    response = client.post(
        f"/api/v1/catalogue/products/{product.pk}/batches",
        {"product_id": str(product.pk), "batch_number": "B201", "manufactured_at": "2026-02-01",
         "expires_at": "2027-02-01", "quantity": 10},
    )
    assert response.status_code == 201
    assert response.data["expiry"] == "2027-02-01"


def test_create_batch_date_errors_use_the_sent_field_name(client, product):
    response = client.post(
        f"/api/v1/catalogue/products/{product.pk}/batches",
        {"product_id": str(product.pk), "batch_number": "B202", "expiry": "not-a-date"},
    )
    assert response.status_code == 400
    assert "expiry" in response.data["error"]["details"]


def test_duplicate_batch_number_is_a_field_error(client, batch, product):
    response = client.post(
        f"/api/v1/catalogue/products/{product.pk}/batches",
        {"product_id": str(product.pk), "batch_number": batch.batch_number, "quantity": 5},
    )
    assert response.status_code == 400
    assert response.data["error"]["code"] == "validation_error"
    assert "batch_number" in response.data["error"]["details"]


def test_patch_batch_with_frontend_names(client, batch, product):
    response = client.patch(
        f"/api/v1/catalogue/products/{product.pk}/batches/{batch.pk}",
        {"expiry": "2029-05-05", "pack_format": "24 x 1"},
    )
    assert response.status_code == 200
    assert response.data["expires_at"] == "2029-05-05"
    assert response.data["pack_format"] == "24 x 1"


def test_global_batch_list_returns_frontend_names(client, batch):
    response = client.get("/api/v1/catalogue/batches")
    assert response.status_code == 200
    row = response.data["results"][0]
    for key in ("manufacture_date", "expiry", "declared_quantity", "pack_format", "issuance"):
        assert key in row


def test_list_batches(client, batch, product):
    response = client.get(f"/api/v1/catalogue/products/{product.pk}/batches")
    assert response.status_code == 200
    assert len(response.data) == 1


def test_get_batch(client, batch, product):
    response = client.get(f"/api/v1/catalogue/products/{product.pk}/batches/{batch.pk}")
    assert response.status_code == 200
    assert response.data["batch_number"] == "B001"


def test_patch_batch_status(client, batch, product):
    response = client.patch(
        f"/api/v1/catalogue/products/{product.pk}/batches/{batch.pk}",
        {"status": Batch.Status.RECALLED},
    )
    assert response.status_code == 200
    batch.refresh_from_db()
    assert batch.status == Batch.Status.RECALLED


# ---------------------------------------------------------------------------
# Bulk import (task runs eagerly in tests via CELERY_TASK_ALWAYS_EAGER)
# ---------------------------------------------------------------------------


def _make_csv(*rows):
    return "\n".join(rows)


def test_bulk_import_brands(client):
    csv_data = _make_csv("name,slug", "GlowMax,glowmax", "PureSkin,pureskin").encode()
    from django.core.files.uploadedfile import SimpleUploadedFile
    f = SimpleUploadedFile("brands.csv", csv_data, content_type="text/csv")
    response = client.post(
        "/api/v1/catalogue/import",
        {"import_type": "brands", "file": f},
        format="multipart",
    )
    assert response.status_code == 202
    job_id = response.data["id"]

    # CELERY_TASK_ALWAYS_EAGER: task has already run by now
    job = BulkImportJob.unscoped.get(pk=job_id)
    assert job.status == BulkImportJob.Status.DONE
    assert job.rows_imported == 2
    assert Brand.unscoped.filter(slug="glowmax").exists()


def test_bulk_import_products(client, brand):
    csv_data = _make_csv(
        "brand_slug,name,sku",
        "acmecare,AcmeCare 200ml,ACM-200ML",
    ).encode()
    from django.core.files.uploadedfile import SimpleUploadedFile
    f = SimpleUploadedFile("products.csv", csv_data, content_type="text/csv")
    response = client.post(
        "/api/v1/catalogue/import",
        {"import_type": "products", "file": f},
        format="multipart",
    )
    assert response.status_code == 202
    job = BulkImportJob.unscoped.get(pk=response.data["id"])
    assert job.status == BulkImportJob.Status.DONE
    assert Product.unscoped.filter(sku="ACM-200ML").exists()


def test_bulk_import_batches(client, product):
    csv_data = _make_csv(
        "sku,batch_number,quantity",
        "ACM-50ML,BATCH-X,250",
    ).encode()
    from django.core.files.uploadedfile import SimpleUploadedFile
    f = SimpleUploadedFile("batches.csv", csv_data, content_type="text/csv")
    response = client.post(
        "/api/v1/catalogue/import",
        {"import_type": "batches", "file": f},
        format="multipart",
    )
    assert response.status_code == 202
    job = BulkImportJob.unscoped.get(pk=response.data["id"])
    assert job.status == BulkImportJob.Status.DONE
    assert Batch.unscoped.filter(batch_number="BATCH-X").exists()


def test_bulk_import_invalid_type(client):
    from django.core.files.uploadedfile import SimpleUploadedFile
    f = SimpleUploadedFile("data.csv", b"a,b\n1,2", content_type="text/csv")
    response = client.post(
        "/api/v1/catalogue/import",
        {"import_type": "invalid", "file": f},
        format="multipart",
    )
    assert response.status_code == 400


def test_bulk_import_job_status_endpoint(client, tenant):
    with tenant_context(tenant.pk):
        job = BulkImportJob.objects.create(
            tenant=tenant,
            import_type=BulkImportJob.ImportType.BRANDS,
            file_name="test.csv",
            submitted_by=User.objects.get(memberships__tenant=tenant),
        )
    response = client.get(f"/api/v1/catalogue/import/{job.pk}")
    assert response.status_code == 200
    assert response.data["id"] == str(job.pk)


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


def test_brand_from_other_tenant_not_visible(client, owner):
    other_tenant = Tenant.objects.create(
        legal_name="Other Corp", status=Tenant.Status.APPROVED
    )
    with tenant_context(other_tenant.pk):
        Brand.objects.create(tenant=other_tenant, name="OtherBrand", slug="otherbrand")

    response = client.get("/api/v1/catalogue/brands")
    assert response.status_code == 200
    slugs = [b["slug"] for b in response.data]
    assert "otherbrand" not in slugs


# ---------------------------------------------------------------------------
# Batch attention markers and the needs_attention filter
# ---------------------------------------------------------------------------


def _batch_with_job(tenant, product, owner, number, *, requested, issued, status, expires=None):
    """A batch with one issuance job and ``issued`` codes."""
    from apps.issuance.models import Code, IssuanceJob

    with tenant_context(tenant.pk):
        b = Batch.objects.create(
            tenant=tenant, product=product, batch_number=number, quantity=requested,
            expires_at=expires,
        )
        job = IssuanceJob.objects.create(
            tenant=tenant, batch=b, quantity_requested=requested, quantity_issued=issued,
            submitted_by=owner, status=status,
        )
        for i in range(issued):
            Code.objects.create(
                tenant=tenant, code=f"{number[:6].upper()}{i:06d}", batch=b, product=product,
                issuance_job=job,
            )
    return b


def _attention(client, product, batch):
    row = client.get(f"/api/v1/catalogue/products/{product.pk}/batches/{batch.pk}").data
    return row["attention"]


def test_attention_empty_for_a_clean_batch(client, tenant, product, owner):
    from apps.issuance.models import IssuanceJob

    b = _batch_with_job(tenant, product, owner, "CLEAN1", requested=3, issued=3,
                        status=IssuanceJob.Status.DONE)
    assert _attention(client, product, b) == []


def test_attention_partial_job_and_unreconciled(client, tenant, product, owner):
    from apps.issuance.models import IssuanceJob

    b = _batch_with_job(tenant, product, owner, "PART01", requested=5, issued=2,
                        status=IssuanceJob.Status.PARTIAL)
    assert _attention(client, product, b) == ["partial_job", "unreconciled"]


def test_attention_running_job_is_not_unreconciled(client, tenant, product, owner):
    from apps.issuance.models import IssuanceJob

    b = _batch_with_job(tenant, product, owner, "RUN001", requested=5, issued=2,
                        status=IssuanceJob.Status.PROCESSING)
    assert _attention(client, product, b) == []


def test_attention_expiring_soon(client, tenant, product, owner):
    from datetime import timedelta

    from django.utils import timezone

    from apps.issuance.models import IssuanceJob

    soon = timezone.localdate() + timedelta(days=30)
    later = timezone.localdate() + timedelta(days=90)
    a = _batch_with_job(tenant, product, owner, "SOON01", requested=1, issued=1,
                        status=IssuanceJob.Status.DONE, expires=soon)
    b = _batch_with_job(tenant, product, owner, "LATE01", requested=1, issued=1,
                        status=IssuanceJob.Status.DONE, expires=later)
    assert _attention(client, product, a) == ["expiring_soon"]
    assert _attention(client, product, b) == []


def test_needs_attention_filter_agrees_with_markers(client, tenant, product, owner):
    from datetime import timedelta

    from django.utils import timezone

    from apps.issuance.models import IssuanceJob

    soon = timezone.localdate() + timedelta(days=10)
    _batch_with_job(tenant, product, owner, "OKAY01", requested=2, issued=2,
                    status=IssuanceJob.Status.DONE)
    _batch_with_job(tenant, product, owner, "PART02", requested=4, issued=1,
                    status=IssuanceJob.Status.PARTIAL)
    _batch_with_job(tenant, product, owner, "EXPR02", requested=1, issued=1,
                    status=IssuanceJob.Status.DONE, expires=soon)
    _batch_with_job(tenant, product, owner, "RUN002", requested=9, issued=0,
                    status=IssuanceJob.Status.PROCESSING)
    # a failed job that produced no codes at all still needs attention
    _batch_with_job(tenant, product, owner, "FAIL01", requested=3, issued=0,
                    status=IssuanceJob.Status.FAILED)

    rows = client.get("/api/v1/catalogue/batches").data["results"]
    expected = {r["batch_number"] for r in rows if r["attention"]}
    assert expected == {"PART02", "EXPR02", "FAIL01"}

    flagged = client.get("/api/v1/catalogue/batches?needs_attention=true").data["results"]
    assert {r["batch_number"] for r in flagged} == expected
    clean = client.get("/api/v1/catalogue/batches?needs_attention=false").data["results"]
    assert {r["batch_number"] for r in clean} == {"OKAY01", "RUN002"}


def test_needs_attention_rejects_bad_value(client):
    assert client.get("/api/v1/catalogue/batches?needs_attention=maybe").status_code == 400
