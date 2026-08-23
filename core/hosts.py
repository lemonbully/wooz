"""Host resolution: turning a Host header into a surface.

This is the first decision made about every request and the one that keeps the
four surfaces apart. It is deliberately cheap — string operations and, for
custom domains, a single Redis set-membership test — because it runs before
anything else on every request including static asset misses.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Callable, NamedTuple

from django.conf import settings

__all__ = [
    "Surface",
    "Resolution",
    "normalize",
    "resolve",
    "register_custom_domain_checker",
]


class Surface(StrEnum):
    MARKETING = "marketing"
    APP = "app"
    ADMIN = "admin"
    STOREFRONT = "storefront"
    UNKNOWN = "unknown"


class Resolution(NamedTuple):
    surface: Surface
    #: Subdomain label for a storefront on the platform apex, else "".
    slug: str
    #: True when the storefront was reached through the owner's own domain.
    is_custom_domain: bool
    #: The normalised host the decision was made from.
    host: str


#: Hosts a developer reaches the runserver on directly. In production these
#: never resolve to a surface — Caddy already refuses TLS for a bare IP — but
#: locally, refusing them means ``runserver`` appears broken to anyone who
#: types the address the startup banner just printed.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "[::1]", "::1"})


def is_loopback(host: str) -> bool:
    return normalize(host) in LOOPBACK_HOSTS


class PathResolution(NamedTuple):
    surface: Surface
    #: Prefix consumed from the path, e.g. "/user". Empty for marketing.
    prefix: str
    #: What is left for the surface's URLconf to resolve, always leading-slash.
    remainder: str
    #: Storefront slug, when the prefix carried one.
    slug: str


def dev_prefix_for(surface: str | Surface) -> str:
    """Path prefix a surface answers on in single-host development mode."""
    prefixes = getattr(settings, "DEV_SURFACE_PREFIXES", {})
    return prefixes.get(str(Surface(surface)), "")


def resolve_dev_path(path: str) -> PathResolution:
    """Pick a surface from a path prefix, for single-host local development.

    On 127.0.0.1 there is only one host, so the Host header cannot distinguish
    the surfaces the way it does in production. Instead the first path segment
    selects one and is then stripped, so each surface's URLconf sees exactly
    the paths it would see on its own domain:

        /                     -> marketing,  urlconf sees /
        /user/dashboard/      -> app,        urlconf sees /dashboard/
        /admin/               -> admin,      urlconf sees /
        /s/acme/cart/         -> storefront, urlconf sees /cart/, slug "acme"

    The stripped prefix becomes the script prefix, so ``reverse()`` and
    ``{% url %}`` put it back automatically. Nothing in a view or template has
    to know which mode it is running under.
    """
    if not path.startswith("/"):
        path = f"/{path}"

    storefront_prefix = dev_prefix_for(Surface.STOREFRONT)

    candidates = [
        (surface, dev_prefix_for(surface))
        for surface in (Surface.ADMIN, Surface.APP, Surface.STOREFRONT)
    ]
    # Longest prefix first, so a nested prefix can never be shadowed by a
    # shorter one that happens to be a prefix of it.
    candidates.sort(key=lambda item: len(item[1]), reverse=True)

    for surface, prefix in candidates:
        if not prefix:
            continue
        if path != prefix and not path.startswith(f"{prefix}/"):
            continue

        remainder = path[len(prefix):] or "/"

        if surface is Surface.STOREFRONT and prefix == storefront_prefix:
            # The segment after the prefix names the store.
            segments = remainder.lstrip("/").split("/", 1)
            slug = segments[0]
            if not slug:
                # "/s/" with no store named is not a storefront.
                return PathResolution(Surface.UNKNOWN, "", path, "")
            rest = f"/{segments[1]}" if len(segments) > 1 else "/"
            return PathResolution(surface, f"{prefix}/{slug}", rest, slug)

        return PathResolution(surface, prefix, remainder, "")

    return PathResolution(Surface.MARKETING, "", path, "")


# The domains app registers a checker at startup. Until it does, no host is
# treated as a verified custom domain — failing closed, so an unverified or
# unknown host can never be served as somebody's storefront.
_custom_domain_checker: Callable[[str], bool] = lambda host: False


def register_custom_domain_checker(checker: Callable[[str], bool]) -> None:
    """Install the verified-custom-domain lookup.

    Called from ``domains.apps.DomainsConfig.ready``. Kept as a hook so ``core``
    carries no dependency on ``domains``.
    """
    global _custom_domain_checker
    _custom_domain_checker = checker


def normalize(raw_host: str | None) -> str:
    """Lowercase, strip the port, strip a trailing dot.

    A trailing dot is a legal fully-qualified form (``example.com.``) that
    resolves identically in DNS but compares unequal as a string. Not
    normalising it is a well-worn way to bypass host allowlists.
    """
    if not raw_host:
        return ""
    host = raw_host.strip().lower()
    # IPv6 literals arrive bracketed: [::1]:8000
    if host.startswith("["):
        closing = host.find("]")
        if closing != -1:
            return host[: closing + 1]
    if ":" in host:
        host = host.split(":", 1)[0]
    return host.rstrip(".")


def resolve(raw_host: str | None) -> Resolution:
    host = normalize(raw_host)
    if not host:
        return Resolution(Surface.UNKNOWN, "", False, "")

    if host == settings.APP_HOST:
        return Resolution(Surface.APP, "", False, host)

    if host == settings.ADMIN_HOST:
        return Resolution(Surface.ADMIN, "", False, host)

    if host in (settings.MARKETING_HOST, f"www.{settings.MARKETING_HOST}"):
        return Resolution(Surface.MARKETING, "", False, host)

    # Storefront on the platform apex: <slug>.onewooz.site
    apex = settings.STOREFRONT_APEX
    suffix = f".{apex}"
    if host.endswith(suffix):
        slug = host[: -len(suffix)]
        # Exactly one label. A nested name like a.b.onewooz.site is not a
        # store we issued and must not resolve to one.
        if slug and "." not in slug:
            return Resolution(Surface.STOREFRONT, slug, False, host)
        return Resolution(Surface.UNKNOWN, "", False, host)

    # The bare apex itself is not a storefront.
    if host == apex:
        return Resolution(Surface.UNKNOWN, "", False, host)

    # Local development on a bare loopback host. The surface comes from the
    # path instead — see resolve_dev_path — so the middleware handles it
    # rather than this function. Gated on DEBUG so a production node reachable
    # by IP never serves a surface this way.
    if settings.DEBUG and host in LOOPBACK_HOSTS:
        return Resolution(Surface.UNKNOWN, "", False, host)

    # Anything left is a storefront only if its owner has proven they control
    # it. Unverified hosts get no surface at all.
    if _custom_domain_checker(host):
        return Resolution(Surface.STOREFRONT, "", True, host)

    return Resolution(Surface.UNKNOWN, "", False, host)
