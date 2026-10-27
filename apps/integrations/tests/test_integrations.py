"""Tests for Phase 7 — GS1 Digital Link + Africa's Talking USSD."""

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
    return User.objects.create_user("owner@integrations.test", "a-long-test-password")


@pytest.fixture
def tenant(owner):
    t = Tenant.objects.create(legal_name="IntegrationsCo", status=Tenant.Status.APPROVED)
    Membership.objects.create(user=owner, tenant=t, role=Membership.Role.OWNER)
    return t


@pytest.fixture
def code(tenant, owner):
    with tenant_context(tenant.pk):
        brand = Brand.objects.create(tenant=tenant, name="IBrand", slug="ibrand")
        product = Product.objects.create(tenant=tenant, brand=brand, name="IProduct", sku="INT-001")
        batch = Batch.objects.create(
            tenant=tenant, product=product, batch_number="INT-BATCH-001", quantity=10
        )
        job = IssuanceJob.objects.create(
            tenant=tenant, batch=batch, quantity_requested=1,
            submitted_by=owner, status=IssuanceJob.Status.DONE, quantity_issued=1,
        )
        return Code.objects.create(
            tenant=tenant, code="INTCODE000001", batch=batch,
            product=product, issuance_job=job,
        )


# ---------------------------------------------------------------------------
# GS1 Digital Link
# ---------------------------------------------------------------------------


def test_gs1_link_redirects_to_verify():
    client = APIClient()
    response = client.get("/api/v1/01/05000169058236/21/INTCODE000001")
    assert response.status_code in (301, 302)
    assert "/api/v1/verify/INTCODE000001" in response["Location"]


# ---------------------------------------------------------------------------
# Africa's Talking USSD
# ---------------------------------------------------------------------------


def test_ussd_first_step_prompts_for_code():
    client = APIClient()
    response = client.post(
        "/api/v1/integrations/africastalking/ussd",
        data={"sessionId": "s1", "serviceCode": "*123#", "phoneNumber": "+2348012345678", "text": ""},
        format="multipart",
    )
    assert response.status_code == 200
    assert response.content.startswith(b"CON")


def test_ussd_genuine_code(code):
    client = APIClient()
    response = client.post(
        "/api/v1/integrations/africastalking/ussd",
        data={"sessionId": "s1", "serviceCode": "*123#", "phoneNumber": "+2348012345678", "text": code.code},
        format="multipart",
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert body.startswith("END")
    assert "Genuine" in body


def test_ussd_unknown_code():
    client = APIClient()
    response = client.post(
        "/api/v1/integrations/africastalking/ussd",
        data={"sessionId": "s1", "serviceCode": "*123#", "phoneNumber": "+2348012345678", "text": "NOTAVALIDCODE"},
        format="multipart",
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert body.startswith("END")
    assert "not found" in body.lower()


def test_ussd_recalled_code(code):
    code.batch.status = Batch.Status.RECALLED
    code.batch.save(update_fields=["status"])

    client = APIClient()
    response = client.post(
        "/api/v1/integrations/africastalking/ussd",
        data={"sessionId": "s1", "serviceCode": "*123#", "phoneNumber": "+2348012345678", "text": code.code},
        format="multipart",
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert "RECALL" in body.upper()
