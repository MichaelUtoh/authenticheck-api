import logging

from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalogue import workflow
from apps.catalogue.models import Batch
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
        "`result` is `genuine`, `recalled`, or `deactivated`. "
        "404 means the code does not exist in the system (likely counterfeit)."
    ),
)
class VerifyView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [VerifyIPThrottle]

    def get(self, request, code_str):
        ip = _get_client_ip(request)
        ua = request.META.get("HTTP_USER_AGENT", "")[:500]

        try:
            code = (
                Code.unscoped
                .select_related(
                    "batch__product__brand",
                    "batch__product__reference_image",
                    "batch__product__tenant",
                    "tenant",
                )
                .get(code=code_str)
            )
        except Code.DoesNotExist:
            _record(code_str, None, ScanEvent.Result.NOT_FOUND, ip, ua)
            return Response(
                {"result": "not_found", "detail": "Code not found."},
                status=404,
            )

        if code.status == Code.Status.DEACTIVATED:
            scan = _record(code_str, code, ScanEvent.Result.DEACTIVATED, ip, ua)
            _evaluate_risk(scan)
            return Response(
                VerifyDeactivatedSerializer({
                    "result": "deactivated",
                    "code": code.code,
                    "deactivated_at": code.deactivated_at,
                    "deactivation_reason": code.deactivation_reason,
                }).data
            )

        batch = code.batch
        product = batch.product

        # Resolvable once approved, and stays so while a revision is in review; staff can suspend.
        if not product.is_publicly_resolvable:
            _record(code_str, code, ScanEvent.Result.NOT_FOUND, ip, ua)
            return Response(
                {"result": "not_found", "detail": "Code not found."},
                status=404,
            )

        result = (
            ScanEvent.Result.RECALLED
            if batch.status == Batch.Status.RECALLED
            else ScanEvent.Result.GENUINE
        )
        scan = _record(code_str, code, result, ip, ua)
        _evaluate_risk(scan)
        _fire_verified(scan, code, result)

        vis = product.consumer_visibility or {}
        consumer = workflow.consumer_payload(product, batch)

        payload = {
            "result": result,
            "code": code.code,
            "product": consumer["product"],
            "batch": {**consumer["batch"], "status": batch.status},
            "manufacturer": {
                "name": code.tenant.legal_name,
            },
        }
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
