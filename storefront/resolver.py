"""Resolve a storefront request to the ``Site`` it should serve.

Two ways in, mirroring the two host shapes the router recognises:

* ``<slug>.onewooz.site`` (and the ``/s/<slug>/`` development path) → look the
  slug up.
* a verified custom domain → look the whole host up.

Anything that does not resolve returns ``None``; the view turns that into a 404.
Nothing here trusts an id from the URL — the slug and the custom domain are the
only public handles, and both are unique across the platform.
"""

from __future__ import annotations

from core import hosts as host_utils
from tenancy.models import Site


def resolve_site(request) -> Site | None:
    if getattr(request, "surface", None) is not host_utils.Surface.STOREFRONT:
        return None

    base = Site.objects.filter(is_deleted=False)

    if getattr(request, "is_custom_domain", False):
        host = host_utils.normalize(request.get_host())
        return base.filter(
            custom_domain=host, custom_domain_verified=True
        ).first()

    slug = getattr(request, "site_slug", "") or ""
    if slug:
        return base.filter(slug=slug).first()

    return None
