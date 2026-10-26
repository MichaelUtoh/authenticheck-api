"""Idempotency for creation / job-kickoff endpoints (EC-010, EC-013).

Usage in a view:

    def post(self, request):
        scope = f"{request.user.pk}:{get_current_tenant_id()}:issuance.create"
        return run_idempotent(request, scope=scope, handler=lambda: self._create(request))

Semantics
- The key is *claimed* (unique insert) before the handler runs, so concurrent duplicates cannot both execute.
- Same key + same request  -> original response is replayed (header `Idempotent-Replayed: true`).
- Same key + different body -> 422. Key still in flight -> 409.
- Requests run inside ATOMIC_REQUESTS: if the handler raises, the claim rolls back with it and a retry
  runs fresh. Only completed responses are replayed.
"""

import hashlib
import json
import re

from django.db import IntegrityError, transaction
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response

from .exceptions import IdempotencyInProgress, IdempotencyKeyRequired, IdempotencyKeyReuse
from .models import IdempotencyRecord

_KEY_PATTERN = re.compile(r"[A-Za-z0-9_.:\-]{8,128}")


def _fingerprint(request) -> str:
    payload = json.dumps(request.data, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(f"{request.method}|{request.path}|{payload}".encode()).hexdigest()


def run_idempotent(request, *, scope: str, handler) -> Response:
    key = request.headers.get("Idempotency-Key", "")
    if not _KEY_PATTERN.fullmatch(key):
        raise IdempotencyKeyRequired()

    fingerprint = _fingerprint(request)
    try:
        with (
            transaction.atomic()
        ):  # savepoint, so a duplicate-key error does not poison the request
            record = IdempotencyRecord.objects.create(
                scope=scope, key=key, request_hash=fingerprint
            )
    except IntegrityError:
        existing = IdempotencyRecord.objects.get(scope=scope, key=key)
        if existing.request_hash != fingerprint:
            raise IdempotencyKeyReuse() from None
        if existing.response_status is None:
            raise IdempotencyInProgress() from None
        replay = Response(existing.response_body, status=existing.response_status)
        replay["Idempotent-Replayed"] = "true"
        return replay

    response = handler()
    record.response_status = response.status_code
    record.response_body = json.loads(JSONRenderer().render(response.data) or b"null")
    record.save(update_fields=["response_status", "response_body"])
    return response
