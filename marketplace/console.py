"""Marketplace console: control OneWooz Apps from a UI, not the Django admin.

Mounted on the internal admin surface (arcane.onewooz.com/marketplace/), so it
sits behind everything that surface already enforces before any view runs:
the network allowlist, a staff account, the admin's own email + password +
emailed-OTP sign-in, and the idle timeout (custom_admin.middleware).

On top of that:

* reading needs a staff account; **changing anything** needs a superuser or
  the ``marketplace.change_app`` permission;
* every change is written to ``core.audit`` and, for the catalogue, a
  before/after ``CatalogRevision``;
* licence revokes / restores and pauses always take a reason.

Saving catalogue rows fires the same signals as the Django admin, so stores
are recompiled in the background and caches are refreshed.
"""

from __future__ import annotations

import functools
import logging
from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core import audit
from core.queries import paginate_sorted, sorted_rows

from . import blocks, health, licences, modules
from .models import (
    App,
    AppHealth,
    AppItem,
    CatalogRevision,
    Installation,
    Licence,
    LicenceEvent,
    MarketOrder,
    MarketplaceAlert,
    MarketplaceAlertRecipient,
    MarketplaceSettings,
    SiteAppRender,
)

logger = logging.getLogger("onewooz.marketplace")

NAV = (
    ("overview", "Overview"),
    ("apps", "Apps"),
    ("designs", "Designs"),
    ("licences", "Licences"),
    ("orders", "Orders"),
    ("health", "Health"),
    ("alerts", "Alerts"),
    ("settings", "Settings"),
    ("activity", "Activity"),
    ("logs", "Logs"),
)


# --------------------------------------------------------------------------
# Access
# --------------------------------------------------------------------------
def can_write(user) -> bool:
    return bool(user and user.is_active and user.is_staff and (user.is_superuser or user.has_perm("marketplace.change_app")))


def _back(request, default: str) -> str:
    """A same-site path from ``next`` / Referer, else ``default`` (no open redirect)."""
    from urllib.parse import urlsplit

    for raw in (request.POST.get("next"), request.META.get("HTTP_REFERER")):
        raw = (raw or "").strip()
        if not raw:
            continue
        parts = urlsplit(raw)
        if parts.netloc and parts.netloc != request.get_host():
            continue
        path = parts.path or "/"
        if path.startswith("/") and not path.startswith("//"):
            return path + (f"?{parts.query}" if parts.query else "")
    return default


