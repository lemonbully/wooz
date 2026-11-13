"""Integration & settings pages: payments, shipping, tracking, Google Merchant."""

from __future__ import annotations

from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from commerce import activity
from commerce.context import get_owned_site, store_context
from commerce.forms import (
    GoogleMerchantForm,
    PaymentConfigForm,
    ShippingProviderForm,
    TrackingConfigForm,
    WarehouseForm,
)
from commerce.models import (
    GoogleMerchantConfig,
    PaymentGatewayConfig,
    Product,
    ShippingProvider,
    TrackingConfig,
    Warehouse,
)


def _get_or_new(model, site):
    """Fetch the store's singleton config row, or an unsaved one to bind to."""
    existing = model.objects.for_site(site).first()
    return existing or model(site=site)


# Fields whose presence means the storefront needs third-party tag hosts in CSP.
_TRACKING_TAG_FIELDS = (
    "google_analytics_id",
    "google_tag_manager_id",
    "google_ads_id",
    "meta_pixel_id",
    "tiktok_pixel_id",
    "pinterest_tag_id",
    "microsoft_uet_id",
    "custom_head_html",
)


def _sync_custom_scripts_flag(site, config) -> None:
    """Turn ``site.allows_custom_scripts`` on when any browser-side tag is
    configured, off when all are cleared  keeping the CSP relaxation exactly
    as wide as it needs to be for this store."""
    wants = any((getattr(config, f) or "").strip() for f in _TRACKING_TAG_FIELDS)
    if bool(site.allows_custom_scripts) != wants:
        site.allows_custom_scripts = wants
        site.save(update_fields=["allows_custom_scripts"])


def _check_gateway_keys(request, config) -> None:
    """Test freshly saved Cashfree keys, so a wrong or mismatched key surfaces
    here rather than as a failed payment at a customer's checkout."""
    if not (config.enabled and config.gateway == PaymentGatewayConfig.Gateway.CASHFREE):
        return
    if not (config.key_id and config.key_secret):
        messages.warning(request, "Add your Cashfree App ID and Secret Key to accept online payments.")
        return
    from commerce.payments import check_cashfree_credentials

    ok, env, message = check_cashfree_credentials(config)
    if ok:
        label = "Test (sandbox)" if env == "sandbox" else "Live (production)"
        messages.info(request, f"Cashfree connected: {label} mode.")
    else:
        messages.error(
            request,
            "Cashfree didn’t accept these keys, so customers can’t pay online yet. "
            "Paste the App ID and Secret Key from the same Cashfree environment "
            f"(Payment Gateway → Developers → API Keys). Cashfree said: {message}",
        )


@login_required
def payments(request, site_id):
    site = get_owned_site(request, site_id)
    instance = _get_or_new(PaymentGatewayConfig, site)
    if request.method == "POST":
        # Capture the stored secrets before the bound form overwrites the
        # instance: password inputs come back empty when the owner isn't
        # changing a key, and we must not wipe a saved credential in that case.
        original_key_id = instance.key_id or ""
        original_key_secret = instance.key_secret or ""

        form = PaymentConfigForm(request.POST, request.FILES, instance=instance)
        if form.is_valid():
            config = form.save(commit=False)
            config.site = site
            if not (form.cleaned_data.get("key_id") or "").strip():
                config.key_id = original_key_id
            if not (form.cleaned_data.get("key_secret") or "").strip():
                config.key_secret = original_key_secret
            config.save()
            activity.record(
                "store.payments.updated", request=request, site=site,
                target=site, target_kind="Site", label="Payment settings",
            )
            messages.success(request, "Payment settings saved.")
            _check_gateway_keys(request, config)
            return redirect("store:payments", site_id=site.id)
        # Don't fail silently  surface why nothing was saved.
        messages.error(request, "Payment settings couldn't be saved  please check the highlighted fields.")
    else:
        form = PaymentConfigForm(instance=instance)

    context = store_context(request, site, "payments")
    context.update({"form": form, "config": instance})
    return render(request, "store/payments.html", context)


@login_required
def tracking(request, site_id):
    site = get_owned_site(request, site_id)
    instance = _get_or_new(TrackingConfig, site)
    if request.method == "POST":
        form = TrackingConfigForm(request.POST, instance=instance)
        if form.is_valid():
            config = form.save(commit=False)
            config.site = site
            config.save()
            # Admit the tracking hosts into this storefront's CSP exactly when
            # something is configured  so the tags actually load  and withdraw
            # the relaxation when every field is cleared. No separate opt-in.
            _sync_custom_scripts_flag(site, config)
            activity.record(
                "store.tracking.updated", request=request, site=site,
                target=site, target_kind="Site", label="Tracking & analytics",
            )
            messages.success(request, "Tracking & ads settings saved.")
            return redirect("store:tracking", site_id=site.id)
    else:
        form = TrackingConfigForm(instance=instance)

    context = store_context(request, site, "tracking")
    context.update({"form": form})
    return render(request, "store/tracking.html", context)


