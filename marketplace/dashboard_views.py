"""App pages inside the store dashboard (``/store/<site_id>/apps/...``).

Every page renders inside :func:`app_boundary`: an exception in an app shows
"This app is temporarily unavailable" inside the normal store shell (sidebar,
orders, products all keep working) and counts an error towards that app's
circuit breaker. Paused apps show the same panel without running anything.

Owner only. Store team members do not see or open apps.
"""

from __future__ import annotations

import functools
import logging
from urllib.parse import urlencode, urlsplit

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render

from core import ratelimit, tokens

from . import api, compiler, health, licences
from .licences import owner_of

logger = logging.getLogger("onewooz.marketplace")


def _owner_site(request, site_id):
    from commerce.context import get_owned_site

    site = get_owned_site(request, site_id)
    if site.owner_id != request.user.pk:
        raise Http404("Apps are managed by the store owner.")
    return site


def _shell(request, site, template, ctx, status=200):
    from commerce.context import store_context

    # The app's own sidebar link is the current one on every page of that app.
    nav_key = ctx.pop("nav_key", "") or getattr(request, "_mkt_nav_key", "")
    app = getattr(request, "_mkt_app", None)
    if not nav_key and app is not None:
        nav_key = api.nav_key(request, request.user, site, app.slug)
    context = store_context(request, site, nav_key or "apps")
    context.update(ctx)
    return render(request, template, context, status=status)


def app_boundary(view):
    @functools.wraps(view)
    def wrapper(request, site_id, *args, **kwargs):
        site = _owner_site(request, site_id)
        try:
            return view(request, site, *args, **kwargs)
        except Http404:
            raise
        except Exception as exc:
            app = getattr(request, "_mkt_app", None)
            logger.exception("app page failed (%s)", getattr(app, "slug", "?"))
            if app is not None:
                health.record_error(app, "dashboard", exc, {"site": site.pk, "path": request.path})
            return _shell(request, site, "marketplace/dashboard/unavailable.html",
                          {"app": app, "crumb": getattr(app, "name", "App")}, status=200)

    return wrapper


def _placement(request, site, placement_id):
    from .models import AppDashboardPlacement

    p = get_object_or_404(AppDashboardPlacement.objects.select_related("app"), pk=placement_id, is_active=True)
    app = p.app
    request._mkt_app = app
    owner = owner_of(site.owner)
    if not licences.is_installed(owner, app) or not licences.has_app(owner, app):
        raise Http404("This app is not installed.")
    if p.point == "sidebar":
        request._mkt_nav_key = f"app:{app.slug}:{p.pk}"
    return p, app


def _paused(app) -> bool:
    return app.slug in set(health.paused_slugs())


@login_required
@app_boundary
def page(request, site, placement_id):
    p, app = _placement(request, site, placement_id)
    if _paused(app):
        return _shell(request, site, "marketplace/dashboard/unavailable.html",
                      {"app": app, "paused": True, "crumb": app.name})
    kind = p.page_kind
    if kind == "settings_form":
        return _settings(request, site, app, p)
    if kind == "module_page":
        from . import modules

        module = modules.get(app.module)
        fn = (module.pages() if module else {}).get(p.page_key)
        if fn is None:
            raise Http404("This app page is not available.")
        return fn(request, site, app, p)
    if kind == "external_link":
        url = p.external_url
        if not url.startswith("https://"):
            raise Http404
        token = tokens.sign("marketplace.external", {"s": str(site.pk), "a": app.slug, "o": str(site.owner_id)})
        sep = "&" if urlsplit(url).query else "?"
        return HttpResponseRedirect(f"{url}{sep}{urlencode({'ow_token': token})}")
    if kind == "embedded":
        url = p.external_url
        if not url.startswith("https://"):
            raise Http404
        token = tokens.sign("marketplace.external", {"s": str(site.pk), "a": app.slug, "o": str(site.owner_id)})
        sep = "&" if urlsplit(url).query else "?"
        origin = "/".join(url.split("/", 3)[:3])
        request.csp_frame_extra = [origin]
        return _shell(request, site, "marketplace/dashboard/embedded.html", {
            "app": app, "placement": p, "frame_url": f"{url}{sep}{urlencode({'ow_token': token})}",
            "crumb": p.label,
        })
    from .sanitize import clean_html

    return _shell(request, site, "marketplace/dashboard/info.html",
                  {"app": app, "placement": p, "body": clean_html(p.body), "crumb": p.label})


