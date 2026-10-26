"""Celery task: generate unique codes for an IssuanceJob.

Design:
- `acks_late=True`: a worker crash redelivers the task; the task is safe to re-run
  because it checks status and skips jobs that are already DONE.
- Codes are generated in bulk_create chunks of _CHUNK_SIZE to bound memory use.
- Each chunk uses a savepoint so a rare collision on a single code only retries
  that code, not the whole chunk.
- The `code` field is globally unique; collisions are ~negligible at any realistic
  scale but we handle them cleanly anyway.
"""

import logging
import secrets

from celery import shared_task
from django.db import IntegrityError, transaction

from apps.core.tenancy import tenant_context

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 500
_MAX_CODE_RETRIES = 10  # per slot; collision probability is so low this is just a safety net


@shared_task(bind=True, acks_late=True, max_retries=3, default_retry_delay=60)
def generate_codes(self, job_id: str):
    from .models import Code, IssuanceJob

    try:
        job = IssuanceJob.unscoped.select_for_update().get(pk=job_id)
    except IssuanceJob.DoesNotExist:
        logger.error("IssuanceJob %s not found", job_id)
        return

    if job.status == IssuanceJob.Status.DONE:
        return  # idempotent: already completed

    if job.status not in (IssuanceJob.Status.PENDING, IssuanceJob.Status.PROCESSING):
        return

    job.status = IssuanceJob.Status.PROCESSING
    job.save(update_fields=["status", "updated_at"])

    already_issued = Code.unscoped.filter(issuance_job=job).count()
    remaining = job.quantity_requested - already_issued

    if remaining <= 0:
        _finalise(job, job.quantity_requested)
        return

    product = job.batch.product
    issued = already_issued

    try:
        while remaining > 0:
            chunk_size = min(remaining, _CHUNK_SIZE)
            chunk = _generate_chunk(job, product, chunk_size)
            issued += len(chunk)
            remaining -= len(chunk)

        _finalise(job, issued)
    except Exception as exc:
        logger.exception("IssuanceJob %s failed during generation", job_id)
        job.refresh_from_db()
        job.status = IssuanceJob.Status.FAILED
        job.errors = [{"error": str(exc)}]
        job.save(update_fields=["status", "errors", "updated_at"])
        raise self.retry(exc=exc)


def _generate_chunk(job, product, size: int) -> list:
    """Generate `size` Code rows. Falls back to one-by-one on bulk_create collision."""
    from .models import Code

    codes = []
    while len(codes) < size:
        batch_tokens = [secrets.token_urlsafe(16) for _ in range(size - len(codes))]
        objs = [
            Code(
                tenant_id=job.tenant_id,
                code=token,
                batch=job.batch,
                product=product,
                issuance_job=job,
            )
            for token in batch_tokens
        ]
        try:
            with transaction.atomic():
                Code.unscoped.bulk_create(objs, ignore_conflicts=False)
            codes.extend(objs)
        except IntegrityError:
            # Collision (extremely rare): fall back to individual inserts
            for obj in objs:
                _insert_one(obj)
                codes.append(obj)
    return codes


def _insert_one(obj):
    """Insert a single Code, retrying with a fresh token on collision."""
    from .models import Code

    for _ in range(_MAX_CODE_RETRIES):
        try:
            with transaction.atomic():
                obj.save()
            return
        except IntegrityError:
            obj.code = secrets.token_urlsafe(16)
    raise RuntimeError("Could not generate a unique code after max retries")


def _finalise(job, issued: int):
    job.quantity_issued = issued
    job.status = "done"
    job.save(update_fields=["quantity_issued", "status", "updated_at"])
