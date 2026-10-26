import logging

from .context import get_request_id


class RequestIDFilter(logging.Filter):
    """Stamp every log record with the current request id ("-" outside a request)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        return True