def console_view(write: bool = False):
    def deco(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            user = getattr(request, "user", None)
            if not (user and user.is_authenticated and user.is_staff):
                raise PermissionDenied
            if (write or request.method == "POST") and not can_write(user):
                messages.error(request, "Your account can view the marketplace console but not change it.")
                return redirect(_back(request, reverse("mkt_console:overview")))
            return view(request, *args, **kwargs)

        return wrapper

    return deco


def _short(label) -> str:
    """'Draft (hidden)' -> 'Draft'; 'Pending payment' stays."""
    return str(label).split(" (")[0]


def _choices(choices) -> list:
    return [(k, _short(v)) for k, v in choices]


def _ctx(request, section: str, **extra) -> dict:
    from core.links import surface_url

    ctx = {
        "nav_items": NAV,
        "section": section,
        "can_write": can_write(request.user),
        "live_url": surface_url("marketplace", "/", request),
        "db_url": reverse("admin:index"),
        "admin_home": reverse("custom_admin:overview"),
        "mkt_settings": MarketplaceSettings.current(),
        "paused_count": App.objects.filter(is_paused=True).count(),
    }
    ctx.update(extra)
    return ctx


def _log(request, action: str, target=None, **context):
    audit.record(f"marketplace.console.{action}", request=request, actor_kind="admin", target=target,
                 target_kind=target.__class__.__name__ if target is not None else "",
                 context={k: str(v)[:300] for k, v in context.items()})


def _revision(request, app, obj, before):
    from custom_admin.marketplace_admin import _revision as rev

    rev(request, app, obj, before)


def _jsonable(obj) -> dict:
    from custom_admin.marketplace_admin import _jsonable as js

    return js(obj)


# --------------------------------------------------------------------------
# Overview
# --------------------------------------------------------------------------
@console_view()
def overview(request):
    from . import registry

    registry.sync()
    now = timezone.now()
    since = now - timedelta(days=30)
    paid = MarketOrder.objects.filter(status=MarketOrder.Status.PAID)
    kpis = [
        {"label": "Published apps", "value": App.objects.filter(status=App.Status.PUBLISHED).count(),
         "sub": f"{App.objects.count()} in total"},
        {"label": "Active installs", "value": Installation.objects.filter(status=Installation.Status.ACTIVE).count(),
         "sub": "plus built-in apps for everyone"},
        {"label": "Active licences", "value": Licence.objects.filter(status=Licence.Status.ACTIVE).count(),
         "sub": f"{Licence.objects.filter(created_at__gte=since).count()} granted in 30 days"},
        {"label": "Revenue, 30 days",
         "value": f"₹{(paid.filter(paid_at__gte=since).aggregate(t=Sum('total_amount'))['t'] or 0):,.0f}",
         "sub": f"{paid.filter(paid_at__gte=since).count()} paid orders"},
        {"label": "Paused apps", "value": App.objects.filter(is_paused=True).count(), "sub": "kill switch or breaker",
         "warn": App.objects.filter(is_paused=True).exists()},
        {"label": "Pending payments", "value": MarketOrder.objects.filter(status=MarketOrder.Status.PENDING).count(),
         "sub": "confirmed by the reconcile sweep"},
    ]
    apps = sorted_rows(App.objects.order_by("order", "name"), limit=8)
    for a in apps:
        a.err_window = health.errors_in_window(a.slug, a.breaker_window_minutes)
        a.status_label = _short(a.get_status_display())
    orders = list(MarketOrder.objects.order_by("-created_at")[:6])
    events = sorted_rows(LicenceEvent.objects.select_related("licence", "licence__app").order_by("-created_at"), limit=8)
    alerts = sorted_rows(MarketplaceAlert.objects.select_related("app").order_by("-created_at"), limit=5)
    return render(request, "marketplace/console/overview.html", _ctx(
        request, "overview", kpis=kpis, apps=apps, orders=orders, events=events, alerts=alerts,
        has_recipients=MarketplaceAlertRecipient.objects.filter(is_active=True).exists(),
    ))


@console_view(write=True)
@require_POST
def toggle_marketplace(request):
    cfg = MarketplaceSettings.current()
    if not cfg.pk:
        cfg = MarketplaceSettings.objects.create()
    cfg.is_enabled = not cfg.is_enabled
    cfg.save()
    _log(request, "toggle", cfg, enabled=cfg.is_enabled)
    messages.success(request, "Marketplace is open." if cfg.is_enabled else
                     "Marketplace closed. Everything installed keeps working.")
    return redirect(_back(request, reverse("mkt_console:overview")))


# --------------------------------------------------------------------------
# Apps
# --------------------------------------------------------------------------
@console_view()
def apps(request):
    from . import registry

    registry.sync()
    status = (request.GET.get("status") or "").strip()
    q = (request.GET.get("q") or "").strip()[:60]
    qs = App.objects.all()
    if status in App.Status.values:
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(slug__icontains=q))
    rows = sorted_rows(qs.order_by("order", "name"))
    installs = dict(
        Installation.objects.filter(status=Installation.Status.ACTIVE).values("app_id").annotate(n=Count("id"))
        .values_list("app_id", "n")
    )
    states = dict(AppHealth.objects.values_list("app_id", "state"))
    for a in rows:
        a.n_installs = "Everyone" if a.is_default else installs.get(a.pk, 0)
        a.health_state = states.get(a.pk, "closed")
        a.n_dash = a.dashboard_placements.count()
        a.status_label = _short(a.get_status_display())
        a.n_store = a.storefront_placements.count()
    return render(request, "marketplace/console/apps.html", _ctx(
        request, "apps", rows=rows, status=status, q=q, statuses=_choices(App.Status.choices),
    ))


