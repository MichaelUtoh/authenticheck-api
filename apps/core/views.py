import logging

from django.core.cache import cache
from django.db import connection, transaction
from django.utils.decorators import method_decorator
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as s
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


class _HealthView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes: list = []


@extend_schema(
    responses={200: inline_serializer("LiveResponse", fields={"status": s.CharField()})},
    tags=["Health"],
    summary="Liveness probe",
)
@method_decorator(transaction.non_atomic_requests, name="dispatch")
class LiveView(_HealthView):
    """Liveness: the process is up. Does not touch dependencies."""

    def get(self, request):
        return Response({"status": "ok"})


@extend_schema(
    responses={
        200: inline_serializer(
            "ReadyResponse",
            fields={
                "status": s.CharField(),
                "checks": s.DictField(child=s.CharField()),
            },
        )
    },
    tags=["Health"],
    summary="Readiness probe",
)
@method_decorator(transaction.non_atomic_requests, name="dispatch")
class ReadyView(_HealthView):
    """Readiness: dependencies are reachable. Used by orchestrators / load balancers."""

    def get(self, request):
        checks = {"database": self._database_ok(), "cache": self._cache_ok()}
        healthy = all(checks.values())
        return Response(
            {
                "status": "ok" if healthy else "unavailable",
                "checks": {name: "ok" if ok else "fail" for name, ok in checks.items()},
            },
            status=200 if healthy else 503,
        )

    @staticmethod
    def _database_ok() -> bool:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            return True
        except Exception:
            logger.exception("Readiness: database check failed")
            return False

    @staticmethod
    def _cache_ok() -> bool:
        try:
            cache.set("health:ping", "1", timeout=5)
            return cache.get("health:ping") == "1"
        except Exception:
            logger.exception("Readiness: cache check failed")
            return False
