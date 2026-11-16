"""Tests for the product workflow decisions: platform approval, versioned edits, withdrawal,
structured size, global GTIN uniqueness, platform taxonomy, support contact and the editor role."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.audit.models import AuditEvent
from apps.catalogue.models import Batch, Brand, Category, Product
from apps.core.tenancy import tenant_context
from apps.issuance.models import Code, IssuanceJob
from apps.tenants.models import Membership, Tenant

User = get_user_model()
pytestmark = pytest.mark.django_db

API = "/api/v1"
VALID_GTIN = "5901234123457"
OTHER_GTIN = "4006381333931"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _tenant_client(user, tenant):
    c = APIClient()
    c.force_authenticate(user=user)
    c.credentials(HTTP_X_TENANT_ID=str(tenant.pk))
    return c


@pytest.fixture
def tenant():
    return Tenant.objects.create(legal_name="Workflow Co", status=Tenant.Status.APPROVED)


@pytest.fixture
def owner(tenant):
    u = User.objects.create_user("owner@workflow.test", "a-long-test-password")
    Membership.objects.create(user=u, tenant=tenant, role=Membership.Role.OWNER)
    return u


@pytest.fixture
def client(owner, tenant):
    return _tenant_client(owner, tenant)


@pytest.fixture
def editor(tenant):
    u = User.objects.create_user("editor@workflow.test", "a-long-test-password")
    Membership.objects.create(user=u, tenant=tenant, role=Membership.Role.PRODUCTION_QUALITY)
    return u


@pytest.fixture
def editor_client(editor, tenant):
    return _tenant_client(editor, tenant)


@pytest.fixture
def staff_client():
    staff = User.objects.create_user("staff@workflow.test", "a-long-test-password", is_staff=True)
    c = APIClient()
    c.force_authenticate(user=staff, token={"amr": ["pwd", "otp"]})
    return c


@pytest.fixture
def brand(tenant):
    with tenant_context(tenant.pk):
        return Brand.objects.create(tenant=tenant, name="Glow", slug="glow")


def body(brand, **overrides):
    data = {
        "brand_id": str(brand.pk),
        "name": "Shea Butter Body Lotion",
        "industry": "cosmetics",
        "variant": "Unscented",
        "size_value": "400",
        "size_unit": "ml",
        "category": "body-care-lotion",
        "sku": "EN-SBL-400",
    }
    data.update(overrides)
    return data


def create(client, brand, **overrides):
    resp = client.post(f"{API}/catalogue/products", body(brand, **overrides), format="json")
    assert resp.status_code == 201, resp.data
    return resp.data


def submit(client, pid):
    return client.post(f"{API}/catalogue/products/{pid}/submit")


def approved_product(client, staff_client, brand, **overrides):
    p = create(client, brand, **overrides)
    assert submit(client, p["id"]).status_code == 200
    resp = staff_client.post(f"{API}/platform/products/{p['id']}/approve", format="json")
    assert resp.status_code == 200, resp.data
    return resp.data


# ---------------------------------------------------------------------------
# Structured size
# ---------------------------------------------------------------------------


def test_size_is_generated_from_structured_fields(client, brand):
    p = create(client, brand)
    assert p["size"] == "400 ml"
    assert p["size_value"] == "400.000"


def test_size_value_must_be_positive(client, brand):
    resp = client.post(f"{API}/catalogue/products", body(brand, size_value="0"), format="json")
    assert resp.status_code == 400


def test_size_value_and_unit_must_travel_together(client, brand):
    resp = client.post(f"{API}/catalogue/products", body(brand, size_unit=""), format="json")
    assert resp.status_code == 400


def test_size_unit_must_suit_category(client, brand):
    resp = client.post(
        f"{API}/catalogue/products", body(brand, size_unit="mg"), format="json"
    )
    assert resp.status_code == 400
    assert "size_unit" in resp.data["error"]["details"]


@pytest.mark.parametrize("field,value", [
    ("pack_configuration", "1 bottle"),
    ("support_contact", "support@example.com"),
])
def test_object_fields_reject_strings(client, brand, field, value):
    resp = client.post(f"{API}/catalogue/products", body(brand, **{field: value}), format="json")
    assert resp.status_code == 400
    assert "must be an object" in str(resp.data["error"]["details"][field]).lower()


def test_unknown_category_rejected(client, brand):
    resp = client.post(f"{API}/catalogue/products", body(brand, category="nonsense"), format="json")
    assert resp.status_code == 400
    assert "category" in resp.data["error"]["details"]


# ---------------------------------------------------------------------------
# Submit / review
# ---------------------------------------------------------------------------


def test_submit_validates_required_fields(client, brand):
    p = create(client, brand, variant="", category="", size_value=None, size_unit="")
    resp = submit(client, p["id"])
    assert resp.status_code == 400
    details = resp.data["error"]["details"]
    assert {"variant", "category", "size_value", "size_unit"} <= set(details)


def test_submit_records_full_snapshot_in_audit(client, brand):
    p = create(client, brand)
    submit(client, p["id"])
    event = AuditEvent.objects.filter(action="catalogue.product.submitted", target_id=p["id"]).get()
    assert event.after["sku"] == "EN-SBL-400"
    assert event.after["size_unit"] == "ml"


def test_tenant_users_cannot_approve(client, brand):
    p = create(client, brand)
    submit(client, p["id"])
    resp = client.post(f"{API}/platform/products/{p['id']}/approve", format="json")
    assert resp.status_code == 403


def test_platform_approval_flow(client, staff_client, brand):
    p = approved_product(client, staff_client, brand)
    assert p["status"] == "approved"
    assert p["approved_version"] == 1


def test_approve_requires_in_review(client, staff_client, brand):
    p = create(client, brand)
    resp = staff_client.post(f"{API}/platform/products/{p['id']}/approve", format="json")
    assert resp.status_code == 409
    assert resp.data["error"]["code"] == "invalid_status_transition"


def test_request_changes_requires_note_and_allows_resubmit(client, staff_client, brand):
    p = create(client, brand)
    submit(client, p["id"])
    url = f"{API}/platform/products/{p['id']}/request-changes"
    assert staff_client.post(url, {}, format="json").status_code == 400
    resp = staff_client.post(url, {"review_note": "Fix the image"}, format="json")
    assert resp.status_code == 200
    assert resp.data["status"] == "changes_requested"
    assert resp.data["review_note"] == "Fix the image"
    assert client.patch(f"{API}/catalogue/products/{p['id']}", {"variant": "Lavender"}, format="json").status_code == 200
    assert submit(client, p["id"]).status_code == 200


def test_reject_records_reason_in_audit(client, staff_client, brand):
    p = create(client, brand)
    submit(client, p["id"])
    resp = staff_client.post(
        f"{API}/platform/products/{p['id']}/reject", {"review_note": "Not a real product"}, format="json"
    )
    assert resp.data["status"] == "rejected"
    event = AuditEvent.objects.get(action="catalogue.product.rejected", target_id=p["id"])
    assert event.reason == "Not a real product"


def test_review_queue_lists_in_review(client, staff_client, brand):
    p = create(client, brand)
    submit(client, p["id"])
    resp = staff_client.get(f"{API}/platform/products")
    assert [r["id"] for r in resp.data["results"]] == [p["id"]]


def test_pharma_cannot_be_created_or_approved(client, brand):
    resp = client.post(
        f"{API}/catalogue/products", body(brand, industry="pharmaceuticals"), format="json"
    )
    assert resp.status_code == 403
    assert resp.data["error"]["code"] == "industry_locked"


# ---------------------------------------------------------------------------
# Withdrawal
# ---------------------------------------------------------------------------


def test_withdraw_returns_to_draft_with_reason(client, brand):
    p = create(client, brand)
    submit(client, p["id"])
    url = f"{API}/catalogue/products/{p['id']}/withdraw"
    assert client.post(url, {}, format="json").status_code == 400  # reason required
    resp = client.post(url, {"reason": "Wrong image"}, format="json")
    assert resp.status_code == 200
    assert resp.data["status"] == "draft"
    event = AuditEvent.objects.get(action="catalogue.product.withdrawn", target_id=p["id"])
    assert event.reason == "Wrong image"
    assert event.actor is not None


def test_cannot_withdraw_after_reviewer_decision(client, staff_client, brand):
    p = approved_product(client, staff_client, brand)
    resp = client.post(
        f"{API}/catalogue/products/{p['id']}/withdraw", {"reason": "oops"}, format="json"
    )
    assert resp.status_code == 409


def test_withdraw_keeps_batches(client, tenant, brand):
    p = create(client, brand)
    with tenant_context(tenant.pk):
        Batch.objects.create(tenant=tenant, product_id=p["id"], batch_number="B1", quantity=1)
    submit(client, p["id"])
    client.post(f"{API}/catalogue/products/{p['id']}/withdraw", {"reason": "x"}, format="json")
    assert Batch.unscoped.filter(product_id=p["id"]).count() == 1


def test_in_review_product_not_editable(client, brand):
    p = create(client, brand)
    submit(client, p["id"])
    resp = client.patch(f"{API}/catalogue/products/{p['id']}", {"variant": "x"}, format="json")
    assert resp.status_code == 409
    assert resp.data["error"]["code"] == "product_not_editable"


# ---------------------------------------------------------------------------
# Editing approved products
# ---------------------------------------------------------------------------


def test_industry_locked_after_creation(client, brand):
    p = create(client, brand)
    resp = client.patch(
        f"{API}/catalogue/products/{p['id']}", {"industry": "pharmaceuticals"}, format="json"
    )
    assert resp.status_code == 409
    assert resp.data["error"]["code"] == "field_locked"


@pytest.mark.parametrize("field,value", [("sku", "NEW-SKU-1"), ("gtin", VALID_GTIN)])
def test_sku_and_gtin_locked_after_approval(client, staff_client, brand, field, value):
    p = approved_product(client, staff_client, brand, gtin=OTHER_GTIN)
    resp = client.patch(f"{API}/catalogue/products/{p['id']}", {field: value}, format="json")
    assert resp.status_code == 409
    assert resp.data["error"]["code"] == "field_locked"
    assert resp.data["error"]["details"]["field"] == field


def test_unchanged_locked_field_in_patch_is_accepted(client, staff_client, brand):
    p = approved_product(client, staff_client, brand)
    resp = client.patch(
        f"{API}/catalogue/products/{p['id']}", {"sku": p["sku"], "description": "internal"}, format="json"
    )
    assert resp.status_code == 200
    assert resp.data["status"] == "approved"


def test_internal_edit_does_not_restart_approval(client, staff_client, brand):
    p = approved_product(client, staff_client, brand)
    resp = client.patch(f"{API}/catalogue/products/{p['id']}", {"description": "Notes"}, format="json")
    assert resp.data["status"] == "approved"
    assert resp.data["pending_revision"] is None
    assert AuditEvent.objects.filter(action="catalogue.product.updated", target_id=p["id"]).exists()


def test_visible_edit_creates_revision_and_keeps_live_values(client, staff_client, brand):
    p = approved_product(client, staff_client, brand)
    resp = client.patch(f"{API}/catalogue/products/{p['id']}", {"name": "Renamed Lotion"}, format="json")
    assert resp.status_code == 200
    assert resp.data["status"] == "in_review"
    assert resp.data["name"] == "Shea Butter Body Lotion"  # live value untouched
    assert resp.data["pending_revision"] == {"name": "Renamed Lotion"}


def test_last_approved_version_stays_verifiable_during_review(client, staff_client, brand, tenant):
    p = approved_product(client, staff_client, brand)
    code = _issue_code(tenant, p["id"])
    client.patch(f"{API}/catalogue/products/{p['id']}", {"name": "Renamed Lotion"}, format="json")

    resp = APIClient().get(f"{API}/verify/{code}")
    assert resp.status_code == 200
    assert resp.data["product"]["name"] == "Shea Butter Body Lotion"


def test_approving_revision_publishes_new_version(client, staff_client, brand):
    p = approved_product(client, staff_client, brand)
    client.patch(
        f"{API}/catalogue/products/{p['id']}",
        {"name": "Renamed Lotion", "size_value": "250", "size_unit": "ml"},
        format="json",
    )
    resp = staff_client.post(f"{API}/platform/products/{p['id']}/approve", format="json")
    assert resp.data["name"] == "Renamed Lotion"
    assert resp.data["size"] == "250 ml"
    assert resp.data["approved_version"] == 2
    assert resp.data["pending_revision"] is None
    event = AuditEvent.objects.filter(action="catalogue.product.approved", target_id=p["id"]).latest("occurred_at")
    assert event.before["name"] == "Shea Butter Body Lotion"  # previous values never lost


def test_changes_requested_on_revision_keeps_live_values(client, staff_client, brand):
    p = approved_product(client, staff_client, brand)
    client.patch(f"{API}/catalogue/products/{p['id']}", {"name": "Renamed"}, format="json")
    resp = staff_client.post(
        f"{API}/platform/products/{p['id']}/request-changes", {"review_note": "No"}, format="json"
    )
    assert resp.data["status"] == "changes_requested"
    assert resp.data["name"] == "Shea Butter Body Lotion"
    assert resp.data["pending_revision"] == {"name": "Renamed"}


def test_controlled_correction_of_sku(client, staff_client, brand):
    p = approved_product(client, staff_client, brand)
    req = client.post(
        f"{API}/catalogue/products/{p['id']}/correction-request",
        {"field": "sku", "requested_value": "EN-SBL-401", "reason": "Typo"},
        format="json",
    )
    assert req.status_code == 200
    assert req.data["correction_request"]["field"] == "sku"

    url = f"{API}/platform/products/{p['id']}/correct"
    assert staff_client.post(url, {"sku": "EN-SBL-401"}, format="json").status_code == 400  # reason
    resp = staff_client.post(url, {"sku": "en-sbl-401", "reason": "Typo"}, format="json")
    assert resp.status_code == 200
    assert resp.data["sku"] == "EN-SBL-401"
    assert resp.data["correction_request"] is None
    event = AuditEvent.objects.get(action="catalogue.product.corrected", target_id=p["id"])
    assert event.before == {"sku": "EN-SBL-400"}
    assert event.reason == "Typo"


def test_suspend_hides_product_publicly(client, staff_client, brand, tenant):
    p = approved_product(client, staff_client, brand)
    code = _issue_code(tenant, p["id"])
    url = f"{API}/platform/products/{p['id']}/suspend"
    assert staff_client.post(url, {}, format="json").status_code == 400
    assert staff_client.post(url, {"reason": "Safety query"}, format="json").status_code == 200
    assert APIClient().get(f"{API}/verify/{code}").status_code == 404
    staff_client.post(f"{API}/platform/products/{p['id']}/unsuspend", {"reason": "Resolved"}, format="json")
    assert APIClient().get(f"{API}/verify/{code}").status_code == 200


def test_draft_product_not_publicly_resolvable(client, brand, tenant):
    p = create(client, brand)
    code = _issue_code(tenant, p["id"])
    assert APIClient().get(f"{API}/verify/{code}").status_code == 404


# ---------------------------------------------------------------------------
# Uniqueness
# ---------------------------------------------------------------------------


def test_sku_conflict_returns_409_with_existing_product(client, brand):
    create(client, brand)
    resp = client.post(
        f"{API}/catalogue/products", body(brand, name="Other", sku="en-sbl-400"), format="json"
    )
    assert resp.status_code == 409
    assert resp.data["error"]["code"] == "sku_conflict"
    assert resp.data["error"]["details"]["existing_product"]["sku"] == "EN-SBL-400"


def test_sku_conflict_includes_archived_products(client, brand, tenant):
    p = create(client, brand)
    Product.unscoped.filter(pk=p["id"]).update(is_active=False)
    resp = client.post(
        f"{API}/catalogue/products", body(brand, name="Other"), format="json"
    )
    assert resp.status_code == 409
    assert resp.data["error"]["code"] == "sku_conflict"


def test_same_sku_allowed_in_different_tenants(client, brand):
    create(client, brand)
    other = Tenant.objects.create(legal_name="Other Co", status=Tenant.Status.APPROVED)
    user = User.objects.create_user("o@other.test", "a-long-test-password")
    Membership.objects.create(user=user, tenant=other, role=Membership.Role.OWNER)
    with tenant_context(other.pk):
        other_brand = Brand.objects.create(tenant=other, name="B", slug="b")
    resp = _tenant_client(user, other).post(
        f"{API}/catalogue/products", body(other_brand), format="json"
    )
    assert resp.status_code == 201


def test_possible_duplicate_needs_confirmation(client, brand):
    create(client, brand)
    payload = body(brand, sku="EN-SBL-500")
    resp = client.post(f"{API}/catalogue/products", payload, format="json")
    assert resp.status_code == 409
    assert resp.data["error"]["code"] == "possible_duplicate"
    resp = client.post(
        f"{API}/catalogue/products", {**payload, "confirm_not_duplicate": True}, format="json"
    )
    assert resp.status_code == 201


def test_gtin_is_optional_and_validated(client, brand):
    assert create(client, brand)["gtin"] == ""
    resp = client.post(
        f"{API}/catalogue/products", body(brand, sku="G-BAD-1", name="Bad", gtin="5901234123458"), format="json"
    )
    assert resp.status_code == 400


def test_gtin_conflict_across_tenants_blocks_approval(client, staff_client, brand):
    first = create(client, brand, gtin=VALID_GTIN)
    assert submit(client, first["id"]).status_code == 200
    assert staff_client.post(f"{API}/platform/products/{first['id']}/approve", format="json").status_code == 200

    other = Tenant.objects.create(legal_name="Rival Co", status=Tenant.Status.APPROVED)
    user = User.objects.create_user("o@rival.test", "a-long-test-password")
    Membership.objects.create(user=user, tenant=other, role=Membership.Role.OWNER)
    with tenant_context(other.pk):
        rival_brand = Brand.objects.create(tenant=other, name="R", slug="r")
    rc = _tenant_client(user, other)
    second = rc.post(
        f"{API}/catalogue/products", body(rival_brand, gtin=VALID_GTIN), format="json"
    ).data
    assert second["gtin_conflict"] is None  # only computed on detail
    assert rc.get(f"{API}/catalogue/products/{second['id']}").data["gtin_conflict"] is True
    submit(rc, second["id"])

    url = f"{API}/platform/products/{second['id']}/approve"
    resp = staff_client.post(url, format="json")
    assert resp.status_code == 409
    assert resp.data["error"]["code"] == "gtin_conflict"

    resp = staff_client.post(
        url, {"gtin_conflict_resolution": "Packaging-level GTIN confirmed with GS1"}, format="json"
    )
    assert resp.status_code == 200
    assert resp.data["status"] == "approved"


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------


def test_industries_endpoint_serves_taxonomy(client):
    resp = client.get(f"{API}/catalogue/industries")
    assert resp.status_code == 200
    cosmetics = next(i for i in resp.data if i["key"] == "cosmetics")
    assert cosmetics["available"] is True
    assert "body-care-lotion" in {c["key"] for c in cosmetics["categories"]}
    pharma = next(i for i in resp.data if i["key"] == "pharmaceuticals")
    assert pharma["available"] is False


def test_only_platform_staff_manage_taxonomy(client, staff_client):
    payload = {"industry": "cosmetics", "key": "lip-balm", "label": "Lip balm", "allowed_units": ["g"]}
    assert client.post(f"{API}/platform/categories", payload, format="json").status_code == 403
    resp = staff_client.post(f"{API}/platform/categories", payload, format="json")
    assert resp.status_code == 201
    keys = {c["key"] for c in next(i for i in client.get(f"{API}/catalogue/industries").data if i["key"] == "cosmetics")["categories"]}
    assert "lip-balm" in keys  # no frontend release needed


def test_deactivated_category_keeps_existing_products(client, staff_client, brand):
    p = create(client, brand)
    cat = Category.objects.get(key="body-care-lotion")
    resp = staff_client.patch(f"{API}/platform/categories/{cat.pk}", {"is_active": False}, format="json")
    assert resp.status_code == 200
    assert client.get(f"{API}/catalogue/products/{p['id']}").data["category"] == "body-care-lotion"
    # but new products cannot use it
    resp = client.post(f"{API}/catalogue/products", body(brand, sku="NEW-1", name="New"), format="json")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Support contact
# ---------------------------------------------------------------------------


def test_tenant_support_contact_settings(client):
    url = f"{API}/tenants/me/support-contact"
    resp = client.patch(url, {"support_email": "help@workflow.test", "support_phone": "+2348012345678"}, format="json")
    assert resp.status_code == 200
    assert client.get(url).data["support_email"] == "help@workflow.test"
    assert client.patch(url, {"support_phone": "abc"}, format="json").status_code == 400
    assert AuditEvent.objects.filter(action="tenant.support_contact.updated").exists()


def test_support_contact_override_precedence_on_verify(client, staff_client, brand, tenant):
    client.patch(f"{API}/tenants/me/support-contact", {"support_email": "tenant@workflow.test"}, format="json")
    p = approved_product(client, staff_client, brand)
    code = _issue_code(tenant, p["id"])
    assert APIClient().get(f"{API}/verify/{code}").data["support_contact"] == {"email": "tenant@workflow.test"}

    client.patch(
        f"{API}/catalogue/products/{p['id']}", {"support_contact": {"email": "product@workflow.test"}}, format="json"
    )
    staff_client.post(f"{API}/platform/products/{p['id']}/approve", format="json")
    assert APIClient().get(f"{API}/verify/{code}").data["support_contact"] == {"email": "product@workflow.test"}


def test_support_contact_hidden_by_toggle(client, staff_client, brand, tenant):
    client.patch(f"{API}/tenants/me/support-contact", {"support_email": "tenant@workflow.test"}, format="json")
    p = approved_product(
        client, staff_client, brand, consumer_visibility={"support_contact": False}
    )
    code = _issue_code(tenant, p["id"])
    assert "support_contact" not in APIClient().get(f"{API}/verify/{code}").data


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


def test_editor_can_create_and_edit_drafts(editor_client, brand):
    p = create(editor_client, brand)
    resp = editor_client.patch(f"{API}/catalogue/products/{p['id']}", {"variant": "Rose"}, format="json")
    assert resp.status_code == 200


def test_editor_cannot_submit_unless_enabled(editor_client, client, editor, tenant, brand):
    p = create(editor_client, brand)
    assert submit(editor_client, p["id"]).status_code == 403

    member = Membership.objects.get(user=editor, tenant=tenant)
    resp = client.patch(
        f"{API}/tenants/me/members/{member.pk}", {"can_submit_products": True}, format="json"
    )
    assert resp.status_code == 200
    assert submit(editor_client, p["id"]).status_code == 200


def test_editor_cannot_change_roles_or_toggle_permission(editor_client, editor, tenant):
    member = Membership.objects.get(user=editor, tenant=tenant)
    resp = editor_client.patch(
        f"{API}/tenants/me/members/{member.pk}", {"can_submit_products": True}, format="json"
    )
    assert resp.status_code == 403


def test_only_owner_changes_roles(client, owner, tenant):
    admin = User.objects.create_user("admin@workflow.test", "a-long-test-password")
    Membership.objects.create(user=admin, tenant=tenant, role=Membership.Role.ADMIN)
    viewer = User.objects.create_user("v@workflow.test", "a-long-test-password")
    m = Membership.objects.create(user=viewer, tenant=tenant, role=Membership.Role.VIEWER)
    assert _tenant_client(admin, tenant).patch(
        f"{API}/tenants/me/members/{m.pk}", {"role": "editor"}, format="json"
    ).status_code == 403
    assert client.patch(f"{API}/tenants/me/members/{m.pk}", {"role": "editor"}, format="json").status_code == 200


def test_viewer_cannot_create_products(brand, tenant):
    viewer = User.objects.create_user("viewer@workflow.test", "a-long-test-password")
    Membership.objects.create(user=viewer, tenant=tenant, role=Membership.Role.VIEWER)
    resp = _tenant_client(viewer, tenant).post(f"{API}/catalogue/products", body(brand), format="json")
    assert resp.status_code == 403


def test_editor_can_enter_batches(editor_client, client, brand):
    p = create(client, brand)
    resp = editor_client.post(
        f"{API}/catalogue/products/{p['id']}/batches", {"product_id": p["id"], "batch_number": "B-1", "quantity": 10}, format="json"
    )
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _issue_code(tenant, product_id) -> str:
    owner = User.objects.create_user(f"issuer{Code.unscoped.count()}@workflow.test", "a-long-test-password")
    with tenant_context(tenant.pk):
        product = Product.objects.get(pk=product_id)
        batch = Batch.objects.create(tenant=tenant, product=product, batch_number="VB-1", quantity=1)
        job = IssuanceJob.objects.create(
            tenant=tenant, batch=batch, quantity_requested=1, submitted_by=owner,
            status=IssuanceJob.Status.DONE, quantity_issued=1,
        )
        return Code.objects.create(
            tenant=tenant, code="WFCODE0000001", batch=batch, product=product, issuance_job=job
        ).code
