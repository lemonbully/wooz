"""Health endpoints.

Two of them, because they answer different questions and a load balancer needs
both:

``/health/live/``   Is this process running? Never touches a dependency, so a
                    database blip does not cause the orchestrator to kill and
                    restart every healthy application node.

``/health/ready/``  Can this process serve traffic? Checks the database and the
                    cache, so a node with a broken dependency is taken out of
                    rotation rather than serving errors.
"""

from __future__ import annotations

from django.core.cache import cache
from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache


@never_cache
def live(request):
    return HttpResponse("ok", content_type="text/plain")


@never_cache
def ready(request):
    checks: dict[str, str] = {}
    healthy = True

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc.__class__.__name__}"
        healthy = False

    try:
        probe = "health-probe"
        cache.set("health:probe", probe, timeout=10)
        # A cache that is configured to ignore exceptions returns None rather
        # than raising, so compare the value instead of trusting the write.
        checks["cache"] = "ok" if cache.get("health:probe") == probe else "degraded"
    except Exception as exc:
        checks["cache"] = f"error: {exc.__class__.__name__}"

    # A degraded cache is survivable  every cache read here has a database
    # fallback  so it does not fail readiness. A missing database does.
    return JsonResponse({"status": "ok" if healthy else "unhealthy", "checks": checks},
                        status=200 if healthy else 503)
