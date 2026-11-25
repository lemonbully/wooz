"""Dashboard placement points: ``{% app_point "settings_card" %}``.

Renders the cards / links that installed apps placed at this point, through the
fail-soft facade (:mod:`marketplace.api`). Any failure prints nothing.
"""

from __future__ import annotations

import logging

from django import template
from django.template.loader import render_to_string
from django.urls import reverse

register = template.Library()
logger = logging.getLogger("onewooz.marketplace")


@register.simple_tag(takes_context=True)
def app_point(context, point: str, **extra):
    request = context.get("request")
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return ""
    site = context.get("site")
    try:
        from marketplace import api

        # Owner's apps only: a staff member's own (empty) app list must never be
        # mixed into the owner's store pages.
        owner = getattr(site, "owner", None) if site is not None else user
        if site is not None and getattr(site, "owner_id", None) != getattr(user, "pk", None):
            return ""
        rows = api.placements(request, owner, point, site=site)
        if not rows:
            return ""
        items = []
        for p in rows:
            if site is not None:
                href = reverse("store_apps:page", kwargs={"site_id": site.id, "placement_id": p.placement_id})
            else:
                href = reverse("mkt_gateway:account_page", kwargs={"placement_id": p.placement_id})
            if extra:
                from urllib.parse import urlencode

                href += "?" + urlencode({k: str(v) for k, v in extra.items()})
            items.append({"p": p, "href": href})
        return render_to_string(
            "marketplace/dashboard/_point.html", {"items": items, "point": point}, request=request,
        )
    except Exception:
        logger.warning("app_point %s failed", point, exc_info=True)
        return ""


@register.simple_tag(takes_context=True)
def marketplace_url(context, path="/"):
    """Absolute link to a marketplace page (keeps the dev path prefix)."""
    try:
        from core.links import surface_url

        return surface_url("marketplace", path, context.get("request"))
    except Exception:
        return ""
