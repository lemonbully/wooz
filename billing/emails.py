"""Transactional email for the subscription purchase flow.

The only message here is the one-time code that confirms a buyer controls the
email address on their order, sent during the agreement-acceptance step of the
pricing-page checkout. Delivery failures are swallowed into a boolean (as in
:mod:`accounts.emails`) so a flaky mail transport never surfaces to the buyer as
a 500  the view reports "we couldn't send that" instead.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from core import links
from core.hosts import Surface

__all__ = [
    "send_agreement_otp",
    "send_subscription_invite",
    "send_subscription_purchase_notice",
    "send_subscription_welcome",
]

logger = logging.getLogger(__name__)


def _surface_link(surface: Surface, urlconf: str, name: str) -> str:
    """Absolute URL of a named route on another surface, built without a request
    (reversed at the root script prefix, then put on that surface's origin)."""
    from django.urls import get_script_prefix, reverse, set_script_prefix

    previous = get_script_prefix()
    try:
        set_script_prefix("/")
        path = reverse(name, urlconf=urlconf)
    finally:
        set_script_prefix(previous)
    return links.surface_url(surface, path)


def send_subscription_welcome(order) -> bool:
    """Welcome the buyer of a newly settled subscription.

    Sent to the order's own email right after the internal purchase notice, from
    the same chokepoint (:func:`billing.cashfree._mark_paid`), so it goes out
    exactly once per order for a paid checkout, a free plan and an admin invite
    alike. A first-time buyer is invited to set up their account (the same
    onboarding link the payment return page uses); a returning customer, whose
    account already exists, is thanked and sent to their dashboard.

    Best-effort like every billing mail: failures are logged and reported as
    ``False``, never raised, because the subscription is already active.
    """
    from django.contrib.auth import get_user_model

    from .models import SubscriptionOrder

    to = (order.customer_email or "").strip()
    if not to:
        return False

    try:
        has_account = get_user_model().objects.filter(email__iexact=to).exists()
        is_returning = (
            SubscriptionOrder.objects.filter(
                customer_email__iexact=to, status=SubscriptionOrder.Status.PAID
            )
            .exclude(pk=order.pk)
            .exists()
        )
        if has_account:
            cta_url = _surface_link(Surface.APP, "onewooz.urls.app", "ud:dashboard")
        else:
            from .onboarding import onboarding_url

            cta_url = onboarding_url(order)
        contact_url = _surface_link(Surface.MARKETING, "onewooz.urls.marketing", "web:contact")
    except Exception:
        logger.exception("Could not build the welcome email for order %s", order.number)
        return False

    name = (order.customer_name or "").strip()
    is_free = order.total_amount <= 0
    context = {
        "brand": "OneWooz",
        "order_number": order.number,
        "first_name": name.split()[0] if name else "",
        "plan_name": order.plan_name,
        "duration_label": order.get_cycle_display(),
        "amount_label": f"{order.currency or 'INR'} {order.total_amount:.2f}",
        "is_free": is_free,
        "period_end": order.current_period_end,
        "has_account": has_account,
        "is_returning": is_returning,
        "cta_url": cta_url,
        "contact_url": contact_url,
        "marketing_origin": links.surface_origin(Surface.MARKETING),
    }
    subject = (
        f"Thank you for staying with OneWooz! Your {order.plan_name} plan is active"
        if is_returning
        else f"Welcome to OneWooz! Your {order.plan_name} plan is ready"
    )

    text_body = render_to_string("billing/email/subscription_welcome.txt", context)
    message = EmailMultiAlternatives(
        subject=subject[:150],
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to],
    )
    try:
        html_body = render_to_string("billing/email/subscription_welcome.html", context)
    except Exception:
        html_body = ""
    if html_body:
        message.attach_alternative(html_body, "text/html")

    try:
        message.send(fail_silently=False)
    except Exception:
        logger.exception("Could not deliver the welcome email for order %s", order.number)
        return False
    return True


