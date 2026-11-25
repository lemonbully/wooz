"""The only door from the owner dashboard into the marketplace. Fail-soft.

Every function here catches its own errors and returns the **last known good**
value (kept in the cache for a day) or an empty result. A marketplace bug, a
missing table or a slow query therefore degrades to "the Apps menu is a little
stale" and never to a dashboard error page.

Results are cached per owner under a version number that any licence or
installation change bumps (:func:`invalidate_owner`), and memoised per request.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.core.cache import cache

logger = logging.getLogger("onewooz.marketplace")

_TTL = 60 * 10
_LKG_TTL = 60 * 60 * 24


@dataclass
class PlacementView:
    """A dashboard placement, flattened for templates (no ORM objects)."""

    app_slug: str
    app_name: str
    placement_id: str
    point: str
    nav_group: str
    order: int
    label: str
    summary: str
    icon_key: str
    page_kind: str
    audience: str


@dataclass
class OwnerApps:
    #: [{"slug","name","icon_key","is_system","paused","order","fits"}]
    apps: list = field(default_factory=list)
    placements: list = field(default_factory=list)  # [PlacementView]
    paused: list = field(default_factory=list)


def _version(owner_ref) -> int:
    return int(cache.get(f"mkt:ownerv:{owner_ref}") or 1)


def invalidate_owner(owner_ref) -> None:
    key = f"mkt:ownerv:{owner_ref}"
    try:
        if cache.add(key, 2, None) is False:
            cache.incr(key)
    except Exception:
        cache.set(key, _version(owner_ref) + 1, None)


def invalidate_all() -> None:
    """Catalogue changed (admin save): bump the global catalogue version."""
    try:
        if cache.add("mkt:catv", 2, None) is False:
            cache.incr("mkt:catv")
    except Exception:
        cache.set("mkt:catv", int(cache.get("mkt:catv") or 1) + 1, None)


def catalogue_version() -> int:
    return int(cache.get("mkt:catv") or 1)


def _compute(owner) -> OwnerApps:
    from . import health, licences, registry

    registry.sync()  # the pre-added apps exist before they are listed (cached)
    paused = set(health.paused_slugs())
    result = OwnerApps(paused=sorted(paused))
    for app in licences.installed_apps(owner):
        result.apps.append(
            {"slug": app.slug, "name": app.name, "icon_key": app.icon_key, "is_system": app.is_system,
             "paused": app.slug in paused, "order": app.order, "fits": app.fits}
        )
        if app.slug in paused:
            continue
        for p in app.dashboard_placements.filter(is_active=True):
            result.placements.append(
                PlacementView(
                    app_slug=app.slug, app_name=app.name, placement_id=str(p.pk), point=p.point,
                    nav_group=p.nav_group, order=p.order, label=p.label, summary=p.summary,
                    icon_key=p.icon_key, page_kind=p.page_kind, audience=p.audience,
                )
            )
    result.placements.sort(key=lambda p: (p.order, p.label))
    return result


def owner_apps(request, user) -> OwnerApps:
    """Installed apps and dashboard placements for ``user`` (fail-soft)."""
    from .licences import owner_of

    owner = owner_of(user)
    if owner is None:
        return OwnerApps()
    memo = getattr(request, "_mkt_owner_apps", None) if request is not None else None
    if memo is not None:
        return memo
    key = f"mkt:apps:2:{owner.ref}:{_version(owner.ref)}:{catalogue_version()}"
    lkg_key = f"mkt:apps:2:lkg:{owner.ref}"
    value = None
    try:
        value = cache.get(key)
        if value is None:
            value = _compute(owner)
            cache.set(key, value, _TTL)
            cache.set(lkg_key, value, _LKG_TTL)
    except Exception:
        logger.warning("marketplace owner_apps failed; serving last known good", exc_info=True)
        try:
            value = cache.get(lkg_key)
        except Exception:
            value = None
        value = value or OwnerApps()
    if request is not None:
        request._mkt_owner_apps = value
    return value


def placements(request, user, point: str, *, site=None) -> list[PlacementView]:
    """Placements at a dashboard ``point`` for this owner (and store audience)."""
    try:
        rows = [p for p in owner_apps(request, user).placements if p.point == point]
        if site is not None:
            bm = (getattr(site, "business_model", "") or "").lower()
            rows = [p for p in rows if p.audience in ("both", "") or bm in ("both", "") or p.audience == bm]
        return rows
    except Exception:
        return []


def sidebar_groups(request, user, site) -> dict[str, list[dict]]:
    """``{nav_group: [nav item dict]}`` for the store sidebar.

    Every app on the owner's dashboard is in it: an app with sidebar placements
    shows those; any other app (OneWooz Templates, an app that only adds a card
    or a storefront widget) gets a link of its own in the "Apps" group, opening
    its page (``store_apps:open``).
    """
    from django.urls import reverse

    from .models import _fits

    out: dict[str, list[dict]] = {}
    try:
        for p in placements(request, user, "sidebar", site=site):
            out.setdefault(p.nav_group or "Apps", []).append(
                {
                    "key": f"app:{p.app_slug}:{p.placement_id}",
                    "label": p.label,
                    "url_name": "",
                    "href": reverse("store_apps:page", kwargs={"site_id": site.id, "placement_id": p.placement_id}),
                    "icon": p.icon_key,
                    "locked": False,
                    "order": p.order,
                    "is_app": True,
                }
            )
        mine = owner_apps(request, user)
        placed = {p.app_slug for p in mine.placements if p.point == "sidebar"}
        model = getattr(site, "business_model", "")
        for a in mine.apps:
            if a["slug"] in placed or a.get("paused") or not _fits(a.get("fits", ""), model):
                continue
            out.setdefault("Apps", []).append(
                {
                    "key": f"app:{a['slug']}",
                    "label": a["name"],
                    "url_name": "",
                    "href": reverse("store_apps:open", kwargs={"site_id": site.id, "slug": a["slug"]}),
                    "icon": a["icon_key"] or "grid",
                    "locked": False,
                    "order": a.get("order", 100),
                    "is_app": True,
                }
            )
    except Exception:
        logger.warning("marketplace sidebar failed", exc_info=True)
        return {}
    return out


def nav_key(request, user, site, slug: str) -> str:
    """The sidebar key of an app's own link (what its pages mark as current)."""
    row = next((p for p in placements(request, user, "sidebar", site=site) if p.app_slug == slug), None)
    return f"app:{slug}:{row.placement_id}" if row is not None else f"app:{slug}"


def is_enabled() -> bool:
    try:
        from .models import MarketplaceSettings

        return MarketplaceSettings.current().is_enabled
    except Exception:
        return True
