"""OneWooz's own Cashfree account  used to take subscription payments.

Distinct from :mod:`commerce.payments`, which runs a *tenant's* gateway for
their storefront. Here the merchant is OneWooz itself, so the credentials come
from settings (Secrets Manager / env), not from a per-store encrypted config.

The trust model is identical and non-negotiable: **we never mark an order paid
on the strength of a redirect or a webhook body.** Both the return page and the
webhook do nothing but trigger :func:`confirm_by_fetch`, which asks Cashfree's
authenticated API for the order's real status. A forged callback confirms
nothing.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from base64 import b64encode
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from core import audit

from . import cycles, emails
from .models import Discount, SubscriptionOrder

logger = logging.getLogger("onewooz.audit")

_API = "https://api.cashfree.com/pg"
_SANDBOX = "https://sandbox.cashfree.com/pg"
_API_VERSION = "2023-08-01"


class CashfreeError(Exception):
    """A gateway call failed. The message is safe to log, not to show raw."""


def _sandbox() -> bool:
    return bool(getattr(settings, "PLATFORM_CASHFREE_SANDBOX", True))


def _base() -> str:
    return _SANDBOX if _sandbox() else _API


def _app_id() -> str:
    return getattr(settings, "PLATFORM_CASHFREE_APP_ID", "") or ""


def _secret_key() -> str:
    return getattr(settings, "PLATFORM_CASHFREE_SECRET_KEY", "") or ""


def is_configured() -> bool:
    """True when platform Cashfree credentials are present."""
    return bool(_app_id() and _secret_key())


def _headers() -> dict:
    return {
        "x-client-id": _app_id(),
        "x-client-secret": _secret_key(),
        "x-api-version": _API_VERSION,
        "Content-Type": "application/json",
    }


def create_order(order: SubscriptionOrder, *, return_url: str, notify_url: str) -> dict:
    """Create the Cashfree order for ``order`` and return the launch params.

    Sets ``order.cf_order_id`` and moves it to ``PENDING``. The returned
    ``payment_session_id`` is what the checkout page hands to the Cashfree JS SDK
    to open hosted checkout. Raises :class:`CashfreeError` on any failure.
    """
    import requests

    if not is_configured():
        raise CashfreeError("platform Cashfree credentials are not configured")

    cf_order_id = f"OWZ-{order.number}-{order.id.hex[:8]}"
    body = {
        "order_id": cf_order_id,
        "order_amount": float(Decimal(order.total_amount)),
        "order_currency": order.currency or "INR",
        "customer_details": {
            "customer_id": order.number,
            "customer_name": order.customer_name or "OneWooz customer",
            "customer_email": order.customer_email,
            "customer_phone": order.customer_phone or "9999999999",
        },
        "order_meta": {"return_url": return_url, "notify_url": notify_url},
        "order_note": f"OneWooz {order.plan_name} · {order.get_cycle_display()}",
        "order_tags": {"kind": "subscription", "plan": (order.plan_name or "")[:60]},
    }
    try:
        resp = requests.post(f"{_base()}/orders", json=body, headers=_headers(), timeout=20)
        data = resp.json()
    except Exception as exc:
        order.status = SubscriptionOrder.Status.FAILED
        order.save(update_fields=["status", "updated_at"])
        raise CashfreeError(f"cashfree create failed: {exc}") from exc

    if resp.status_code >= 300 or not data.get("payment_session_id"):
        order.status = SubscriptionOrder.Status.FAILED
        order.save(update_fields=["status", "updated_at"])
        # data may carry a gateway message; log it but don't surface raw.
        logger.warning("cashfree create rejected order=%s: %s", order.number, data)
        raise CashfreeError("cashfree rejected the order")

    order.cf_order_id = cf_order_id
    order.status = SubscriptionOrder.Status.PENDING
    order.gateway_status = str(data.get("order_status") or "")
    order.save(update_fields=["cf_order_id", "status", "gateway_status", "updated_at"])

    return {
        "payment_session_id": data["payment_session_id"],
        "cf_order_id": cf_order_id,
        "mode": "sandbox" if _sandbox() else "production",
    }


def _mark_paid(order: SubscriptionOrder, *, payment_id: str = "", gateway_status: str = "PAID") -> None:
    """Idempotently mark the order paid and open its subscription window."""
    if order.status == SubscriptionOrder.Status.PAID:
        return

    with transaction.atomic():
        # Re-read under a row lock so two confirmations (return + webhook racing)
        # cannot both count the redemption or both write paid_at.
        locked = SubscriptionOrder.objects.select_for_update().get(pk=order.pk)
        if locked.status == SubscriptionOrder.Status.PAID:
            return

        today = timezone.localdate()
        locked.status = SubscriptionOrder.Status.PAID
        locked.paid_at = timezone.now()
        locked.gateway_status = gateway_status
        if payment_id:
            locked.cf_payment_id = payment_id
        locked.current_period_start = today
        locked.current_period_end = cycles.add_months(today, locked.months)
        locked.save(update_fields=[
            "status", "paid_at", "gateway_status", "cf_payment_id",
            "current_period_start", "current_period_end", "updated_at",
        ])

        if locked.discount_id:
            Discount.objects.filter(pk=locked.discount_id).update(
                times_redeemed=F("times_redeemed") + 1
            )
        # Reflect the new state onto the caller's instance.
        order.status = locked.status
        order.paid_at = locked.paid_at
        order.current_period_start = locked.current_period_start
        order.current_period_end = locked.current_period_end

    audit.record(
        "billing.subscription.paid",
        target=order,
        target_kind="SubscriptionOrder",
        target_label=order.number,
        actor_kind="customer",
        severity="notice",
        context={
            "plan": order.plan_name,
            "cycle": order.cycle,
            "total": f"{order.total_amount:.2f}",
            "currency": order.currency,
            "email": order.customer_email,
        },
    )
    logger.warning(
        "subscription paid: order=%s plan=%s cycle=%s total=%s",
        order.number, order.plan_name, order.cycle, order.total_amount,
    )

    # Freeze what this subscription grants (limits, feature areas, commission,
    # penalties) onto the owner's entitlement, taken from the plan as it stands
    # now. Re-run on every settlement  a renewal re-snapshots from the live
    # plan. Best-effort: a snapshot failure must never undo a confirmed payment,
    # exactly like the notification email below.
    try:
        from . import entitlements
        entitlements.grant_from_order(order)
    except Exception:
        logger.exception("entitlement snapshot failed for order=%s", order.number)

    # Notify the internal inbox of the purchase (buyer email, plan, duration,
    # amount). Runs exactly once per order  the early return above makes
    # _mark_paid idempotent  and never raises: a mail failure is logged inside
    # the sender and must not undo a confirmed payment.
    emails.send_subscription_purchase_notice(order)
    # ...and welcome the buyer themselves (same once-per-order guarantee; a
    # failure is logged inside the sender and never raised).
    emails.send_subscription_welcome(order)


def mark_free_paid(order: SubscriptionOrder) -> None:
    """Settle a zero-total order without any gateway call.

    Used by admin-issued free invites: the full checkout runs (email OTP,
    agreement), but there is nothing to charge, so the order is opened directly
    with the same period/redemption/audit path a paid order takes.
    """
    _mark_paid(order, gateway_status="FREE")


def confirm_by_fetch(order: SubscriptionOrder) -> bool:
    """Ask Cashfree for the order's real status; mark paid if PAID.

    Authoritative. Called by both the redirect return and the webhook. Returns
    True only when the order is confirmed paid.
    """
    import requests

    if not order.cf_order_id or not is_configured():
        return False
    try:
        resp = requests.get(
            f"{_base()}/orders/{order.cf_order_id}", headers=_headers(), timeout=20
        )
        data = resp.json()
    except Exception as exc:
        logger.warning("cashfree fetch failed order=%s: %s", order.number, exc)
        return False

    status = str(data.get("order_status") or "").upper()
    if status and status != order.gateway_status:
        order.gateway_status = status
        order.save(update_fields=["gateway_status", "updated_at"])

    if status == "PAID":
        _mark_paid(order, payment_id=str(data.get("cf_order_id") or ""))
        return True
    return False


def order_for_cf_id(cf_order_id: str) -> SubscriptionOrder | None:
    """Find a subscription order by its Cashfree order id (used by webhooks)."""
    if not cf_order_id:
        return None
    return SubscriptionOrder.objects.filter(cf_order_id=cf_order_id).first()


def verify_webhook_signature(raw_body: bytes, timestamp: str, signature: str) -> bool:
    """Verify a Cashfree webhook signature (base64 HMAC-SHA256 of ``ts + body``).

    Belt and braces only: even a verified webhook is treated as a nudge to
    :func:`confirm_by_fetch`, never as proof of payment. Returns False when no
    webhook secret is configured, so an unverified webhook still triggers a
    fetch rather than being trusted.
    """
    secret = getattr(settings, "PLATFORM_CASHFREE_WEBHOOK_SECRET", "") or ""
    if not secret or not signature or not timestamp:
        return False
    mac = hmac.new(secret.encode(), (timestamp + raw_body.decode("utf-8", "replace")).encode(), hashlib.sha256)
    expected = b64encode(mac.digest()).decode()
    return hmac.compare_digest(expected, signature)
