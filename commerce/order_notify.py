"""Email the shopper when their order changes after it was placed.

Three moments, each one branded email from the store's own address (the same
sender, transport and look as the order confirmation in
:func:`commerce.emails.send_order_confirmation`):

* ``updated``: the store edited the order (items, quantities, prices,
  delivery address, contact details, tracking number). The email lists exactly
  what changed, old value → new value, then the order as it now stands.
* ``cancelled``: the order was cancelled (by the store, or by the courier /
  Shiprocket).
* ``returned``: the courier couldn't deliver it and the parcel was returned to
  the store (RTO delivered).
* ``shipped``: the parcel left with the courier (with the courier name,
  tracking number and tracking link when known).
* ``delivered``: the courier delivered it.

Sending happens after the surrounding transaction commits, on the Celery
worker (inline when no worker runs), so a bulk cancel or a courier webhook is
never slowed down or failed by mail. Every kind except ``updated`` goes out at
most once per order (courier webhooks retry, and a status can be set both by
the owner and by the courier).
"""

from __future__ import annotations

import logging

from django.core.cache import cache
from django.db import transaction

logger = logging.getLogger(__name__)

KINDS = ("updated", "cancelled", "returned", "shipped", "delivered")
# Kinds sent at most once per order.
_ONCE = ("cancelled", "returned", "shipped", "delivered")
# How long the "already told the shopper" mark is kept for once-only kinds.
_ONCE_TTL = 120 * 24 * 3600


# ── What changed (order edit page) ───────────────────────────────────────────

def _addr(o) -> str:
    place = " ".join(p for p in (o.get("shipping_city"), o.get("shipping_state"), o.get("shipping_pincode")) if p)
    return ", ".join(p for p in (o.get("shipping_address"), place) if p)


def snapshot(site, order) -> dict:
    """The customer-visible state of ``order`` (taken before and after an edit)."""
    from commerce.models import OrderItem

    items = {}
    for it in OrderItem.objects.for_site(site).filter(order=order).order_by("pk"):
        key = (it.title or "", (it.variant_label or "").strip())
        prev = items.get(key)
        items[key] = {
            "qty": int(it.quantity or 0) + (prev["qty"] if prev else 0),
            "price": str(it.unit_price),
        }
    return {
        "customer_name": (order.customer_name or "").strip(),
        "customer_phone": (order.customer_phone or "").strip(),
        "customer_email": (order.customer_email or "").strip(),
        "shipping_address": (order.shipping_address or "").strip(),
        "shipping_city": (order.shipping_city or "").strip(),
        "shipping_state": (order.shipping_state or "").strip(),
        "shipping_pincode": (order.shipping_pincode or "").strip(),
        "billing_address": (order.billing_address or "").strip(),
        "company_name": (order.company_name or "").strip(),
        "gstin": (order.gstin or "").strip(),
        "tracking_id": (order.tracking_id or "").strip(),
        "total": str(order.total),
        "items": items,
    }


def diff(before: dict, after: dict, sym: str) -> list[dict]:
    """``[{label, before, after}]`` for every change the shopper should hear
    about, in a readable order. Empty when nothing customer-visible changed."""
    out = []

    def add(label, old, new):
        out.append({"label": label, "before": old or "(none)", "after": new or "(removed)"})

    # Items: added, removed, quantity and price changes.
    b_items, a_items = before["items"], after["items"]
    for key in list(a_items.keys()) + [k for k in b_items if k not in a_items]:
        title, label = key
        name = f"{title} ({label})" if label else title
        old, new = b_items.get(key), a_items.get(key)
        if old is None:
            out.append({"label": "Item added", "before": "", "after": f"{name} × {new['qty']}"})
        elif new is None:
            out.append({"label": "Item removed", "before": f"{name} × {old['qty']}", "after": ""})
        else:
            if old["qty"] != new["qty"]:
                add(f"Quantity of {name}", str(old["qty"]), str(new["qty"]))
            if old["price"] != new["price"]:
                add(f"Price of {name}", f"{sym}{old['price']}", f"{sym}{new['price']}")

    if _addr(before) != _addr(after):
        add("Delivery address", _addr(before), _addr(after))
    for field, label in (
        ("customer_name", "Name"),
        ("customer_phone", "Phone"),
        ("customer_email", "Email"),
        ("billing_address", "Billing address"),
        ("company_name", "Company"),
        ("gstin", "GSTIN"),
        ("tracking_id", "Tracking number"),
    ):
        if before[field] != after[field]:
            add(label, before[field], after[field])
    if before["total"] != after["total"]:
        add("Order total", f"{sym}{before['total']}", f"{sym}{after['total']}")
    return out


# ── Queueing ─────────────────────────────────────────────────────────────────

def _once_key(kind: str, order_id) -> str:
    return f"order-mail:{kind}:{order_id}"


