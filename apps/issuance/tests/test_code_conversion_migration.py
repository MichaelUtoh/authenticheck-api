"""Migration 0005: legacy 22-char codes become 12-char codes, reversibly."""
import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.catalogue.models import Batch, Brand, Product
from apps.core.tenancy import tenant_context
from apps.issuance.codes import find_code, is_valid_format
from apps.issuance.models import IssuanceJob
from apps.tenants.models import Tenant

BEFORE = [("issuance", "0004_code_legacy_code")]
AFTER = [("issuance", "0006_code_max_length_16")]


def _migrate(targets):
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    return MigrationExecutor(connection).loader.project_state(targets).apps


@pytest.mark.django_db(transaction=True)
def test_conversion_forward_and_back():
    tenant = Tenant.objects.create(legal_name="ConvCo", status=Tenant.Status.APPROVED)
    user = get_user_model().objects.create_user("c@conv.test", "a-long-test-password")
    with tenant_context(tenant.pk):
        brand = Brand.objects.create(tenant=tenant, name="B", slug="b")
        product = Product.objects.create(tenant=tenant, brand=brand, name="P", sku="C-1")
        batch = Batch.objects.create(tenant=tenant, product=product, batch_number="C-B", quantity=5)
        job = IssuanceJob.objects.create(
            tenant=tenant, batch=batch, quantity_requested=2, submitted_by=user,
            status=IssuanceJob.Status.DONE, quantity_issued=2,
        )

    old = _migrate(BEFORE)
    Code = old.get_model("issuance", "Code")
    legacy = ["aB3_x-Qz9Lk2mNpQrStUv1", "Zz9_y-Xw8Vu7TsRqPoNm0L"]
    for value in legacy:
        Code.objects.create(
            tenant_id=tenant.pk, code=value, batch_id=batch.pk,
            product_id=product.pk, issuance_job_id=job.pk,
        )

    try:
        new = _migrate(AFTER)
        Code = new.get_model("issuance", "Code")
        rows = list(Code.objects.order_by("legacy_code"))
        assert sorted(r.legacy_code for r in rows) == sorted(legacy)
        assert all(len(r.code) == 12 and is_valid_format(r.code) for r in rows)
        assert len({r.code for r in rows}) == 2

        # Old printed codes keep resolving (exact, case-sensitive) to the same row.
        for r in rows:
            assert find_code(r.code).pk == r.pk
            assert find_code(r.legacy_code).pk == r.pk
            assert find_code(r.legacy_code.lower()) is None

        # Reverse restores the originals.
        old = _migrate(BEFORE)
        Code = old.get_model("issuance", "Code")
        assert sorted(Code.objects.values_list("code", flat=True)) == sorted(legacy)
        assert not Code.objects.filter(legacy_code__isnull=False).exists()
    finally:
        _migrate(AFTER)
