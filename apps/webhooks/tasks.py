"""Celery task: deliver a single WebhookDelivery via HTTP POST with HMAC signing.

Retry schedule: immediate → 1 min → 5 min → 30 min → 2 h (4 retries, 5 total attempts).
Non-2xx responses and network errors both trigger retries. After exhausting retries,
the delivery is marked FAILED and the endpoint's failure_count is incremented.
"""

import json
import logging

from celery import shared_task
from django.db.models import F
from django.utils import timezone

logger = logging.getLogger(__name__)

_RETRY_DELAYS = [0, 60, 300, 1800, 7200]  # seconds: immediate, 1m, 5m, 30m, 2h


@shared_task(bind=True, acks_late=True, max_retries=4)
def deliver_webhook(self, delivery_id: str):
    import httpx

    from apps.webhooks.models import WebhookDelivery, WebhookEndpoint
    from apps.webhooks.services import build_signature_header

    try:
        delivery = WebhookDelivery.unscoped.select_related("endpoint").get(pk=delivery_id)
    except WebhookDelivery.DoesNotExist:
        logger.error("WebhookDelivery %s not found", delivery_id)
        return

    if delivery.status == WebhookDelivery.Status.SUCCESS:
        return  # idempotent

    endpoint = delivery.endpoint
    if endpoint.status != WebhookEndpoint.Status.ACTIVE:
        return

    body = json.dumps(delivery.payload, separators=(",", ":"))
    ts, sig_header = build_signature_header(endpoint.secret, body)

    response_status = None
    response_body = ""
    success = False

    try:
        resp = httpx.post(
            endpoint.url,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-AuthentiCheck-Signature": sig_header,
                "X-AuthentiCheck-Event": delivery.event_type,
                "X-AuthentiCheck-Delivery": str(delivery.pk),
            },
            timeout=10.0,
        )
        response_status = resp.status_code
        response_body = resp.text[:500]
        success = 200 <= resp.status_code < 300
    except Exception as exc:
        logger.warning("WebhookDelivery %s: network error: %s", delivery_id, exc)

    WebhookDelivery.unscoped.filter(pk=delivery_id).update(
        attempt_count=F("attempt_count") + 1,
        response_status=response_status,
        response_body=response_body,
    )

    if success:
        WebhookDelivery.unscoped.filter(pk=delivery_id).update(
            status=WebhookDelivery.Status.SUCCESS,
            delivered_at=timezone.now(),
        )
        WebhookEndpoint.unscoped.filter(pk=endpoint.pk).update(
            last_success_at=timezone.now(),
            failure_count=0,
        )
        return

    attempt = self.request.retries
    if attempt < self.max_retries:
        raise self.retry(countdown=_RETRY_DELAYS[attempt + 1])

    WebhookDelivery.unscoped.filter(pk=delivery_id).update(
        status=WebhookDelivery.Status.FAILED,
    )
    WebhookEndpoint.unscoped.filter(pk=endpoint.pk).update(
        failure_count=F("failure_count") + 1,
    )