TABS = (
    ("general", "General"),
    ("form", "Owner settings form"),
    ("dashboard", "Dashboard placements"),
    ("storefront", "Storefront widgets"),
    ("items", "Items"),
    ("screens", "Screenshots"),
)


@console_view()
def app_edit(request, app_id=None):
    from . import console_forms as cf

    app = get_object_or_404(App, pk=app_id) if app_id else None
    tab = request.GET.get("tab") or request.POST.get("tab") or "general"
    if app is None:
        tab = "general"
    if tab not in dict(TABS):
        raise Http404

    form = formset = None
    if tab == "general":
        form = cf.AppForm(request.POST or None, request.FILES or None, instance=app)
        if request.method == "POST":
            if not can_write(request.user):
                raise PermissionDenied
            before = _jsonable(App.objects.get(pk=app.pk)) if app else {}
            was_paused = bool(app and app.is_paused)
            if form.is_valid():
                obj = form.save()
                _revision(request, obj, obj, before)
                if obj.is_paused != was_paused:
                    health.refresh_paused_set()
                _log(request, "app.saved", obj, slug=obj.slug, created=not before)
                messages.success(request, f"{obj.name} saved." + (" Stores are updating in the background." if before else ""))
                return redirect(reverse("mkt_console:app_edit", kwargs={"app_id": obj.pk}) + "?tab=general")
            messages.error(request, "Please fix the highlighted fields.")
    else:
        factory = {
            "form": cf.SettingsFieldFormSet,
            "dashboard": cf.DashboardFormSet,
            "storefront": cf.StorefrontFormSet,
            "items": cf.ItemFormSet,
            "screens": cf.ScreenshotFormSet,
        }[tab]
        formset = factory(request.POST or None, request.FILES or None, instance=app, prefix=tab)
        if request.method == "POST":
            if not can_write(request.user):
                raise PermissionDenied
            if formset.is_valid():
                instances = formset.save(commit=False)
                for inst in instances:
                    before = {}
                    if inst.pk:
                        old = type(inst).objects.filter(pk=inst.pk).first()
                        before = _jsonable(old) if old else {}
                    inst.save()
                    _revision(request, app, inst, before)
                for deleted in formset.deleted_objects:
                    _revision(request, app, deleted, _jsonable(deleted))
                    deleted.delete()
                formset.save_m2m()
                _log(request, f"app.{tab}.saved", app, slug=app.slug, changed=len(instances),
                     deleted=len(formset.deleted_objects))
                messages.success(request, "Saved. Stores that have this app are updating in the background.")
                return redirect(reverse("mkt_console:app_edit", kwargs={"app_id": app.pk}) + f"?tab={tab}")
            messages.error(request, "Please fix the highlighted rows.")

    module = modules.get(app.module) if app else None
    if app:
        app.status_label = _short(app.get_status_display())
    info = {}
    if app:
        h = AppHealth.objects.filter(app=app).first()
        info = {
            "installs": "Everyone" if app.is_default else Installation.objects.filter(
                app=app, status=Installation.Status.ACTIVE).count(),
            "licences": Licence.objects.filter(app=app, status=Licence.Status.ACTIVE).count(),
            "errors": health.errors_in_window(app.slug, app.breaker_window_minutes),
            "health": h.get_state_display() if h else "Healthy",
            "revisions": sorted_rows(CatalogRevision.objects.filter(app=app).order_by("-created_at"), limit=6),
        }
    return render(request, "marketplace/console/app_edit.html", _ctx(
        request, "apps", app=app, tab=tab, tabs=TABS if app else TABS[:1], form=form, formset=formset,
        sections=cf.APP_SECTIONS, module=module, info=info, widget_help=cf.WIDGET_HELP,
        field_types=blocks.FIELD_TYPES.values(), slots=blocks.SLOTS.values(), points=blocks.PLACEMENT_POINTS.items(),
        pause_form=cf.PauseForm(),
        marketplace_url=_marketplace_app_url(request, app) if app else "",
    ))


