from apps.identity.throttles import _ExtendedRate
from rest_framework.throttling import AnonRateThrottle


class VerifyIPThrottle(_ExtendedRate, AnonRateThrottle):
    """30 per minute per IP — blocks enumeration without punishing legitimate consumer scans."""

    scope = "verify_ip"
