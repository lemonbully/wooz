"""URLs for arcane.onewooz.com  the internal admin surface.

Every route is behind :class:`custom_admin.middleware.AdminAccessMiddleware`,
which requires a staff account holding a session established through the admin's
own email + password + emailed-OTP sign-in.

Django's model admin is mounted at ``/db/`` for full table/model management. It
sits behind the same gate (the middleware requires a verified admin session for
every non-public admin path, so the model admin is only reachable once signed
in) and is registered tenant-safely in ``custom_admin/admin.py``.
"""

from __future__ import annotations

from django.contrib import admin
from django.urls import include, path

from core import health

# Branding for the mounted Django admin (shown in its header / title / tab).
admin.site.site_header = "OneWooz Admin  Database"
admin.site.site_title = "OneWooz Admin"
admin.site.index_title = "Models"

urlpatterns = [
    path("health/live/", health.live, name="health_live"),
    # Full model admin (tables/CRUD) under /db/, gated by AdminAccessMiddleware.
    path("db/", admin.site.urls),
    # OneWooz Apps control console (UI, not the model admin). Behind the same
    # admin gate as everything on this surface.
    path("marketplace/", include("marketplace.console_urls", namespace="mkt_console")),
    # Ask OneWooz console: moderation, members, topic rooms, digest, settings.
    path("ask/", include("community.console_urls", namespace="ask_console")),
    path("", include("custom_admin.urls")),
]

# Surface-aware error pages  see onewooz.urls.marketing.
handler400 = "core.errors.handler400"
handler403 = "core.errors.handler403"
handler404 = "core.errors.handler404"
handler500 = "core.errors.handler500"
