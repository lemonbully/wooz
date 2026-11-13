"""Renewal checkout  the endpoints the dashboard renewal popup calls.

These are the authenticated, in-dashboard counterpart to the anonymous pricing
checkout in :mod:`billing.views`. The owner is already signed in, so the email
they renew under is their own account address (never taken from the form) and
the email-OTP step the public flow uses to *prove* control of an address is
redundant  being authenticated already proves it. The agreement acceptance is
kept: the owner ticks it, and it is recorded against their address exactly as
the public flow records it.

Everything else matches the public flow's trust model: the amount charged is
recomputed here from the plan, never trusted from the browser, and a payment is
confirmed only by fetching Cashfree's authoritative status  never on the
strength of a redirect or webhook body.

    renew_quote (POST)    -> JSON breakdown (base + 18% GST) for the summary
    renew_agreement (GET) -> the agreement to accept, as JSON
    renew_session (POST)  -> record acceptance, create the order + Cashfree
                             order, return the payment_session_id for the SDK
    renew_free (POST)     -> settle a ₹0 free plan and unlock immediately
    renew_return (GET)    -> Cashfree redirect back; confirm by fetch, then
                             return to the dashboard
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from core import audit, tokens
from core.links import surface_url
from core.ratelimit import RateLimitExceeded, check, client_identity

from . import cashfree, renewal
from .models import AgreementAcceptance, SubscriptionOrder, SubscriptionPlan, UserAgreement
from .pricing import quote
from .views import _agreement_payload, _clean_phone, _free_plan, _paid_plan

#: Ties a renewal order to its Cashfree return link (purpose-salted, see
#: core.tokens)  a token minted here cannot be replayed against another flow.
_RETURN_PURPOSE = "billing.renew.return"


def _owner_email(request) -> str:
    return (getattr(request.user, "email", "") or "").strip()[:254]


def _record_acceptance(request) -> "UserAgreement | None":
    """Record the owner's acceptance of the current agreement and return it."""
    agreement = UserAgreement.active()
    AgreementAcceptance.objects.create(
        agreement=agreement,
        email=_owner_email(request),
        ip_address=request.META.get("REMOTE_ADDR") or None,
        user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:400],
    )
    return agreement


def _plan_refused(request, plan) -> "JsonResponse | None":
    """Refuse any plan other than the lapsed one while the owner is locked.

    The wall only ever shows the previous plan, but the slug comes from the
    browser, so the rule is enforced here too. Returns ``None`` when allowed.
    """
    if renewal.plan_allowed(renewal.state(request), plan):
        return None
    return JsonResponse(
        {
            "ok": False,
            "error": "Please renew your previous plan first. You can switch plans once it is active again.",
        },
        status=403,
    )


def _code(request) -> str:
    return (request.POST.get("code") or request.POST.get("discount_code") or "").strip()[:40]


def _agreed(request) -> bool:
    return (request.POST.get("agree") or "").strip() in ("1", "true", "on", "yes")


