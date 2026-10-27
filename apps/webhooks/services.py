"""Webhook signing and fan-out service.

Call fire_event() from any domain service; it is intentionally best-effort so a webhook
misconfiguration never breaks a primary write path.
"""

import hashlib
import hmac
import json
import logging
import secrets
import time

logger = logging.getLogger(__name__)

SUPPORTED_EVENTS = frozenset(
    [
        "code.verified",
        "batch.recalled",
        "risk.flag.created",
    ]
)


def generate_secret() -> str:
    return secrets.token_hex(32)  # 64 hex chars


def sign_payload(secret: str, timestamp: int, body: str) -> str:
    signed_content = f"{timestamp}.{body}"
    return hmac.new(secret.encode(), signed_content.encode(), hashlib.sha256).hexdigest()


def build_signature_header(secret: str, body: str) -> tuple[int, str]:
    ts = int(time.time())
    sig = sign_payload(secret, ts, body)
    return ts, f"t={ts},v1={sig}"


def fire_event(tenant_id, event_type: str, payload: dict) -> None:
    """Schedule Celery delivery tasks for all active endpoints subscribed to event_type."""
    try:
        from apps.webhooks.models import WebhookDelivery, WebhookEndpoint
        from apps.webhooks.tasks import deliver_webhook

        endpoints = WebhookEndpoint.unscoped.filter(
            tenant_id=tenant_id,
            status=WebhookEndpoint.Status.ACTIVE,
            events__contains=[event_type],
        )
        for ep in endpoints:
            delivery = WebhookDelivery.unscoped.create(
                tenant_id=tenant_id,
                endpoint=ep,
                event_type=event_type,
                payload={"event": event_type, **payload},
            )
            deliver_webhook.apply_async(args=[str(delivery.pk)], countdown=0)
    except Exception:
        logger.exception("fire_event failed for tenant=%s event=%s", tenant_id, event_type)
