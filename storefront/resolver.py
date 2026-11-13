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

from core import hosts as host_utils
from tenancy.models import Site


def resolve_site(request) -> Site | None:
    if getattr(request, "surface", None) is not host_utils.Surface.STOREFRONT:
        return None

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
