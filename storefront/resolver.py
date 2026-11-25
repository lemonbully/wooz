"""Resolve a storefront request to the ``Site`` it should serve.

Two ways in, mirroring the two host shapes the router recognises:

* ``<slug>.onewooz.site`` (and the ``/s/<slug>/`` development path) → look the
  slug up.
* a verified custom domain → look the whole host up.

Anything that does not resolve returns ``None``; the view turns that into a 404.
Nothing here trusts an id from the URL  the slug and the custom domain are the
only public handles, and both are unique across the platform.
"""

from __future__ import annotations

from django.core.cache import cache

from core import hosts as host_utils
from tenancy.models import Site

# Scanners request made-up store hosts (random subdomains of the wildcard
# domains) hundreds of times a day; each would cost a database lookup that can
# only miss. A miss is remembered briefly so repeats skip the database. Short on
# purpose: a store created or verified a moment later is at most this many
# seconds from being reachable. Cache errors are ignored (the lookup just runs).
_MISS_TTL = 60


def miss_key(kind: str, label: str) -> str:
    """Cache key for a remembered miss. ``kind`` is "slug" or "ref"."""
    return f"sf:miss:{kind}:{(label or '').lower()}"[:200]


def forget_miss(kind: str, label: str) -> None:
    """Drop a remembered miss (a store or collaboration just took that label)."""
    if label:
        try:
            cache.delete(miss_key(kind, label))
        except Exception:  # noqa: BLE001
            pass


def _request_miss_key(request) -> str:
    # Custom domains are not cached here: host routing only treats a host as a
    # storefront once it is a verified custom domain (storefront.apps, with its
    # own short cache and invalidation), so a miss there is already rare.
    if getattr(request, "is_custom_domain", False):
        return ""
    slug = getattr(request, "site_slug", "") or ""
    if not slug:
        return ""
    return miss_key("ref" if getattr(request, "is_referral", False) else "slug", slug)


def resolve_site(request) -> Site | None:
    if getattr(request, "surface", None) is not host_utils.Surface.STOREFRONT:
        return None
    try:
        key = _request_miss_key(request)
        if key and cache.get(key):
            return None
    except Exception:  # noqa: BLE001
        key = ""
    site = _resolve(request)
    if site is None and key:
        try:
            cache.set(key, 1, _MISS_TTL)
        except Exception:  # noqa: BLE001
            pass
    return site


def _resolve(request) -> Site | None:
    base = Site.objects.filter(is_deleted=False)

    # A UGC collaboration store (<subdomain>.myugcstore.com): the subdomain maps
    # to a verified Collaboration, whose parent Site owns the catalogue. The
    # collaboration is attached to the request so the storefront can scope the
    # catalogue and attribute orders to the creator.
    if getattr(request, "is_referral", False):
        return _resolve_referral(request, base)

    if getattr(request, "is_custom_domain", False):
        from .apps import custom_domain_candidates

        host = host_utils.normalize(request.get_host())
        # Exact host first, then its www twin (www.brand.com → brand.com).
        for candidate in custom_domain_candidates(host):
            site = base.filter(custom_domain=candidate, custom_domain_verified=True).first()
            if site is not None:
                return site
        return None

    slug = getattr(request, "site_slug", "") or ""
    if slug:
        return base.filter(slug=slug).first()

    return None


def _resolve_referral(request, base) -> Site | None:
    """Map a referral subdomain to the verified collaboration's parent Site."""
    from commerce.models import Collaboration

    slug = (getattr(request, "site_slug", "") or "").lower()
    if not slug:
        return None
    live = {Collaboration.Status.VERIFIED, Collaboration.Status.ACTIVE}
    # Crosses tenants deliberately  routing looks a collaborator up by their
    # globally-unique subdomain, before any site is known.
    from core.queries import first_row

    # ``.first()`` sorts by created_at with the store's whole ``content`` JSON
    # joined in; sort keys only (MySQL error 1038 on large stores).
    collab = first_row(
        Collaboration.objects.across_all_sites()
        .filter(subdomain=slug, status__in=live)
        .select_related("site")
    )
    if collab is None or collab.site is None or collab.site.is_deleted:
        return None
    request.collaboration = collab
    return collab.site
