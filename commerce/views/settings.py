"""Store settings  identity, currency, contact and store-wide toggles."""

from __future__ import annotations

import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from commerce.context import get_owned_site, store_context
from commerce.forms import SiteForm

# Formats the invoice PDF can always draw (commerce.label_pdf reads the
# signature with Pillow); the library also holds AVIF/HEIC, which may not.
_SIGNATURE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_SIGNATURE_MIMES = {"image/png", "image/jpeg", "image/webp"}


def _picked_signature(site, asset_id):
    """``(asset, error)`` for the media-library image picked as the signature."""
    from commerce.models import MediaAsset

    try:
        asset = (
            MediaAsset.objects.for_site(site)
            .filter(pk=asset_id, kind=MediaAsset.Kind.IMAGE)
            .first()
        )
    except Exception:  # malformed id
        asset = None
    if asset is None or not asset.file:
        return None, "That image is no longer in your media library. Please choose it again."
    ext = os.path.splitext(asset.file.name or "")[1].lower()
    mime = (getattr(asset, "mime_type", "") or "").lower()
    if ext not in _SIGNATURE_EXTS and mime not in _SIGNATURE_MIMES:
        return None, "Choose a PNG, JPG or WEBP image for the signature."
    return asset, ""


def _design_options(site, request=None) -> list:
    """The Storefront design picker's cards: the owner's design library from the
    OneWooz Templates marketplace app (Template 1, designs they added or bought,
    and the store's current design), with thumbnail and licence state. Falls
    back to the legacy catalogue if the marketplace is unavailable."""
    from billing import website_templates

    options = website_templates.options_for_site(site)
    for o in options:
        if o.marketplace_url:
            try:
                from marketplace import links as mkt_links

                o.marketplace_url = mkt_links.marketplace_url(
                    request, "marketplace:item", slug="templates", key=o.key
                ) if request is not None else ""
            except Exception:
                o.marketplace_url = ""
    # Template 1 is a different design per business model; describe the one
    # this store would actually get (unless staff rewrote the description).
    for o in options:
        if o.key == "1" and o.description.startswith("The original design for your business"):
            o.description = (
                "The original catalogue design, built for trade buyers: product range, enquiries and quotes."
                if site.business_model == site.BusinessModel.B2B
                else "The original modern storefront: bold banners, shoppable reels and a quick cart drawer."
            )
    return options


def _design_url(site) -> str:
    return reverse("store:settings", kwargs={"site_id": site.id}) + "#design"


def _design_back(request, site) -> str:
    """Back to the page a design was switched from: OneWooz Templates or Settings."""
    if request.POST.get("from") == "templates":
        return reverse("store_apps:open", kwargs={"site_id": site.id, "slug": "templates"})
    return _design_url(site)


def design_context(request, site) -> dict:
    """Everything store/_design_cards.html needs, for Settings and for the
    OneWooz Templates page (marketplace.modules.storefront_designs).
    """
    from billing import cashfree
    from billing import website_templates

    if request.method == "GET":
        # Settle a template payment whose return was missed, and move the store
        # off a design whose yearly licence has lapsed.
        try:
            website_templates.reconcile_pending(site)
            lapsed = website_templates.enforce_expiry(site)
            if lapsed:
                messages.error(
                    request,
                    f"Your yearly licence for {lapsed} has expired, so your store is back on a free design. "
                    f"Buy {lapsed} again to switch back.",
                )
            site.refresh_from_db(fields=["theme"])
        except Exception:
            pass

    design_options = _design_options(site, request)
    cashfree_ready = cashfree.is_configured()
    # Library mode (marketplace): buying and renewing happen on OneWooz Apps.
    from_marketplace = any(o.marketplace_url for o in design_options)
    design_checkout = cashfree_ready and not from_marketplace and any(o.can_buy for o in design_options)
    if design_checkout:
        # The Buy button hands off to Cashfree's hosted checkout via their JS SDK.
        request.csp_payment = True
    return {
        "design_options": design_options,
        "design_from_marketplace": from_marketplace,
        "cashfree_ready": cashfree_ready,
        "design_checkout": design_checkout,
        "is_store_owner": _is_owner(request, site),
    }


