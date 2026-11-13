"""Pay a UGC-store order on the owner's own store domain.

Payment gateways only accept payments from websites the merchant registered
with them: Cashfree checks a domain whitelist, and Razorpay blocks payments
from a website that doesn't match the account's registered website(s). A store
owner registers their own storefront domain, never every collaborator's
``<sub>.myugcstore.com``, so the in-page checkout fails on a UGC store.

Instead, a UGC checkout that pays online (Cashfree or Razorpay):

1. creates the order exactly as before (hidden, awaiting payment, attributed
   to the collaborator); for Cashfree its ``return_url`` / ``notify_url`` are
   on the **owner's** domain;
2. sends the shopper to ``<owner store>/pay/<token>/``, a minimal page there
   that opens the gateway's checkout (the owner's own gateway and keys, on a
   domain the gateway already trusts);
3. the shopper ends at ``<owner store>/pay/<token>/done/``, which confirms the
   payment authoritatively (Razorpay: its signature; otherwise a fetch from
   the gateway, never the query string's word) and sends the shopper back to
   the UGC store's order confirmation, where the cart is cleared.

The token is purpose-signed and short-lived and carries the order, the store
and the UGC return URLs, so the pay page can only open that one order on that
one store and can only send the shopper back where checkout started.
"""

from __future__ import annotations

import logging

from django.core.cache import cache
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import render
from django.urls import get_script_prefix, reverse, set_script_prefix

from core import tokens

logger = logging.getLogger("onewooz.audit")

PURPOSE = "storefront.hosted_pay"
# Session key on the UGC store: the order paid elsewhere, so its confirmation
# page (back on the UGC domain) clears the cart there.
SESSION_KEY = "ow_hosted_pay_order"
_SESSION_CACHE = "hosted_pay:session:{}"


# Gateways whose checkout runs on the owner's domain for a UGC store.
GATEWAYS = ("cashfree", "razorpay")


def applies(request, cfg) -> bool:
    """Whether this checkout must pay on the owner's domain: a UGC store
    paying online through a gateway that only trusts registered websites."""
    on_ugc = bool(getattr(request, "is_referral", False) or getattr(request, "collaboration", None))
    return bool(
        on_ugc
        and cfg is not None
        and (cfg.gateway or "").lower() in GATEWAYS
    )


def owner_origin(site, request=None) -> str:
    """The owner's own store origin: the verified custom domain when there is
    one (the domain shoppers, and so the owner's Cashfree whitelist, use),
    else the platform storefront subdomain."""
    from core import links

    domain = (getattr(site, "custom_domain", "") or "").strip().lower().rstrip("/")
    if domain and getattr(site, "custom_domain_verified", False) and not links._in_path_mode(request):
        return f"https://{domain}"
    return links.storefront_origin(site.slug, request)


def _owner_path(name: str, **kwargs) -> str:
    """A path on the owner storefront's urlconf, whatever host is serving now."""
    previous = get_script_prefix()
    try:
        set_script_prefix("/")
        return reverse(name, urlconf="onewooz.urls.storefront", kwargs=kwargs or None)
    finally:
        set_script_prefix(previous)


def make_token(site, order, *, return_url: str, back_url: str) -> str:
    return tokens.sign(
        PURPOSE,
        {"o": str(order.pk), "s": str(site.pk), "ret": return_url, "back": back_url},
    )


def urls(site, request, token: str) -> dict:
    """Absolute owner-domain URLs for one hosted payment."""
    origin = owner_origin(site, request)
    return {
        "pay": origin + _owner_path("public_hosted_pay", token=token),
        "done": origin + _owner_path("public_hosted_pay_done", token=token),
        "notify": origin + _owner_path("public_webhook_cashfree"),
    }


def remember_session(order, payment_session_id: str, mode: str = "") -> None:
    """Keep the Cashfree payment session (and the environment it was created
    in) for the pay page; it is fetched back from Cashfree if this cache entry
    is gone."""
    if payment_session_id:
        cache.set(
            _SESSION_CACHE.format(order.pk),
            {"sid": payment_session_id, "mode": mode or ""},
            tokens.ttl_for(PURPOSE),
        )


# ── Views on the owner's store domain ────────────────────────────────────────

def _resolve(request, token):
    """``(site, order, payload)`` for a valid token on this store, else 404."""
    from commerce.models import Order

    from .views import _site_or_404

    site = _site_or_404(request)
    try:
        payload = tokens.unsign(PURPOSE, token)
    except tokens.TokenError:
        raise Http404("This payment link has expired.")
    if str(payload.get("s")) != str(site.pk):
        raise Http404("No such payment.")
    order = Order.all_objects.filter(site=site, pk=payload.get("o")).first()
    if order is None:
        raise Http404("No such payment.")
    return site, order, payload


def _pending_payment(site, order):
    """The order's latest gateway payment attempt (Cashfree or Razorpay)."""
    from commerce.models import Payment

    return (
        Payment.objects.for_site(site)
        .filter(order=order)
        .exclude(provider_order_id="")
        .order_by("-created_at")
        .first()
    )


