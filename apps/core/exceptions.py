"""One error envelope for the whole API:

    {"error": {"code": "...", "message": "...", "details": {...}?, "request_id": "..."}}

Unhandled exceptions return a generic 500: no stack traces, no internals (EC-027).
"""

import logging

from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework.views import set_rollback

from .context import get_request_id

logger = logging.getLogger(__name__)

_PASSTHROUGH_HEADERS = ("Retry-After", "WWW-Authenticate", "Allow")


class LoginFailed(APIException):
    """Use instead of AuthenticationFailed on endpoints with no authentication_classes.
    DRF converts AuthenticationFailed → 403 when there is no WWW-Authenticate header to attach;
    this exception bypasses that and always returns 401."""

    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = "Invalid credentials."
    default_code = "authentication_failed"


class IdempotencyKeyRequired(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "A valid Idempotency-Key header (8-128 URL-safe characters) is required."
    default_code = "idempotency_key_required"


class IdempotencyKeyReuse(APIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = "This Idempotency-Key was already used with a different request."
    default_code = "idempotency_key_reuse"


class IdempotencyInProgress(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "A request with this Idempotency-Key is still being processed."
    default_code = "idempotency_in_progress"


class ApiError(APIException):
    """A business-rule error with an explicit HTTP status, machine-readable code and optional
    details, e.g. ApiError(409, "sku_conflict", "SKU is already used.", {"existing_product": ...})."""

    def __init__(self, status_code: int, code: str, message: str, details=None):
        self.status_code = status_code
        self.detail = {"code": code, "message": message}
        if details is not None:
            self.detail["details"] = details


def _envelope(code: str, message: str, status_code: int, details=None) -> Response:
    error = {"code": code, "message": message, "request_id": get_request_id()}
    if details is not None:
        error["details"] = details
    return Response({"error": error}, status=status_code)


def api_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)

    if response is None:  # not an API/HTTP error: a bug or an infrastructure failure
        logger.exception("Unhandled exception", exc_info=exc)
        set_rollback()
        return _envelope("server_error", "An unexpected error occurred.", 500)

    if isinstance(exc, ValidationError):
        code, message, details = (
            "validation_error",
            "One or more fields are invalid.",
            response.data,
        )
    else:
        detail = getattr(exc, "detail", None)
        if isinstance(detail, dict) and "message" in detail:
            code = str(detail.get("code", getattr(exc, "default_code", "error")))
            message = str(detail["message"])
            details = detail.get("details")
        else:
            code = getattr(detail, "code", None) or getattr(exc, "default_code", "error")
            message = (
                str(detail)
                if detail is not None and not isinstance(detail, list | dict)
                else "Request failed."
            )
            details = None

    wrapped = _envelope(code, message, response.status_code, details)
    for header in _PASSTHROUGH_HEADERS:
        if response.has_header(header):
            wrapped[header] = response[header]
    return wrapped
