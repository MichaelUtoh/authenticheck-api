"""Lightweight verify-endpoint metrics for the platform "operations at a glance" panel.

Every request to ``/api/v1/verify/`` bumps three counters in the shared cache (Redis), bucketed by
minute: total requests, server errors (5xx) and a latency histogram. Counters are atomic increments
and expire after two hours, so there is nothing to clean up and no extra table. The p95 is an
estimate: the upper bound of the histogram bucket that contains the 95th percentile.
"""

import contextlib
import time

from django.core.cache import cache

PREFIX = "/api/v1/verify/"
TTL = 2 * 60 * 60
BUCKETS_MS = [50, 100, 200, 300, 500, 1000, 2000, 5000]  # last bucket is "> 5000"


def _minute() -> int:
    return int(time.time() // 60)


def _bump(key: str) -> None:
    cache.add(key, 0, TTL)
    try:
        cache.incr(key)
    except ValueError:  # expired between add and incr
        cache.set(key, 1, TTL)


def record_verify(duration_ms: float, status_code: int) -> None:
    minute = _minute()
    _bump(f"ops:verify:{minute}:total")
    if status_code >= 500:
        _bump(f"ops:verify:{minute}:err")
    bucket = next((i for i, edge in enumerate(BUCKETS_MS) if duration_ms <= edge), len(BUCKETS_MS))
    _bump(f"ops:verify:{minute}:b{bucket}")


def verify_metrics(window_minutes: int = 60) -> dict:
    """{requests, errors, error_rate, p95_ms} over the last ``window_minutes`` (None if no data)."""
    now = _minute()
    minutes = range(now - window_minutes + 1, now + 1)
    keys = {}
    for m in minutes:
        keys[f"ops:verify:{m}:total"] = "total"
        keys[f"ops:verify:{m}:err"] = "err"
        for i in range(len(BUCKETS_MS) + 1):
            keys[f"ops:verify:{m}:b{i}"] = f"b{i}"
    found = cache.get_many(list(keys))
    total = errors = 0
    hist = [0] * (len(BUCKETS_MS) + 1)
    for key, value in found.items():
        kind = keys[key]
        if kind == "total":
            total += value
        elif kind == "err":
            errors += value
        else:
            hist[int(kind[1:])] += value

    p95 = None
    if total:
        threshold, running = total * 0.95, 0
        for i, count in enumerate(hist):
            running += count
            if running >= threshold:
                p95 = BUCKETS_MS[i] if i < len(BUCKETS_MS) else BUCKETS_MS[-1]
                break
    return {
        "window_minutes": window_minutes,
        "requests": total,
        "errors": errors,
        "error_rate": round(errors / total, 4) if total else None,
        "p95_ms": p95,
        "p95_is_upper_bound": True,
    }


class VerifyMetricsMiddleware:
    """Times requests to the public verify endpoint. Never lets a cache outage break a request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith(PREFIX):
            return self.get_response(request)
        started = time.perf_counter()
        status_code = 500
        try:
            response = self.get_response(request)
            status_code = response.status_code
            return response
        finally:
            with contextlib.suppress(Exception):  # metrics are best effort
                record_verify((time.perf_counter() - started) * 1000, status_code)
