"""Per-batch issuance counts for the batch list and detail.

Computed from the code and job tables in two grouped queries, so the numbers are exact (the codes
list API is paginated and cannot be summed client-side).
"""

from django.db.models import Case, Count, F, IntegerField, Q, Sum, Value, When

from .models import Code, IssuanceJob


def issuance_counts(batch_ids) -> dict:
    """{batch_id: {requested, generated, failed, deactivated}}.

    requested   sum of quantity_requested over the batch's jobs
    generated   codes actually created
    failed      codes still missing from partial or failed jobs (resumable shortfall)
    deactivated codes with status deactivated
    """
    ids = list(batch_ids)
    counts = {
        pk: {"requested": 0, "generated": 0, "failed": 0, "deactivated": 0} for pk in ids
    }
    if not ids:
        return counts

    stopped = [IssuanceJob.Status.PARTIAL, IssuanceJob.Status.FAILED]
    jobs = (
        IssuanceJob.unscoped.filter(batch_id__in=ids)
        .values("batch_id")
        .annotate(
            requested=Sum("quantity_requested"),
            failed=Sum(
                Case(
                    When(status__in=stopped, then=F("quantity_requested") - F("quantity_issued")),
                    default=Value(0),
                    output_field=IntegerField(),
                )
            ),
        )
    )
    for row in jobs:
        counts[row["batch_id"]]["requested"] = row["requested"] or 0
        counts[row["batch_id"]]["failed"] = max(row["failed"] or 0, 0)

    codes = (
        Code.unscoped.filter(batch_id__in=ids)
        .values("batch_id")
        .annotate(
            generated=Count("id"),
            deactivated=Count("id", filter=Q(status=Code.Status.DEACTIVATED)),
        )
    )
    for row in codes:
        counts[row["batch_id"]]["generated"] = row["generated"]
        counts[row["batch_id"]]["deactivated"] = row["deactivated"]
    return counts


EXPIRING_SOON_DAYS = 60  # matches the dashboard's "expire within 60 days"


def job_states(batch_ids) -> dict:
    """{batch_id: (latest_job_status | None, has_open_job)} in one query."""
    ids = list(batch_ids)
    states = {pk: (None, False) for pk in ids}
    if not ids:
        return states
    seen = set()
    for batch_id, status in (
        IssuanceJob.unscoped.filter(batch_id__in=ids)
        .order_by("batch_id", "-created_at")
        .values_list("batch_id", "status")
    ):
        latest, open_job = states[batch_id]
        if batch_id not in seen:  # first row per batch is its latest job
            seen.add(batch_id)
            latest = status
        if status in (IssuanceJob.Status.PENDING, IssuanceJob.Status.PROCESSING):
            open_job = True
        states[batch_id] = (latest, open_job)
    return states


def attention_markers(batch, counts: dict, latest_status, has_open_job, today) -> list[str]:
    """Server-computed "needs attention" markers for one batch.

    partial_job    the latest issuance job stopped partway (resumable)
    unreconciled   jobs have finished running but codes generated != codes requested
    expiring_soon  an active batch whose best-before date is within 60 days
    (approval_pending is not produced yet: there is no deactivation-approval step.)
    """
    from datetime import timedelta

    markers = []
    if latest_status == IssuanceJob.Status.PARTIAL:
        markers.append("partial_job")
    if counts["requested"] and not has_open_job and counts["generated"] != counts["requested"]:
        markers.append("unreconciled")
    if (
        batch.status == "active"
        and batch.expires_at
        and today <= batch.expires_at <= today + timedelta(days=EXPIRING_SOON_DAYS)
    ):
        markers.append("expiring_soon")
    return markers
