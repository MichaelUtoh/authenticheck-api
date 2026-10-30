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
