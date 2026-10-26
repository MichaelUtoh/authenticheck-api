import pytest
from django.core.cache import cache
from django.db import connection


@pytest.fixture(autouse=True)
def clear_cache():
    """Prevent locmem cache from leaking auth state between tests."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    """Create the table for the unmanaged, test-only Widget model once per session.

    Raw SQL (no FK constraint) so that mid-session TRUNCATE from transaction=True
    tests does not fail with a foreign-key violation on tenants_tenant.
    """
    with django_db_blocker.unblock():
        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS testsupport_widget (
                    id uuid NOT NULL PRIMARY KEY,
                    tenant_id uuid NOT NULL,
                    created_at timestamptz NOT NULL,
                    updated_at timestamptz NOT NULL,
                    name varchar(50) NOT NULL
                )
            """)
    yield
    with django_db_blocker.unblock():
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS testsupport_widget")