def _changed_keys(rev) -> list[str]:
    before, after = rev.before or {}, rev.after or {}
    if not before:
        return []
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k) and k not in ("updated_at",))[:12]


def _marketplace_app_url(request, app) -> str:
    from . import links

    try:
        return links.marketplace_url(request, "marketplace:app", slug=app.slug)
    except Exception:
        return ""


@console_view(write=True)
@require_POST
def app_action(request, app_id):
    from . import console_forms as cf
    from . import tasks

    app = get_object_or_404(App, pk=app_id)
    action = request.POST.get("action", "")
    back = reverse("mkt_console:app_edit", kwargs={"app_id": app.pk})
    if action in ("publish", "unlist", "retire", "draft"):
        target = {"publish": App.Status.PUBLISHED, "unlist": App.Status.UNLISTED,
                  "retire": App.Status.RETIRED, "draft": App.Status.DRAFT}[action]
        if app.is_system and target in (App.Status.DRAFT, App.Status.RETIRED):
            messages.error(request, "A system app cannot be hidden or retired.")
            return redirect(back)
        before = _jsonable(app)
        app.status = target
        app.save()
        _revision(request, app, app, before)
        _log(request, f"app.{action}", app, slug=app.slug)
        messages.success(request, f"{app.name} is now {app.get_status_display().lower()}.")
    elif action == "pause":
        form = cf.PauseForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Give the pause a length (minutes) and a reason.")
            return redirect(back)
        health.pause(app, minutes=form.cleaned_data["minutes"] or None,
                     reason=form.cleaned_data["reason"], by=str(request.user))
        _log(request, "app.paused", app, reason=form.cleaned_data["reason"])
        messages.success(request, f"{app.name} paused. Its pages and widgets are hidden everywhere.")
    elif action == "resume":
        health.resume(app, by=str(request.user))
        _log(request, "app.resumed", app)
        messages.success(request, f"{app.name} resumed.")
    elif action == "recompile":
        tasks.enqueue_recompile_app(app.pk, force=True)
        _log(request, "app.recompile", app)
        messages.success(request, "Recompile queued for every store that has this app.")
    else:
        messages.error(request, "Unknown action.")
    return redirect(_back(request, back))


# --------------------------------------------------------------------------
# Designs (OneWooz Templates items) quick pricing
# --------------------------------------------------------------------------
@console_view()
def designs(request):
    from . import console_forms as cf
    from .registry import templates_app

    app = templates_app()
    if app is None:
        messages.error(request, "OneWooz Templates is not set up yet (run migrations).")
        return redirect("mkt_console:overview")
    formset = cf.ItemFormSet(request.POST or None, request.FILES or None, instance=app, prefix="items")
    if request.method == "POST":
        if formset.is_valid():
            n = 0
            for inst in formset.save(commit=False):
                old = AppItem.objects.filter(pk=inst.pk).first() if inst.pk else None
                before = _jsonable(old) if old else {}
                inst.save()
                _revision(request, app, inst, before)
                n += 1
            _log(request, "designs.saved", app, changed=n)
            from . import api

            api.invalidate_all()
            messages.success(request, f"Saved {n} design{'s' if n != 1 else ''}.")
            return redirect("mkt_console:designs")
        messages.error(request, "Please fix the highlighted designs.")
    owners = dict(
        Licence.objects.filter(app=app, status=Licence.Status.ACTIVE).exclude(item=None)
        .values("item_id").annotate(n=Count("id")).values_list("item_id", "n")
    )
    for f in formset.forms:
        f.owners = owners.get(f.instance.pk, 0) if f.instance.pk else 0
    return render(request, "marketplace/console/designs.html", _ctx(
        request, "designs", app=app, formset=formset,
        marketplace_url=_marketplace_app_url(request, app),
    ))