# --------------------------------------------------------------------------
# Google Merchant Center (Merchant API v1; see commerce.google_merchant)
# --------------------------------------------------------------------------
_GM_ACCOUNTS_KEY = "gm_accounts:{}"


def _gm_config(site):
    from commerce.models import GoogleMerchantConfig

    cfg = GoogleMerchantConfig.objects.for_site(site).first()
    if cfg is None:
        cfg = GoogleMerchantConfig.objects.create(site=site)
    cfg.site = site
    return cfg


def _gm_status_payload(cfg) -> dict:
    progress = cfg.sync_progress or {}
    return {
        "success": True,
        "status": cfg.status,
        "sync_state": cfg.sync_state,
        "progress": {
            "total": int(progress.get("total") or 0),
            "done": int(progress.get("done") or 0),
            "failed": int(progress.get("failed") or 0),
            "invalid": int(progress.get("invalid") or 0),
        },
        "summary": cfg.status_summary or {},
        "last_synced": (
            timezone.localtime(cfg.last_synced_at).strftime("%d %b %Y, %I:%M %p")
            if cfg.last_synced_at else ""
        ),
        "last_error": cfg.last_error or "",
    }


def _gm_start_full_sync(cfg, *, force=False, purge=None) -> None:
    """Mark the store as syncing and queue the full sync after commit."""
    from django.db import transaction

    from commerce.tasks import google_merchant_full_sync

    type(cfg).all_objects.filter(pk=cfg.pk).update(
        sync_state=type(cfg).SyncState.RUNNING,
        sync_started_at=timezone.now(),
        sync_progress={"total": 0, "done": 0, "failed": 0, "invalid": 0},
    )
    site_id = str(cfg.site_id)
    transaction.on_commit(lambda: google_merchant_full_sync.delay(site_id, force, purge))


@login_required
def google_merchant(request, site_id):
    """The Google Merchant page: connect, choose an account, settings, sync
    status and per-product approval/issues."""
    from commerce import google_merchant as gm
    from commerce.models import GoogleMerchantConfig, GoogleMerchantProductStatus

    site = get_owned_site(request, site_id)
    cfg = _gm_config(site)

    if request.method == "POST":
        form = GoogleMerchantForm(request.POST, instance=cfg)
        if form.is_valid():
            before = (cfg.default_country, cfg.default_language)
            old_target = {
                "language": cfg.content_language,
                "feed_label": cfg.effective_feed_label,
                "data_source": cfg.data_source,
            }
            cfg = form.save(commit=False)
            cfg.site = site
            changed_target = before != (cfg.default_country, cfg.default_language)
            if changed_target:
                # A new country/language needs its own data source + offers.
                cfg.data_source = ""
                cfg.feed_label = ""
            cfg.save()
            activity.record(
                "store.merchant.updated", request=request, site=site,
                target=site, target_kind="Site", label="Google Merchant settings",
            )
            if changed_target and cfg.is_connected:
                # Take the listings down under the old target, then re-send
                # everything under the new one (new data source).
                _gm_start_full_sync(cfg, force=True, purge=old_target)
                messages.success(request, "Settings saved. Re-syncing your products for the new target.")
            else:
                messages.success(request, "Google Merchant settings saved.")
            return redirect("store:google_merchant", site_id=site.id)
        messages.error(request, "Couldn't save. Check the highlighted fields.")
    else:
        form = GoogleMerchantForm(instance=cfg)

    accounts = request.session.get(_GM_ACCOUNTS_KEY.format(site.pk)) or []
    if cfg.status == GoogleMerchantConfig.Status.PENDING and not accounts and cfg.refresh_token and gm.is_configured():
        try:
            accounts = gm.MerchantClient(cfg).list_accounts()
            request.session[_GM_ACCOUNTS_KEY.format(site.pk)] = accounts
        except gm.MerchantError as exc:
            cfg.last_error = exc.message

    statuses = []
    state_filter = (request.GET.get("state") or "").strip()
    page = None
    if cfg.is_connected:
        from django.core.paginator import Paginator

        qs = (
            GoogleMerchantProductStatus.objects.for_site(site)
            .exclude(state=GoogleMerchantProductStatus.State.REMOVED)
            .select_related("product")
            .order_by("-last_synced_at")
        )
        if state_filter in GoogleMerchantProductStatus.State.values:
            qs = qs.filter(state=state_filter)
        page = Paginator(qs, 25).get_page(request.GET.get("page") or 1)
        statuses = list(page.object_list)

    product_count = Product.objects.for_site(site).filter(is_deleted=False, is_active=True).count()
    context = store_context(request, site, "google_merchant")
    context.update(
        {
            "form": form,
            "config": cfg,
            "gm_configured": gm.is_configured(),
            "is_connected": cfg.is_connected,
            "is_pending": cfg.status == GoogleMerchantConfig.Status.PENDING and bool(cfg.refresh_token),
            # Accounts couldn't be listed because the platform's one-time Google
            # registration is pending, not because this owner has none.
            "gm_registration_pending": cfg.last_error == gm.REGISTRATION_PENDING_MESSAGE,
            "gm_can_register": cfg.last_error == gm.REGISTRATION_PENDING_MESSAGE and _gm_can_register_platform(request),
            "gm_platform_account_id": getattr(django_settings, "GOOGLE_MERCHANT_PLATFORM_ACCOUNT_ID", ""),
            "accounts": accounts,
            "product_count": product_count,
            "statuses": statuses,
            "status_page": page,
            "state_filter": state_filter,
            "state_choices": GoogleMerchantProductStatus.State.choices,
            "summary": cfg.status_summary or {},
            "redirect_uri": gm.redirect_uri(request) if request.user.is_staff or not gm.is_configured() else "",
            "mc_products_url": gm.merchant_center_url(cfg.merchant_id) if cfg.merchant_id else "",
            "mc_shipping_url": gm.merchant_center_url(cfg.merchant_id, "shipping") if cfg.merchant_id else "",
            "mc_business_url": gm.merchant_center_url(cfg.merchant_id, "business") if cfg.merchant_id else "",
            "status_json": _gm_status_payload(cfg),
            "has_site_verification": gm.SCOPE_SITE_VERIFICATION in (cfg.granted_scopes or "").split(),
        }
    )
    return render(request, "store/google_merchant.html", context)


