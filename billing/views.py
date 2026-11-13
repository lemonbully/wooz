"""Subscription purchase flow  modal-driven, on the marketing pricing page.

Replicates the 1matrix ``onematrix`` plans-and-pricing flow: there is **no
separate checkout page**. A plan's button on the pricing page opens a details
modal (email + phone + optional discount), then a breakdown modal showing the
18% GST split, then ``create_session`` mints a Cashfree order and the page hands
off to Cashfree hosted checkout via their JS SDK. Cashfree returns to
``payment_return``, which confirms the payment authoritatively.

    quote (POST)          -> JSON breakdown for the discount + breakdown modals
    create_session (POST) -> create the order + Cashfree order, return the
                             payment_session_id for the SDK
    return (GET)          -> Cashfree redirect back; confirm by fetch, show outcome
    webhook (POST)        -> Cashfree server nudge; also only triggers a fetch

Amounts are always recomputed server-side from the plan, cadence and code  the
browser's numbers are display only and are never trusted.
"""

from __future__ import annotations

import json
import re

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from accounts import otp
from core import audit, tokens
from core.links import surface_url
from core.ratelimit import RateLimitExceeded, check, client_identity

from . import cashfree, cycles, emails
from .onboarding import onboarding_url
from .models import (
    AgreementAcceptance,
    SubscriptionInvite,
    SubscriptionOrder,
    SubscriptionPlan,
    UserAgreement,
)
from .pricing import quote

_TOKEN_PURPOSE = "billing.checkout"
# Ties an admin-issued custom-priced invite to its temporary checkout link.
_INVITE_PURPOSE = "billing.invite"
# Proof that the email on an order passed the OTP step. Minted by ``verify_otp``
# and required by ``create_session`` so a payment can never be started for an
# unverified address.
_VERIFIED_PURPOSE = "billing.otp_verified"
# Namespaces the code in the shared OTP store (see :mod:`accounts.otp`).
_OTP_PURPOSE = "billing.agreement"
_OTP_MINUTES = 10
_PHONE_RE = re.compile(r"\D+")

