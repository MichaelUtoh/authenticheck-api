"""Structural tenant isolation.

Tenant-owned models inherit TenantScopedModel. Their default manager (`objects`) is *fail-closed*:
it raises unless a tenant context is active, so a forgotten filter cannot leak another tenant's rows.
`unscoped` is the explicit escape hatch for platform admin tooling and background jobs.

The active tenant is set per request by the (Phase 1) tenant-context permission/middleware after
verifying the user's membership, never from a bare client-supplied tenant ID.

Caveat: bulk_create() and raw SQL bypass save(); pass tenant explicitly and review in code review.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from django.db import models

from .models import BaseModel

_current_tenant_id: ContextVar[uuid.UUID | None] = ContextVar("current_tenant_id", default=None)


class TenantContextMissing(Exception):
    """A tenant-scoped query/write happened with no active tenant context."""


class CrossTenantWriteError(Exception):
    """Attempt to write a row belonging to a different tenant than the active context."""


def get_current_tenant_id() -> uuid.UUID | None:
    return _current_tenant_id.get()


@contextmanager
def tenant_context(tenant_id: uuid.UUID) -> Iterator[None]:
    token = _current_tenant_id.set(tenant_id)
    try:
        yield
    finally:
        _current_tenant_id.reset(token)


class TenantManager(models.Manager):
    def get_queryset(self):
        tenant_id = get_current_tenant_id()
        if tenant_id is None:
            raise TenantContextMissing(
                f"{self.model.__name__}.objects used without a tenant context. "
                "Use tenant_context(...) or, for platform-level code, Model.unscoped."
            )
        return super().get_queryset().filter(tenant_id=tenant_id)


class TenantScopedModel(BaseModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="+")

    objects = TenantManager()
    unscoped = models.Manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        ctx = get_current_tenant_id()
        if self.tenant_id is None:
            if ctx is None:
                raise TenantContextMissing("Cannot save a tenant-scoped row without a tenant.")
            self.tenant_id = ctx
        elif ctx is not None and self.tenant_id != ctx:
            raise CrossTenantWriteError(
                "Row belongs to a different tenant than the active context."
            )
        super().save(*args, **kwargs)