def send_subscription_purchase_notice(order) -> bool:
    """Notify the internal inbox that a subscription was purchased.

    Sent to ``settings.SUBSCRIPTION_NOTICE_EMAIL`` (myonewooz@gmail.com) the
    moment an order is confirmed paid  for a gateway payment, a free grant, or
    an admin invite alike, since all three settle through the single chokepoint
    in :func:`billing.cashfree._mark_paid`. Carries the details asked for: which
    email bought, which plan, its duration, and the amount paid.

    Delivery failure is swallowed into a boolean (and logged) so a flaky mail
    transport can never turn a successful payment into an error for the buyer 
    the money is already taken and the subscription already active by this point.
    """
    to = getattr(settings, "SUBSCRIPTION_NOTICE_EMAIL", "") or ""
    if not to:
        return False

    amount_label = f"{order.currency or 'INR'} {order.total_amount:.2f}"
    context = {
        "brand": "OneWooz",
        "order": order,
        "order_number": order.number,
        "customer_email": order.customer_email,
        "customer_name": order.customer_name or "",
        "customer_phone": order.customer_phone or "",
        "plan_name": order.plan_name,
        "duration_label": order.get_cycle_display(),
        "amount_label": amount_label,
        "currency": order.currency or "INR",
        "total_amount": order.total_amount,
        "is_free": order.total_amount <= 0,
        "paid_at": order.paid_at,
        "period_start": order.current_period_start,
        "period_end": order.current_period_end,
        "marketing_origin": links.surface_origin(Surface.MARKETING),
    }
    subject = f"New subscription: {order.plan_name} · {order.customer_email}"

    text_body = render_to_string("billing/email/subscription_purchase_notice.txt", context)
    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to],
    )
    try:
        html_body = render_to_string("billing/email/subscription_purchase_notice.html", context)
    except Exception:
        html_body = ""
    if html_body:
        message.attach_alternative(html_body, "text/html")

    try:
        message.send(fail_silently=False)
    except Exception:
        logger.exception("Could not deliver subscription purchase notice for order %s", order.number)
        return False
    return True


def send_agreement_otp(email: str, code: str, *, minutes: int) -> bool:
    """Mail ``code`` to ``email`` for agreement acceptance. Returns success."""
    context = {
        "code": code,
        "minutes": minutes,
        "brand": "OneWooz",
        "marketing_origin": links.surface_origin(Surface.MARKETING),
    }
    text_body = render_to_string("billing/email/agreement_otp.txt", context)
    message = EmailMultiAlternatives(
        subject="Your OneWooz confirmation code",
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email],
    )
    try:
        html_body = render_to_string("billing/email/agreement_otp.html", context)
    except Exception:
        html_body = ""
    if html_body:
        message.attach_alternative(html_body, "text/html")

    try:
        message.send(fail_silently=False)
    except Exception:
        logger.exception("Could not deliver agreement OTP email")
        return False
    return True


def send_subscription_invite(
    email: str,
    *,
    link: str,
    plan_name: str,
    cycle_label: str,
    amount_label: str,
    is_free: bool,
    minutes: int,
) -> bool:
    """Email an admin-issued custom-priced invite link to ``email``.

    Carries the plan, the price (or "Free"), the link and how long it stays
    valid. Delivery failures are swallowed into a boolean so issuing an invite
    never 500s on a flaky transport  the admin is told the mail didn't go.
    """
    context = {
        "brand": "OneWooz",
        "link": link,
        "plan_name": plan_name,
        "cycle_label": cycle_label,
        "amount_label": amount_label,
        "is_free": is_free,
        "minutes": minutes,
        "marketing_origin": links.surface_origin(Surface.MARKETING),
    }
    subject = (
        f"Your OneWooz {plan_name} plan is ready to activate"
        if is_free
        else f"Complete your OneWooz {plan_name} subscription"
    )
    text_body = render_to_string("billing/email/subscription_invite.txt", context)
    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email],
    )
    try:
        html_body = render_to_string("billing/email/subscription_invite.html", context)
    except Exception:
        html_body = ""
    if html_body:
        message.attach_alternative(html_body, "text/html")

    try:
        message.send(fail_silently=False)
    except Exception:
        logger.exception("Could not deliver subscription invite email")
        return False
    return True
