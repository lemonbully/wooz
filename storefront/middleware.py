"""Attach the current store to storefront requests.

``HostRoutingMiddleware`` decides *which surface* a request belongs to and, for a
storefront, records the subdomain label (``request.site_slug``) or flags a
verified custom domain (``request.is_custom_domain``) — but it deliberately
leaves ``request.site = None`` because ``core`` carries no dependency on
``tenancy``. This middleware fills that gap: on a storefront request it resolves
the ``Site`` and attaches it, so views, the context builder and the per-request
CSP (which checks ``site.allows_custom_scripts``) all see the same object.

It is a no-op on every other surface.
"""

from __future__ import annotations

from core import hosts as host_utils

from .resolver import resolve_site


class CurrentSiteMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if getattr(request, "surface", None) is host_utils.Surface.STOREFRONT:
            request.site = resolve_site(request)
        return self.get_response(request)
