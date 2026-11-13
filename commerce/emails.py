"""Transactional email for store growth flows.

Right now this holds one message: the **collaboration invite**  the mail that
recognises a creator as a referral partner for a particular store and hands them
the expiring contract smart-link (plus the OTP) they use to go live at
``<subdomain>.myugcstore.com``.

It leaves from a single fixed address on the referral root domain,
``no-reply@myugcstore.com`` (``settings.REFERRAL_FROM_EMAIL``), with the store's
name as the From display name so the creator can see who invited them. Like the
rest of OneWooz's outbound mail this is best-effort: a delivery failure is
swallowed into a boolean and logged, so a flaky transport can never turn an
owner inviting a collaborator into a 500  the owner is still shown the OTP and
the copyable link as a fallback.

Transport is the same one store→customer mail uses
(:func:`core.mail.store_mail_connection` → the SES backend in production), so the
referral domain must be a verified SES identity that the task-role policy admits
as a ``From``. See ``deploy/aws/ses/README.md``.
"""

from __future__ import annotations

import logging
from email.utils import formataddr

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from core.mail import store_mail_connection

__all__ = [
    "referral_from_header",
    "send_collaboration_invite",
    "send_collaboration_verified",
    "send_referral_login_otp",
    "send_collaboration_paid",
    "send_order_confirmation",
]

logger = logging.getLogger(__name__)


def referral_from_header(store_name: str = "") -> str:
    """The ``From`` header for collaboration mail: the store name (when known) in
    front of ``settings.REFERRAL_FROM_EMAIL`` (``no-reply@myugcstore.com``).

    Falls back to the bare address when there is no name or it cannot be encoded,
    so the header is always well-formed.
    """
    address = settings.REFERRAL_FROM_EMAIL
    display = (store_name or "").strip()
    if not display:
        return address
    try:
        return formataddr((f"{display} on OneWooz", address))
    except Exception:
        return address


def send_collaboration_invite(collab, *, contract_url: str, hours: int = 48) -> bool:
    """Email a creator the invite + contract link for ``collab``. Returns success.

    ``collab`` is a :class:`commerce.models.Collaboration` with a freshly minted
    OTP and invite token (call ``_mint_invite`` first). ``contract_url`` is the
    absolute smart-link to the public contract page. Delivery failure is logged
    and returned as ``False``  the caller keeps showing the OTP so the owner can
    still share it by hand.
    """
    to = (getattr(collab, "email", "") or "").strip()
    if not to:
        return False

    site = collab.site
    store_name = (getattr(site, "name", "") or "").strip() or "the store"

    if collab.commission_type == collab.CommissionType.PERCENT:
        commission_label = f"{collab.commission_value:.0f}% of each attributed sale"
    else:
        commission_label = f"₹{collab.commission_value:.2f} per attributed sale"

    context = {
        "brand": store_name,
        "store_name": store_name,
        "collab_name": (collab.name or "").strip(),
        "contract_url": contract_url,
        "otp": collab.otp,
        "hours": hours,
        "commission_label": commission_label,
    }
    subject = f"You've been invited to collaborate with {store_name}"

    text_body = render_to_string("commerce/email/collaboration_invite.txt", context)
    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=referral_from_header(store_name),
        to=[to],
        connection=store_mail_connection(fail_silently=True),
    )
    try:
        html_body = render_to_string("commerce/email/collaboration_invite.html", context)
    except Exception:
        html_body = ""
    if html_body:
        message.attach_alternative(html_body, "text/html")

    try:
        message.send(fail_silently=False)
    except Exception:
        logger.exception("Could not deliver collaboration invite to %s", to)
        return False
    return True


def send_collaboration_verified(collab, *, dashboard_url: str, store_url: str = "") -> bool:
    """Confirm to a creator that they're verified, with their myUGCstore
    dashboard link (and their own store link). Returns success.

    Sent once, right after the creator enters the correct OTP on the contract
    page. From the store (its name on the referral identity), with replies going
    to the store's support email when it has one.
    """
    to = (getattr(collab, "email", "") or "").strip()
    if not to:
        return False

    site = collab.site
    store_name = (getattr(site, "name", "") or "").strip() or "the store"

    if collab.commission_type == collab.CommissionType.PERCENT:
        commission_label = f"{collab.commission_value:.0f}% of each attributed sale"
    else:
        commission_label = f"₹{collab.commission_value:.2f} per attributed sale"

    context = {
        "brand": store_name,
        "store_name": store_name,
        "collab_name": (collab.name or "").strip(),
        "email": to,
        "dashboard_url": dashboard_url,
        "store_url": store_url,
        "commission_label": commission_label,
    }
    reply_to = (getattr(site, "support_email", "") or "").strip()

    text_body = render_to_string("commerce/email/collaboration_verified.txt", context)
    message = EmailMultiAlternatives(
        subject=f"You're verified: your collaboration with {store_name} is live",
        body=text_body,
        from_email=referral_from_header(store_name),
        to=[to],
        reply_to=[reply_to] if reply_to else None,
        connection=store_mail_connection(fail_silently=True),
    )
    try:
        html_body = render_to_string("commerce/email/collaboration_verified.html", context)
    except Exception:
        html_body = ""
    if html_body:
        message.attach_alternative(html_body, "text/html")

    try:
        message.send(fail_silently=False)
    except Exception:
        logger.exception("Could not deliver collaboration verified email to %s", to)
        return False
    return True


