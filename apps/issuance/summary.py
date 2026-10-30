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
