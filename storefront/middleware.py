"""Attach the current store to storefront requests.

``HostRoutingMiddleware`` decides *which surface* a request belongs to and, for a
storefront, records the subdomain label (``request.site_slug``) or flags a
verified custom domain (``request.is_custom_domain``)  but it deliberately
leaves ``request.site = None`` because ``core`` carries no dependency on
``tenancy``. This middleware fills that gap: on a storefront request it resolves
the ``Site`` and attaches it, so views, the context builder and the per-request
CSP (which checks ``site.allows_custom_scripts``) all see the same object.

It also performs the canonical-domain redirect: a store reached on its
``onewooz.site`` subdomain  or on the ``www`` twin of its custom domain  whose
own custom domain is verified is 301'd to that domain, so the storefront is
served on a single canonical host.

It is a no-op on every other surface.
"""

from __future__ import annotations

from django.http import HttpResponsePermanentRedirect

from core import hosts as host_utils

from .resolver import resolve_site


class CurrentSiteMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        is_storefront = getattr(request, "surface", None) is host_utils.Surface.STOREFRONT
        if is_storefront:
            request.site = resolve_site(request)
            site = getattr(request, "site", None)

            # Canonical-domain redirect: a store reached on its onewooz.site
            # subdomain whose own custom domain is verified should send visitors
            # to that domain, so the storefront is served on one canonical host.
            redirect = self._canonical_redirect(request, site)
            if redirect is not None:
                return redirect

            # Hand the storefront tracker its beacon URL + first-party ids, so
            # analytics collection is live on every rendered page. GET only, and
            # never for bots  telemetry for humans browsing pages.
            if site is not None and request.method == "GET":
                from . import analytics as an

                if not an.is_bot(request.META.get("HTTP_USER_AGENT", "")):
                    request.analytics_context = an.config_for(request, site)

        response = self.get_response(request)

        if is_storefront and getattr(request, "_ow_analytics_cookies", None):
            from . import analytics as an

            an.apply_cookies(request, response)
        return response

    @staticmethod
    def _canonical_redirect(request, site):
        """A 301 to the store's verified custom domain when the request arrived
        on the onewooz.site subdomain instead.

        Only for safe (GET/HEAD) requests, so a POST webhook, beacon or form
        submission that lands on the subdomain is never turned into a broken
        redirect. Skipped in the loopback path-prefix dev mode (there is no
        real custom-domain host to send a developer to), and skipped when the
        request is already on the custom domain.
        """
        if site is None or getattr(request, "dev_path_mode", False):
            return None
        # A collaborator sells on their own referral subdomain, not the owner's
        # canonical domain  never redirect a UGC store off its subdomain.
        if getattr(request, "is_referral", False):
            return None
        if request.method not in ("GET", "HEAD"):
            return None
        domain = (getattr(site, "custom_domain", "") or "").strip()
        if getattr(request, "is_custom_domain", False):
            # Reached on the www twin of the connected domain (or the bare
            # domain when www.* is the one connected): send it to the canonical
            # host so the store lives on a single address.
            host = host_utils.normalize(request.get_host())
            if domain and host != domain.lower():
                return HttpResponsePermanentRedirect(f"https://{domain}{request.get_full_path()}")
            return None
        if not domain or not getattr(site, "custom_domain_verified", False):
            return None
        return HttpResponsePermanentRedirect(f"https://{domain}{request.get_full_path()}")