# Shown in the acceptance modal when an operator has not authored an agreement
# in the admin yet  so the flow is never dead on a fresh install. Editing a
# UserAgreement in the admin supersedes this immediately.
_DEFAULT_AGREEMENT = {
    "id": "default",
    "title": "OneWooz Subscription Agreement",
    "content": (
        "<p>By subscribing you agree to OneWooz's Terms of Service and Privacy "
        "Policy. Your subscription gives you access to the OneWooz platform to "
        "build and run your online store for the billing period you select.</p>"
        "<p>Prices are shown exclusive of GST, which is added at checkout. "
        "Payments are processed securely by Cashfree; OneWooz never sees or "
        "stores your card details. Your store's own sales settle to your own "
        "payment account  OneWooz does not take a cut of your sales.</p>"
        "<p>You can stop your subscription at any time; access continues until "
        "the end of the paid period. This confirmation step verifies that you "
        "control the email address on this order.</p>"
    ),
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _paid_plan(slug: str) -> SubscriptionPlan | None:
    """An active plan that goes through checkout  recurring paid or one-time."""
    plan = SubscriptionPlan.objects.filter(slug=slug, is_active=True).first()
    return plan if (plan and plan.is_purchasable) else None


def _free_plan(slug: str) -> SubscriptionPlan | None:
    """An active free plan  bought through the same OTP flow, no gateway."""
    plan = SubscriptionPlan.objects.filter(slug=slug, is_active=True).first()
    return plan if (plan and plan.plan_type == SubscriptionPlan.Kind.FREE) else None


def _checkout_plan(slug: str) -> SubscriptionPlan | None:
    """Any active plan that runs the checkout/OTP flow  paid, one-time or free.

    Free plans reach the same email-OTP verification before onboarding; only the
    settle step differs (a free grant instead of a gateway payment).
    """
    return _paid_plan(slug) or _free_plan(slug)


def _clean_phone(raw: str) -> str:
    return _PHONE_RE.sub("", raw or "")


def _valid_email(raw: str) -> bool:
    try:
        validate_email((raw or "").strip())
        return True
    except ValidationError:
        return False


# --------------------------------------------------------------------------
# Live breakdown (discount + GST) for the modals
# --------------------------------------------------------------------------
@require_POST
def quote_view(request):
    """JSON breakdown for the details/breakdown modals. Validates the discount
    code and returns the full 18% GST split. Email/phone not required here."""
    try:
        check("subscription.quote", client_identity(request))
    except RateLimitExceeded as exc:
        return JsonResponse(
            {"ok": False, "error": "Too many attempts. Please wait a moment."},
            status=429, headers={"Retry-After": str(exc.retry_after)},
        )

    slug = (request.POST.get("slug") or request.POST.get("plan") or "").strip()
    plan = _paid_plan(slug)
    if plan is None:
        return JsonResponse({"ok": False, "error": "Unknown plan."}, status=404)

    cycle = cycles.normalise_cycle(request.POST.get("cycle"))
    code = (request.POST.get("code") or request.POST.get("discount_code") or "").strip()
    q = quote(plan, cycle, discount_code=code)
    payload = q.as_json()
    payload["valid"] = bool(q.discount)  # a code was supplied and accepted
    payload["plan_name"] = plan.name
    return JsonResponse({"ok": True, "quote": payload})


# --------------------------------------------------------------------------
# Agreement acceptance + email OTP (between the details and payment steps)
# --------------------------------------------------------------------------
def _agreement_payload(agreement: UserAgreement | None) -> dict:
    if agreement is None:
        return dict(_DEFAULT_AGREEMENT)
    return {"id": str(agreement.id), "title": agreement.title, "content": agreement.content}


@require_GET
def active_agreement(request):
    """The agreement the buyer must accept, as JSON for the acceptance modal.

    Falls back to a built-in default when none is authored, so the checkout is
    never blocked on manual admin setup."""
    return JsonResponse({"ok": True, "agreement": _agreement_payload(UserAgreement.active())})


@require_POST
def send_otp(request):
    """Email a one-time code to confirm the buyer controls the order's address.

    Called when the buyer accepts the agreement and moves to the payment
    summary (or, for a free plan, straight to verification). Requires an active
    plan and a valid email/phone; the code itself is stored server-side by
    :mod:`accounts.otp`."""
    slug = (request.POST.get("slug") or request.POST.get("plan") or "").strip()
    if _checkout_plan(slug) is None:
        return JsonResponse({"ok": False, "error": "Unknown plan."}, status=404)

    email = (request.POST.get("email") or "").strip()[:254]
    phone = _clean_phone(request.POST.get("phone"))
    if not _valid_email(email):
        return JsonResponse({"ok": False, "errors": {"email": "Enter a valid email address."}}, status=400)
    if not (10 <= len(phone) <= 15):
        return JsonResponse({"ok": False, "errors": {"phone": "Enter a valid phone number."}}, status=400)

    identity = client_identity(request)
    try:
        check("subscription.otp.send", email.lower())
        check("subscription.otp.send.ip", identity)
    except RateLimitExceeded as exc:
        return JsonResponse(
            {"ok": False, "error": "Too many code requests. Please wait a moment and try again."},
            status=429, headers={"Retry-After": str(exc.retry_after)},
        )

    code = otp.issue(_OTP_PURPOSE, email, ttl=_OTP_MINUTES * 60)
    if not emails.send_agreement_otp(email, code, minutes=_OTP_MINUTES):
        otp.invalidate(_OTP_PURPOSE, email)
        return JsonResponse(
            {"ok": False, "error": "We couldn’t send the code. Check the address and try again."},
            status=502,
        )
    # Identical response whether or not the address already has an order  the
    # send endpoint never discloses account or purchase history.
    return JsonResponse({"ok": True})


@require_POST
def verify_otp(request):
    """Check the emailed code; on success record acceptance and mint the token
    that ``create_session`` requires to start a payment."""
    email = (request.POST.get("email") or "").strip()[:254]
    code = (request.POST.get("otp") or request.POST.get("code") or "").strip()
    if not _valid_email(email):
        return JsonResponse({"ok": False, "error": "Enter a valid email address."}, status=400)

    try:
        check("subscription.otp.verify", email.lower())
    except RateLimitExceeded as exc:
        return JsonResponse(
            {"ok": False, "error": "Too many attempts. Please wait a moment and try again."},
            status=429, headers={"Retry-After": str(exc.retry_after)},
        )

    result = otp.verify(_OTP_PURPOSE, email, code)
    if not result.ok:
        msg = {
            "expired": "That code has expired. Please request a new one.",
            "too_many_attempts": "Too many wrong attempts. Please request a new code.",
        }.get(result.reason, "That code is incorrect. Please try again.")
        return JsonResponse({"ok": False, "error": msg}, status=400)

    # Verified: record the acceptance of the current agreement and hand back a
    # short-lived token binding this address for the payment step.
    agreement = UserAgreement.active()
    AgreementAcceptance.objects.create(
        agreement=agreement,
        email=email,
        ip_address=request.META.get("REMOTE_ADDR") or None,
        user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:400],
    )
    audit.record(
        "billing.agreement.accepted",
        request=request,
        actor_kind="customer",
        context={"email": email, "agreement": str(agreement.id) if agreement else "default"},
    )
    token = tokens.sign(
        _VERIFIED_PURPOSE,
        {"email": email.lower(), "agreement": str(agreement.id) if agreement else ""},
    )
    return JsonResponse({"ok": True, "verified": token})


