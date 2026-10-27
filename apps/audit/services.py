import hashlib
import uuid

from django.db import connection
from django.utils import timezone

from apps.core.context import get_request_id

from .models import AuditEvent


class AuditError(Exception):
    pass


def record(
    *,
    action: str,
    actor=None,
    tenant=None,
    target=None,
    target_type: str = "",
    target_id: str = "",
    reason: str = "",
    before: dict | None = None,
    after: dict | None = None,
    metadata: dict | None = None,
) -> AuditEvent:
    """Write an audit event. Must run inside the same transaction as the change it describes, so a
    failed audit write rolls the mutation back (EC-084). ATOMIC_REQUESTS covers API requests; Celery
    tasks must wrap the change and this call in transaction.atomic()."""
    if not connection.in_atomic_block:
        raise AuditError("audit.record() must be called inside transaction.atomic().")

    if target is not None:
        target_type, target_id = target._meta.label_lower, str(target.pk)

    actor_obj = actor if getattr(actor, "is_authenticated", False) else None
    event_id = uuid.uuid4()
    occurred_at = timezone.now()

    prev_hash = _get_prev_hash()
    event_hash = _compute_hash(
        event_id=event_id,
        occurred_at=occurred_at,
        actor_id=actor_obj.pk if actor_obj else None,
        tenant_id=tenant.pk if tenant else None,
        action=action,
        prev_hash=prev_hash,
    )

    return AuditEvent.objects.create(
        id=event_id,
        occurred_at=occurred_at,
        action=action,
        actor=actor_obj,
        tenant=tenant,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        before=before,
        after=after,
        metadata=metadata or {},
        request_id=get_request_id() or "",
        prev_hash=prev_hash or "",
        event_hash=event_hash,
    )


def _get_prev_hash() -> str | None:
    last = AuditEvent.objects.order_by("-occurred_at").values("event_hash").first()
    return last["event_hash"] if last else None


def _compute_hash(
    event_id,
    occurred_at,
    actor_id,
    tenant_id,
    action: str,
    prev_hash: str | None,
) -> str:
    raw = "|".join([
        str(event_id),
        occurred_at.isoformat(),
        str(actor_id or ""),
        str(tenant_id or ""),
        action,
        prev_hash or "",
    ])
    return hashlib.sha256(raw.encode()).hexdigest()
