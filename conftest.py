import pytest
from django.db import connection


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    """Create the table for the unmanaged, test-only Widget model once per session."""
    from apps.testsupport.models import Widget

    with django_db_blocker.unblock(), connection.schema_editor() as editor:
        editor.create_model(Widget)
    yield
