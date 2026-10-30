"""Product workflow rules (PO decisions, 2026-10).

draft -> in_review -> approved | changes_requested | rejected, with withdraw (in_review -> draft).
Once a product has been approved, the live columns hold the last approved values. Edits to
consumer-visible fields wait in ``pending_revision`` (status back to in_review) and replace the live
values only when a platform reviewer approves them. Nothing is overwritten silently: every
transition and edit is audited with before/after values.
"""

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.core.exceptions import ApiError

from .models import Brand, Category, Industry, Product, ProductImage

# Changing any of these on an approved product creates a new version for review.
CONSUMER_VISIBLE_FIELDS = (
    "name",
    "brand_id",
    "variant",
    "size_value",
    "size_unit",
    "pack_configuration",
    "dosage_form",
    "strength",
    "category",
    "consumer_visibility",
    "support_contact",
    "reference_image_id",
)
# Locked once approved; corrected only through a platform-reviewed controlled correction.
LOCKED_AFTER_APPROVAL = ("sku", "gtin")

_SNAPSHOT_FIELDS = (
    "name", "sku", "industry", "variant", "size_value", "size_unit", "pack_configuration",
    "dosage_form", "strength", "category", "gtin", "consumer_visibility", "support_contact",
)

REVIEWABLE_STATUS = Product.Status.IN_REVIEW


def _jsonable(value):
    if isinstance(value, Decimal):
        return str(value)
    return value


def snapshot(product: Product) -> dict:
    data = {f: _jsonable(getattr(product, f)) for f in _SNAPSHOT_FIELDS}
    data["brand_id"] = str(product.brand_id)
    data["reference_image_id"] = str(product.reference_image_id) if product.reference_image_id else None
    data["status"] = product.status
    data["approved_version"] = product.approved_version
    return data


def live_value(product: Product, field: str):
    if field in ("brand_id", "reference_image_id"):
        raw = getattr(product, field)
        return str(raw) if raw else None
    return _jsonable(getattr(product, field))


# ---------------------------------------------------------------------------
# Lookups and guards
# ---------------------------------------------------------------------------

def product_summary(product: Product) -> dict:
    return {
        "id": str(product.pk),
        "name": product.name,
        "variant": product.variant,
        "size": product.size,
        "sku": product.sku,
        "batch_count": product.batches.count(),
    }


def guard_industry(industry: str) -> None:
    """Pharmaceuticals stay locked until the platform marks the industry available."""
    if not industry:
        return
    row = Industry.objects.filter(key=industry).first()
    available = row.available if row else industry != Product.Industry.PHARMACEUTICALS
    if not available:
        raise ApiError(403, "industry_locked", "This industry is not available yet.")


def validate_taxonomy(industry: str, category: str, size_unit: str) -> dict:
    """Category must be an active category of the industry; the unit must suit the category."""
    errors = {}
    if category and industry:
        cat = Category.objects.filter(industry__key=industry, key=category, is_active=True).first()
        if cat is None:
            errors["category"] = ["Unknown or inactive category for this industry."]
        elif size_unit and cat.allowed_units and size_unit not in cat.allowed_units:
            allowed = ", ".join(cat.allowed_units)
            errors["size_unit"] = [f"Unit not allowed for this category. Allowed: {allowed}."]
    return errors


def check_sku_conflict(tenant_id, sku: str, exclude_pk=None) -> None:
    qs = Product.unscoped.filter(tenant_id=tenant_id, sku__iexact=sku)  # archived included
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    existing = qs.first()
    if existing:
        raise ApiError(
            409, "sku_conflict", "SKU is already used by another product in this company.",
            {"existing_product": product_summary(existing)},
        )


def check_possible_duplicate(tenant_id, brand_id, name, variant, size_value, size_unit, exclude_pk=None):
    qs = Product.unscoped.filter(
        tenant_id=tenant_id, brand_id=brand_id, name__iexact=name.strip(),
        variant__iexact=variant.strip(), size_value=size_value, size_unit=size_unit,
    )
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    existing = qs.first()
    if existing:
        raise ApiError(
            409, "possible_duplicate", "A similar product already exists.",
            {"existing_product": product_summary(existing)},
        )


def gtin_conflicts(product: Product):
    """Other products (any tenant) registered with the same GTIN."""
    if not product.gtin:
        return Product.unscoped.none()
    return Product.unscoped.filter(gtin=product.gtin).exclude(pk=product.pk)