@login_required
@require_POST
def google_merchant_connect(request, site_id):
    """Start the one-click Google sign-in (consent for Merchant Center + Site
    Verification, offline access)."""
    from commerce import google_merchant as gm

    site = get_owned_site(request, site_id)
    if not gm.is_configured():
        messages.error(request, "Google sign-in isn't set up on OneWooz yet. Please contact support.")
        return redirect("store:google_merchant", site_id=site.id)
    _gm_config(site)
    return redirect(gm.authorization_url(request, site))


@login_required
def google_merchant_callback(request):
    """Google's redirect back after consent. One fixed URL for every store (the
    store travels in the signed ``state``)."""
    from commerce import features
    from commerce import google_merchant as gm
    from commerce import staff as staff_mod
    from commerce.context import get_owned_site

    from django.http import Http404

    state = request.GET.get("state") or ""
    try:
        data = gm.read_state(request, state)
    except gm.MerchantError as exc:
        messages.error(request, exc.message)
        return redirect("ud:dashboard")

    try:
        site = get_owned_site(request, data["site"])
    except Http404:
        messages.error(request, "That store isn't available to your account.")
        return redirect("ud:dashboard")
    access = getattr(request, "store_access", None)
    if access is not None and not access.can("google_merchant", "edit"):
        messages.error(request, "You don't have permission to connect Google Merchant for this store.")
        return redirect("ud:dashboard")
    allowed = features.allowed_features_for_owner(getattr(site, "owner", None))
    if allowed is not None and "google_merchant" not in allowed:
        return redirect("store:google_merchant", site_id=site.id)

    back = redirect("store:google_merchant", site_id=site.id)
    if request.GET.get("error"):
        err = request.GET.get("error")
        messages.error(
            request,
            "Google sign-in was cancelled." if err == "access_denied"
            else f"Google sign-in failed ({err}). Please try again.",
        )
        return back
    code = request.GET.get("code") or ""
    if not code:
        messages.error(request, "Google didn't return a sign-in code. Please try again.")
        return back

    cfg = _gm_config(site)
    try:
        accounts = gm.complete_authorization(request, cfg, code)
    except gm.MerchantError as exc:
        messages.error(request, exc.message)
        return back
    request.session[_GM_ACCOUNTS_KEY.format(site.pk)] = accounts
    activity.record(
        "store.merchant.authorized", request=request, site=site,
        target=site, target_kind="Site", label=cfg.google_email or "Google",
    )

    usable = [a for a in accounts if not a.get("test")] or accounts
    if len(usable) == 1:
        try:
            gm.finish_connection(cfg, usable[0]["id"])
        except gm.MerchantError as exc:
            messages.error(request, exc.message)
            return back
        _gm_start_full_sync(cfg)
        activity.record(
            "store.merchant.connected", request=request, site=site,
            target=site, target_kind="Site", label=f"Merchant Center {cfg.merchant_id}",
        )
        messages.success(request, f"Connected to Merchant Center “{cfg.account_name or cfg.merchant_id}”. Syncing your products now.")
    elif accounts:
        messages.info(request, "Choose which Merchant Center account to connect.")
    elif not cfg.last_error:
        # (A listing error, e.g. the pending developer registration, is shown
        # on the page itself; repeating it as a banner just duplicates it.)
        messages.info(
            request,
            "Signed in with Google. We couldn't find a Merchant Center account for this Google "
            "account; create one, or enter your Merchant Center ID below.",
        )
    return back


