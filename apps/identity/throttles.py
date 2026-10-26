from rest_framework.throttling import AnonRateThrottle


class LoginRateThrottle(AnonRateThrottle):
    rate = "10/min"
    scope = "login"


class MFARateThrottle(AnonRateThrottle):
    """Applied to MFA verify so brute-forcing OTPs is expensive even without a session."""

    rate = "10/min"
    scope = "mfa"
