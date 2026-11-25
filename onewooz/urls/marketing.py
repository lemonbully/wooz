"""URLs for onewooz.com  the public marketing site.

Anonymous surface. No authenticated routes and no tenant data.
"""

from __future__ import annotations

from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

from core import health

urlpatterns = [
    path("health/live/", health.live, name="health_live"),
    path("health/ready/", health.ready, name="health_ready"),
    # Subscription purchase flow lives under /pricing/checkout/…; declared before
    # web.urls so those paths resolve to billing while /pricing/ itself falls
    # through to the marketing pricing page.
    path("pricing/", include("billing.urls")),
    # Ask OneWooz community (community.urls, namespace "ask").
    path("ask/", include("community.urls")),
    path("", include("web.urls")),
]

# Surface-aware error pages (see core.errors). Django resolves these from the
# urlconf active when the error occurs, which HostRoutingMiddleware sets per
# surface  so each surface's urlconf names the same handlers.
handler400 = "core.errors.handler400"
handler403 = "core.errors.handler403"
handler404 = "core.errors.handler404"
handler500 = "core.errors.handler500"

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
