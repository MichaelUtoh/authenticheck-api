"""Tests for Phase 7 — Outbound webhooks."""

import json

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from unittest.mock import patch, MagicMock

from apps.tenants.models import Membership, Tenant
from apps.webhooks.models import WebhookDelivery, WebhookEndpoint
from apps.webhooks.services import fire_event, sign_payload

User = get_user_model()
pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def owner():
    return User.objects.create_user("owner@webhooks.test", "a-long-test-password")


@pytest.fixture
def tenant(owner):
    t = Tenant.objects.create(legal_name="WebhookCo", status=Tenant.Status.APPROVED)
    Membership.objects.create(user=owner, tenant=t, role=Membership.Role.OWNER)
    return t


@pytest.fixture
def client(owner, tenant):
    c = APIClient()
    c.force_authenticate(user=owner)
    c.credentials(HTTP_X_TENANT_ID=str(tenant.pk))
    return c


@pytest.fixture
def endpoint(tenant):
    return WebhookEndpoint.unscoped.create(
        tenant=tenant,
        url="https://example.com/hook",
        events=["code.verified"],
        secret="abc123",
    )


# ---------------------------------------------------------------------------
# HMAC signing
# ---------------------------------------------------------------------------


def test_sign_payload_deterministic():
    sig = sign_payload("secret", 1234567890, '{"event":"code.verified"}')
    assert len(sig) == 64
    assert sig == sign_payload("secret", 1234567890, '{"event":"code.verified"}')


def test_sign_payload_changes_with_timestamp():
    body = '{"event":"code.verified"}'
    assert sign_payload("s", 1000, body) != sign_payload("s", 1001, body)


# ---------------------------------------------------------------------------
# Endpoint CRUD
# ---------------------------------------------------------------------------


def test_create_endpoint_returns_secret(client):
    response = client.post(
        "/api/v1/webhooks",
        {"url": "https://example.com/hook", "events": ["code.verified"]},
    )
    assert response.status_code == 201
    assert "secret" in response.data
    assert len(response.data["secret"]) == 64


def test_create_endpoint_rejects_invalid_event(client):
    response = client.post(
        "/api/v1/webhooks",
        {"url": "https://example.com/hook", "events": ["not.a.real.event"]},
    )
    assert response.status_code == 400


def test_list_endpoints(client, endpoint):
    response = client.get("/api/v1/webhooks")
    assert response.status_code == 200
    assert any(str(e["id"]) == str(endpoint.pk) for e in response.data)


def test_get_endpoint(client, endpoint):
    response = client.get(f"/api/v1/webhooks/{endpoint.pk}")
    assert response.status_code == 200
    assert response.data["url"] == "https://example.com/hook"
    assert "secret" not in response.data  # not exposed after creation


def test_delete_endpoint(client, endpoint):
    response = client.delete(f"/api/v1/webhooks/{endpoint.pk}")
    assert response.status_code == 204
    assert not WebhookEndpoint.unscoped.filter(pk=endpoint.pk).exists()


def test_list_deliveries(client, endpoint, tenant):
    WebhookDelivery.unscoped.create(
        tenant=tenant,
        endpoint=endpoint,
        event_type="code.verified",
        payload={"event": "code.verified"},
    )
    response = client.get(f"/api/v1/webhooks/{endpoint.pk}/deliveries")
    assert response.status_code == 200
    assert response.data["count"] == 1


# ---------------------------------------------------------------------------
# fire_event fan-out
# ---------------------------------------------------------------------------


def test_fire_event_creates_delivery_for_subscribed_endpoint(tenant, endpoint):
    with patch("apps.webhooks.tasks.deliver_webhook.apply_async") as mock_task:
        fire_event(tenant.pk, "code.verified", {"code": "ABC123", "result": "genuine"})

    assert WebhookDelivery.unscoped.filter(
        tenant=tenant, event_type="code.verified"
    ).count() == 1
    mock_task.assert_called_once()


def test_fire_event_skips_unsubscribed_endpoint(tenant, endpoint):
    with patch("apps.webhooks.tasks.deliver_webhook.apply_async") as mock_task:
        fire_event(tenant.pk, "batch.recalled", {"batch_id": "x"})

    assert WebhookDelivery.unscoped.filter(
        tenant=tenant, event_type="batch.recalled"
    ).count() == 0
    mock_task.assert_not_called()


def test_fire_event_skips_disabled_endpoint(tenant, endpoint):
    endpoint.status = WebhookEndpoint.Status.DISABLED
    endpoint.save(update_fields=["status", "updated_at"])

    with patch("apps.webhooks.tasks.deliver_webhook.apply_async") as mock_task:
        fire_event(tenant.pk, "code.verified", {"code": "X"})

    mock_task.assert_not_called()


# ---------------------------------------------------------------------------
# deliver_webhook task
# ---------------------------------------------------------------------------


def test_deliver_webhook_success(tenant, endpoint):
    delivery = WebhookDelivery.unscoped.create(
        tenant=tenant,
        endpoint=endpoint,
        event_type="code.verified",
        payload={"event": "code.verified", "code": "X"},
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "ok"

    with patch("httpx.post", return_value=mock_resp):
        from apps.webhooks.tasks import deliver_webhook
        deliver_webhook(str(delivery.pk))

    delivery.refresh_from_db()
    assert delivery.status == WebhookDelivery.Status.SUCCESS
    assert delivery.response_status == 200
    assert delivery.delivered_at is not None


def test_deliver_webhook_non_2xx_marks_attempt(tenant, endpoint):
    delivery = WebhookDelivery.unscoped.create(
        tenant=tenant,
        endpoint=endpoint,
        event_type="code.verified",
        payload={"event": "code.verified", "code": "X"},
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "error"

    with patch("httpx.post", return_value=mock_resp):
        from apps.webhooks.tasks import deliver_webhook
        from celery.exceptions import Retry
        with pytest.raises(Retry):
            deliver_webhook(str(delivery.pk))

    delivery.refresh_from_db()
    assert delivery.attempt_count == 1
    assert delivery.status == WebhookDelivery.Status.PENDING


def test_deliver_webhook_idempotent_on_success(tenant, endpoint):
    delivery = WebhookDelivery.unscoped.create(
        tenant=tenant,
        endpoint=endpoint,
        event_type="code.verified",
        payload={"event": "code.verified", "code": "X"},
        status=WebhookDelivery.Status.SUCCESS,
    )

    with patch("httpx.post") as mock_post:
        from apps.webhooks.tasks import deliver_webhook
        deliver_webhook(str(delivery.pk))

    mock_post.assert_not_called()
