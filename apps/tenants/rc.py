"""Duplicate CAC (rc_number) detection.

Registration never tells the registrant that a number is taken (that would reveal which companies
use the platform). Instead the clash is shown to platform reviewers, and approving a tenant whose
number belongs to another non-rejected tenant needs a written resolution."""

from .models import Tenant


def rc_duplicates(tenant: Tenant):
    """Other tenants registered with the same rc_number, excluding rejected ones."""
    if not tenant.rc_number:
        return Tenant.objects.none()
    return (
        Tenant.objects.filter(rc_number=tenant.rc_number)
        .exclude(pk=tenant.pk)
        .exclude(status=Tenant.Status.REJECTED)
    )