def _gm_can_register_platform(request) -> bool:
    """Who may run OneWooz's one-time Google developer registration from the
    dashboard: OneWooz staff (directly, or while impersonating an owner), or
    anyone when the platform account is pinned in settings, since the call
    can then only ever target OneWooz's own Merchant Center account."""
    from django.conf import settings as dj_settings

    user = request.user
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True
    if getattr(request, "impersonation", None):
        return True
    return bool(getattr(dj_settings, "GOOGLE_MERCHANT_PLATFORM_ACCOUNT_ID", ""))


@login_required
@require_POST
def google_merchant_register_platform(request, site_id):
    """One-time platform setup, from the "Choose your account" card: register
    OneWooz's Google Cloud project with OneWooz's own Merchant Center account
    (``developerRegistration:registerGcp``), using the Google account just
    signed in here. Google requires that sign-in to be an ADMIN of that
    Merchant Center account, the account to be a production one with a
    verified website, and allows only one registered account per project."""
    import re

    from django.conf import settings as dj_settings

    from commerce import google_merchant as gm

    site = get_owned_site(request, site_id)
    back = redirect("store:google_merchant", site_id=site.id)
    if not _gm_can_register_platform(request):
        messages.error(request, "Only the OneWooz team can run the developer registration.")
        return back
    cfg = _gm_config(site)
    if not cfg.refresh_token:
        messages.error(request, "Sign in with Google first (an ADMIN of OneWooz's Merchant Center account).")
        return back

    pinned = re.sub(r"\D", "", str(getattr(dj_settings, "GOOGLE_MERCHANT_PLATFORM_ACCOUNT_ID", "") or ""))
    account_id = pinned or re.sub(r"\D", "", request.POST.get("platform_account_id") or "")
    if not account_id:
        messages.error(request, "Enter OneWooz's own Merchant Center ID.")
        return back
    email = (request.POST.get("developer_email") or "").strip() or cfg.google_email

    try:
        result = gm.register_platform_project(cfg, developer_email=email, account_id=account_id)
    except gm.MerchantError as exc:
        detail = f"{exc.reason} {exc.message}".upper()
        if "ALREADY" in detail:
            messages.success(
                request,
                "The Cloud project is already registered. Wait 5 minutes, then click “Connect with Google” again.",
            )
        else:
            hint = ""
            if exc.status == 403 or "PERMISSION" in detail:
                hint = f" Make sure {cfg.google_email or 'this Google account'} is an ADMIN of Merchant Center account {account_id}."
            elif "WEBSITE" in detail or "VERIF" in detail or "PRECONDITION" in detail:
                hint = " Verify your website in that Merchant Center account (Business info → Website) and try again."
            elif "TEST" in detail:
                hint = " Test accounts can't be registered; use a production Merchant Center account."
            messages.error(request, f"Registration failed: {exc.message} [{exc.reason}]{hint}")
        return back

    gcp = ", ".join(result.get("gcpIds") or []) or "OneWooz's Cloud project"
    activity.record(
        "store.merchant.platform_registered", request=request, site=site,
        target=site, target_kind="Site", label=f"registerGcp {account_id}",
    )
    messages.success(
        request,
        f"Registered {gcp} with Merchant Center account {account_id}. If {email} wasn't already a user "
        "there, accept Google's invitation email first. Then wait 5 minutes and click “Connect with Google” again.",
    )
    return back


