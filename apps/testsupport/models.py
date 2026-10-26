from django.db import models

from apps.core.tenancy import TenantScopedModel


class Widget(TenantScopedModel):
    """Test-only tenant-scoped model. Unmanaged: its table is created in the root conftest."""

    name = models.CharField(max_length=50)

    class Meta:
        managed = False