def _is_owner(request, site) -> bool:
    return getattr(site, "owner_id", None) == getattr(request.user, "pk", None)


@login_required
def settings(request, site_id):
    site = get_owned_site(request, site_id)
    # Read before binding: an invalid POST mutates the instance, and the page
    # must keep previewing the *saved* signature.
    signature_url = ""
    if site.authorized_signature:
        try:
            signature_url = site.authorized_signature.url
        except Exception:
            signature_url = ""
    signature_error = ""

    if request.method == "POST":
        old_signature = site.authorized_signature.name if site.authorized_signature else ""
        form = SiteForm(request.POST, instance=site)

        # The signature comes from the media library modal: the picked asset's
        # id (copied into the store's own signatures/ folder, so deleting it
        # from the library later never blanks the invoice), or a removal.
        asset_id = (request.POST.get("media_signature") or "").strip()
        remove_signature = request.POST.get("remove_signature") == "1"
        asset = None
        if asset_id:
            asset, signature_error = _picked_signature(site, asset_id)

        if form.is_valid() and not signature_error:
            new_signature = old_signature
            if asset is not None:
                from commerce.media_processing import clone_asset_file

                try:
                    new_signature = clone_asset_file(asset, f"signatures/{site.id}")
                except Exception:
                    signature_error = "Couldn’t use that image. Please try again or upload it again."
            elif remove_signature:
                new_signature = ""

            if not signature_error:
                site = form.save(commit=False)
                site.authorized_signature = new_signature
                # Keep the Products page copy of the abandoned cart switch in step.
                content = site.content if isinstance(site.content, dict) else {}
                content["abandoned_cart_emails_enabled"] = bool(site.abandoned_cart_emails_enabled)
                site.content = content
                site.save()
                # A replaced or removed signature leaves no orphan file behind.
                # (Only the store's own copy; the library asset is untouched.)
                if old_signature and old_signature != new_signature:
                    try:
                        site.authorized_signature.storage.delete(old_signature)
                    except Exception:
                        pass
                messages.success(request, "Store settings saved.")
                return redirect("store:settings", site_id=site.id)
    else:
        form = SiteForm(instance=site)

    from billing import gst

    designs = design_context(request, site)
    context = store_context(request, site, "settings")
    context.update(designs)
    context.update({
        "form": form,
        "gstin_state": gst.state_from_gstin(site.gstin),
        "signature_url": signature_url,
        "signature_error": signature_error,
        "deletion_days": _deletion_days(),
        "support_url": reverse("ud:business_hub"),
    })
    return render(request, "store/settings.html", context)


def _deletion_days() -> int:
    from accounts import deletion

    return deletion.retention_days()


@login_required
@require_POST
def theme(request, site_id):
    """Switch the storefront design (Settings → Storefront design).

    Takes effect on the next storefront request: every theme renders from the
    same store content and catalogue, so nothing is copied or lost; switching
    back restores the previous look exactly. "Template 1" is the original
    design for the store's business model, "Template 2" the Pebble storefront.
    """
    from billing import website_templates
    from tenancy.models import Site

    site = get_owned_site(request, site_id)
    choice = (request.POST.get("template_choice") or "").strip()
    if choice not in website_templates.registered_keys():
        messages.error(request, "Choose a design first.")
        return redirect(_design_back(request, site))

    option = next((o for o in website_templates.options_for_site(site) if o.key == choice), None)
    label = option.name if option else f"Template {choice}"
    new_theme = Site.theme_for_choice(choice, site.business_model)
    if new_theme == site.template_dir:
        messages.info(request, f"Your store already uses {label}.")
        return redirect(_design_back(request, site))

    if option is None or not website_templates.can_use(site, choice):
        if option is not None and option.is_priced:
            messages.error(
                request,
                f"{label} is a paid design. Buy it first, then it switches on automatically.",
            )
        else:
            messages.error(request, "That design isn't available for this store.")
        return redirect(_design_back(request, site))

    old_theme = site.template_dir
    site.theme = new_theme
    site.save(update_fields=["theme", "updated_at"])

    from commerce import activity

    activity.record(
        "store.design.changed", request=request, site=site,
        target=site, target_kind="Site", label=label,
        changes={"theme": [old_theme, new_theme]},
    )
    messages.success(
        request,
        f"Your storefront now uses {label}. Your content, products and settings carry over as they are.",
    )
    return redirect(_design_back(request, site))


