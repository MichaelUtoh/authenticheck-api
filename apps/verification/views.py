import logging

from drf_spectacular.utils import extend_schema
from django.utils import timezone
from django.utils.cache import add_never_cache_headers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalogue import workflow
from apps.catalogue.models import Batch
from apps.core.exceptions import ApiError
from apps.issuance.codes import clean_code, find_code, is_valid_format
from apps.issuance.models import Code

from .models import ScanEvent
from .serializers import VerifyDeactivatedSerializer, VerifyGenuineSerializer, VerifyNotFoundSerializer
from .throttles import VerifyIPThrottle

logger = logging.getLogger(__name__)


@extend_schema(
    responses={
        200: VerifyGenuineSerializer,
        404: VerifyNotFoundSerializer,
    },
    tags=["Verification"],
    summary="Verify a product authentication code",
    description=(
        "Public endpoint. No authentication required. "
        "Resolves a code to its product, batch, and manufacturer. "
        "`result` is `genuine`, `recalled`, `expired` or `deactivated`. "
        "400 `invalid_code_format` means the input cannot be a code (not recorded). "
        "404 means the code does not exist in the system (likely counterfeit)."
    ),
)
class VerifyView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [VerifyIPThrottle]

    def finalize_response(self, request, response, *args, **kwargs):
        # Every call records a scan, so no browser, proxy or CDN may answer it from cache.
        # Applies to all outcomes, including 400, 404 and 429.
        response = super().finalize_response(request, response, *args, **kwargs)
        add_never_cache_headers(response)
        return response

    def get(self, request, code_str):
        ip = _get_client_ip(request)
        ua = request.META.get("HTTP_USER_AGENT", "")[:500]

        code_str = clean_code(code_str)
        code = find_code(
            code_str,
            Code.unscoped.select_related(
                "batch__product__brand",
                "batch__product__reference_image",
                "batch__product__tenant",
                "tenant",
            ),
        )
        if code is None:
            if not is_valid_format(code_str):
                # Not a possible code: don't record it (keeps analytics and risk rules clean).
                raise ApiError(400, "invalid_code_format", "That is not a valid code format.")
            _record(code_str, None, ScanEvent.Result.NOT_FOUND, ip, ua)
            return Response(
                {"result": "not_found", "detail": "Code not found."},
                status=404,
            )

        # A recalled batch has all its codes deactivated by the recall; consumers must
        # still see the recall message, so recall takes priority over deactivation.
        recalled = code.batch.status == Batch.Status.RECALLED

        if code.status == Code.Status.DEACTIVATED and not recalled:
            scan = _record(code_str, code, ScanEvent.Result.DEACTIVATED, ip, ua)
            _evaluate_risk(scan)
            # The internal deactivation_reason is deliberately not exposed to consumers.
            payload = {
                "result": "deactivated",
                "code": code.code,
                "deactivated_at": code.deactivated_at,
                "manufacturer": {"name": code.tenant.legal_name},
            }
            contact = _deactivated_support_contact(code)
            if contact:
                payload["support_contact"] = contact
            return Response(VerifyDeactivatedSerializer(payload).data)

        batch = code.batch
        product = batch.product

        # Resolvable once approved, and stays so while a revision is in review; staff can suspend.
        if not product.is_publicly_resolvable:
            _record(code_str, code, ScanEvent.Result.NOT_FOUND, ip, ua)
            return Response(
                {"result": "not_found", "detail": "Code not found."},
                status=404,
            )

        if batch.status == Batch.Status.RECALLED:
            result = ScanEvent.Result.RECALLED
        elif batch.effective_status == Batch.Status.EXPIRED:
            result = ScanEvent.Result.EXPIRED
        else:
            result = ScanEvent.Result.GENUINE
        now = timezone.now()
        # Atomic: exactly one concurrent first check wins.
        is_first = Code.unscoped.filter(pk=code.pk, first_checked_at__isnull=True).update(first_checked_at=now) == 1
        first_checked_at = now if is_first else Code.unscoped.values_list("first_checked_at", flat=True).get(pk=code.pk)

        scan = _record(code_str, code, result, ip, ua)
        _evaluate_risk(scan)
        _fire_verified(scan, code, result)

        vis = product.consumer_visibility or {}
        consumer = workflow.consumer_payload(product, batch)

        payload = {
            "result": result,
            "code": code.code,
            "is_first_check": is_first,
            "checked_at": now,
            "product": consumer["product"],
            "batch": {**consumer["batch"], "status": batch.status},
            "manufacturer": {
                "name": code.tenant.legal_name,
            },
        }
        if not is_first:
            payload["first_checked_at"] = first_checked_at
        if result == ScanEvent.Result.RECALLED:
            if batch.recalled_at:
                payload["effective_at"] = batch.recalled_at
            if batch.recall_message:
                payload["consumer_message"] = batch.recall_message
        if "support_contact" in consumer:
            payload["support_contact"] = consumer["support_contact"]

        if vis.get("product_image", True) and product.reference_image_id:
            payload["product_image"] = {
                "url": product.reference_image.url,
                "thumbnail_url": product.reference_image.thumbnail_url,
            }

        if vis.get("regulatory_reference", True) and getattr(code.tenant, "is_pharma", False):
            payload["regulatory_refs"] = list(
                product.regulatory_refs
                .values("country_code", "reference_number", "valid_until", "verification_status")
                .order_by("country_code")
            )

        return Response(VerifyGenuineSerializer(payload).data)


def _deactivated_support_contact(code) -> dict | None:
    """Who the consumer can ask about a deactivated code.

    Uses the product's override / the tenant default, like a genuine result, but only
    shows product-level data while the product is publicly resolvable and has not hidden
    its contact; otherwise falls back to the tenant default alone.
    """
    tenant = code.tenant
    product = code.batch.product
    tenant_default = {
        k: v for k, v in (
            ("email", tenant.support_email),
            ("phone", tenant.support_phone),
            ("url", tenant.support_url),
        ) if v
    }
    if not product.is_publicly_resolvable:
        return tenant_default or None
    if not (product.consumer_visibility or {}).get("support_contact", True):
        return None
    return workflow.effective_support_contact(product)


def _record(code_str: str, code, result: str, ip, ua: str) -> ScanEvent | None:
    try:
        return ScanEvent.objects.create(
            code_str=code_str,
            code=code,
            result=result,
            ip_address=ip,
            user_agent=ua,
        )
    except Exception:
        logger.exception("Failed to record ScanEvent for code_str=%s", code_str)
        return None


def _evaluate_risk(scan_event: ScanEvent | None) -> None:
    if scan_event is None:
        return
    try:
        from apps.risk.services import evaluate_scan
        evaluate_scan(scan_event)
    except Exception:
        logger.exception("Risk evaluation failed for scan_event=%s", scan_event.pk)


def _fire_verified(scan: ScanEvent | None, code, result: str) -> None:
    if scan is None or code is None:
        return
    try:
        from apps.webhooks.services import fire_event
        fire_event(
            tenant_id=code.tenant_id,
            event_type="code.verified",
            payload={
                "code": code.code,
                "result": result,
                "scan_id": str(scan.pk),
            },
        )
    except Exception:
        logger.exception("fire_event failed for scan_event=%s", scan.pk)


def _get_client_ip(request) -> str | None:
    from django.conf import settings

    proxy_count = getattr(settings, "TRUSTED_PROXY_COUNT", 0)
    if proxy_count > 0:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            ips = [ip.strip() for ip in forwarded.split(",")]
            if len(ips) >= proxy_count:
                return ips[-proxy_count]
    return request.META.get("REMOTE_ADDR")
