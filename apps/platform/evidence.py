"""Tenant evidence checklist shown to platform reviewers.

Each item is ``missing`` (the manufacturer has not given us the data), ``manufacturer_provided``
(data exists, nobody has checked it) or ``checked`` (a reviewer ticked it). "Provided" is derived
from what the tenant has entered, so there is nothing for the manufacturer to upload twice.
"""

from apps.catalogue.models import Brand, RegulatoryRef
from apps.tenants.models import Membership, Tenant, TenantEvidenceCheck

CHECKLIST = [
    ("cac_certificate", "CAC certificate"),
    ("brand_ownership", "Brand ownership"),
    ("nafdac_registrations", "NAFDAC registrations"),
    ("signatory", "Authorised signatory"),
    ("support_contact", "Support contact"),
]
KEYS = {key for key, _ in CHECKLIST}


def _provided(tenant: Tenant) -> dict[str, tuple[bool, str]]:
    """{key: (provided, what we found)}."""
    owner = (
        Membership.objects.filter(
            tenant=tenant, role=Membership.Role.OWNER, status=Membership.Status.ACTIVE
        )
        .select_related("user")
        .order_by("created_at")
        .first()
    )
    brands = Brand.unscoped.filter(tenant=tenant, is_active=True).count()
    refs = RegulatoryRef.unscoped.filter(tenant=tenant).count()
    contact = [v for v in (tenant.support_email, tenant.support_phone, tenant.support_url) if v]
    return {
        "cac_certificate": (bool(tenant.rc_number), tenant.rc_number),
        "brand_ownership": (brands > 0, f"{brands} active brand(s)" if brands else ""),
        "nafdac_registrations": (refs > 0, f"{refs} regulatory reference(s)" if refs else ""),
        "signatory": (
            bool(owner and owner.user.full_name),
            f"{owner.user.full_name} ({owner.user.email})" if owner and owner.user.full_name else "",
        ),
        "support_contact": (bool(contact), ", ".join(contact)),
    }


def evidence_checklist(tenant: Tenant) -> list[dict]:
    provided = _provided(tenant)
    checks = {
        c.key: c
        for c in TenantEvidenceCheck.objects.filter(tenant=tenant).select_related("checked_by")
    }
    rows = []
    for key, label in CHECKLIST:
        has_data, detail = provided[key]
        check = checks.get(key)
        rows.append(
            {
                "key": key,
                "label": label,
                "checked": check is not None,
                "status": "checked" if check else ("manufacturer_provided" if has_data else "missing"),
                "provided": has_data,
                "detail": detail,
                "checked_at": check.checked_at if check else None,
                "checked_by": check.checked_by.email if check and check.checked_by else None,
                "note": check.note if check else "",
            }
        )
    return rows
