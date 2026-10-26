import pytest

from apps.core.tenancy import CrossTenantWriteError, TenantContextMissing, tenant_context
from apps.tenants.models import Tenant
from apps.testsupport.models import Widget

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenants():
    return Tenant.objects.create(legal_name="A Ltd"), Tenant.objects.create(legal_name="B Ltd")


def test_query_without_context_fails_closed():
    with pytest.raises(TenantContextMissing):
        Widget.objects.all()


def test_save_takes_tenant_from_context(tenants):
    a, _ = tenants
    with tenant_context(a.id):
        widget = Widget.objects.create(name="one")
    assert widget.tenant_id == a.id


def test_queries_only_see_active_tenant_rows(tenants):
    a, b = tenants
    with tenant_context(a.id):
        Widget.objects.create(name="a-widget")
    with tenant_context(b.id):
        Widget.objects.create(name="b-widget")

    with tenant_context(a.id):
        assert list(Widget.objects.values_list("name", flat=True)) == ["a-widget"]
    with tenant_context(b.id):
        assert list(Widget.objects.values_list("name", flat=True)) == ["b-widget"]


def test_cross_tenant_write_is_refused(tenants):
    a, b = tenants
    with tenant_context(a.id), pytest.raises(CrossTenantWriteError):
        Widget(name="sneaky", tenant=b).save()


def test_save_without_tenant_or_context_is_refused():
    with pytest.raises(TenantContextMissing):
        Widget(name="orphan").save()


def test_unscoped_manager_sees_everything(tenants):
    a, b = tenants
    with tenant_context(a.id):
        Widget.objects.create(name="x")
    with tenant_context(b.id):
        Widget.objects.create(name="y")
    assert Widget.unscoped.count() == 2
