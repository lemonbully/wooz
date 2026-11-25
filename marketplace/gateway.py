"""The install gateway on app.onewooz.com (the owner dashboard).

Every install, add, purchase and uninstall comes through here:

1. **Signed in** to the dashboard (``login_required``: password + TOTP, passkey,
   or magic link / OTP, with the dashboard's own session limits).
2. **Re-authenticated just now** (:func:`reauth`): password (+ authenticator code
   when enrolled), a passkey, or a 6-digit code emailed to the account address.
   Valid for ``MarketplaceSettings.reauth_window_minutes`` (0 = every action).
3. **Consent screen** listing what the app adds to the dashboard and the store,
   its price and licence, before the owner confirms.
4. **Owner only**: a store team member can never install or buy.

Prices are computed server-side; payment is confirmed by fetching Cashfree.
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from core import audit, ratelimit, tokens

from . import auth, links, licences, orders
from .licences import owner_of

logger = logging.getLogger("onewooz.marketplace")

_RETURN_PURPOSE = "marketplace.order.return"
_CODE_PURPOSE = "marketplace.reauth"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _settings():
    from .models import MarketplaceSettings

    return MarketplaceSettings.current()


def _load(app_slug: str, item_key: str = ""):
    from .models import App, AppItem

    app = App.objects.filter(slug=app_slug).exclude(status=App.Status.DRAFT).first()
    if app is None:
        raise Http404("No such app.")
    item = None
    if item_key:
        item = AppItem.objects.filter(app=app, key=item_key).first()
        if item is None or not (item.is_listed or item.status == AppItem.Status.RETIRED):
            raise Http404("No such item.")
    return app, item


def _owned_sites(user):
    return list(user.sites.filter(is_deleted=False).defer("content").order_by("-created_at"))


def _active_site(request, sites):
    """The store the owner is working on (``sites`` is never empty here)."""
    from commerce.context import active_site_id

    active = str(active_site_id(request) or "")
    return next((s for s in sites if str(s.pk) == active), sites[0])


def _open_url(site, app) -> str:
    """Where an app opens in the dashboard, on one store."""
    return reverse("store_apps:open", kwargs={"site_id": site.pk, "slug": app.slug})


def _is_store_staff_only(user) -> bool:
    """A team member with no store of their own cannot buy for anyone."""
    return not user.sites.filter(is_deleted=False).exists() and _staff_sites(user)


def _staff_sites(user) -> bool:
    try:
        from commerce import staff as staff_mod

        return bool(staff_mod.accessible_sites(user))
    except Exception:
        return False


def _authorize_url(app_slug: str, item_key: str = "", mode: str = "") -> str:
    q = {"app": app_slug}
    if item_key:
        q["item"] = item_key
    if mode:
        q["mode"] = mode
    return reverse("mkt_gateway:authorize") + "?" + urlencode(q)


def _rate(name: str, identity: str) -> bool:
    return ratelimit.consume(name, identity).allowed


# --------------------------------------------------------------------------
# Sign-in hand-off to the marketplace
# --------------------------------------------------------------------------
@login_required
@require_GET
def connect(request):
    if not _rate("marketplace.handoff", str(request.user.pk)):
        messages.error(request, "Too many sign-in attempts. Please wait a minute.")
        return redirect("ud:dashboard")
    token = auth.mint_handoff(request)
    nxt = (request.GET.get("next") or "").strip()
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = ""
    q = urlencode({"t": token, **({"next": nxt[:500]} if nxt else {})})
    return HttpResponseRedirect(links.marketplace_url(request, "marketplace:handoff", q))


# --------------------------------------------------------------------------
# Authorize: re-auth, then consent
# --------------------------------------------------------------------------
@login_required
@require_GET
def authorize(request):
    cfg = _settings()
    if not cfg.is_enabled:
        return render(request, "marketplace/gateway/closed.html", {"message": cfg.closed_message}, status=503)
    app_slug = (request.GET.get("app") or "").strip()[:60]
    item_key = (request.GET.get("item") or "").strip()[:40]
    mode = (request.GET.get("mode") or "").strip()[:10]
    cycle = (request.GET.get("cycle") or "").strip()[:12]
    app, item = _load(app_slug, item_key)
    user = request.user
    sites = _owned_sites(user)
    if not sites and _is_store_staff_only(user):
        return render(request, "marketplace/gateway/owner_only.html", {"app": app}, status=403)

    if not auth.reauth_fresh(request):
        return _reauth_page(request, next_url=request.get_full_path(), app=app, item=item)

    owner = owner_of(user)
    from . import library

    library.ensure(owner)
    ctx = _consent_context(request, owner, app, item, sites, mode, cycle)
    if ctx.get("needs_checkout"):
        request.csp_payment = True
    return render(request, "marketplace/gateway/consent.html", ctx)


def _consent_context(request, owner, app, item, sites, mode, cycle: str = "") -> dict:
    from billing import cashfree

    from .models import Licence

    installed = licences.is_installed(owner, app)
    has = licences.has_app(owner, app)
    item_lic = None
    app_lic = None
    if item is not None:
        rows = [l for l in licences.active_licences(owner, app=app) if l.item_key == item.key and not l.site_ref]
        item_lic = rows[0] if rows else None
    trial_used = Licence.objects.filter(owner_ref=owner.ref, app=app, source=Licence.Source.TRIAL).exists()

    if item is not None:
        if item.is_system_default or (item_lic is not None and not item.is_yearly):
            action = "owned"
        elif item.is_priced:
            action = "buy"
        else:
            action = "add"
    else:
        if installed and has:
            app_lic = licences.app_licence(owner, app)
            # A paid app bought (or trialled) for a period can be renewed at
            # any time; the new period starts when the current one ends.
            if app.is_paid and app.is_periodic and app_lic is not None and app_lic.expires_at:
                action = "renew"
            else:
                action = "installed"
        elif app.is_paid and mode == "trial" and app.trial_days and not trial_used:
            action = "trial"
        elif app.is_paid and not has:
            action = "buy"
        elif app.pricing == app.Pricing.PLAN_INCLUDED and not has:
            action = "upgrade"
        else:
            action = "install"

    pays = action in ("buy", "renew")
    # Billing cycles: every offered cycle with its own quote, so the page can
    # switch between them without a round trip. The server re-prices the
    # chosen one at payment time.
    cycle_rows = []
    if pays and item is None and app.pricing == app.Pricing.CYCLES:
        if app.cycle_option(cycle) is None:
            cycle = app.default_cycle()
        for opt in app.cycle_options():
            cq = orders.quote(app, None, opt["key"])
            cycle_rows.append({**opt, "gst": cq["gst"], "grand": cq["total"], "gst_rate": cq["gst_rate"],
                               "selected": opt["key"] == cycle})
    q = orders.quote(app, item, cycle) if pays else None
    fits = [s for s in sites if (item or app).fits_model(s.business_model)]
    return {
        "app": app,
        "item": item,
        "action": action,
        "quote": q,
        "cycles": cycle_rows,
        "cycle": cycle,
        "app_licence": app_lic,
        "item_licence": item_lic,
        "sites": fits,
        "all_sites": sites,
        "installed": installed,
        "dashboard_points": list(app.dashboard_placements.filter(is_active=True)),
        "storefront_points": list(app.storefront_placements.filter(is_active=True)),
        "reauth_method": auth.reauth_method(request),
        "cashfree_ready": cashfree.is_configured(),
        "needs_checkout": pays and cashfree.is_configured(),
        "marketplace_app_url": links.marketplace_url(request, "marketplace:app", slug=app.slug),
        "upgrade_url": reverse("ud:upgrade"),
        "mode": mode,
    }


# --------------------------------------------------------------------------
# Re-authentication
# --------------------------------------------------------------------------
def _reauth_page(request, *, next_url: str, app=None, item=None, status=200):
    user = request.user
    return render(
        request,
        "marketplace/gateway/reauth.html",
        {
            "next": next_url,
            "app": app,
            "item": item,
            "has_password": user.has_usable_password(),
            "has_totp": user.has_totp,
            "has_passkey": user.has_passkey,
            "masked_email": _mask(user.email),
        },
        status=status,
    )


def _mask(email: str) -> str:
    name, _, domain = (email or "").partition("@")
    if not domain:
        return email
    return (name[:2] + "*" * max(1, len(name) - 2)) + "@" + domain


def _next(request) -> str:
    nxt = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if not nxt.startswith("/") or nxt.startswith("//"):
        return reverse("mkt_gateway:my_apps")
    return nxt[:600]


def _reauth_limited(request) -> bool:
    ok_user = _rate("marketplace.reauth", str(request.user.pk))
    ok_ip = _rate("marketplace.reauth.ip", ratelimit.client_identity(request))
    return not (ok_user and ok_ip)


def _reauth_ok(request, method: str):
    auth.mark_reauthed(request, method)
    audit.record("marketplace.reauth.ok", request=request, context={"method": method})


def _reauth_fail(request, method: str):
    audit.record("marketplace.reauth.failed", request=request, severity="warning", context={"method": method})


@login_required
@require_POST
def reauth_password(request):
    nxt = _next(request)
    if _reauth_limited(request):
        messages.error(request, "Too many attempts. Please wait a few minutes and try again.")
        return _reauth_page(request, next_url=nxt, status=429)
    user = request.user
    password = request.POST.get("password") or ""
    if not user.has_usable_password() or not user.check_password(password):
        _reauth_fail(request, "password")
        messages.error(request, "That password is not right.")
        return _reauth_page(request, next_url=nxt, status=400)
    if user.has_totp:
        from accounts import totp

        code = (request.POST.get("code") or "").strip()
        device = user.totp_devices.filter(confirmed_at__isnull=False).first()
        result = totp.verify(device, code) if device is not None else None
        if result is None or not result.ok:
            _reauth_fail(request, "password+totp")
            messages.error(request, "That authenticator code is not right.")
            return _reauth_page(request, next_url=nxt, status=400)
        _reauth_ok(request, "password+totp")
    else:
        _reauth_ok(request, "password")
    return HttpResponseRedirect(nxt)


@login_required
@require_POST
def reauth_code_send(request):
    user = request.user
    if not _rate("marketplace.reauth.code", str(user.pk)) or not _rate(
        "otp.send.ip", ratelimit.client_identity(request)
    ):
        return JsonResponse({"ok": False, "error": "Too many codes requested. Please wait a few minutes."}, status=429)
    from accounts import emails, otp

    code = otp.issue(_CODE_PURPOSE, user.email)
    if not emails.send_login_code(user.email, code, minutes=otp.TTL_SECONDS // 60):
        return JsonResponse({"ok": False, "error": "We couldn't send the code right now. Please try again."}, status=502)
    return JsonResponse({"ok": True, "email": _mask(user.email)})


@login_required
@require_POST
def reauth_code_verify(request):
    nxt = _next(request)
    if _reauth_limited(request):
        messages.error(request, "Too many attempts. Please wait a few minutes and try again.")
        return _reauth_page(request, next_url=nxt, status=429)
    from accounts import otp

    result = otp.verify(_CODE_PURPOSE, request.user.email, request.POST.get("code") or "")
    if not result.ok:
        _reauth_fail(request, "email_code")
        messages.error(request, "That code is not right or has expired.")
        return _reauth_page(request, next_url=nxt, status=400)
    _reauth_ok(request, "email_code")
    return HttpResponseRedirect(nxt)


@login_required
@require_POST
def reauth_passkey_begin(request):
    from accounts import passkeys

    if not request.user.has_passkey:
        return JsonResponse({"ok": False, "error": "No passkey on this account."}, status=400)
    return JsonResponse({"options": passkeys.begin_authentication(request, request.user)}, safe=False)


@login_required
@require_POST
def reauth_passkey_finish(request):
    from accounts import passkeys

    if _reauth_limited(request):
        return JsonResponse({"ok": False, "error": "Too many attempts. Please wait a few minutes."}, status=429)
    try:
        user, _cred = passkeys.finish_authentication(
            request, request.body.decode("utf-8"), credential_id=request.headers.get("X-Credential-Id", "")
        )
    except passkeys.PasskeyError as exc:
        _reauth_fail(request, "passkey")
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    if user.pk != request.user.pk:
        _reauth_fail(request, "passkey")
        return JsonResponse({"ok": False, "error": "That passkey belongs to another account."}, status=403)
    _reauth_ok(request, "passkey")
    return JsonResponse({"ok": True})


# --------------------------------------------------------------------------
# Install / add / trial / buy
# --------------------------------------------------------------------------
@login_required
@require_POST
def install(request):
    cfg = _settings()
    app_slug = (request.POST.get("app") or "").strip()[:60]
    item_key = (request.POST.get("item") or "").strip()[:40]
    action = (request.POST.get("action") or "").strip()[:10]
    cycle = (request.POST.get("cycle") or "").strip()[:12]
    back = _authorize_url(app_slug, item_key)
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    def fail(msg, status=400):
        if is_ajax:
            return JsonResponse({"ok": False, "error": msg}, status=status)
        messages.error(request, msg)
        return redirect(back)

    if not cfg.is_enabled:
        return fail(cfg.closed_message, 503)
    if not auth.reauth_fresh(request):
        if is_ajax:
            return JsonResponse({"ok": False, "reauth": True, "error": "Please confirm it's you again."}, status=401)
        return redirect(back)
    if not _rate("marketplace.install", str(request.user.pk)):
        return fail("Too many installs in a short time. Please wait a few minutes.", 429)

    app, item = _load(app_slug, item_key)
    user = request.user
    sites = _owned_sites(user)
    if not sites and _is_store_staff_only(user):
        return fail("Only a store owner can install apps.", 403)
    owner = owner_of(user)
    method = auth.reauth_method(request)
    ctx = _consent_context(request, owner, app, item, sites, "trial" if action == "trial" else "", cycle)
    if ctx["action"] != action:
        return fail("That option isn't available any more. Please review and try again.", 409)

    site = None
    site_id = (request.POST.get("site") or "").strip()
    if site_id:
        site = next((s for s in sites if str(s.pk) == site_id), None)

    if action in ("buy", "renew"):
        if not _rate("marketplace.buy", str(user.pk)):
            return fail("Too many payment attempts. Please wait a minute.", 429)

        def return_url_for(order):
            t = tokens.sign(_RETURN_PURPOSE, {"o": str(order.pk), "u": str(user.pk)})
            return request.build_absolute_uri(reverse("mkt_gateway:order_return") + "?" + urlencode({"t": t}))

        notify_url = links.webhook_url(request)
        try:
            params = orders.start(request, owner, app, item, site=site, return_url_for=return_url_for,
                                  notify_url=notify_url, reauth_method=method, cycle=cycle)
        except orders.OrderError as exc:
            return fail(str(exc))
        audit.record("marketplace.order.started", request=request, target=params["order"],
                     target_kind="MarketOrder", target_label=params["order"].number,
                     context={"app": app.slug, "item": item_key, "cycle": cycle, "action": action,
                              "total": f"{params['order'].total_amount:.2f}"})
        auth.consume_reauth(request)
        return JsonResponse({"ok": True, "payment_session_id": params["payment_session_id"], "mode": params["mode"]})

    if action in ("install", "trial"):
        if action == "trial":
            from datetime import timedelta

            from django.utils import timezone

            licences.grant(owner, app, source="trial",
                           expires_at=timezone.now() + timedelta(days=app.trial_days),
                           price=0, reason="Trial", request=request)
        licences.install(owner, app, source="owner", reauth_method=method, request=request)
        msg = f"{app.name} is installed." + (f" Your trial runs for {app.trial_days} days." if action == "trial" else "")
    elif action == "add":
        licences.grant(owner, app, item=item, source="free_add", price=0, reason="Added from the marketplace",
                       actor_kind="owner", actor_label=user.email, request=request)
        if not app.is_system:
            licences.install(owner, app, source="owner", reauth_method=method, request=request)
        msg = f"{item.name} is in your library."
    else:
        return fail("Nothing to do here.")
    auth.consume_reauth(request)
    request.session["mkt_done"] = {"app": app.slug, "item": item_key, "msg": msg}
    if is_ajax:
        return JsonResponse({"ok": True, "redirect": reverse("mkt_gateway:done")})
    return redirect("mkt_gateway:done")


@login_required
@require_GET
def order_return(request):
    from .models import MarketOrder

    order = None
    try:
        payload = tokens.unsign(_RETURN_PURPOSE, request.GET.get("t") or "")
        if str(payload.get("u")) == str(request.user.pk):
            order = MarketOrder.objects.filter(pk=payload.get("o")).first()
    except tokens.TokenError:
        order = None
    if order is None:
        messages.error(request, "We couldn't find that payment. If you were charged, contact us and we will sort it out.")
        return redirect("mkt_gateway:my_apps")
    line = order.lines.select_related("app", "item").first()
    if orders.confirm(order):
        name = line.description if line else "Your purchase"
        request.session["mkt_done"] = {
            "app": line.app.slug if line else "", "item": getattr(line.item, "key", "") if line else "",
            "msg": f"Payment received. {name} is yours.",
        }
        return redirect("mkt_gateway:done")
    messages.error(request, "The payment was not completed, so nothing was charged.")
    return redirect(_authorize_url(line.app.slug, getattr(line.item, "key", "")) if line else reverse("mkt_gateway:my_apps"))


@login_required
@require_GET
def done(request):
    data = request.session.pop("mkt_done", None) or {}
    from .models import App

    app = App.objects.filter(slug=data.get("app", "")).first()
    sites = _owned_sites(request.user)
    first_page = None
    design_url = ""
    if app is not None and sites:
        first_page = _open_url(_active_site(request, sites), app)
        if app.module == "storefront_designs":
            design_url = first_page
    back = links.marketplace_url(request, "marketplace:app", slug=app.slug) if app else links.marketplace_url(
        request, "marketplace:home"
    )
    return render(request, "marketplace/gateway/done.html", {
        "app": app, "message": data.get("msg", "Done."), "first_page": first_page, "design_url": design_url,
        "sites": sites,
        "marketplace_back": reverse("mkt_gateway:connect") + "?" + urlencode({"next": links.path_of(back)}),
    })


# --------------------------------------------------------------------------
# My apps (account level) + uninstall
# --------------------------------------------------------------------------
@login_required
@require_GET
def my_apps(request):
    owner = owner_of(request.user)
    from . import library

    library.ensure(owner)
    try:
        apps = licences.installed_apps(owner)
        lics = licences.active_licences(owner)
    except Exception:
        logger.exception("my apps failed")
        apps, lics = [], []
    from . import health

    paused = set(health.paused_slugs())
    by_app: dict = {}
    for lic in lics:
        by_app.setdefault(lic.app_id, []).append(lic)
    sites = _owned_sites(request.user)
    active = _active_site(request, sites) if sites else None
    rows = []
    for app in apps:
        fits = [s for s in sites if app.fits_model(s.business_model)]
        # The store being worked on first; the app's own link opens there.
        fits.sort(key=lambda s: s.pk != active.pk)
        # Designs are listed on the OneWooz Templates page, not here.
        lics = [] if app.module == "storefront_designs" else by_app.get(app.pk, [])
        rows.append({"app": app, "licences": lics, "paused": app.slug in paused,
                     "stores": [{"name": s.name, "url": _open_url(s, app)} for s in fits]})
    return render(request, "marketplace/gateway/my_apps.html", {
        "rows": rows, "nav_active": "apps",
        "marketplace_home": reverse("mkt_gateway:connect") + "?" + urlencode(
            {"next": links.path_of(links.marketplace_url(request, "marketplace:home"))}
        ),
    })


@login_required
def uninstall(request, slug):
    from .models import App

    app = App.objects.filter(slug=slug).first()
    if app is None:
        raise Http404
    # A default app is part of every dashboard: no removal page, no re-check.
    if app.is_system:
        messages.error(request, f"{app.name} is a default app, so it can't be removed.")
        return redirect("mkt_gateway:my_apps")
    if request.method == "GET":
        if not auth.reauth_fresh(request):
            return _reauth_page(request, next_url=request.get_full_path(), app=app)
        return render(request, "marketplace/gateway/uninstall.html", {"app": app})
    if not auth.reauth_fresh(request):
        return redirect("mkt_gateway:uninstall", slug=slug)
    owner = owner_of(request.user)
    licences.uninstall(owner, app, reauth_method=auth.reauth_method(request), request=request)
    auth.consume_reauth(request)
    messages.success(request, f"{app.name} was removed from your dashboard and stores. Your licence is kept, "
                              "so you can install it again any time.")
    return redirect("mkt_gateway:my_apps")


@login_required
def account_page(request, placement_id):
    """A dashboard-card placement opened without a store: use the active store."""
    sites = _owned_sites(request.user)
    if not sites:
        messages.info(request, "Create a store first, then open this app.")
        return redirect("ud:dashboard")
    active = str(getattr(request.user, "active_site_id", "") or "")
    site = next((s for s in sites if str(s.pk) == active), sites[0])
    return redirect("store_apps:page", site_id=site.pk, placement_id=placement_id)