@login_required
@require_POST
def google_merchant_select_account(request, site_id):
    """Pick (or type) the Merchant Center account to connect."""
    from commerce import google_merchant as gm

    site = get_owned_site(request, site_id)
    cfg = _gm_config(site)
    if not cfg.refresh_token:
        messages.error(request, "Sign in with Google first.")
        return redirect("store:google_merchant", site_id=site.id)
    account_id = (request.POST.get("account_id") or request.POST.get("manual_account_id") or "").strip()
    try:
        gm.finish_connection(cfg, account_id)
    except gm.MerchantError as exc:
        cfg.status = cfg.Status.PENDING
        cfg.last_error = exc.message
        # Keep the typed ID: the platform's own account needs it for the
        # one-time developer registration (admin) before lookups can succeed.
        cfg.save(update_fields=["status", "last_error", "merchant_id", "updated_at"])
        messages.error(request, exc.message)
        return redirect("store:google_merchant", site_id=site.id)
    request.session.pop(_GM_ACCOUNTS_KEY.format(site.pk), None)
    _gm_start_full_sync(cfg)
    activity.record(
        "store.merchant.connected", request=request, site=site,
        target=site, target_kind="Site", label=f"Merchant Center {cfg.merchant_id}",
    )
    messages.success(request, f"Connected to Merchant Center “{cfg.account_name or cfg.merchant_id}”. Syncing your products now.")
    return redirect("store:google_merchant", site_id=site.id)


@login_required
@require_POST
def google_merchant_sync(request, site_id):
    """Start a full catalogue sync in the background (progress via status)."""
    from core import ratelimit

    site = get_owned_site(request, site_id)
    cfg = _gm_config(site)
    if not cfg.is_connected:
        return JsonResponse({"success": False, "error": "Connect Google Merchant first."}, status=400)
    if cfg.sync_state == cfg.SyncState.RUNNING and cfg.sync_started_at and (
        timezone.now() - cfg.sync_started_at
    ).total_seconds() < 7200:
        return JsonResponse(_gm_status_payload(cfg))
    if not ratelimit.consume("merchant.sync", str(site.pk)).allowed:
        return JsonResponse(
            {"success": False, "error": "You've synced several times this hour. Please wait a little."},
            status=429,
        )
    force = str(request.POST.get("force") or "").lower() in ("1", "true", "yes")
    _gm_start_full_sync(cfg, force=force)
    activity.record(
        "store.merchant.synced", request=request, site=site,
        target=site, target_kind="Site", label="Google Merchant sync",
    )
    cfg.refresh_from_db()
    return JsonResponse(_gm_status_payload(cfg))


@login_required
def google_merchant_status(request, site_id):
    """Live sync progress + approval tally (polled by the page)."""
    site = get_owned_site(request, site_id)
    cfg = _gm_config(site)
    return JsonResponse(_gm_status_payload(cfg))


@login_required
@require_POST
def google_merchant_claim(request, site_id):
    """Retry verifying and claiming the store's website."""
    from commerce import google_merchant as gm

    site = get_owned_site(request, site_id)
    cfg = _gm_config(site)
    if not cfg.is_connected:
        return redirect("store:google_merchant", site_id=site.id)
    try:
        gm.MerchantClient(cfg).ensure_website(gm.store_public_base(site))
    except gm.MerchantError as exc:
        messages.error(request, exc.message)
        return redirect("store:google_merchant", site_id=site.id)
    cfg.refresh_from_db()
    if cfg.website_claimed:
        messages.success(request, "Your website is verified and claimed in Merchant Center.")
    else:
        messages.error(request, cfg.website_error or "Google couldn't verify the website yet.")
    return redirect("store:google_merchant", site_id=site.id)


@login_required
@require_POST
def google_merchant_refresh_statuses(request, site_id):
    """Read approval statuses back from Google now."""
    from commerce import google_merchant as gm

    site = get_owned_site(request, site_id)
    cfg = _gm_config(site)
    if not cfg.is_connected:
        return JsonResponse({"success": False, "error": "Connect Google Merchant first."}, status=400)
    try:
        gm.refresh_statuses(cfg)
    except gm.MerchantError as exc:
        return JsonResponse({"success": False, "error": exc.message}, status=400)
    cfg.refresh_from_db()
    return JsonResponse(_gm_status_payload(cfg))


@login_required
@require_POST
def google_merchant_auto_sync(request, site_id):
    site = get_owned_site(request, site_id)
    cfg = _gm_config(site)
    cfg.auto_sync = str(request.POST.get("enabled")).strip().lower() in ("1", "true", "yes", "on")
    cfg.save(update_fields=["auto_sync", "updated_at"])
    return JsonResponse({"success": True, "enabled": cfg.auto_sync})