@login_required
@app_boundary
def open_app(request, site, slug):
    """An app's front door (its sidebar link and "Open" on Your apps).

    The module's own page when it has one, else the app's sidebar page, else its
    settings on this store, else the first page it has anywhere.
    """
    from . import modules
    from .models import App

    app = get_object_or_404(App, slug=slug)
    request._mkt_app = app
    owner = owner_of(site.owner)
    if not licences.is_installed(owner, app) or not licences.has_app(owner, app):
        messages.info(request, f"{app.name} is not on your dashboard. You can add it from OneWooz Apps.")
        return redirect("mkt_gateway:my_apps")
    if _paused(app):
        return _shell(request, site, "marketplace/dashboard/unavailable.html",
                      {"app": app, "paused": True, "crumb": app.name})
    module = modules.get(app.module)
    response = module.home(request, site, app) if module is not None else None
    if response is not None:
        return response
    points = list(app.dashboard_placements.filter(is_active=True))
    page_ = next((p for p in points if p.point == "sidebar"), None)
    if page_ is None and not app.settings_fields.exists() and not app.storefront_placements.filter(is_active=True).exists():
        page_ = points[0] if points else None
    if page_ is not None:
        return redirect("store_apps:page", site_id=site.pk, placement_id=page_.pk)
    return _settings(request, site, app, None)


@login_required
@app_boundary
def configure(request, site, slug):
    """Owner settings for one app on this store (from My apps)."""
    from .models import App

    app = get_object_or_404(App, slug=slug)
    request._mkt_app = app
    owner = owner_of(site.owner)
    if not licences.is_installed(owner, app) or not licences.has_app(owner, app):
        raise Http404("This app is not installed.")
    if _paused(app):
        return _shell(request, site, "marketplace/dashboard/unavailable.html",
                      {"app": app, "paused": True, "crumb": app.name})
    return _settings(request, site, app, None)


def _settings(request, site, app, placement):
    from .forms import AppSettingsForm
    from .models import SiteAppConfig

    config = SiteAppConfig.objects.for_site(site).filter(app=app).first()
    placements = list(app.storefront_placements.filter(is_active=True))
    if request.method == "POST":
        if not ratelimit.consume("marketplace.settings", str(request.user.pk)).allowed:
            messages.error(request, "Too many saves in a short time. Please wait a minute.")
            return redirect(request.path)
        form = AppSettingsForm(request.POST, request.FILES, app=app, site=site, config=config, placements=placements)
        if form.is_valid():
            values, secrets, slots = form.cleaned_values()
            if not form.errors:
                if config is None:
                    config = SiteAppConfig(site=site, app=app)
                config.values = values
                config.secrets = secrets
                config.slot_choices = slots
                if placements:
                    config.enabled = bool(form.cleaned_data.get("enabled"))
                config.updated_by_ref = request.user.pk
                config.save()
                from . import modules

                modules.call_hook(app, "on_settings_saved", site=site, app=app, config=config)
                compiler.compile_site(site)
                from commerce import activity

                activity.record("store.app.settings_saved", request=request, site=site, target=site,
                                target_kind="Site", label=app.name)
                messages.success(request, f"{app.name} settings saved." + (
                    " Live on your store now." if config.enabled and placements else ""))
                return redirect(request.path)
    else:
        form = AppSettingsForm(app=app, site=site, config=config, placements=placements)

    from .compiler import load_bundle

    preview_errors = ""
    try:
        from .models import SiteAppRender

        row = SiteAppRender.objects.for_site(site).first()
        if row and row.last_error:
            preview_errors = "\n".join(l for l in row.last_error.splitlines() if l.startswith(app.slug + ":"))
    except Exception:
        pass
    groups: dict[str, list] = {}
    for f in form.app_fields:
        groups.setdefault(f.group or "", []).append(form[f"f_{f.key}"])
    slot_fields = [form[name] for name in form.fields if name.startswith("slot_")]
    return _shell(request, site, "marketplace/dashboard/settings.html", {
        "app": app, "placement": placement, "form": form, "groups": groups, "slot_fields": slot_fields,
        "has_storefront": bool(placements), "storefront_points": placements,
        "preview_errors": preview_errors, "crumb": app.name,
        "storefront_url": site.storefront_url(request) if hasattr(site, "storefront_url") else "",
        "has_bundle": bool(load_bundle(site)),
    })
