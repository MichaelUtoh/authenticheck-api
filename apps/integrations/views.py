"""Third-party integrations.

GS1 Digital Link: redirects /01/{gtin}/21/{serial} to the verify endpoint.

Africa's Talking USSD: stateless handler — first step prompts for code,
second step returns a human-readable verification result.
HMAC verification uses AT's `apiKey` query param matched against
settings.AFRICASTALKING_API_KEY (if configured; skip check if not set).
"""

import logging

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import redirect
from django.views import View
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GS1 Digital Link
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Integrations — GS1"],
    summary="GS1 Digital Link resolver — redirects to /verify/{serial}",
    description=(
        "Compliant with the GS1 Digital Link standard. "
        "The GTIN (`01`) is accepted but not validated; the serial number (`21`) is "
        "used as the AuthentiCheck code."
    ),
)
class GS1DigitalLinkView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, gtin, serial):
        return redirect(f"/api/v1/verify/{serial}", permanent=False)


# ---------------------------------------------------------------------------
# Africa's Talking USSD
# ---------------------------------------------------------------------------


def _at_token_valid(request) -> bool:
    expected = getattr(settings, "AFRICASTALKING_API_KEY", None)
    if not expected:
        return True  # not configured → accept all (dev / test)
    provided = request.GET.get("apiKey", "") or request.POST.get("apiKey", "")
    return provided == expected


class ATUssdView(View):
    """Stateless USSD handler for Africa's Talking.

    Step 0 (empty text): prompt for code.
    Step 1 (code entered): look up and return result.
    """

    def post(self, request):
        if not _at_token_valid(request):
            return HttpResponse("END Unauthorized.", content_type="text/plain")

        text = request.POST.get("text", "").strip()

        if text == "":
            return HttpResponse("CON Welcome to AuthentiCheck.\nEnter your product code:", content_type="text/plain")

        return self._lookup(text)

    def _lookup(self, code_str: str) -> HttpResponse:
        from apps.catalogue.models import Batch
        from apps.issuance.models import Code

        try:
            code = (
                Code.unscoped
                .select_related("batch__product__brand")
                .get(code=code_str)
            )
        except Code.DoesNotExist:
            return HttpResponse(
                "END Code not found.\nThis product may not be registered or may be counterfeit.",
                content_type="text/plain",
            )

        if code.status == Code.Status.DEACTIVATED:
            return HttpResponse(
                "END This code has been deactivated.",
                content_type="text/plain",
            )

        batch = code.batch
        if batch.status == Batch.Status.RECALLED:
            return HttpResponse(
                f"END RECALL ALERT\nBatch: {batch.batch_number}\n"
                "Do not use this product.",
                content_type="text/plain",
            )

        product = batch.product
        return HttpResponse(
            f"END Genuine product verified.\n"
            f"Product: {product.name}\n"
            f"Brand: {product.brand.name}\n"
            f"Batch: {batch.batch_number}",
            content_type="text/plain",
        )