@login_required
@require_POST
def google_merchant_disconnect(request, site_id):
    """Disconnect: optionally take every product down from Google first, then
    revoke OneWooz's access and forget the tokens."""
    from commerce import google_merchant as gm
    from commerce.models import GoogleMerchantProductStatus

    site = get_owned_site(request, site_id)
    cfg = _gm_config(site)
    removed = 0
    if request.POST.get("remove_products") and cfg.is_connected:
        try:
            client = gm.MerchantClient(cfg)
            client.ensure_data_source()
            for row in GoogleMerchantProductStatus.objects.for_site(site):
                for oid in row.offer_ids or []:
                    try:
                        client.delete_offer(oid)
                        removed += 1
                    except gm.MerchantError as exc:
                        if exc.fatal:
                            raise
        except gm.MerchantError as exc:
            messages.error(request, f"Couldn't remove products from Google: {exc.message}")
    gm.revoke(cfg.refresh_token)
    GoogleMerchantProductStatus.objects.for_site(site).delete()
    for field in (
        "merchant_id", "service_account_json", "google_email", "refresh_token", "access_token",
        "granted_scopes", "account_name", "data_source", "feed_label", "site_verification_token",
        "website_url", "website_error", "last_error",
    ):
        setattr(cfg, field, "")
    cfg.access_token_expires_at = None
    cfg.website_verified = cfg.website_claimed = False
    cfg.status = cfg.Status.DISCONNECTED
    cfg.sync_state = cfg.SyncState.IDLE
    cfg.sync_progress, cfg.status_summary = {}, {}
    cfg.save()
    request.session.pop(_GM_ACCOUNTS_KEY.format(site.pk), None)
    activity.record(
        "store.merchant.disconnected", request=request, site=site,
        target=site, target_kind="Site", label="Google Merchant",
    )
    messages.success(
        request,
        f"Google Merchant disconnected{f' and {removed} listings removed from Google' if removed else ''}.",
    )
    return redirect("store:google_merchant", site_id=site.id)


