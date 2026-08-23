"""URLs for onewooz.com — the public marketing site.

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
    path("", include("web.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