def _verified_agreement(token: str, email: str) -> "tuple[bool, UserAgreement | None]":
    """Return (ok, agreement) for a verified token that matches ``email``."""
    try:
        payload = tokens.unsign(_VERIFIED_PURPOSE, token or "")
    except tokens.TokenError:
        return False, None
    if (payload.get("email") or "").lower() != email.lower():
        return False, None
    agreement_id = payload.get("agreement") or ""
    agreement = None
    if agreement_id:
        agreement = UserAgreement.objects.filter(pk=agreement_id).first()
    return True, agreement


# --------------------------------------------------------------------------
# Create the order + the Cashfree session (called on "Proceed to payment")
# --------------------------------------------------------------------------
@require_POST
def create_session(request):
    slug = (request.POST.get("slug") or request.POST.get("plan") or "").strip()
    plan = _paid_plan(slug)
    if plan is None:
        return JsonResponse({"ok": False, "error": "Unknown plan."}, status=404)

    cycle = cycles.normalise_cycle(request.POST.get("cycle"))
    code = (request.POST.get("code") or request.POST.get("discount_code") or "").strip()
    name = (request.POST.get("name") or "").strip()[:120]
    email = (request.POST.get("email") or "").strip()[:254]
    phone = _clean_phone(request.POST.get("phone"))

    errors = {}
    if not _valid_email(email):
        errors["email"] = "Enter a valid email address."
    if not (10 <= len(phone) <= 15):
        errors["phone"] = "Enter a valid phone number."
    if errors:
        return JsonResponse({"ok": False, "errors": errors}, status=400)

    # Gate: a payment can only start for an address that just passed the email
    # OTP step. The token is bound to the email, so a form replayed with a
    # different address (or none) cannot reach Cashfree.
    verified_ok, agreement = _verified_agreement(request.POST.get("verified"), email)
    if not verified_ok:
        return JsonResponse(
            {"ok": False, "verify_required": True,
             "error": "Please verify your email before paying."},
            status=403,
        )

    try:
        check("subscription.checkout", client_identity(request))
    except RateLimitExceeded as exc:
        return JsonResponse(
            {"ok": False, "error": "Too many attempts. Please wait a minute and try again."},
            status=429, headers={"Retry-After": str(exc.retry_after)},
        )

    if not cashfree.is_configured():
        return JsonResponse(
            {"ok": False, "error": "Online payment isn’t available right now. Please contact us."},
            status=503,
        )

    # Authoritative recompute  the amount charged comes from here, never the form.
    q = quote(plan, cycle, discount_code=code)

    order = SubscriptionOrder.objects.create(
        plan=plan,
        plan_name=plan.name,
        cycle=q.cycle,
        agreement=agreement,
        customer_name=name,
        customer_email=email,
        customer_phone=phone,
        currency=plan.currency or "INR",
        per_month_price=q.per_month,
        months=q.months,
        base_amount=q.base,
        discount=q.discount,
        discount_code=(q.discount.code if q.discount else ""),
        discount_amount=q.discount_amount,
        taxable_amount=q.taxable,
        gst_rate=q.gst_rate,
        gst_amount=q.gst_amount,
        total_amount=q.total,
        status=SubscriptionOrder.Status.CREATED,
    )

    token = tokens.sign(_TOKEN_PURPOSE, {"order": str(order.id)})
    return_url = request.build_absolute_uri(reverse("billing:return") + f"?t={token}")
    notify_url = request.build_absolute_uri(reverse("billing:webhook"))

    try:
        params = cashfree.create_order(order, return_url=return_url, notify_url=notify_url)
    except cashfree.CashfreeError:
        return JsonResponse(
            {"ok": False, "error": "We couldn’t start the payment. Please try again."},
            status=502,
        )

    audit.record(
        "billing.subscription.started",
        request=request,
        target=order,
        target_kind="SubscriptionOrder",
        target_label=order.number,
        actor_kind="customer",
        context={"plan": order.plan_name, "cycle": order.cycle, "total": f"{order.total_amount:.2f}"},
    )

    return JsonResponse(
        {
            "ok": True,
            "payment_session_id": params["payment_session_id"],
            "mode": params["mode"],
            "order_id": order.number,
        }
    )