def send_collaboration_paid(collab, *, amount, dashboard_url: str, note: str = "") -> bool:
    """Tell a creator the store has paid them ``amount``. Returns success.

    Sent whenever the owner marks a payout request paid or records a payout
    from the Collaborations page. From the store (its name on the referral
    identity), replies to the store's support email when it has one.
    """
    from django.utils import timezone

    to = (getattr(collab, "email", "") or "").strip()
    if not to:
        return False
    site = collab.site
    store_name = (getattr(site, "name", "") or "").strip() or "the store"
    context = {
        "brand": store_name,
        "store_name": store_name,
        "collab_name": (collab.name or "").strip(),
        "amount": f"{amount:,.2f}",
        "total_paid": f"{(collab.total_paid or 0):,.2f}",
        "outstanding": f"{collab.outstanding:,.2f}",
        "paid_on": f"{timezone.localdate():%d %b %Y}",
        "note": (note or "").strip(),
        "dashboard_url": dashboard_url,
    }
    reply_to = (getattr(site, "support_email", "") or "").strip()
    text_body = render_to_string("commerce/email/collaboration_paid.txt", context)
    message = EmailMultiAlternatives(
        subject=f"{store_name} has paid you ₹{context['amount']}",
        body=text_body,
        from_email=referral_from_header(store_name),
        to=[to],
        reply_to=[reply_to] if reply_to else None,
        connection=store_mail_connection(fail_silently=True),
    )
    try:
        html_body = render_to_string("commerce/email/collaboration_paid.html", context)
    except Exception:
        html_body = ""
    if html_body:
        message.attach_alternative(html_body, "text/html")
    try:
        message.send(fail_silently=False)
    except Exception:
        logger.exception("Could not deliver collaboration paid email to %s", to)
        return False
    return True


def send_referral_login_otp(email: str, code: str, *, minutes: int) -> bool:
    """Mail a one-time sign-in code to a collaborator for the portal login.

    From ``no-reply@myugcstore.com`` (the referral identity). In development the
    default console email backend prints it to the runserver output, so no SMTP
    is needed to test the login. Returns success as a boolean; a delivery failure
    never surfaces to the visitor (the login view reports a generic message).
    """
    email = (email or "").strip()
    if not email:
        return False

    context = {"code": code, "minutes": minutes, "brand": "myUGCstore"}
    text_body = render_to_string("referral_portal/email/login_otp.txt", context)
    message = EmailMultiAlternatives(
        subject="Your myUGCstore sign-in code",
        body=text_body,
        from_email=referral_from_header(""),
        to=[email],
    )
    try:
        html_body = render_to_string("referral_portal/email/login_otp.html", context)
    except Exception:
        html_body = ""
    if html_body:
        message.attach_alternative(html_body, "text/html")

    try:
        message.send(fail_silently=False)
    except Exception:
        logger.exception("Could not deliver referral login OTP")
        return False
    return True


def send_order_confirmation(order, *, confirmation_url: str = "") -> bool:
    """Email the shopper a branded confirmation of a placed order.

    Itemised with each product's chosen variation (``variant_label``), quantity,
    unit price and line total, plus the order's subtotal / discount / GST / total.
    Sent from the store's own branded address on the shared SES domain (the same
    transport as other store→customer mail), replying to the owner's support
    inbox when set. Best-effort: a delivery failure is logged and returned as
    ``False`` so it can never turn placing an order into an error for the shopper.
    """
    to = (getattr(order, "customer_email", "") or "").strip()
    if not to:
        return False

    site = order.site
    store_name = (getattr(site, "name", "") or "").strip() or "the store"

    from storefront.templatetags.website_filters import currency_symbol

    sym = currency_symbol(getattr(site, "currency", "INR"))

    # Explicitly tenant-scoped: OneWooz's OrderItem queryset refuses to evaluate
    # via a bare ``order.items.all()``.
    from commerce.models import OrderItem

    items = [
        {
            "title": it.title,
            "variant_label": it.variant_label,
            "quantity": it.quantity,
            "unit_price": it.unit_price,
            "line_total": it.line_total,
        }
        for it in OrderItem.objects.for_site(site).filter(order=order).order_by("pk")
    ]

    # An absolute link to the shopper's confirmation page. The caller passes one
    # built from the request when it has it; otherwise derive it from the store's
    # public origin so the mail is still actionable.
    if not confirmation_url:
        try:
            confirmation_url = f"{site.storefront_url()}orders/{order.id}/confirmation/"
        except Exception:
            confirmation_url = ""

    context = {
        "brand": store_name,
        "customer_name": (getattr(order, "customer_name", "") or "").strip(),
        "order_number": order.display_number,
        "items": items,
        "sym": sym,
        "subtotal": order.subtotal,
        "discount": order.discount_amount,
        "credit": order.credit_discount_amount,
        "tax": order.tax_amount,
        "total": order.total,
        "confirmation_url": confirmation_url,
        "reply_to": bool(getattr(site, "support_email", "")),
        "powered_by": True,
    }
    subject = f"Your {store_name} order #{order.display_number} is confirmed"

    try:
        text_body = render_to_string("commerce/email/order_confirmation.txt", context)
    except Exception:
        logger.exception("order confirmation text render failed for order=%s", order.pk)
        return False

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=site.customer_from_header,
        to=[to],
        reply_to=[site.support_email] if getattr(site, "support_email", "") else None,
        connection=store_mail_connection(fail_silently=True),
    )
    try:
        html_body = render_to_string("commerce/email/order_confirmation.html", context)
    except Exception:
        html_body = ""
    if html_body:
        message.attach_alternative(html_body, "text/html")

    try:
        message.send(fail_silently=False)
    except Exception:
        logger.exception("Could not deliver order confirmation to %s", to)
        return False
    return True