def queue(order, kind: str, changes: list | None = None) -> None:
    """Send the ``kind`` email for ``order`` once the current transaction
    commits. A no-op without a customer email, for an order still awaiting
    payment, or (for cancelled / returned) when it was already sent."""
    from commerce.models import Order

    if kind not in KINDS:
        return
    if not (getattr(order, "customer_email", "") or "").strip():
        return
    if getattr(order, "status", "") == Order.Status.AWAITING_PAYMENT:
        return
    if kind == "updated" and not changes:
        return
    order_id = str(order.pk)
    payload = list(changes or [])

    def _send():
        if kind in _ONCE and not cache.add(_once_key(kind, order_id), 1, _ONCE_TTL):
            return  # already told the shopper
        try:
            from commerce.tasks import send_order_update_email

            send_order_update_email.delay(order_id, kind, payload)
        except Exception:
            # No broker: send inline rather than lose the email.
            try:
                send(order_id, kind, payload)
            except Exception:
                logger.exception("order %s email failed order=%s", kind, order_id)

    transaction.on_commit(_send)


# ── Sending ──────────────────────────────────────────────────────────────────

_SUBJECTS = {
    "updated": "Your {brand} order #{num} has been updated",
    "cancelled": "Your {brand} order #{num} has been cancelled",
    "returned": "Your {brand} order #{num} was returned to the store",
    "shipped": "Your {brand} order #{num} has shipped",
    "delivered": "Your {brand} order #{num} has been delivered",
}


def _tracking(site, order) -> dict:
    """Courier name, tracking number, tracking link and expected delivery for
    the order's latest shipment ({} when it has none)."""
    from commerce.models import Shipment

    sh = (
        Shipment.objects.for_site(site)
        .filter(order=order)
        .exclude(status=Shipment.Status.CANCELLED)
        .order_by("-created_at")
        .first()
    )
    awb = ((sh.awb if sh else "") or order.tracking_id or "").strip()
    if sh is None and not awb:
        return {}
    url = ((sh.tracking_url if sh else "") or "").strip()
    etd = ""
    try:
        from commerce.shipping import latest_event_for_order

        evt = latest_event_for_order(site, order)
        payload = evt.raw_payload if evt is not None and isinstance(evt.raw_payload, dict) else {}
        if not url:
            url = str(payload.get("track_url") or "").strip()
        etd = str(payload.get("etd") or "").strip()[:10]
    except Exception:
        pass
    if url and not url.lower().startswith("https://"):
        url = ""
    return {
        "courier": ((sh.courier_name if sh else "") or "").strip(),
        "awb": awb,
        "url": url,
        "etd": etd,
    }


def send(order_id: str, kind: str, changes: list | None = None) -> bool:
    """Render and send one order-update email. Best-effort: returns False on
    any failure, never raises."""
    from django.core.mail import EmailMultiAlternatives
    from django.db.models import Sum
    from django.template.loader import render_to_string

    from core.mail import store_mail_connection

    from commerce.models import Order, OrderItem, Payment

    order = Order.all_objects.select_related("site").filter(pk=order_id).first()
    if order is None or kind not in KINDS:
        return False
    to = (order.customer_email or "").strip()
    if not to:
        return False
    site = order.site
    brand = (getattr(site, "name", "") or "").strip() or "the store"

    from storefront.templatetags.website_filters import currency_symbol

    sym = currency_symbol(getattr(site, "currency", "INR"))
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
    paid_online = (
        Payment.objects.for_site(site)
        .filter(order=order, status=Payment.Status.PAID)
        .aggregate(s=Sum("amount"))["s"]
    )
    try:
        order_url = f"{site.storefront_url()}orders/{order.id}/confirmation/"
    except Exception:
        order_url = ""

    context = {
        "kind": kind,
        "brand": brand,
        "customer_name": (order.customer_name or "").strip(),
        "order_number": order.display_number,
        "changes": changes or [],
        "items": items,
        "sym": sym,
        "subtotal": order.subtotal,
        "discount": order.discount_amount,
        "credit": order.credit_discount_amount,
        "tax": order.tax_amount,
        "shipping": order.shipping_amount,
        "total": order.total,
        "paid_online": paid_online,
        "order_url": order_url if kind != "cancelled" else "",
        "tracking": _tracking(site, order) if kind in ("shipped", "delivered") else {},
        "reply_to": bool(getattr(site, "support_email", "")),
        "support_phone": (getattr(site, "support_phone", "") or "").strip(),
        "powered_by": True,
    }
    subject = _SUBJECTS[kind].format(brand=brand, num=order.display_number)
    try:
        text_body = render_to_string("commerce/email/order_update.txt", context)
    except Exception:
        logger.exception("order %s email render failed order=%s", kind, order.pk)
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
        html_body = render_to_string("commerce/email/order_update.html", context)
    except Exception:
        html_body = ""
    if html_body:
        message.attach_alternative(html_body, "text/html")
    try:
        message.send(fail_silently=False)
    except Exception:
        logger.exception("Could not deliver order %s email to %s", kind, to)
        return False
    return True
