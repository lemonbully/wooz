"""Building links that cross from one surface to another.

Within a surface, always use a plain path (``/sign-in/``)  it stays on the
current host and needs nothing from this module. But a link from the marketing
site to the dashboard, or from an email to a storefront, has to name a
different host, and that is where hardcoding goes wrong.

Two things vary between development and production and both must be derived,
never written into a template:

* **Scheme.** ``https`` in production, ``http`` locally  there is no
  certificate on ``runserver``.
* **Port.** Production is on 443 and the port is implied. Locally everything
  is on one port, whatever ``runserver`` was given, and a link that omits it
  points at port 80 where nothing is listening.

Pass a request wherever one exists and the port is taken from the live one, so
running on 8001 needs no configuration change.
"""

from __future__ import annotations

from django.conf import settings

from . import hosts
from .hosts import Surface

__all__ = [
    "surface_host",
    "surface_origin",
    "surface_url",
    "storefront_origin",
    "referral_portal_url",
    "referral_store_origin",
]

_DEFAULT_PORTS = {"80", "443"}


def surface_host(surface: str | Surface) -> str:
    """The bare hostname serving a surface, without scheme or port."""
    surface = Surface(surface)
    if surface is Surface.APP:
        return settings.APP_HOST
    if surface is Surface.ADMIN:
        return settings.ADMIN_HOST
    if surface is Surface.STOREFRONT:
        return settings.STOREFRONT_APEX
    if surface is Surface.MARKETPLACE:
        return getattr(settings, "MARKETPLACE_HOST", "") or f"marketplace.{settings.MARKETING_HOST}"
    return settings.MARKETING_HOST


def _scheme(request=None) -> str:
    if request is not None:
        return "https" if request.is_secure() else "http"
    return "http" if settings.DEBUG else "https"


def _port_suffix(request=None) -> str:
    """The ``:8000`` part, or empty when the port is implied by the scheme."""
    if not settings.DEBUG:
        return ""

    port = ""
    if request is not None:
        port = str(request.get_port() or "")
    if not port:
        # No request  a Celery task or an email being rendered offline.
        port = str(getattr(settings, "DEV_PORT", "8000"))

    return "" if port in _DEFAULT_PORTS else f":{port}"


def _in_path_mode(request) -> bool:
    """True when this request is being served in single-host development mode.

    In that mode the surfaces are separated by path prefix on one loopback
    host, so a cross-surface link must stay on the current host and change its
    prefix instead of changing hostname.
    """
    return bool(request is not None and getattr(request, "dev_path_mode", False))


def surface_origin(surface: str | Surface, request=None) -> str:
    """Scheme, host and port for a surface, with no trailing slash.

    In single-host development mode this includes the surface's path prefix,
    because that is what identifies the surface there.
    """
    if _in_path_mode(request):
        prefix = hosts.dev_prefix_for(surface)
        return f"{_scheme(request)}://{request.get_host()}{prefix}"
    return f"{_scheme(request)}://{surface_host(surface)}{_port_suffix(request)}"


def surface_url(surface: str | Surface, path: str = "/", request=None) -> str:
    """An absolute URL to ``path`` on another surface."""
    if not path.startswith("/"):
        path = f"/{path}"
    # surface_origin never ends in a slash, so concatenation is safe in both
    # modes: ".../user" + "/sign-up/" and "https://app.onewooz.com" + "/sign-up/".
    return f"{surface_origin(surface, request)}{path}"


def storefront_origin(slug: str, request=None) -> str:
    """Origin of a generated storefront on the platform apex.

    Custom domains are not built here: those are stored whole on the domain
    record, because the owner chose them.
    """
    if _in_path_mode(request):
        prefix = hosts.dev_prefix_for(Surface.STOREFRONT)
        return f"{_scheme(request)}://{request.get_host()}{prefix}/{slug}"
    host = f"{slug}.{settings.STOREFRONT_APEX}"
    return f"{_scheme(request)}://{host}{_port_suffix(request)}"


def _referral_root() -> str:
    return getattr(settings, "REFERRAL_ROOT_DOMAIN", "") or "myugcstore.com"


def referral_portal_url(path: str = "/", request=None) -> str:
    """Absolute URL on the collaborator portal (the referral apex,
    ``myugcstore.com``)  its login and dashboard."""
    if not path.startswith("/"):
        path = f"/{path}"
    if _in_path_mode(request):
        prefix = hosts.dev_prefix_for(Surface.REFERRAL)
        return f"{_scheme(request)}://{request.get_host()}{prefix}{path}"
    return f"{_scheme(request)}://{_referral_root()}{_port_suffix(request)}{path}"


def referral_store_origin(subdomain: str, request=None) -> str:
    """Origin of a collaborator's own store, ``<sub>.myugcstore.com``."""
    return f"{_scheme(request)}://{subdomain}.{_referral_root()}{_port_suffix(request)}"