# --------------------------------------------------------------------------
# Free plan  same details → agreement → OTP flow, settled without a gateway
# --------------------------------------------------------------------------
@require_POST
def free_session(request):
    """Settle a free plan straight to onboarding (no payment).

    A free plan on the pricing page runs the identical checkout up to the email
    OTP; there is nothing to charge, so this records a ₹0 order, grants it, and
    hands back the onboarding link. The email must have just passed the OTP step,
    exactly as a paid checkout requires before it can start a payment.
    """
    slug = (request.POST.get("slug") or request.POST.get("plan") or "").strip()
    plan = _free_plan(slug)
    if plan is None:
        return JsonResponse({"ok": False, "error": "Unknown plan."}, status=404)

    name = (request.POST.get("name") or "").strip()[:120]
    email = (request.POST.get("email") or "").strip()[:254]
    phone = _clean_phone(request.POST.get("phone"))

    errors = {}
    if not _valid_email(email):
        errors["email"] = "Enter a valid email address."
    if not (10 <= len(phone) <= 15):
        errors["phone"] = "Enter a valid phone number."
    if errors:
        return JsonResponse({"ok": False, "errors": errors}, status=400)

    verified_ok, agreement = _verified_agreement(request.POST.get("verified"), email)
    if not verified_ok:
        return JsonResponse(
            {"ok": False, "verify_required": True,
             "error": "Please verify your email before continuing."},
            status=403,
        )

    try:
        check("subscription.checkout", client_identity(request))
    except RateLimitExceeded as exc:
        return JsonResponse(
            {"ok": False, "error": "Too many attempts. Please wait a minute and try again."},
            status=429, headers={"Retry-After": str(exc.retry_after)},
        )

    # Authoritative ₹0 breakdown  a free plan has no price, discount or GST.
    q = quote(plan)

    order = SubscriptionOrder.objects.create(
        plan=plan,
        plan_name=plan.name,
        cycle=q.cycle,
        agreement=agreement,
        customer_name=name,
        customer_email=email,
        customer_phone=phone,
        currency=plan.currency or "INR",
        per_month_price=q.per_month,
        months=q.months,
        base_amount=q.base,
        discount=None,
        discount_code="",
        discount_amount=q.discount_amount,
        taxable_amount=q.taxable,
        gst_rate=q.gst_rate,
        gst_amount=q.gst_amount,
        total_amount=q.total,
        status=SubscriptionOrder.Status.CREATED,
    )
    cashfree.mark_free_paid(order)

    audit.record(
        "billing.subscription.free",
        request=request,
        target=order,
        target_kind="SubscriptionOrder",
        target_label=order.number,
        actor_kind="customer",
        context={"plan": order.plan_name, "free": True, "email": email},
    )
    return JsonResponse({"ok": True, "free": True, "redirect": onboarding_url(order, request)})