@login_required
def shipping(request, site_id):
    site = get_owned_site(request, site_id)
    provider = ShippingProvider.objects.for_site(site).first() or ShippingProvider(site=site)
    warehouses = Warehouse.objects.for_site(site)

    if request.method == "POST":
        intent = request.POST.get("intent")
        if intent == "provider":
            pform = ShippingProviderForm(request.POST, instance=provider, site=site)
            wform = WarehouseForm()
            if pform.is_valid():
                p = pform.save(commit=False)
                p.site = site
                # New Shiprocket credentials: drop the cached token, or the
                # next call (pickup locations, wallet, booking) would still
                # read the previously connected account.
                if {"api_email", "api_password", "base_url"} & set(pform.changed_data):
                    p.access_token = ""
                    p.access_token_expires_at = None
                # Preferred Shiprocket courier (chosen from the dropdown) lives in
                # the provider's settings JSON  no dedicated column, no migration.
                cid = (request.POST.get("shiprocket_courier_id") or "").strip()
                settings = p.settings or {}
                if cid and p.provider == ShippingProvider.Provider.SHIPROCKET:
                    settings["shiprocket_courier_id"] = cid
                    settings["shiprocket_courier_name"] = (
                        request.POST.get("shiprocket_courier_name") or ""
                    ).strip()
                else:
                    settings.pop("shiprocket_courier_id", None)
                    settings.pop("shiprocket_courier_name", None)
                p.settings = settings
                p.save()
                # Keep the Orders-page auto-ship switch in step with this checkbox
                # so both places agree on whether new orders auto-book.
                from commerce import shipping as shipping_svc

                shipping_svc.set_auto_ship(site, p.auto_ship)
                activity.record(
                    "store.shipping.updated", request=request, site=site,
                    target=site, target_kind="Site", label="Shipping provider",
                )
                messages.success(request, "Shipping provider saved.")
                return redirect("store:shipping", site_id=site.id)
        elif intent in ("webhook_rotate", "webhook_sync"):
            from commerce import courier_webhook

            if not provider.pk:
                messages.error(request, "Save your carrier first, then set up the tracking webhook.")
            elif intent == "webhook_rotate":
                courier_webhook.ensure_webhook_token(provider, rotate=True)
                activity.record(
                    "store.shipping.updated", request=request, site=site,
                    target=site, target_kind="Site", label="Tracking webhook token rotated",
                )
                messages.success(
                    request,
                    "New webhook token generated. Update the URL/token in your courier dashboard; the old one no longer works.",
                )
            else:
                from core import ratelimit

                if not ratelimit.consume("shipping.tracking_sync", str(site.pk)).allowed:
                    messages.error(request, "You've synced several times recently. Please wait a few minutes.")
                elif (provider.provider or "").lower() != "shiprocket" or not provider.enabled:
                    messages.error(request, "Live sync needs Shiprocket enabled as your carrier.")
                else:
                    result = courier_webhook.sync_open_shipments(site)
                    if not (result["synced"] or result["failed"]):
                        messages.info(request, "No shipments in transit to sync.")
                    else:
                        messages.success(
                            request,
                            f"Synced {result['synced']} shipment(s) from Shiprocket"
                            + (f", {result['changed']} status update(s)" if result["changed"] else ", no status changes")
                            + (f"; {result['failed']} couldn't be tracked yet." if result["failed"] else "."),
                        )
            return redirect(f"{reverse('store:shipping', args=[site.id])}#tracking-webhook")
        elif intent == "warehouse":
            wform = WarehouseForm(request.POST)
            pform = ShippingProviderForm(instance=provider, site=site)
            if wform.is_valid():
                w = wform.save(commit=False)
                w.site = site
                # The first warehouse a store adds is its default; only one
                # warehouse can be the default at a time.
                if not Warehouse.objects.for_site(site).exists():
                    w.is_default = True
                w.save()
                if w.is_default:
                    Warehouse.objects.for_site(site).exclude(pk=w.pk).update(is_default=False)
                activity.record(
                    "store.shipping.updated", request=request, site=site,
                    target=site, target_kind="Site", label=f"Warehouse · {w.name}",
                )
                messages.success(request, f"Warehouse “{w.name}” added.")
                return redirect("store:shipping", site_id=site.id)
        else:
            pform = ShippingProviderForm(instance=provider, site=site)
            wform = WarehouseForm()
    else:
        pform = ShippingProviderForm(instance=provider, site=site)
        wform = WarehouseForm()

    # When Shiprocket is the connected courier, offer a "preferred courier"
    # dropdown populated from the account's courier list (best-effort  a fetch
    # failure just leaves it on "Automatic").
    shiprocket_couriers: list = []
    shiprocket_courier_id = str((provider.settings or {}).get("shiprocket_courier_id") or "")
    # Shiprocket pickup-location dropdown: every pickup address saved in the
    # connected account, read live once the API email + password are saved.
    pickup_locations: list = []
    pickup_locations_error = ""
    has_sr_credentials = False
    if (provider.provider or "").strip().lower() == "shiprocket" and provider.pk:
        from commerce import shipping_shiprocket as sr

        has_sr_credentials = sr.has_credentials(provider)
        if has_sr_credentials:
            try:
                shiprocket_couriers = sr.list_couriers(provider)
            except Exception:
                shiprocket_couriers = []
            try:
                pickup_locations = sr.list_pickup_locations(provider)
            except sr.ShiprocketError as exc:
                pickup_locations_error = _shiprocket_reason(exc)
            except Exception:
                pickup_locations_error = "Couldn't reach Shiprocket to load your pickup locations."
    current_pickup = (provider.pickup_location or "").strip()
    pickup_known = any(loc["name"] == current_pickup for loc in pickup_locations)

    # Tracking webhook card: the public URL (+ token) to paste into Shiprocket.
    # Received events are processed in the background and not listed here.
    webhook = None
    if provider.pk:
        from commerce import courier_webhook

        webhook = courier_webhook.webhook_urls(request, site, provider)

    context = store_context(request, site, "shipping")
    context.update(
        {
            "webhook": webhook,
            "pform": pform,
            "wform": wform,
            "provider": provider,
            "warehouses": warehouses,
            "shiprocket_couriers": shiprocket_couriers,
            "shiprocket_courier_id": shiprocket_courier_id,
            "pickup_locations": pickup_locations,
            "pickup_locations_error": pickup_locations_error,
            "has_sr_credentials": has_sr_credentials,
            # The bound value, so a re-rendered invalid POST keeps the choice.
            "pickup_selected": (pform["pickup_location"].value() or current_pickup or "").strip(),
            "pickup_current_missing": bool(current_pickup and pickup_locations and not pickup_known),
            # The wallet box follows the carrier dropdown (shown for Shiprocket).
            "sr_wallet_hidden": (pform["provider"].value() or "") != ShippingProvider.Provider.SHIPROCKET,
        }
    )
    return render(request, "store/shipping.html", context)


def _shiprocket_reason(exc) -> str:
    """An owner-friendly reason from a Shiprocket client error."""
    text = str(exc or "")
    if text.startswith("authentication failed"):
        return (
            "Shiprocket didn't accept this API email/password. Check them "
            "(Shiprocket > Settings > API) and try again."
        )
    return text.replace("Shiprocket rejected the request: ", "") or "Shiprocket returned an error."


def _format_rupees(amount) -> str:
    sign = "-" if amount < 0 else ""
    return f"{sign}\u20b9{abs(amount):,.2f}"


