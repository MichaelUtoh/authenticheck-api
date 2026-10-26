import re
import uuid

from .context import reset_request_id, set_request_id
from .tenancy import _current_tenant_id

_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._\-]{8,64}")


class RequestIDMiddleware:
    """Accept a well-formed inbound X-Request-ID or mint one; echo it on the response."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _VALID_REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        token = set_request_id(request_id)
        try:
            response = self.get_response(request)
        finally:
            reset_request_id(token)
        response["X-Request-ID"] = request_id
        return response


class TenantContextMiddleware:
    """Ensure the tenant context is clean for every request regardless of thread reuse."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _current_tenant_id.set(None)
        try:
            return self.get_response(request)
        finally:
            _current_tenant_id.reset(token)
