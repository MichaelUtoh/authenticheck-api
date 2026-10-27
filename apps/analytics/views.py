from datetime import date, timedelta

from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from drf_spectacular.types import OpenApiTypes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.issuance.models import Code
from apps.risk.models import Case, RiskFlag
from apps.tenants.permissions import HasActiveTenantMembership
from apps.verification.models import ScanEvent

_BASE_PERMS = [IsAuthenticated, HasActiveTenantMembership]
_MAX_DAYS = 90


@extend_schema(
    parameters=[
        OpenApiParameter(
            "days",
            OpenApiTypes.INT,
            description=f"Lookback window in days (1–{_MAX_DAYS}, default 30).",
        )
    ],
    responses={
        200: {
            "type": "object",
            "properties": {
                "period_days": {"type": "integer"},
                "total_scans": {"type": "integer"},
                "by_result": {"type": "object"},
                "by_day": {"type": "array"},
            },
        }
    },
    tags=["Analytics"],
    summary="Scan volume and outcome breakdown for the active tenant",
)
class ScanAnalyticsView(APIView):
    permission_classes = _BASE_PERMS

    def get(self, request):
        days = _parse_days(request)
        cutoff = timezone.now() - timedelta(days=days)
        tenant_id = request.active_tenant_id

        base_qs = ScanEvent.objects.filter(
            code__tenant_id=tenant_id,
            occurred_at__gte=cutoff,
        )

        total = base_qs.count()

        by_result_qs = (
            base_qs.values("result").annotate(count=Count("id"))
        )
        by_result = {row["result"]: row["count"] for row in by_result_qs}

        by_day_qs = (
            base_qs
            .annotate(day=TruncDate("occurred_at"))
            .values("day")
            .annotate(count=Count("id"))
            .order_by("day")
        )
        day_map = {row["day"]: row["count"] for row in by_day_qs}
        start_date = (timezone.now() - timedelta(days=days)).date()
        by_day = _fill_series(day_map, start_date, timezone.now().date())

        return Response({
            "period_days": days,
            "total_scans": total,
            "by_result": by_result,
            "by_day": by_day,
        })


@extend_schema(
    responses={
        200: {
            "type": "object",
            "properties": {
                "total_issued": {"type": "integer"},
                "active": {"type": "integer"},
                "deactivated": {"type": "integer"},
            },
        }
    },
    tags=["Analytics"],
    summary="Current code inventory snapshot for the active tenant",
)
class CodeAnalyticsView(APIView):
    permission_classes = _BASE_PERMS

    def get(self, request):
        stats = Code.objects.aggregate(
            total_issued=Count("id"),
            active=Count("id", filter=Q(status=Code.Status.ACTIVE)),
            deactivated=Count("id", filter=Q(status=Code.Status.DEACTIVATED)),
        )
        return Response(stats)


@extend_schema(
    responses={
        200: {
            "type": "object",
            "properties": {
                "open_flags": {"type": "object"},
                "flags_last_30_days": {"type": "integer"},
                "cases": {"type": "object"},
            },
        }
    },
    tags=["Analytics"],
    summary="Risk flag and case summary for the active tenant",
)
class RiskAnalyticsView(APIView):
    permission_classes = _BASE_PERMS

    def get(self, request):
        cutoff_30d = timezone.now() - timedelta(days=30)

        flag_stats = RiskFlag.objects.aggregate(
            high=Count("id", filter=Q(is_reviewed=False, severity=RiskFlag.Severity.HIGH)),
            medium=Count("id", filter=Q(is_reviewed=False, severity=RiskFlag.Severity.MEDIUM)),
            low=Count("id", filter=Q(is_reviewed=False, severity=RiskFlag.Severity.LOW)),
            last_30_days=Count("id", filter=Q(created_at__gte=cutoff_30d)),
        )
        case_stats = Case.objects.aggregate(
            open=Count("id", filter=Q(status=Case.Status.OPEN)),
            under_review=Count("id", filter=Q(status=Case.Status.UNDER_REVIEW)),
            closed=Count("id", filter=Q(status=Case.Status.CLOSED)),
        )

        return Response({
            "open_flags": {
                "high": flag_stats["high"],
                "medium": flag_stats["medium"],
                "low": flag_stats["low"],
            },
            "flags_last_30_days": flag_stats["last_30_days"],
            "cases": {
                "open": case_stats["open"],
                "under_review": case_stats["under_review"],
                "closed": case_stats["closed"],
            },
        })


def _parse_days(request) -> int:
    raw = request.query_params.get("days", "30")
    try:
        days = int(raw)
    except ValueError:
        raise ValidationError({"days": "Must be an integer."})
    if not (1 <= days <= _MAX_DAYS):
        raise ValidationError({"days": f"Must be between 1 and {_MAX_DAYS}."})
    return days


def _fill_series(day_map: dict, start: date, end: date) -> list[dict]:
    result = []
    current = start
    while current <= end:
        result.append({"date": current.isoformat(), "count": day_map.get(current, 0)})
        current += timedelta(days=1)
    return result
