from django.db import connection

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

    return AuditEvent.objects.create(
        action=action,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        tenant=tenant,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        before=before,
        after=after,
        metadata=metadata or {},
        request_id=get_request_id() or "",
    )
