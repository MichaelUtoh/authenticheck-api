from apps.identity.throttles import _ExtendedRate
from rest_framework.throttling import AnonRateThrottle


class ReportIPThrottle(_ExtendedRate, AnonRateThrottle):
    """5 per hour per IP — slows automated spam without blocking genuine consumer tips."""

    scope = "report_ip"