def _session(order, payment, cfg) -> tuple[str, str]:
    """``(payment_session_id, environment)`` for the order's Cashfree payment."""
    cached = cache.get(_SESSION_CACHE.format(order.pk)) or {}
    if isinstance(cached, dict) and cached.get("sid"):
        return cached["sid"], cached.get("mode") or ""
    # Not cached (expired / evicted): ask Cashfree for the order's session.
    from commerce.payments import _cashfree_call

    try:
        env, status, data = _cashfree_call(cfg, "GET", f"/orders/{payment.provider_order_id}")
    except Exception:
        logger.warning("hosted pay: session fetch failed order=%s", order.pk, exc_info=True)
        return "", ""
    if status >= 300:
        return "", ""
    return str((data or {}).get("payment_session_id") or ""), env


def _razorpay_params(site, order, payment, cfg) -> dict:
    """What Razorpay's checkout.js needs to open this payment's order (the
    same values the in-page checkout would have received)."""
    from commerce.payments import _to_paise

    return {
        "key": cfg.key_id,
        "order_id": payment.provider_order_id,
        "amount": _to_paise(payment.amount),
        "currency": payment.currency or "INR",
        "name": site.name,
        "prefill": {
            "name": order.customer_name or "",
            "email": order.customer_email or "",
            "contact": order.customer_phone or "",
        },
    }


def pay(request, token):
    """``/pay/<token>/``: open Cashfree for a UGC-store order on this (the
    owner's, Cashfree-whitelisted) domain."""
    from commerce.models import Order, Payment
    from commerce.payments import _cashfree_env_guess, gateway_config

    site, order, payload = _resolve(request, token)
    back = payload.get("back") or ""
    ret = payload.get("ret") or ""

    # Already settled (paid in another tab, or the webhook got there first).
    if order.status != Order.Status.AWAITING_PAYMENT:
        return HttpResponseRedirect(ret or back)

    cfg = gateway_config(site)
    payment = _pending_payment(site, order)
    error = ""
    gateway = ""
    session_id = mode = ""
    razorpay = None
    if cfg is None or payment is None or payment.status != Payment.Status.PROCESSING:
        error = "This payment can no longer be completed. Please go back and place the order again."
    elif payment.gateway == Payment.Gateway.RAZORPAY:
        gateway = "razorpay"
        razorpay = _razorpay_params(site, order, payment, cfg)
    else:
        gateway = "cashfree"
        session_id, mode = _session(order, payment, cfg)
        mode = mode or _cashfree_env_guess(cfg)
        if not session_id:
            error = "We couldn't open the payment window. Please go back and try again."

    # Admit Cashfree's SDK / frames on this one response (see core.middleware).
    request.csp_payment = True
    from decimal import Decimal

    amount = payment.amount if payment is not None else order.total
    return render(
        request,
        "storefront/hosted_pay.html",
        {
            "site": site,
            "order": order,
            "amount": Decimal(amount or 0).quantize(Decimal("0.01")),
            "gateway": "" if error else gateway,
            "session_id": session_id,
            "mode": "production" if mode == "production" else "sandbox",
            "razorpay": razorpay,
            "error": error,
            "back_url": back,
            "done_url": _owner_path("public_hosted_pay_done", token=token),
        },
    )


def done(request, token):
    """``/pay/<token>/done/``: Cashfree's return. Confirm by fetching the
    order from Cashfree, then send the shopper back to the UGC store."""
    from commerce.models import Order
    from commerce.payments import confirm_by_fetch, gateway_config

    from commerce.models import Payment
    from commerce.payments import verify_razorpay_return

    site, order, payload = _resolve(request, token)
    if order.status == Order.Status.AWAITING_PAYMENT:
        payment = _pending_payment(site, order)
        cfg = gateway_config(site)
        if payment is not None and cfg is not None:
            try:
                confirmed = False
                # Razorpay's checkout hands back an HMAC signature: verified
                # with the store's own key secret it is authoritative, and it
                # works before the payment is captured (a fetch right after
                # paying can still see it as only "authorized").
                rp_id = (request.GET.get("razorpay_payment_id") or "").strip()
                rp_sig = (request.GET.get("razorpay_signature") or "").strip()
                if payment.gateway == Payment.Gateway.RAZORPAY and rp_id and rp_sig:
                    confirmed = verify_razorpay_return(
                        payment, cfg, razorpay_payment_id=rp_id, razorpay_signature=rp_sig
                    )
                if not confirmed:
                    confirm_by_fetch(payment, cfg)
            except Exception:
                logger.warning("hosted pay: confirm failed order=%s", order.pk, exc_info=True)
    # The UGC confirmation page re-checks too: paid shows the order; still
    # unpaid returns the shopper to the UGC checkout with the cart intact.
    return HttpResponseRedirect(payload.get("ret") or payload.get("back") or "/")
