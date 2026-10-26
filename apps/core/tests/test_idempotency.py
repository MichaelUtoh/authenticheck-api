import pytest
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory

from apps.core.idempotency import run_idempotent

pytestmark = pytest.mark.django_db
factory = APIRequestFactory()
KEY = "key-12345678"


@pytest.fixture
def create_view():
    calls = []

    @api_view(["POST"])
    @authentication_classes([])
    @permission_classes([AllowAny])
    def view(request):
        def handler():
            calls.append(1)
            return Response({"id": "abc", "n": len(calls)}, status=201)

        return run_idempotent(request, scope="test:create", handler=handler)

    view.calls = calls
    return view


def _post(view, body, key=KEY):
    headers = {"HTTP_IDEMPOTENCY_KEY": key} if key else {}
    return view(factory.post("/things/", body, format="json", **headers))


def test_missing_key_is_rejected(create_view):
    response = _post(create_view, {"a": 1}, key=None)
    assert response.status_code == 400
    assert response.data["error"]["code"] == "idempotency_key_required"
    assert create_view.calls == []


def test_retry_replays_original_response_without_rerunning_handler(create_view):
    first = _post(create_view, {"a": 1})
    second = _post(create_view, {"a": 1})
    assert first.status_code == second.status_code == 201
    assert second.data == first.data
    assert second["Idempotent-Replayed"] == "true"
    assert len(create_view.calls) == 1


def test_same_key_different_body_is_rejected(create_view):
    _post(create_view, {"a": 1})
    response = _post(create_view, {"a": 2})
    assert response.status_code == 422
    assert response.data["error"]["code"] == "idempotency_key_reuse"
    assert len(create_view.calls) == 1


def test_different_keys_run_independently(create_view):
    _post(create_view, {"a": 1}, key="key-aaaaaaaa")
    _post(create_view, {"a": 1}, key="key-bbbbbbbb")
    assert len(create_view.calls) == 2
