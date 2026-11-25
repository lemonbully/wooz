"""Paid installs: platform Cashfree checkout, confirmed by fetch only.

* The client sends an app slug (and item key) and nothing else; price, GST and
  total are computed here.
* Settlement is idempotent under a row lock and writes the licence(s) and the
  installation in the same transaction. A licence is unique per order line, so
  a repeated webhook can never grant twice.
* Status is only ever learned by fetching the order from Cashfree (the return
  page, the webhook nudge, or the reconciliation sweep), never from a redirect
  or a webhook body.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .licences import Owner

logger = logging.getLogger("onewooz.marketplace")

PREFIX = "OWM-"


class OrderError(Exception):
    """Shown to the owner as is."""


def quote(app, item=None, cycle: str = "") -> dict:
    """Price breakdown for one app or item (server-side only).

    For an app sold on billing cycles, ``cycle`` picks the cycle; an unknown or
    missing one falls back to the app's default cycle (use :func:`start`'s
    strict check before charging)."""
    from billing import cycles
    from billing.pricing import gst_rate

    option = None
    if item is None and app.pricing == app.Pricing.CYCLES:
        option = app.cycle_option(cycle) or app.cycle_option(app.default_cycle())
    if item is not None:
        base = Decimal(item.price or 0)
    elif option is not None:
        base = Decimal(option["total"])
    else:
        base = Decimal(app.price or 0)
    rate = gst_rate() if app.gst_applicable else Decimal("0")
    base = cycles.money(base)
    gst = cycles.money(base * rate / Decimal(100))
    if item is not None:
        period = 365 if item.is_yearly else None
        licence = "yearly" if item.is_yearly else "permanent"
    elif option is not None:
        period = option["days"]
        licence = option["key"]
    else:
        period = app.period_days
        licence = app.pricing
    return {"base": base, "gst_rate": rate, "gst": gst, "total": cycles.money(base + gst),
            "period_days": period, "licence": licence,
            "cycle": option["key"] if option else "",
            "cycle_label": option["label"] if option else "",
            "months": option["months"] if option else None,
            "per_month": option["per_month"] if option else None}


def start(request, owner: Owner, app, item=None, *, site=None, return_url_for, notify_url: str,
          reauth_method: str = "", cycle: str = "") -> dict:
    from billing import cashfree

    from .models import MarketOrder, MarketOrderLine

    if item is not None and not item.is_priced:
        raise OrderError("That item is free. Just add it.")
    if item is None and not app.is_paid:
        raise OrderError("That app is free. Just install it.")
    if item is None and app.pricing == app.Pricing.CYCLES and app.cycle_option(cycle) is None:
        raise OrderError("Please choose how often you'd like to pay.")
    if not cashfree.is_configured():
        raise OrderError("Online payment isn't available right now. Please contact us.")
    if not owner.email:
        raise OrderError("Your account has no email address to attach the purchase to.")

    q = quote(app, item, cycle)
    description = f"{app.name}: {item.name}" if item is not None else app.name
    if q["cycle_label"]:
        description = f"{app.name} ({q['cycle_label']})"
    with transaction.atomic():
        order = MarketOrder.objects.create(
            owner_ref=owner.ref, owner_email=owner.email, site_ref=getattr(site, "pk", None),
            currency=app.currency or "INR", base_amount=q["base"], gst_rate=q["gst_rate"],
            gst_amount=q["gst"], total_amount=q["total"], reauth_method=reauth_method,
        )
        MarketOrderLine.objects.create(
            order=order, app=app, item=item,
            description=description[:160],
            licence_type=q["licence"], period_days=q["period_days"], base_amount=q["base"],
        )
    cf_order_id = f"{PREFIX}{order.number}-{order.id.hex[:8]}"
    try:
        params = cashfree.create_payment_order(
            cf_order_id=cf_order_id,
            amount=q["total"],
            currency=order.currency,
            customer_id=order.number,
            customer_name=(owner.name or owner.email)[:100],
            customer_email=owner.email,
            customer_phone=(getattr(getattr(request, "user", None), "phone", "") or getattr(site, "support_phone", "") or "9999999999")[:15],
            return_url=return_url_for(order),
            notify_url=notify_url,
            note=f"OneWooz Apps: {app.name}"[:200],
            tags={"kind": "marketplace", "app": app.slug, "item": getattr(item, "key", ""), "cycle": q["cycle"]},
        )
    except cashfree.CashfreeError as exc:
        order.status = MarketOrder.Status.FAILED
        order.save(update_fields=["status", "updated_at"])
        raise OrderError("We couldn't start the payment. Please try again.") from exc
    order.cf_order_id = cf_order_id
    order.status = MarketOrder.Status.PENDING
    order.gateway_status = str((params.get("data") or {}).get("order_status") or "")
    order.save(update_fields=["cf_order_id", "status", "gateway_status", "updated_at"])
    return {"payment_session_id": params["payment_session_id"], "mode": params["mode"], "order": order}


def confirm(order) -> bool:
    """Fetch the real status; settle idempotently when PAID."""
    from billing import cashfree

    from .models import MarketOrder

    if order is None:
        return False
    if order.is_paid:
        return True
    status, data = cashfree.fetch_order_status(order.cf_order_id)
    if status and status != order.gateway_status:
        order.gateway_status = status
        order.save(update_fields=["gateway_status", "updated_at"])
    if status != "PAID":
        if status in ("EXPIRED", "TERMINATED", "CANCELLED"):
            order.status = MarketOrder.Status.FAILED
            order.save(update_fields=["status", "updated_at"])
        return False
    return settle(order, payment_id=str((data or {}).get("cf_order_id") or ""))


def settle(order, *, payment_id: str = "") -> bool:
    from core import audit

    from . import licences
    from .models import MarketOrder

    owner = Owner(ref=order.owner_ref, email=order.owner_email)
    with transaction.atomic():
        locked = MarketOrder.objects.select_for_update().get(pk=order.pk)
        if locked.is_paid:
            order.refresh_from_db()
            return True
        locked.status = MarketOrder.Status.PAID
        locked.paid_at = timezone.now()
        locked.cf_payment_id = payment_id[:64]
        locked.save(update_fields=["status", "paid_at", "cf_payment_id", "updated_at"])
        for line in locked.lines.select_related("app", "item"):
            licences.grant(
                owner, line.app, item=line.item, source="purchase", period_days=line.period_days,
                order_line=line, price=line.base_amount, reason=f"Order {locked.number}",
            )
            if line.item is None or line.app.kind != line.app.Kind.SYSTEM:
                licences.install(owner, line.app, source="owner", reauth_method=locked.reauth_method)
    order.refresh_from_db()
    audit.record(
        "marketplace.order.paid", target=order, target_kind="MarketOrder", target_label=order.number,
        actor_kind="owner", context={"owner": order.owner_email, "total": f"{order.total_amount:.2f}"},
    )
    return True


def for_cf_id(cf_order_id: str):
    from .models import MarketOrder

    if not cf_order_id or not str(cf_order_id).startswith(PREFIX):
        return None
    return MarketOrder.objects.filter(cf_order_id=cf_order_id).first()


def reconcile_recent(days: int = 3) -> int:
    """Confirm pending orders whose return / webhook was missed, and flag paid
    orders that have no licence (should never happen; alerts staff)."""
    from . import alerts
    from .models import Licence, MarketOrder

    since = timezone.now() - timedelta(days=days)
    settled = 0
    for order in MarketOrder.objects.filter(status=MarketOrder.Status.PENDING, created_at__gte=since).exclude(
        cf_order_id=""
    )[:200]:
        try:
            if confirm(order):
                settled += 1
        except Exception:
            logger.exception("reconcile confirm failed for %s", order.number)
    for order in MarketOrder.objects.filter(status=MarketOrder.Status.PAID, paid_at__gte=since)[:500]:
        line_ids = list(order.lines.values_list("pk", flat=True))
        if line_ids and Licence.objects.filter(order_line_id__in=line_ids).count() < len(line_ids):
            alerts.notify(
                "payment_mismatch", None,
                subject=f"[OneWooz Apps] Paid order {order.number} has no licence",
                body=f"Order {order.number} for {order.owner_email} is paid but at least one line has no licence.",
            )
    return settled
