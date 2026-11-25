"""The ``storefront_designs`` module: what makes OneWooz Templates work.

Items are the storefront designs deployed in code
(:data:`billing.website_templates.REGISTRY`) plus designs that exist in the
repository but are not integrated yet (kept ``draft``, so hidden). Applying a
design is the existing ``Site.theme`` switch, done from the app's own dashboard
page (:meth:`StorefrontDesigns.home`) or Store Settings; nothing here ever runs
on a storefront request.
"""

from __future__ import annotations

from urllib.parse import urlencode

from django.urls import reverse

from . import Module

#: Designs in the repository that are not wired into Site.theme yet. Listed as
#: hidden drafts so staff can see them coming; they cannot be applied.
_NOT_INTEGRATED = (
    {"key": "5", "name": "Template 5", "description": "Omega style: furniture and lifestyle, editorial type.", "order": 5},
    {"key": "6", "name": "Template 6", "description": "Azimuth style: home tech, rounded cards, glass accents.", "order": 6},
)


class StorefrontDesigns(Module):
    key = "storefront_designs"
    name = "Storefront designs (OneWooz Templates)"
    description = "Sells and licenses storefront designs; applying one switches Site.theme."

    def items(self) -> list[dict]:
        from billing.website_templates import REGISTRY

        out = []
        for spec in REGISTRY:
            out.append(
                {
                    "key": spec["key"],
                    "name": spec["name"],
                    "description": spec.get("description", ""),
                    "order": spec.get("order", 0),
                    "published": bool(spec.get("available", False)),
                    "system_default": spec["key"] == "1",
                    "applicable": True,
                }
            )
        for spec in _NOT_INTEGRATED:
            out.append({**spec, "published": False, "system_default": False, "applicable": False})
        return out

    def applicable_keys(self) -> set[str]:
        from billing.website_templates import registered_keys

        return set(registered_keys())

    def pages(self) -> dict:
        return {"library": self._library_page}

    def _library_page(self, request, site, app, placement):
        return self.home(request, site, app)

    def home(self, request, site, app):
        """The OneWooz Templates page: this store's design library, then the
        designs that can still be added to it."""
        from commerce.views.settings import design_context

        from ..dashboard_views import _shell

        ctx = design_context(request, site)
        ctx.update(
            {
                "app": app,
                "crumb": app.name,
                "more_designs": self._more_designs(site, app),
                "storefront_url": site.storefront_url(request),
            }
        )
        return _shell(request, site, "marketplace/dashboard/templates.html", ctx)

    def _more_designs(self, site, app) -> list[dict]:
        """Listed designs that fit this store and are not in its library yet."""
        from .. import api, library

        if not api.is_enabled():
            return []
        applicable = self.applicable_keys()
        out = []
        for e in library.entries_for_site(site) or []:
            it = e.item
            if e.in_library or e.key not in applicable or not it.is_listed or not it.fits_model(site.business_model):
                continue
            try:
                thumb = it.thumbnail.url if it.thumbnail else ""
            except Exception:
                thumb = ""
            out.append(
                {
                    "key": e.key,
                    "name": it.name,
                    "description": it.tagline or it.description,
                    "thumbnail_url": thumb,
                    "is_priced": it.is_priced,
                    "is_yearly": it.is_yearly,
                    "price": it.price,
                    "lapsed": e.lapsed,
                    "add_url": reverse("mkt_gateway:authorize") + "?" + urlencode({"app": app.slug, "item": e.key}),
                }
            )
        return out

    def on_licence_change(self, *, licence, event: str) -> None:
        # A lapsed or revoked design licence moves affected stores back to a
        # design in their library (Template 1 at worst). Runs from the expiry
        # sweep / revoke on the marketplace queue, never on a storefront.
        if event not in ("expired", "revoked") or not licence.item_key:
            return
        from tenancy.models import Site

        from .. import library

        sites = Site.objects.filter(owner_id=licence.owner_ref, is_deleted=False).defer("content")
        for site in sites:
            if site.theme_choice == licence.item_key:
                library.fallback_site_design(site, reason=f"{licence.name_snapshot} licence {event}")
