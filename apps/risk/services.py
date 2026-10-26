"""Risk signal evaluation. All functions are best-effort — callers must catch exceptions."""

import logging

from django.conf import settings

from apps.verification.models import ScanEvent

from .models import RiskFlag

logger = logging.getLogger(__name__)

_DEFAULT_REPEATED_SCAN_THRESHOLD = 10


def evaluate_scan(scan_event: ScanEvent) -> None:
    """Create risk flags for suspicious scan patterns."""
    if scan_event.result == ScanEvent.Result.DEACTIVATED and scan_event.code_id:
        _flag_deactivated_scan(scan_event)
    elif scan_event.code_id is not None:
        _check_repeated_scan(scan_event)


def create_report_flag(code) -> RiskFlag | None:
    """Create a CONSUMER_REPORT risk flag when a valid code is reported."""
    if code is None:
        return None
    return RiskFlag.unscoped.create(
        tenant_id=code.tenant_id,
        kind=RiskFlag.Kind.CONSUMER_REPORT,
        severity=RiskFlag.Severity.MEDIUM,
        code=code,
        batch=code.batch,
        detail={},
    )


def _flag_deactivated_scan(scan_event: ScanEvent) -> None:
    code = scan_event.code
    RiskFlag.unscoped.create(
        tenant_id=code.tenant_id,
        kind=RiskFlag.Kind.SCAN_AFTER_DEACTIVATION,
        severity=RiskFlag.Severity.HIGH,
        code=code,
        batch=code.batch,
        detail={"scan_event_id": str(scan_event.pk), "ip": scan_event.ip_address},
    )


def _check_repeated_scan(scan_event: ScanEvent) -> None:
    threshold = getattr(settings, "RISK_REPEATED_SCAN_THRESHOLD", _DEFAULT_REPEATED_SCAN_THRESHOLD)
    scan_count = ScanEvent.objects.filter(code_id=scan_event.code_id).count()
    # Create the flag only when crossing the threshold, not on every subsequent scan.
    if scan_count >= threshold and (scan_count - 1) < threshold:
        code = scan_event.code
        RiskFlag.unscoped.create(
            tenant_id=code.tenant_id,
            kind=RiskFlag.Kind.REPEATED_SCAN,
            severity=RiskFlag.Severity.MEDIUM,
            code=code,
            batch=code.batch,
            detail={"scan_count": scan_count, "scan_event_id": str(scan_event.pk)},
        )
