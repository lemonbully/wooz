"""URLs for generated storefronts.

Served for ``<slug>.onewooz.site`` and for every verified custom domain. The
site is resolved from the Host header by HostRoutingMiddleware and attached as
``request.site``, so paths here are written exactly as a visitor sees them —
no slug prefix, no path rewriting.

The platform this replaces faked these routes by mutating ``request.path_info``
to ``/website/s/<slug>/...`` inside middleware, then issued redirects in both
directions to hide the rewrite. Routing by host instead removes the need for
any of that.
"""

from __future__ import annotations

from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

from core import health

urlpatterns = [
    path("health/live/", health.live, name="health_live"),
    path("", include("storefront.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