# --------------------------------------------------------------------------
# Return from Cashfree + webhook  both confirm by fetch, never trust the body
# --------------------------------------------------------------------------
@require_GET
def payment_return(request):
    token = request.GET.get("t") or ""
    order = None
    try:
        payload = tokens.unsign(_TOKEN_PURPOSE, token)
        order = (
            SubscriptionOrder.objects.filter(pk=payload.get("order"))
            .select_related("plan")
            .first()
        )
    except tokens.TokenError:
        order = None

    if order is None:
        raise Http404("Unknown order")

    if not order.is_paid:
        cashfree.confirm_by_fetch(order)
    if order.is_paid:
        _complete_linked_invite(order)

        # Once settled, the buyer's next step is onboarding  create the account
        # and capture the tax profile. Send them straight there, unless an
        # account already exists for this email (they purchased with an address
        # they had registered), in which case signing in unlocks the plan.
        email = (order.customer_email or "").strip().lower()
        if not get_user_model().objects.filter(email=email).exists():
            return redirect(onboarding_url(order, request))

    return render(
        request,
        "billing/return.html",
        {
            "page_title": "Your subscription",
            "order": order,
            "paid": order.is_paid,
            "sign_in_url": surface_url("app", "/sign-in/", request),
            "pricing_url": reverse("web:pricing"),
        },
    )


@csrf_exempt
@require_POST
def webhook(request):
    """Cashfree server-to-server notification. Only ever triggers a fetch."""
    try:
        body = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return HttpResponse(status=400)

    data = body.get("data") or body
    cf_order_id = (data.get("order") or {}).get("order_id") or data.get("order_id") or ""
    order = cashfree.order_for_cf_id(cf_order_id)
    if order is not None and not order.is_paid:
        cashfree.confirm_by_fetch(order)
    if order is not None and order.is_paid:
        _complete_linked_invite(order)

    # Always 200 so Cashfree stops retrying a webhook we've handled or ignored.
    return HttpResponse(status=200)


# --------------------------------------------------------------------------
# Admin-issued custom-priced invite  same checkout, a fixed price for one email
# --------------------------------------------------------------------------
#
# An operator picks a plan and a price for an email in the Django admin, which
# mints a short-lived link to a page on the marketing site. That page runs the
# *same* subscription checkout (details → agreement → email OTP → summary →
# pay), but every money figure comes from the invite's ``custom_amount``, never
# the plan's list price and never the browser. A zero amount runs the whole flow
# and settles free, skipping the gateway entirely.


def invite_link(invite: SubscriptionInvite, request=None) -> str:
    """The absolute onewooz.com URL for an invite's temporary checkout page.

    Reversed against the marketing urlconf explicitly: this is called from the
    Django admin (a different surface, whose active urlconf has no ``billing``
    namespace), so the request's own urlconf can't resolve the route.
    """
    from django.conf import settings

    token = tokens.sign(_INVITE_PURPOSE, {"invite": str(invite.id)})
    path = reverse("billing:invite", args=[token], urlconf=settings.ROOT_URLCONF)
    return surface_url("marketing", path, request)