# --------------------------------------------------------------------------
# Licences
# --------------------------------------------------------------------------
@console_view()
def licences_view(request):
    from . import console_forms as cf

    q = (request.GET.get("q") or "").strip()[:120]
    app_id = (request.GET.get("app") or "").strip()
    status = (request.GET.get("status") or "").strip()
    source = (request.GET.get("source") or "").strip()
    qs = Licence.objects.select_related("app", "item")
    if q:
        qs = qs.filter(Q(owner_email__icontains=q) | Q(name_snapshot__icontains=q) | Q(legacy_ref__icontains=q))
    if app_id:
        qs = qs.filter(app_id=app_id)
    if status in Licence.Status.values:
        qs = qs.filter(status=status)
    if source in Licence.Source.values:
        qs = qs.filter(source=source)
    page = paginate_sorted(qs.order_by("-created_at"), 40, request.GET.get("page"))

    grant = cf.LicenceGrantForm(request.POST or None, prefix="grant")
    if request.method == "POST":
        if grant.is_valid():
            user = grant._user
            owner = licences.owner_of(user)
            lic = licences.grant(
                owner, grant.cleaned_data["app"], item=grant.cleaned_data.get("item"), source=Licence.Source.ADMIN_GRANT,
                expires_at=grant.cleaned_data.get("expires_at"), actor_kind="admin", actor_label=str(request.user),
                reason=grant.cleaned_data["reason"], request=request,
            )
            if lic.item_id is None and not lic.app.is_system:
                licences.install(owner, lic.app, source="admin", request=request)
            messages.success(request, f"Granted {lic.name_snapshot} to {lic.owner_email}.")
            return redirect("mkt_console:licences")
        messages.error(request, "The grant could not be saved. Check the form.")
    return render(request, "marketplace/console/licences.html", _ctx(
        request, "licences", page=page, q=q, app_id=app_id, status=status, source=source, grant=grant,
        apps=sorted_rows(App.objects.order_by("name")), statuses=Licence.Status.choices, sources=Licence.Source.choices,
    ))


@console_view(write=True)
@require_POST
def licence_action(request, licence_id):
    from . import console_forms as cf

    lic = get_object_or_404(Licence.objects.select_related("app"), pk=licence_id)
    form = cf.ReasonForm(request.POST)
    if not form.is_valid():
        messages.error(request, "A reason is required.")
        return redirect(_back(request, reverse("mkt_console:licences")))
    reason = form.cleaned_data["reason"]
    if request.POST.get("action") == "revoke":
        licences.revoke(lic, reason=reason, actor_label=str(request.user), request=request)
        messages.success(request, f"Revoked {lic.name_snapshot} for {lic.owner_email}. Their stores fall back safely.")
    elif request.POST.get("action") == "restore":
        licences.restore(lic, reason=reason, actor_label=str(request.user), request=request)
        messages.success(request, f"Restored {lic.name_snapshot} for {lic.owner_email}.")
    return redirect(_back(request, reverse("mkt_console:licences")))


# --------------------------------------------------------------------------
# Orders
# --------------------------------------------------------------------------
@console_view()
def orders_view(request):
    q = (request.GET.get("q") or "").strip()[:120]
    status = (request.GET.get("status") or "").strip()
    qs = MarketOrder.objects.prefetch_related("lines")
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(owner_email__icontains=q) | Q(cf_order_id__icontains=q))
    if status in MarketOrder.Status.values:
        qs = qs.filter(status=status)
    page = paginate_sorted(qs.order_by("-created_at"), 40, request.GET.get("page"))
    since = timezone.now() - timedelta(days=30)
    totals = MarketOrder.objects.filter(status=MarketOrder.Status.PAID, paid_at__gte=since).aggregate(
        t=Sum("total_amount"), g=Sum("gst_amount"))
    return render(request, "marketplace/console/orders.html", _ctx(
        request, "orders", page=page, q=q, status=status, statuses=_choices(MarketOrder.Status.choices), totals=totals,
    ))


