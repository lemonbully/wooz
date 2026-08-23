"""URLs for app.onewooz.com — the authenticated owner dashboard.

Everything under this urlconf belongs to a signed-in site owner. Sign-in and
sign-up live here too, since this is where an owner arrives after clicking a
call to action on the marketing site.
"""

from __future__ import annotations

from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

from core import health

urlpatterns = [
    path("health/live/", health.live, name="health_live"),
    path("health/ready/", health.ready, name="health_ready"),
    path("", include("accounts.urls")),
    path("", include("ud.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