def resolve_image(tenant_id, image_id):
    if not image_id:
        return None
    try:
        return ProductImage.unscoped.get(pk=image_id, tenant_id=tenant_id)
    except ProductImage.DoesNotExist:
        from rest_framework.exceptions import ValidationError

        raise ValidationError({"reference_image_id": ["Image not found."]}) from None


def resolve_brand(tenant_id, brand_id) -> Brand:
    from rest_framework.exceptions import NotFound

    try:
        return Brand.unscoped.get(pk=brand_id, tenant_id=tenant_id, is_active=True)
    except Brand.DoesNotExist:
        raise NotFound("Brand not found.") from None


def save_product(product: Product, **kwargs) -> None:
    try:
        with transaction.atomic():
            product.save(**kwargs)
    except IntegrityError:
        check_sku_conflict(product.tenant_id, product.sku, exclude_pk=product.pk)
        raise


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------

def apply_edit(product: Product, data: dict, user, tenant) -> None:
    """Apply a validated PATCH. ``data`` holds model-field names plus brand_id / reference_image_id
    as UUID strings (already tenant-checked) and may include ``industry``."""
    if product.status == Product.Status.IN_REVIEW:
        raise ApiError(
            409, "product_not_editable",
            "This product is under review. Withdraw it from review to edit it.",
        )

    if "industry" in data and product.industry and data["industry"] != product.industry:
        raise ApiError(
            409, "field_locked",
            "Industry cannot be changed after creation. Create a new product or ask "
            "AuthentiCheck for a controlled migration.",
            {"field": "industry"},
        )
    if "industry" in data:
        guard_industry(data["industry"])

    approved_before = product.approved_version > 0
    if approved_before:
        for field in LOCKED_AFTER_APPROVAL:
            if field in data and data[field] != getattr(product, field):
                raise ApiError(
                    409, "field_locked",
                    f"{field.upper()} is locked after approval. Request a controlled correction.",
                    {"field": field},
                )

    before = snapshot(product)
    pending = dict(product.pending_revision or {})
    revision_changed = False

    for field, value in data.items():
        if field in LOCKED_AFTER_APPROVAL and approved_before:
            continue  # unchanged (checked above)
        if approved_before and field in CONSUMER_VISIBLE_FIELDS:
            value = _jsonable(value)
            current = pending.get(field, live_value(product, field))
            if value != current:
                pending[field] = value
                revision_changed = True
        else:
            setattr(product, _model_attr(field), value)

    if revision_changed:
        # Keep only values that differ from the live (approved) ones.
        pending = {k: v for k, v in pending.items() if v != live_value(product, k)}
        product.pending_revision = pending or None
        if pending and product.status == Product.Status.APPROVED:
            product.status = Product.Status.IN_REVIEW
            product.submitted_by = user
            product.submitted_at = timezone.now()
            product.review_note = ""

    save_product(product)
    audit.record(
        action="catalogue.product.updated",
        actor=user,
        tenant=tenant,
        target=product,
        before=before,
        after={**snapshot(product), "pending_revision": product.pending_revision},
    )


def _model_attr(field: str) -> str:
    return {"brand_id": "brand_id", "reference_image_id": "reference_image_id"}.get(field, field)


def apply_revision(product: Product) -> None:
    """Promote pending_revision to the live columns (called on approval)."""
    for field, value in (product.pending_revision or {}).items():
        if field == "size_value":
            value = Decimal(value) if value is not None else None
        setattr(product, field, value)
    product.pending_revision = None


# ---------------------------------------------------------------------------
# Submission validation
# ---------------------------------------------------------------------------

def missing_required_fields(product: Product) -> dict:
    """Required fields for submit come from the industry and category taxonomy."""
    required = {"name", "sku", "industry"}
    industry = Industry.objects.filter(key=product.industry).first()
    if industry:
        required.update(industry.required_fields)
    cat = Category.objects.filter(industry__key=product.industry, key=product.category).first()
    if cat:
        required.update(cat.required_fields)

    # A revision under review is validated as it would look once approved.
    values = snapshot(product)
    values.update(product.pending_revision or {})
    errors = {}
    for field in sorted(required):
        if field == "pack_configuration":
            ok = bool(values.get(field))
        else:
            ok = values.get(field) not in (None, "", {})
        if not ok:
            errors[field] = ["This field is required."]
    return errors


