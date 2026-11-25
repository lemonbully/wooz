"""Cross-surface links for the marketplace <-> dashboard round trips."""

from __future__ import annotations

from django.urls import get_script_prefix, reverse

from core.links import surface_url

APP_URLCONF = "onewooz.urls.app"
MARKETPLACE_URLCONF = "onewooz.urls.marketplace"


def path_on(urlconf: str, name: str, **kwargs) -> str:
    """A path for ``name`` in another surface's URLconf, without this request's
    script prefix (which belongs to the current surface in dev path mode)."""
    path = reverse(name, urlconf=urlconf, kwargs=kwargs or None)
    prefix = get_script_prefix()
    if prefix not in ("", "/") and path.startswith(prefix):
        path = "/" + path[len(prefix):]
    return path


def app_url(request, name: str, query: str = "", **kwargs) -> str:
    url = surface_url("app", path_on(APP_URLCONF, name, **kwargs), request)
    return f"{url}?{query}" if query else url


def marketplace_url(request, name: str, query: str = "", **kwargs) -> str:
    url = surface_url("marketplace", path_on(MARKETPLACE_URLCONF, name, **kwargs), request)
    return f"{url}?{query}" if query else url


def webhook_url(request) -> str:
    """Cashfree notify URL: the platform webhook on the marketing surface (it
    only ever nudges a confirm-by-fetch)."""
    from django.conf import settings

    return surface_url("marketing", path_on(settings.ROOT_URLCONF, "billing:webhook"), request)


def path_of(url: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(url).path or "/"
