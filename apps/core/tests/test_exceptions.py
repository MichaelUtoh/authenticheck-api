import pytest
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.test import APIRequestFactory

pytestmark = pytest.mark.django_db
factory = APIRequestFactory()


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def _boom(_request):
    raise RuntimeError("secret internal detail")


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def _not_found(_request):
    from rest_framework.exceptions import NotFound

    raise NotFound("thing not found")


def test_envelope_shape_on_4xx():
    request = factory.get("/things/1/")
    response = _not_found(request)
    assert response.status_code == 404
    error = response.data["error"]
    assert error["code"] == "not_found"
    assert "message" in error
    assert "request_id" in error
    assert "stack" not in response.data


def test_unhandled_exception_returns_500_without_leaking_internals():
    request = factory.get("/boom/")
    response = _boom(request)
    assert response.status_code == 500
    error = response.data["error"]
    assert error["code"] == "server_error"
    assert "secret" not in str(response.data)
    assert "Traceback" not in str(response.data)
    assert "request_id" in error


def test_request_id_in_envelope_matches_header(client):
    response = client.get("/api/v1/health/live", HTTP_X_REQUEST_ID="test-req-id001")
    assert response["X-Request-ID"] == "test-req-id001"