@console_view(write=True)
@require_POST
def order_check(request, order_id):
    from . import orders

    order = get_object_or_404(MarketOrder, pk=order_id)
    ok = orders.confirm(order)
    _log(request, "order.checked", order, number=order.number, paid=ok)
    messages.success(request, f"{order.number}: {'paid and settled' if ok else 'not paid (status ' + (order.gateway_status or 'unknown') + ')'}.")
    return redirect(_back(request, reverse("mkt_console:orders")))


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------
@console_view()
def health_view(request):
    from . import console_forms as cf

    rows = []
    hmap = {h.app_id: h for h in AppHealth.objects.all()}
    for app in sorted_rows(App.objects.exclude(status=App.Status.DRAFT).order_by("order", "name")):
        h = hmap.get(app.pk)
        rows.append({
            "app": app, "health": h,
            "errors": health.errors_in_window(app.slug, app.breaker_window_minutes),
            "state": h.get_state_display() if h else "Healthy",
            "state_key": h.state if h else "closed",
        })
    renders_with_errors = SiteAppRender.all_objects.exclude(last_error="").count()
    return render(request, "marketplace/console/health.html", _ctx(
        request, "health", rows=rows, pause_form=cf.PauseForm(), renders_with_errors=renders_with_errors,
        paused=health.paused_slugs(),
    ))


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------
@console_view()
def alerts_view(request, recipient_id=None):
    from . import console_forms as cf

    recipient = get_object_or_404(MarketplaceAlertRecipient, pk=recipient_id) if recipient_id else None
    form = cf.AlertRecipientForm(request.POST or None, instance=recipient, prefix="r")
    if request.method == "POST":
        if form.is_valid():
            obj = form.save()
            _log(request, "alerts.recipient.saved", obj, email=obj.email)
            messages.success(request, f"{obj.name} will receive marketplace alerts.")
            return redirect("mkt_console:alerts")
        messages.error(request, "Please fix the highlighted fields.")
    recipients = list(MarketplaceAlertRecipient.objects.prefetch_related("apps").order_by("name"))
    log = sorted_rows(MarketplaceAlert.objects.select_related("app").order_by("-created_at"), limit=40)
    return render(request, "marketplace/console/alerts.html", _ctx(
        request, "alerts", form=form, recipient=recipient, recipients=recipients, log=log,
    ))


@console_view(write=True)
@require_POST
def alert_action(request, recipient_id):
    from django.conf import settings
    from django.core.mail import send_mail

    r = get_object_or_404(MarketplaceAlertRecipient, pk=recipient_id)
    action = request.POST.get("action")
    if action == "delete":
        _log(request, "alerts.recipient.deleted", r, email=r.email)
        r.delete()
        messages.success(request, "Recipient removed.")
    elif action == "toggle":
        r.is_active = not r.is_active
        r.save(update_fields=["is_active", "updated_at"])
        messages.success(request, f"{r.name} {'will' if r.is_active else 'will no longer'} receive alerts.")
    elif action == "test":
        try:
            send_mail("[OneWooz Apps] Test alert", "Marketplace alerts will reach you here.",
                      getattr(settings, "DEFAULT_FROM_EMAIL", None), [r.email])
            messages.success(request, f"Test alert sent to {r.email}.")
        except Exception as exc:
            messages.error(request, f"Could not send: {exc}")
    return redirect("mkt_console:alerts")