# ---------------------------------------------------------------------------
# Review transitions (platform staff)
# ---------------------------------------------------------------------------

def _require_in_review(product: Product) -> None:
    if product.status != Product.Status.IN_REVIEW:
        raise ApiError(
            409, "invalid_status_transition",
            f"Cannot review a product with status '{product.status}'.",
        )


def approve(product: Product, reviewer, resolution: str = "") -> None:
    _require_in_review(product)
    guard_industry(product.industry)  # pharma approval never unlocks live verification
    if gtin_conflicts(product).exists():
        if not resolution and not product.gtin_conflict_resolution:
            raise ApiError(
                409, "gtin_conflict",
                "This GTIN is registered against another product. Investigate and record a "
                "resolution before approving.",
                {"gtin": product.gtin},
            )
        if resolution:
            product.gtin_conflict_resolution = resolution

    before = snapshot(product)
    had_revision = bool(product.pending_revision)
    apply_revision(product)
    product.status = Product.Status.APPROVED
    product.approved_version += 1
    product.reviewed_by = reviewer
    product.reviewed_at = timezone.now()
    product.review_note = ""
    save_product(product)
    audit.record(
        action="catalogue.product.approved",
        actor=reviewer,
        tenant=product.tenant,
        target=product,
        before=before,
        after={**snapshot(product), "revision_applied": had_revision},
        reason=resolution,
    )


def decline(product: Product, reviewer, note: str, *, final: bool) -> None:
    """request changes (editable, resubmittable) or reject (final reviewer decision)."""
    _require_in_review(product)
    before = snapshot(product)
    product.status = Product.Status.REJECTED if final else Product.Status.CHANGES_REQUESTED
    product.reviewed_by = reviewer
    product.reviewed_at = timezone.now()
    product.review_note = note
    save_product(product)
    audit.record(
        action="catalogue.product.rejected" if final else "catalogue.product.changes_requested",
        actor=reviewer,
        tenant=product.tenant,
        target=product,
        before=before,
        after={"status": product.status},
        reason=note,
    )


def withdraw(product: Product, user, reason: str) -> None:
    """in_review -> draft, only while no reviewer decision exists. History is preserved."""
    _require_in_review(product)
    product.status = Product.Status.DRAFT
    save_product(product)
    audit.record(
        action="catalogue.product.withdrawn",
        actor=user,
        tenant=product.tenant,
        target=product,
        before={"status": Product.Status.IN_REVIEW},
        after={"status": product.status, "withdrawn_at": timezone.now().isoformat()},
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Consumer view (shared by GET /verify/{code} and the consumer preview)
# ---------------------------------------------------------------------------

def effective_support_contact(product: Product) -> dict | None:
    """Product-level contact overrides the tenant default; staff details are never exposed."""
    contact = product.support_contact
    if not contact:
        tenant = product.tenant
        contact = {
            k: v for k, v in (
                ("email", tenant.support_email),
                ("phone", tenant.support_phone),
                ("url", tenant.support_url),
            ) if v
        }
    return contact or None


def consumer_payload(product: Product, batch) -> dict:
    """Product/batch/support sections with hidden fields omitted (not nulled). Uses the live
    columns, i.e. the last approved version, never a pending revision."""
    vis = product.consumer_visibility or {}
    product_payload = {
        "name": product.name,
        "sku": product.sku,
        "brand": product.brand.name,
        "industry": product.industry,
        "variant": product.variant,
        "size": product.size,
    }
    batch_payload = {
        "batch_number": batch.batch_number if batch else None,
        "manufactured_at": batch.manufactured_at if batch else None,
        "expires_at": batch.expires_at if batch else None,
        "status": batch.status if batch else None,
    }
    if not vis.get("brand", True):
        product_payload.pop("brand")
    if not vis.get("variant_size", True):
        product_payload.pop("variant")
        product_payload.pop("size")
    if not vis.get("batch_number", True):
        batch_payload.pop("batch_number")
    if not vis.get("manufacture_date", True):
        batch_payload.pop("manufactured_at")
    if not vis.get("expiry", True):
        batch_payload.pop("expires_at")
    out = {"product": product_payload, "batch": batch_payload}
    if vis.get("support_contact", True):
        contact = effective_support_contact(product)
        if contact:
            out["support_contact"] = contact
    return out
