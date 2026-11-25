"""Compile a store's app widgets into ``SiteAppRender`` (validate-then-swap).

This is the bridge that keeps storefronts isolated: everything about apps
(licences, installations, catalogue, owner settings) is resolved **here**, on
the marketplace queue or in the owner's own save request, and the result is one
row of ready, sanitised HTML per store. The storefront reads that row only.

* An app whose compile raises keeps its **previous** fragments (never blanks a
  store) and the error counts towards that app's circuit breaker.
* Total output per store is capped; an oversized app is dropped from the new
  bundle, keeping the previous one.
"""

from __future__ import annotations

import logging

from django.core.cache import cache
from django.utils import timezone

from . import blocks, health, widgets
from .licences import installed_apps, owner_of

logger = logging.getLogger("onewooz.marketplace")

MAX_BUNDLE = 512 * 1024
RENDER_CACHE_TTL = 60 * 60 * 6


def render_cache_key(site_id) -> str:
    return f"mkt:render:{site_id}"


def _previous_fragments(old_bundle: dict, slug: str) -> list[tuple[str, dict]]:
    out = []
    for slot, frags in (old_bundle or {}).items():
        for f in frags or []:
            if f.get("app") == slug:
                out.append((slot, f))
    return out


def _compile_app(site, app, config) -> list[tuple[str, dict]]:
    field_keys = set(app.settings_fields.values_list("key", flat=True))
    out = []
    for p in app.storefront_placements.filter(is_active=True):
        allowed = p.slots()
        slot = (config.slot_choices or {}).get(str(p.pk)) if p.owner_can_choose_slot else None
        if slot not in allowed:
            slot = p.default_slot if p.default_slot in allowed else (allowed[0] if allowed else "")
        if slot not in blocks.SLOTS:
            continue
        html, origins = widgets.render(
            site=site, app=app, placement=p, owner_values=config.values or {}, field_keys=field_keys,
        )
        if not html:
            continue
        out.append(
            (
                slot,
                {
                    "app": app.slug,
                    "placement": str(p.pk),
                    "kind": p.widget_type,
                    "html": html,
                    "pages": p.page_list(),
                    "devices": p.devices,
                    "order": p.order,
                    "origins": origins,
                },
            )
        )
    return out


def compile_site(site) -> dict:
    """Recompile one store. Returns the new bundle. Never raises."""
    from .models import SiteAppConfig, SiteAppRender

    try:
        old = SiteAppRender.objects.for_site(site).first()
    except Exception:
        logger.warning("marketplace compile skipped (tables missing?)", exc_info=True)
        return {}
    old_bundle = (old.bundle if old else {}) or {}
    owner = owner_of(site)
    bundle: dict[str, list[dict]] = {}
    compiled_from: dict[str, int] = {}
    errors: list[str] = []
    total = 0
    try:
        apps = installed_apps(owner)
        configs = {c.app_id: c for c in SiteAppConfig.objects.for_site(site)}
    except Exception as exc:
        logger.exception("marketplace compile could not read installs for %s", site.pk)
        apps, configs = [], {}
        errors.append(f"read: {exc}")
        # Keep the old bundle entirely: we know nothing new.
        bundle = old_bundle
    for app in apps:
        config = configs.get(app.pk)
        if config is None or not config.enabled:
            continue
        try:
            frags = _compile_app(site, app, config)
            size = sum(len(f["html"].encode("utf-8")) for _, f in frags)
            if total + size > MAX_BUNDLE:
                raise widgets.WidgetError("store app output is over the 512 KB cap")
            total += size
        except Exception as exc:
            errors.append(f"{app.slug}: {exc}")
            if not isinstance(exc, widgets.WidgetError):
                health.record_error(app, "compile", exc, {"site": site.pk})
            frags = _previous_fragments(old_bundle, app.slug)
        for slot, frag in frags:
            bundle.setdefault(slot, []).append(frag)
        compiled_from[app.slug] = app.config_version
    for frags in bundle.values():
        frags.sort(key=lambda f: (f.get("order", 100), f.get("app", "")))
    origins = sorted({o for frags in bundle.values() for f in frags for o in f.get("origins", [])})
    try:
        SiteAppRender.all_objects.update_or_create(
            site=site,
            defaults={
                "bundle": bundle,
                "script_origins": origins,
                "compiled_from": compiled_from,
                "compiled_at": timezone.now(),
                "last_error": "\n".join(errors)[:4000],
            },
        )
        cache.set(render_cache_key(site.pk), {"bundle": bundle}, RENDER_CACHE_TTL)
    except Exception:
        logger.exception("marketplace compile could not save for %s", site.pk)
    return bundle


def compile_owner(owner_ref) -> int:
    from tenancy.models import Site

    n = 0
    for site in Site.objects.filter(owner_id=owner_ref, is_deleted=False).select_related("owner").defer("content"):
        compile_site(site)
        n += 1
    return n


def load_bundle(site) -> dict:
    """The storefront's read: cache, else one row. Never raises; {} on failure."""
    if site is None or not getattr(site, "pk", None):
        return {}
    key = render_cache_key(site.pk)
    try:
        cached = cache.get(key)
        if cached is not None:
            return cached.get("bundle") or {}
    except Exception:
        pass
    try:
        from .models import SiteAppRender

        row = SiteAppRender.objects.for_site(site).only("bundle").first()
        bundle = (row.bundle if row else {}) or {}
    except Exception:
        return {}
    try:
        cache.set(key, {"bundle": bundle}, RENDER_CACHE_TTL)
    except Exception:
        pass
    return bundle
