import hashlib
import re

from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class _ExtendedRate:
    """Mixin: extends DRF parse_rate to support multi-unit periods like '15min', '2h', '30s'."""

    def parse_rate(self, rate):
        if rate is None:
            return (None, None)
        num, period = rate.split("/")
        match = re.match(r"^(\d+)?([smhd])", period)
        if not match:
            raise ValueError(f"Invalid throttle rate period: {period!r}")
        multiplier = int(match.group(1)) if match.group(1) else 1
        duration = multiplier * _UNITS[match.group(2)]
        return (int(num), duration)


class LoginIPThrottle(_ExtendedRate, AnonRateThrottle):
    """30 per 15 min per IP — stops broad brute-force without locking a victim out everywhere."""

    scope = "login_ip"


class LoginEmailIPThrottle(_ExtendedRate, AnonRateThrottle):
    """5 per 15 min per (SHA-256(email) + IP) — targeted limit per attacker/victim pair."""

    scope = "login_user"

    def get_cache_key(self, request, view):
        email = (request.data.get("email") or "").lower().strip()
        email_hash = hashlib.sha256(email.encode()).hexdigest()[:16]
        ident = f"{email_hash}:{self.get_ident(request)}"
        return self.cache_format % {"scope": self.scope, "ident": ident}


class RegisterIPThrottle(_ExtendedRate, AnonRateThrottle):
    """5 per hour per IP."""

    scope = "register"


class RefreshThrottle(_ExtendedRate, AnonRateThrottle):
    """60 per minute — keyed by a prefix of the refresh token hash to rate-limit per session."""

    scope = "refresh"

    def get_cache_key(self, request, view):
        from django.conf import settings

        raw = request.COOKIES.get(settings.REFRESH_COOKIE_NAME, "")
        token_prefix = hashlib.sha256(raw.encode()).hexdigest()[:16] if raw else "none"
        return self.cache_format % {"scope": self.scope, "ident": token_prefix}


class MFARateThrottle(_ExtendedRate, AnonRateThrottle):
    """10 per 15 min per IP — general MFA endpoint protection."""

    scope = "mfa"


class InvitationSendThrottle(_ExtendedRate, UserRateThrottle):
    """Per user: stops an account being used to spam invitation emails."""

    scope = "invitation_send"


class InvitationAcceptThrottle(_ExtendedRate, AnonRateThrottle):
    """Per IP, for accepting an invitation."""

    scope = "invitation_accept"


class InvitationPreviewThrottle(_ExtendedRate, AnonRateThrottle):
    """Per IP, for looking an invitation up by token."""

    scope = "invitation_preview"
