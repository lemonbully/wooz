"""URLs for marketplace.onewooz.com: OneWooz Apps.

Its own URLconf, so nothing on this host can resolve a dashboard, admin or
storefront route. Browsing is public; installing continues on the dashboard
(app.onewooz.com/marketplace/...) behind a fresh re-authentication.
"""

from __future__ import annotations

from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

from core import health

urlpatterns = [
    path("health/live/", health.live, name="health_live"),
    path("health/ready/", health.ready, name="health_ready"),
    path("", include(("marketplace.urls", "marketplace"), namespace="marketplace")),
]

handler400 = "core.errors.handler400"
handler403 = "core.errors.handler403"
handler404 = "core.errors.handler404"
handler500 = "core.errors.handler500"

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