def _load_invite(token: str) -> SubscriptionInvite | None:
    """Resolve a signed invite token to a still-usable invite, or ``None``.

    Returns ``None`` for a tampered/expired token and for an invite that is no
    longer pending or has passed its own expiry  the caller must not disclose
    which, only that the link no longer works.
    """
    try:
        payload = tokens.unsign(_INVITE_PURPOSE, token or "")
    except tokens.TokenError:
        return None
    invite = (
        SubscriptionInvite.objects.filter(pk=payload.get("invite"))
        .select_related("plan")
        .first()
    )
    if invite is None or not invite.is_usable or invite.plan is None:
        return None
    return invite


def _invite_quote(invite: SubscriptionInvite):
    """Authoritative breakdown for an invite.

    ``custom_amount`` is a custom **per-month** price, so the cycle base is that
    figure times the plan's months (a quarterly plan at ₹100/mo bills ₹300 before
    GST)  the same way the platform's own plans are priced.
    """
    return quote(invite.plan, per_month_override=invite.custom_amount)


@require_GET
def invite_checkout(request, token):
    """The temporary, custom-priced checkout page opened from an invite link."""
    invite = _load_invite(token)
    if invite is None:
        return render(
            request,
            "billing/invite.html",
            {"page_title": "Checkout link", "invite_error": True},
            status=410,
        )

    plan = invite.plan
    q = _invite_quote(invite)
    is_free = q.total <= 0
    # A paid invite hands off to Cashfree's hosted checkout via their JS SDK, so
    # opt this one response into the payment-CSP relaxation (see core.middleware).
    # A free invite never loads the gateway, so it stays nonce-only.
    if not is_free:
        request.csp_payment = True
    ctx = {
        "page_title": f"Subscribe · {plan.name}",
        "invite_error": False,
        "token": token,
        "invite": invite,
        "plan": plan,
        "is_one_time": plan.is_one_time,
        "cycle_label": (
            "One-time · 1 year" if plan.is_one_time
            else cycles.DURATION_LABELS.get(plan.duration, "")
        ),
        "quote": q.as_json(),
        "is_free": is_free,
        # Endpoints the page's checkout JS calls (agreement + OTP are shared).
        "quote_url": reverse("billing:invite_quote"),
        "session_url": reverse("billing:invite_session"),
        "agreement_url": reverse("billing:agreement"),
        "otp_send_url": reverse("billing:otp_send"),
        "otp_verify_url": reverse("billing:otp_verify"),
    }
    return render(request, "billing/invite.html", ctx)


@require_POST
def invite_quote(request):
    """JSON breakdown for the invite's summary modal  the custom price + GST."""
    invite = _load_invite(request.POST.get("token"))
    if invite is None:
        return JsonResponse(
            {"ok": False, "error": "This link has expired or was already used."},
            status=410,
        )
    q = _invite_quote(invite)
    payload = q.as_json()
    payload["plan_name"] = invite.plan.name
    return JsonResponse({"ok": True, "quote": payload})