# --------------------------------------------------------------------------
# Settings + activity
# --------------------------------------------------------------------------
@console_view()
def settings_view(request):
    from . import console_forms as cf

    cfg = MarketplaceSettings.current()
    if not cfg.pk:
        cfg = MarketplaceSettings.objects.create()
    form = cf.MarketplaceSettingsForm(request.POST or None, instance=cfg)
    if request.method == "POST":
        before = _jsonable(MarketplaceSettings.objects.get(pk=cfg.pk))
        if form.is_valid():
            obj = form.save()
            _log(request, "settings.saved", obj, before=before, after=_jsonable(obj))
            messages.success(request, "Marketplace settings saved.")
            return redirect("mkt_console:settings")
        messages.error(request, "Please fix the highlighted fields.")
    return render(request, "marketplace/console/settings.html", _ctx(
        request, "settings", form=form, field_types=blocks.FIELD_TYPES.values(), slots=blocks.SLOTS.values(),
        widgets=blocks.WIDGET_TYPES.values(), points=blocks.PLACEMENT_POINTS.items(), module_list=modules.choices()[1:],
    ))


@console_view()
def activity(request):
    revisions = paginate_sorted(CatalogRevision.objects.select_related("app").order_by("-created_at"), 40,
                                request.GET.get("page"))
    events = sorted_rows(LicenceEvent.objects.select_related("licence", "licence__app").order_by("-created_at"), limit=30)
    for r in revisions.object_list:
        r.changed = _changed_keys(r)
    return render(request, "marketplace/console/activity.html", _ctx(
        request, "activity", revisions=revisions, events=events,
    ))


# --------------------------------------------------------------------------
# Logs (each app's working log: marketplace.applog)
# --------------------------------------------------------------------------
@console_view()
def logs_view(request):
    """Every app's working log, newest first: filter by app, level, store,
    run, or words in the message. Read-only; lines expire after
    ``applog.RETENTION_DAYS`` days."""
    import json
    from urllib.parse import urlencode

    from . import applog
    from .models import AppLog

    app_slug = (request.GET.get("app") or "").strip()[:60]
    level = (request.GET.get("level") or "").strip()
    run = (request.GET.get("run") or "").strip()[:40]
    store = (request.GET.get("store") or "").strip()[:120]
    q = (request.GET.get("q") or "").strip()[:120]

    qs = AppLog.objects.all()
    if app_slug:
        qs = qs.filter(app_slug=app_slug)
    if level in AppLog.Level.values:
        qs = qs.filter(level=level)
    if run:
        qs = qs.filter(run_ref=run)
    if store:
        qs = qs.filter(site_label__icontains=store)
    if q:
        qs = qs.filter(Q(message__icontains=q) | Q(event__icontains=q))
    page = paginate_sorted(qs.order_by("-created_at"), 50, request.GET.get("page"))
    names = dict(App.objects.order_by().values_list("slug", "name"))
    for row in page.object_list:
        row.details = json.dumps(row.context, indent=1, ensure_ascii=False) if row.context else ""
        row.app_name = names.get(row.app_slug, row.app_slug)

    since = timezone.now() - timedelta(hours=24)
    recent = AppLog.objects.filter(created_at__gte=since)
    if app_slug:
        recent = recent.filter(app_slug=app_slug)
    counts = {r["level"]: r["n"] for r in recent.order_by().values("level").annotate(n=Count("pk"))}

    keep = {k: v for k, v in (("app", app_slug), ("run", run), ("store", store), ("q", q)) if v}
    logged = set(AppLog.objects.order_by().values_list("app_slug", flat=True).distinct()[:200])
    app_options = sorted(((slug, names.get(slug, slug)) for slug in set(names) | logged), key=lambda x: x[1].lower())
    return render(request, "marketplace/console/logs.html", _ctx(
        request, "logs", page=page, app_slug=app_slug, level=level, run=run, store=store, q=q,
        levels=AppLog.Level.choices, app_options=app_options, app_label=names.get(app_slug, app_slug),
        counts={"info": counts.get("info", 0), "warning": counts.get("warning", 0), "error": counts.get("error", 0)},
        keep_qs=urlencode(keep), level_qs=urlencode({**keep, **({"level": level} if level else {})}),
        retention_days=applog.RETENTION_DAYS,
    ))