def _new_order(request, plan: SubscriptionPlan, q, agreement) -> SubscriptionOrder:
    """Create the (unpaid) renewal order under the owner's own account email."""
    return SubscriptionOrder.objects.create(
        plan=plan,
        plan_name=plan.name,
        cycle=q.cycle,
        agreement=agreement,
        customer_name=(getattr(request.user, "display_name", "") or "")[:120],
        customer_email=_owner_email(request),
        customer_phone=_clean_phone(request.POST.get("phone")),
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


# --------------------------------------------------------------------------
# Live breakdown for the summary modal
# --------------------------------------------------------------------------
@require_POST
@login_required
def renew_quote(request):
    """JSON breakdown (base + 18% GST) for a purchasable plan's summary."""
    try:
        check("subscription.quote", client_identity(request))
    except RateLimitExceeded as exc:
        return JsonResponse(
            {"ok": False, "error": "Too many attempts. Please wait a moment."},
            status=429, headers={"Retry-After": str(exc.retry_after)},
        )

    plan = _paid_plan((request.POST.get("slug") or "").strip())
    if plan is None:
        return JsonResponse({"ok": False, "error": "Unknown plan."}, status=404)
    refused = _plan_refused(request, plan)
    if refused is not None:
        return refused

    q = quote(plan, discount_code=_code(request))
    payload = q.as_json()
    payload["plan_name"] = plan.name
    return JsonResponse({"ok": True, "quote": payload})


@require_GET
@login_required
def renew_agreement(request):
    """The agreement the owner must accept, as JSON (built-in default if none)."""
    return JsonResponse({"ok": True, "agreement": _agreement_payload(UserAgreement.active())})


# --------------------------------------------------------------------------
# Paid renewal  create the order + Cashfree session
# --------------------------------------------------------------------------
@require_POST
@login_required
def renew_session(request):
    plan = _paid_plan((request.POST.get("slug") or "").strip())
    if plan is None:
        return JsonResponse({"ok": False, "error": "Unknown plan."}, status=404)
    refused = _plan_refused(request, plan)
    if refused is not None:
        return refused

    if not _agreed(request):
        return JsonResponse(
            {"ok": False, "error": "Please accept the agreement to continue."},
            status=400,
        )

    try:
        check("subscription.renew", client_identity(request))
    except RateLimitExceeded as exc:
        return JsonResponse(
            {"ok": False, "error": "Too many attempts. Please wait a minute and try again."},
            status=429, headers={"Retry-After": str(exc.retry_after)},
        )

    # Authoritative recompute  the amount charged comes from here, never the
    # form. A discount code (if any) is re-resolved server-side; an invalid one
    # is simply ignored, exactly as the public checkout does.
    q = quote(plan, discount_code=_code(request))

    # A code that covers the whole amount leaves nothing to charge, and the
    # gateway rejects a zero order: settle it directly (same period, redemption
    # and entitlement path as a paid order) and send the owner back in.
    if q.total <= 0:
        agreement = _record_acceptance(request)
        order = _new_order(request, plan, q, agreement)
        cashfree.mark_free_paid(order)
        audit.record(
            "billing.subscription.renew_free",
            request=request,
            target=order,
            target_kind="SubscriptionOrder",
            target_label=order.number,
            actor_kind="owner",
            context={"plan": order.plan_name, "discount": order.discount_code, "total": "0.00"},
        )
        return JsonResponse({"ok": True, "free": True, "redirect": reverse("ud:dashboard")})

    if not cashfree.is_configured():
        return JsonResponse(
            {"ok": False, "error": "Online payment isn’t available right now. Please contact us."},
            status=503,
        )

    agreement = _record_acceptance(request)
    order = _new_order(request, plan, q, agreement)

    token = tokens.sign(_RETURN_PURPOSE, {"order": str(order.id)})
    return_url = request.build_absolute_uri(reverse("billing_renew:renew_return") + f"?t={token}")
    # The webhook lives on the marketing surface; reverse it against that
    # urlconf (this request's urlconf has no billing namespace) and make it
    # absolute for that host. It only ever nudges a confirm-by-fetch.
    notify_url = surface_url(
        "marketing",
        reverse("billing:webhook", urlconf=settings.ROOT_URLCONF),
        request,
    )

    try:
        params = cashfree.create_order(order, return_url=return_url, notify_url=notify_url)
    except cashfree.CashfreeError:
        return JsonResponse(
            {"ok": False, "error": "We couldn’t start the payment. Please try again."},
            status=502,
        )

    audit.record(
        "billing.subscription.renew_started",
        request=request,
        target=order,
        target_kind="SubscriptionOrder",
        target_label=order.number,
        actor_kind="owner",
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
# Free renewal  settle a ₹0 plan and unlock immediately
# --------------------------------------------------------------------------
@require_POST
@login_required
def renew_free(request):
    """Move the owner onto a free plan, settling a ₹0 order that unlocks the
    dashboard right away (the active free grant clears the renewal wall; area
    access is then governed by :mod:`commerce.features`)."""
    plan = _free_plan((request.POST.get("slug") or "").strip())
    if plan is None:
        return JsonResponse({"ok": False, "error": "Unknown plan."}, status=404)
    refused = _plan_refused(request, plan)
    if refused is not None:
        return refused

    if not _agreed(request):
        return JsonResponse(
            {"ok": False, "error": "Please accept the agreement to continue."},
            status=400,
        )

    try:
        check("subscription.renew", client_identity(request))
    except RateLimitExceeded as exc:
        return JsonResponse(
            {"ok": False, "error": "Too many attempts. Please wait a minute and try again."},
            status=429, headers={"Retry-After": str(exc.retry_after)},
        )

    q = quote(plan)
    agreement = _record_acceptance(request)
    order = _new_order(request, plan, q, agreement)
    cashfree.mark_free_paid(order)

    audit.record(
        "billing.subscription.renew_free",
        request=request,
        target=order,
        target_kind="SubscriptionOrder",
        target_label=order.number,
        actor_kind="owner",
        context={"plan": order.plan_name, "free": True},
    )
    return JsonResponse({"ok": True, "free": True, "redirect": reverse("ud:dashboard")})


# --------------------------------------------------------------------------
# Return from Cashfree  confirm by fetch, then back to the dashboard
# --------------------------------------------------------------------------
@require_GET
@login_required
def renew_return(request):
    try:
        payload = tokens.unsign(_RETURN_PURPOSE, request.GET.get("t") or "")
        order = SubscriptionOrder.objects.filter(pk=payload.get("order")).first()
    except tokens.TokenError:
        order = None

    if order is None:
        raise Http404("Unknown order")

    if not order.is_paid:
        cashfree.confirm_by_fetch(order)

    # Either way, land the owner back on the dashboard: if the payment
    # confirmed, the renewal wall is now cleared; if it didn't, the wall simply
    # reappears so they can try again.
    return redirect(reverse("ud:dashboard"))