# --------------------------------------------------------------------------
# Priced website templates: bought through OneWooz's own Cashfree account
# --------------------------------------------------------------------------
_TEMPLATE_RETURN_PURPOSE = "billing.template.return"


@login_required
@require_POST
def template_buy(request, site_id):
    """Start paying for a priced design; JSON launch params for the Cashfree SDK."""
    from django.conf import settings as dj_settings

    from billing import website_templates
    from core import audit, tokens
    from core.links import surface_url
    from core.ratelimit import RateLimitExceeded, check, client_identity

    site = get_owned_site(request, site_id)
    if not _is_owner(request, site):
        return JsonResponse({"ok": False, "error": "Only the store owner can buy a design."}, status=403)
    try:
        check("subscription.renew", client_identity(request))
    except RateLimitExceeded as exc:
        return JsonResponse(
            {"ok": False, "error": "Too many attempts. Please wait a minute and try again."},
            status=429, headers={"Retry-After": str(exc.retry_after)},
        )

    key = (request.POST.get("template") or "").strip()

    def return_url_for(purchase):
        token = tokens.sign(_TEMPLATE_RETURN_PURPOSE, {"p": str(purchase.id), "s": str(site.id)})
        return request.build_absolute_uri(
            reverse("store:settings_template_return", kwargs={"site_id": site.id}) + f"?t={token}"
        )

    # The Cashfree webhook lives on the marketing surface; it only ever nudges a
    # confirm-by-fetch.
    notify_url = surface_url(
        "marketing", reverse("billing:webhook", urlconf=dj_settings.ROOT_URLCONF), request
    )
    try:
        params = website_templates.start_purchase(
            request, site, key, return_url_for=return_url_for, notify_url=notify_url
        )
    except website_templates.PurchaseError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    purchase = params["purchase"]
    audit.record(
        "billing.template.purchase_started",
        request=request,
        target=purchase,
        target_kind="TemplatePurchase",
        target_label=purchase.number,
        actor_kind="owner",
        context={"template": purchase.template_key, "total": f"{purchase.total_amount:.2f}"},
    )
    return JsonResponse(
        {"ok": True, "payment_session_id": params["payment_session_id"], "mode": params["mode"]}
    )


@login_required
@require_GET
def template_return(request, site_id):
    """Back from Cashfree: confirm by fetch, switch the store, report in green/red."""
    from billing import website_templates
    from billing.models import TemplatePurchase
    from core import tokens

    site = get_owned_site(request, site_id)
    purchase = None
    try:
        payload = tokens.unsign(_TEMPLATE_RETURN_PURPOSE, request.GET.get("t") or "")
        if str(payload.get("s")) == str(site.id):
            purchase = TemplatePurchase.objects.filter(pk=payload.get("p")).first()
    except tokens.TokenError:
        purchase = None

    if purchase is None:
        messages.error(
            request,
            "We couldn\u2019t find that payment. If you were charged, contact us and we will sort it out.",
        )
        return redirect(_design_url(site))

    if website_templates.confirm_purchase(purchase):
        purchase.refresh_from_db()
        until = (
            f" Your licence runs until {purchase.expires_at:%d %b %Y}."
            if purchase.expires_at else " It is yours for good."
        )
        messages.success(
            request,
            f"Payment received. {purchase.template_name} is now live on your store.{until}",
        )
    else:
        messages.error(
            request,
            f"The payment for {purchase.template_name} was not completed, so nothing was charged "
            "and your design is unchanged.",
        )
    return redirect(_design_url(site))