@login_required
def shiprocket_wallet(request, site_id):
    """Live Shiprocket wallet balance for the balance box (Orders + Shipping).

    Cached for two minutes; ``?refresh=1`` (the box's Refresh button) reads it
    fresh, rate-limited per store. A fresh reading below Rs.100 alerts the
    owner once by email and bell (see :func:`commerce.shipping.shiprocket_wallet`).
    """
    from core import ratelimit

    from commerce import shipping as shipping_svc

    site = get_owned_site(request, site_id)
    refresh = request.GET.get("refresh") == "1"
    if refresh and not ratelimit.consume("shipping.wallet_refresh", str(site.pk)).allowed:
        refresh = False  # fall back to the cached reading rather than fail
    data = shipping_svc.shiprocket_wallet(site, refresh=refresh)
    shipping_url = reverse("store:shipping", args=[site.id])
    if not data["ok"]:
        return JsonResponse(
            {
                "success": False,
                "needs_setup": data.get("needs_setup", False),
                "error": data["error"],
                "shipping_url": shipping_url,
            }
        )
    return JsonResponse(
        {
            "success": True,
            "balance": str(data["balance"]),
            "balance_display": _format_rupees(data["balance"]),
            "low": data["low"],
            "threshold": data["threshold"],
            "threshold_display": _format_rupees(data["threshold"]),
            "checked_at": timezone.localtime(data["checked_at"]).strftime("%d %b, %I:%M %p"),
            "shipping_url": shipping_url,
        }
    )


@login_required
@require_POST
def shiprocket_pickup_locations(request, site_id):
    """Pickup locations of a Shiprocket account, for the Shipping page's
    "Load pickup locations" button.

    Uses the API email/password typed into the form when they differ from the
    saved ones (nothing is stored), else the saved credentials.
    """
    from core import ratelimit

    from commerce import shipping_shiprocket as sr

    site = get_owned_site(request, site_id)
    if not ratelimit.consume("shipping.pickup_lookup", str(site.pk)).allowed:
        return JsonResponse(
            {"success": False, "error": "Too many lookups in a short time. Please wait a few minutes."},
            status=429,
        )
    provider = (
        ShippingProvider.objects.for_site(site)
        .filter(provider=ShippingProvider.Provider.SHIPROCKET)
        .first()
    )
    email = (request.POST.get("api_email") or "").strip()
    password = request.POST.get("api_password") or ""
    saved_email = (provider.api_email or "").strip() if provider else ""
    saved_password = (provider.api_password or "") if provider else ""
    try:
        if email and password and (email != saved_email or password != saved_password):
            locations = sr.pickup_locations_for_credentials(
                email, password, base_url=(provider.base_url if provider else "") or ""
            )
        elif provider is not None and sr.has_credentials(provider):
            locations = sr.list_pickup_locations(provider)
        else:
            return JsonResponse(
                {
                    "success": False,
                    "error": "Enter your Shiprocket API email and password first, then load your pickup locations.",
                },
                status=400,
            )
    except sr.ShiprocketError as exc:
        return JsonResponse({"success": False, "error": _shiprocket_reason(exc)}, status=400)
    except Exception:
        return JsonResponse(
            {"success": False, "error": "Couldn't reach Shiprocket. Try again in a minute."}, status=502
        )
    if not locations:
        return JsonResponse(
            {
                "success": False,
                "error": (
                    "This Shiprocket account has no pickup addresses yet. Add one in Shiprocket "
                    "(Settings > Pickup Addresses), then load again."
                ),
            },
            status=404,
        )
    return JsonResponse(
        {
            "success": True,
            "locations": locations,
            "selected": ((provider.pickup_location if provider else "") or "").strip(),
        }
    )


@login_required
@require_POST
def warehouse_set_default(request, site_id, warehouse_id):
    site = get_owned_site(request, site_id)
    warehouse = get_object_or_404(Warehouse.objects.for_site(site), pk=warehouse_id)
    Warehouse.objects.for_site(site).exclude(pk=warehouse.pk).update(is_default=False)
    if not warehouse.is_default:
        warehouse.is_default = True
        warehouse.save(update_fields=["is_default"])
    activity.record(
        "store.shipping.updated", request=request, site=site,
        target=site, target_kind="Site", label=f"Default warehouse · {warehouse.name}",
    )
    messages.success(request, f"“{warehouse.name}” is now your default pickup address.")
    return redirect("store:shipping", site_id=site.id)


@login_required
@require_POST
def warehouse_delete(request, site_id, warehouse_id):
    site = get_owned_site(request, site_id)
    warehouse = get_object_or_404(Warehouse.objects.for_site(site), pk=warehouse_id)
    name, was_default = warehouse.name, warehouse.is_default
    warehouse.delete()
    # If we removed the default, promote the next warehouse so the store always
    # has one pickup address marked default when any remain.
    if was_default:
        nxt = Warehouse.objects.for_site(site).first()
        if nxt is not None:
            nxt.is_default = True
            nxt.save(update_fields=["is_default"])
    activity.record(
        "store.shipping.updated", request=request, site=site,
        target=site, target_kind="Site", label=f"Removed warehouse · {name}",
    )
    messages.success(request, f"Warehouse “{name}” removed.")
    return redirect("store:shipping", site_id=site.id)
