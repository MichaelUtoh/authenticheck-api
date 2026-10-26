import pytest

pytestmark = pytest.mark.django_db


def test_liveness(client):
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_reports_dependencies(client):
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json()["checks"] == {"database": "ok", "cache": "ok"}


def test_request_id_is_generated_and_echoed(client):
    response = client.get("/api/v1/health/live")
    assert len(response["X-Request-ID"]) == 32


def test_valid_inbound_request_id_is_kept(client):
    response = client.get("/api/v1/health/live", HTTP_X_REQUEST_ID="trace-abc-12345")
    assert response["X-Request-ID"] == "trace-abc-12345"


def test_malformed_inbound_request_id_is_replaced(client):
    response = client.get("/api/v1/health/live", HTTP_X_REQUEST_ID="bad id\nwith newline")
    assert response["X-Request-ID"] != "bad id\nwith newline"


def test_openapi_schema_is_served_when_docs_enabled(client):
    assert client.get("/api/v1/schema/").status_code == 200