@require_POST
def invite_session(request):
    """Create the order for an invite and either settle it free or start payment.

    The amount is recomputed here from the invite (never the form). A verified
    email token (from the shared OTP step) is required before any charge, exactly
    as on the pricing page.
    """
    invite = _load_invite(request.POST.get("token"))
    if invite is None:
        return JsonResponse(
            {"ok": False, "error": "This link has expired or was already used."},
            status=410,
        )

    plan = invite.plan
    name = (request.POST.get("name") or "").strip()[:120]
    email = (request.POST.get("email") or "").strip()[:254]
    phone = _clean_phone(request.POST.get("phone"))

    errors = {}
    if not _valid_email(email):
        errors["email"] = "Enter a valid email address."
    elif email.lower() != invite.email.lower():
        # The invite is bound to one address; the OTP was sent there too.
        errors["email"] = "This link was issued for a different email address."
    if not (10 <= len(phone) <= 15):
        errors["phone"] = "Enter a valid phone number."
    if errors:
        return JsonResponse({"ok": False, "errors": errors}, status=400)

    # Same gate as the public flow: a payment can only start for an address that
    # just passed the email OTP step.
    verified_ok, agreement = _verified_agreement(request.POST.get("verified"), email)
    if not verified_ok:
        return JsonResponse(
            {"ok": False, "verify_required": True,
             "error": "Please verify your email before continuing."},
            status=403,
        )

    try:
        check("subscription.checkout", client_identity(request))
    except RateLimitExceeded as exc:
        return JsonResponse(
            {"ok": False, "error": "Too many attempts. Please wait a minute and try again."},
            status=429, headers={"Retry-After": str(exc.retry_after)},
        )

    q = _invite_quote(invite)
    is_free = q.total <= 0

    if not is_free and not cashfree.is_configured():
        return JsonResponse(
            {"ok": False, "error": "Online payment isn’t available right now. Please contact us."},
            status=503,
        )

    order = SubscriptionOrder.objects.create(
        plan=plan,
        plan_name=plan.name,
        cycle=q.cycle,
        agreement=agreement,
        customer_name=name,
        customer_email=email,
        customer_phone=phone,
        currency=plan.currency or "INR",
        per_month_price=q.per_month,
        months=q.months,
        base_amount=q.base,
        discount=None,
        discount_code="",
        discount_amount=q.discount_amount,
        taxable_amount=q.taxable,
        gst_rate=q.gst_rate,
        gst_amount=q.gst_amount,
        total_amount=q.total,
        status=SubscriptionOrder.Status.CREATED,
    )
    invite.order = order
    invite.save(update_fields=["order", "updated_at"])

    # Free grant  run no gateway; settle the order and finish the invite.
    if is_free:
        cashfree.mark_free_paid(order)
        invite.status = SubscriptionInvite.Status.COMPLETED
        invite.completed_at = timezone.now()
        invite.save(update_fields=["status", "completed_at", "updated_at"])
        audit.record(
            "billing.invite.completed",
            request=request,
            target=order,
            target_kind="SubscriptionOrder",
            target_label=order.number,
            actor_kind="customer",
            context={"plan": order.plan_name, "free": True, "email": email},
        )
        token = tokens.sign(_TOKEN_PURPOSE, {"order": str(order.id)})
        return JsonResponse(
            {"ok": True, "free": True, "redirect": reverse("billing:return") + f"?t={token}"}
        )

    # Paid  mint the return token and hand off to Cashfree hosted checkout.
    token = tokens.sign(_TOKEN_PURPOSE, {"order": str(order.id)})
    return_url = request.build_absolute_uri(reverse("billing:return") + f"?t={token}")
    notify_url = request.build_absolute_uri(reverse("billing:webhook"))
    try:
        params = cashfree.create_order(order, return_url=return_url, notify_url=notify_url)
    except cashfree.CashfreeError:
        return JsonResponse(
            {"ok": False, "error": "We couldn’t start the payment. Please try again."},
            status=502,
        )

    audit.record(
        "billing.invite.started",
        request=request,
        target=order,
        target_kind="SubscriptionOrder",
        target_label=order.number,
        actor_kind="customer",
        context={"plan": order.plan_name, "total": f"{order.total_amount:.2f}", "email": email},
    )
    return JsonResponse(
        {
            "ok": True,
            "payment_session_id": params["payment_session_id"],
            "mode": params["mode"],
            "order_id": order.number,
        }
    )


def _complete_linked_invite(order: SubscriptionOrder) -> None:
    """Mark a pending invite completed once its paid order settles."""
    invite = (
        SubscriptionInvite.objects.filter(
            order=order, status=SubscriptionInvite.Status.PENDING
        ).first()
    )
    if invite is None:
        return
    invite.status = SubscriptionInvite.Status.COMPLETED
    invite.completed_at = timezone.now()
    invite.save(update_fields=["status", "completed_at", "updated_at"])
