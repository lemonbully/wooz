"""The ``magic_seo`` module: what makes the Magic SEO app work.

Magic SEO adds one control to the owner dashboard: a pencil in Edit content >
SEO that fills every field of every page's SEO with one click. The writing
itself lives in :mod:`prompts.magic_seo` (store data, the admin prompt, the
OpenAI call, validation, apply and undo); this module only answers "may this
store use it?" and gives the app's dashboard link somewhere to open.

* :func:`available` is the editor's fail-soft check (cached marketplace facade,
  never raises): it decides whether the pencil is shown.
* :func:`entitled` is the authoritative check (direct licence lookup), made
  again when a run is started and when the background job runs, so an
  expired, uninstalled or paused app can never write anything.
"""

from __future__ import annotations

import logging

from . import Module

logger = logging.getLogger("onewooz.marketplace")

SLUG = "magic-seo"
KEY = "magic_seo"


def _app_slugs() -> set[str]:
    """Slugs of every app built on this module (staff may add more than one)."""
    from django.core.cache import cache

    key = "mkt:module_slugs:magic_seo"
    slugs = cache.get(key)
    if slugs is None:
        from ..models import App

        slugs = set(App.objects.filter(module=KEY).values_list("slug", flat=True))
        cache.set(key, slugs, 300)
    return set(slugs)


def entitled(site) -> bool:
    """Is a Magic SEO app installed, licensed and not paused for this store's
    owner? Direct lookup; used before any write."""
    from .. import health, licences
    from ..models import App

    owner = licences.owner_of(site)
    if owner is None:
        return False
    paused = set(health.paused_slugs())
    for app in App.objects.filter(module=KEY).exclude(status=App.Status.DRAFT):
        if app.is_paused or app.slug in paused:
            continue
        if not app.fits_model(site.business_model):
            continue
        if licences.is_installed(owner, app) and licences.has_app(owner, app):
            return True
    return False


def available(request, site) -> bool:
    """Should the editor show the pencil? Fail-soft: any error means no."""
    try:
        from .. import api

        slugs = _app_slugs()
        if not slugs:
            return False
        rows = api.owner_apps(request, site.owner).apps
        return any(r.get("slug") in slugs and not r.get("paused") and _fits(r.get("fits"), site) for r in rows)
    except Exception:
        logger.warning("magic seo availability check failed", exc_info=True)
        return False


def _fits(fits, site) -> bool:
    from ..models import _fits as fits_model

    return fits_model(fits or "", site.business_model)


class MagicSeo(Module):
    key = KEY
    name = "Magic SEO (AI writes the SEO tab)"
    description = "A pencil in Edit content > SEO that fills every SEO field of every page in one click."

    def home(self, request, site, app):
        """The app's own sidebar link opens the SEO tab, where the pencil is."""
        from django.shortcuts import redirect
        from django.urls import reverse

        return redirect(reverse("store:editor", kwargs={"site_id": site.pk}) + "#seo")
