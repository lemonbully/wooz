"""Shipping orchestration: booking shipments and applying tracking updates.

This is the seam the reference filled with live Delhivery/Shiprocket clients.
Here the *flow* is faithful  accepting an order books a shipment, which moves
the order into the courier-specific tab; a courier webhook (or a manual sync)
advances the shipment through pickup → transit → delivered/RTO, which moves the
order between the Shipped / Delivered / RTO tabs. The network calls themselves
are stubbed behind :class:`LocalCourier` so real provider SDKs can replace it
without touching the views.

Nothing here evaluates a tenant queryset without ``.for_site`` / an explicit
``site`` filter, so tenant isolation holds.
"""

from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime

from django.utils import timezone

from commerce.models import CourierWebhookEvent, Order, Shipment, ShippingProvider

logger = logging.getLogger("onewooz.audit")


# ── Provider resolution ──────────────────────────────────────────────────────

def enabled_provider(site) -> ShippingProvider | None:
    """The store's enabled courier, if any (OneWooz keeps one active row)."""
    return ShippingProvider.objects.for_site(site).filter(enabled=True).first()


def selected_provider_key(site) -> str:
    """Lowercase slug of the enabled courier  ``''`` when self-ship/none."""
    p = enabled_provider(site)
    return (p.provider if p else "") or ""


def auto_ship_enabled(site) -> bool:
    """Owner setting: new orders are booked with the courier immediately and
    skip the Pending step.

    The setting can be toggled in two places  the Orders-page switch (stored on
    ``site.content['auto_ship_enabled']``, as the reference kept it) and the
    Shipping-settings checkbox (``ShippingProvider.auto_ship``). Either being on
    means on, so wherever the owner flips it, booking honours it.
    """
    try:
        if bool((site.content or {}).get("auto_ship_enabled", False)):
            return True
    except Exception:
        pass
    provider = enabled_provider(site)
    return bool(provider and provider.auto_ship)


def set_auto_ship(site, enabled: bool) -> None:
    content = site.content or {}
    content["auto_ship_enabled"] = bool(enabled)
    site.content = content
    site.save(update_fields=["content"])


# ── Booking a shipment (the "create in courier" step) ────────────────────────

@dataclass
class BookingResult:
    awb: str
    courier_name: str
    status: str
    tracking_url: str = ""
    meta: dict = field(default_factory=dict)


class LocalCourier:
    """Stub courier. Assigns a deterministic-looking AWB and an initial status
    that mirrors what each real provider would report right after order
    creation. Swap this for a live client that returns the same shape."""

    def __init__(self, provider_key: str):
        self.provider_key = (provider_key or "manual").lower()

    @property
    def courier_name(self) -> str:
        return {
            "delhivery": "Delhivery",
            "shiprocket": "Shiprocket",
            "manual": "Self-ship",
        }.get(self.provider_key, self.provider_key.title() or "Courier")

    def create_shipment(self, site, order) -> BookingResult:
        prefix = {"delhivery": "DLV", "shiprocket": "SR", "manual": "OW"}.get(
            self.provider_key, "OW"
        )
        awb = f"{prefix}{order.number:05d}{secrets.token_hex(3).upper()}"
        # Shiprocket submits the order and reports "pickup scheduled"; Delhivery
        # returns a created waybill that still needs a pickup request.
        status = (
            Shipment.Status.PICKUP_REQUESTED
            if self.provider_key == "shiprocket"
            else Shipment.Status.CREATED
        )
        return BookingResult(
            awb=awb,
            courier_name=self.courier_name,
            status=status,
            meta={"booked_by": "local", "provider": self.provider_key},
        )


_PRE_PICKUP = {
    Shipment.Status.PENDING,
    Shipment.Status.CREATED,
    Shipment.Status.BOOKED,
}


def _is_real_booking(shipment) -> bool:
    """A Shiprocket shipment is usable only once Shiprocket returned its
    shipment id; other couriers' local bookings are always usable."""
    if shipment.provider_key != "shiprocket":
        return True
    return bool(str((shipment.meta or {}).get("shipment_id") or "").strip())


def retire_stale_shipment(shipment) -> None:
    """Mark a booking that can't be used any more as cancelled (kept for history)."""
    meta = dict(shipment.meta or {})
    meta["retired_at"] = timezone.now().isoformat()
    meta["retired_reason"] = "replaced by a booking with the store's current courier"
    shipment.status = Shipment.Status.CANCELLED
    shipment.meta = meta
    shipment.save(update_fields=["status", "meta", "updated_at"])


def ensure_shipment(site, order) -> Shipment | None:
    """Book a shipment for ``order`` with the store's enabled courier, once.

    Idempotent: returns the existing shipment if one is already booked. Mirrors
    the reference's ``_ensure_delhivery_shipment`` / ``_ensure_shiprocket_shipment``
    called on Accept. Does nothing when no courier is enabled.
    """
    provider_row = enabled_provider(site)
    if not provider_row:
        return None
    # Reuse a live booking (e.g. an order accepted, moved back to Pending, then
    # accepted again); a booking the courier cancelled is replaced.
    existing = (
        Shipment.objects.for_site(site)
        .filter(order=order)
        .exclude(status=Shipment.Status.CANCELLED)
        .order_by("-created_at")
        .first()
    )
    if existing:
        if existing.provider_id == provider_row.pk and _is_real_booking(existing):
            return existing
        if existing.status not in _PRE_PICKUP:
            # The parcel already left with whichever courier booked it.
            return existing
        # A booking made with a different courier (the store switched couriers
        # after accepting) or a Shiprocket row without its Shiprocket ids: it
        # can never be picked up by the current courier, so retire it and book
        # afresh below.
        retire_stale_shipment(existing)

    # Shiprocket books against the merchant's real account (an order appears in
    # their Shiprocket dashboard). Delhivery / manual remain the local stub until
    # their live clients are ported.
    if (provider_row.provider or "").strip().lower() == "shiprocket":
        return _book_shiprocket(site, order, provider_row)

    courier = LocalCourier(provider_row.provider)
    result = courier.create_shipment(site, order)
    shipment = Shipment.objects.create(
        site=site,
        order=order,
        provider=provider_row,
        warehouse=provider_row.default_warehouse,
        awb=result.awb,
        courier_name=result.courier_name,
        tracking_url=result.tracking_url,
        status=result.status,
        meta=result.meta,
    )
    if result.awb and result.awb != (order.tracking_id or ""):
        order.tracking_id = result.awb
        order.save(update_fields=["tracking_id"])
    return shipment


def _book_shiprocket(site, order, provider_row) -> Shipment:
    """Create the order in the store's real Shiprocket account and record it.

    Raises :class:`commerce.shipping_shiprocket.ShiprocketError` when credentials
    are missing or the API rejects the order, so the caller can surface a real
    message and leave the order in Pending rather than silently stranding it.
    """
    from commerce import shipping_shiprocket as sr

    if not sr.is_configured(provider_row):
        raise sr.ShiprocketError(
            "Shiprocket isn't fully connected yet  in Shipping settings add your "
            "API email, API password, and the pickup location (the exact Pickup "
            "Address nickname saved in your Shiprocket dashboard)."
        )
    booking = sr.create_adhoc_order(provider_row, order)
    awb = (booking.get("awb") or "").strip()
    # A freshly created Shiprocket order is "Ready to Ship": the merchant still
    # has to request the pickup (which assigns an AWB if needed and schedules the
    # rider). Only that step moves it to "Pickup Scheduled".
    status = Shipment.Status.CREATED
    shipment = Shipment.objects.create(
        site=site,
        order=order,
        provider=provider_row,
        warehouse=provider_row.default_warehouse,
        awb=awb,
        courier_name=booking.get("courier_name") or "Shiprocket",
        tracking_url=booking.get("tracking_url") or "",
        status=status,
        meta={
            "provider": "shiprocket",
            "sr_order_id": booking.get("sr_order_id"),
            "shipment_id": booking.get("shipment_id"),
            "shiprocket": booking.get("raw"),
        },
    )
    if awb and awb != (order.tracking_id or ""):
        order.tracking_id = awb
        order.save(update_fields=["tracking_id"])
    return shipment


def auto_ship_if_enabled(site, order) -> bool:
    """Book a shipment immediately when the store has auto-ship enabled.

    Port of the reference's ``_auto_ship_if_enabled``. When auto-ship is on and a
    courier is configured, book the shipment now and move a still-pending order
    (``created`` / ``paid``) to ``confirmed``  so it leaves the Pending tab and
    appears in the courier tab, exactly as the reference does after a payment.

    Fail-safe (a booking error leaves the order untouched) and idempotent (an
    existing shipment is reused). Returns ``True`` when a shipment is in place.
    """
    if not auto_ship_enabled(site):
        return False
    if enabled_provider(site) is None:
        return False
    try:
        shipment = ensure_shipment(site, order)
    except Exception as exc:
        # A booking failure (e.g. Shiprocket credentials/pickup-location wrong)
        # must not fail the payment. Leave the order in Pending and log why, so
        # the owner can fix it and accept the order manually.
        logger.warning("auto-ship booking failed order=%s: %s", getattr(order, "number", order.pk), exc)
        return False
    if shipment is None:
        return False
    if order.status in (Order.Status.CREATED, Order.Status.PAID):
        order.status = Order.Status.CONFIRMED
        order.save(update_fields=["status", "confirmed_at"])
    return True


# ── Delhivery manual pickup (button + modal + time-window + generate/pickup) ──
#
# Shiprocket schedules its own pickup the moment create/adhoc runs, so it has no
# manual step  the Orders page never offers "Request Pickup" for a Shiprocket
# store, and :func:`request_delhivery_pickup` refuses any non-Delhivery provider.
# Delhivery is the opposite: the merchant asks for a warehouse-level pickup, and
# the courier only accepts the request inside a morning window and once a day.

class PickupError(Exception):
    """A Delhivery pickup couldn't be requested. Message is safe to show the owner."""


# Delhivery accepts pickup requests only between 12:00 AM and 11:00 AM (local).
PICKUP_WINDOW_END_HOUR = 11
# Delhivery grants one pickup slot per day; a second request the same day is
# rejected, so we stop it before spending the API call.
DAILY_PICKUP_LIMIT = 1


# Pickup date rules shared by both couriers' "Generate & Request Pickup":
# a request made at or after 12:00 noon can't be for today (the rider's day is
# already planned), so the earliest date is tomorrow; and no pickup may be
# booked further out than a week from today.
PICKUP_SAME_DAY_CUTOFF_HOUR = 12
PICKUP_MAX_DAYS_AHEAD = 7


def pickup_date_window(now=None):
    """``(earliest, latest)`` pickup dates (``date`` objects, store-local)."""
    from datetime import timedelta

    now = timezone.localtime(now) if now else timezone.localtime()
    today = now.date()
    earliest = today if now.hour < PICKUP_SAME_DAY_CUTOFF_HOUR else today + timedelta(days=1)
    return earliest, today + timedelta(days=PICKUP_MAX_DAYS_AHEAD)


def clean_pickup_date(raw: str) -> str:
    """Validate an owner-chosen pickup date against :func:`pickup_date_window`.

    Blank means "the earliest allowed date", so a request after noon never
    lands on today. Returns ``YYYY-MM-DD``; raises :class:`PickupError` with an
    owner-safe message for a malformed or out-of-window date.
    """
    from datetime import date as _date

    earliest, latest = pickup_date_window()
    value = (raw or "").strip()
    if not value:
        return earliest.isoformat()
    try:
        chosen = _date.fromisoformat(value)
    except ValueError:
        raise PickupError("Choose a valid pickup date.")
    fmt = lambda d: d.strftime("%d %b %Y")  # noqa: E731
    if chosen < earliest:
        if earliest > timezone.localdate():
            raise PickupError(
                "Pickups requested after 12:00 noon can be scheduled from tomorrow "
                f"({fmt(earliest)}) onwards. Choose a later date."
            )
        raise PickupError(f"The pickup date can't be in the past. Choose {fmt(earliest)} or later.")
    if chosen > latest:
        raise PickupError(
            f"A pickup can be scheduled at most {PICKUP_MAX_DAYS_AHEAD} days ahead "
            f"(up to {fmt(latest)}). Choose an earlier date."
        )
    return chosen.isoformat()


def delhivery_pickup_window_open() -> bool:
    """True inside Delhivery's 12:00 AM–11:00 AM pickup-request window (local)."""
    return timezone.localtime().hour < PICKUP_WINDOW_END_HOUR


def delhivery_pickups_today(provider, user) -> int:
    """How many pickups this user has already requested today for this store.

    Tracked in the provider row's ``settings`` JSON (only today's tally is kept,
    so it never grows) rather than by scanning shipments  one pickup request
    moves many shipments, so counting shipments would overcount the limit.
    """
    today = timezone.localdate().isoformat()
    uid = str(getattr(user, "id", "") or "")
    log = (provider.settings or {}).get("pickup_log") or {}
    return int((log.get(today) or {}).get(uid) or 0)


def _record_pickup(provider, user) -> None:
    today = timezone.localdate().isoformat()
    uid = str(getattr(user, "id", "") or "")
    settings = provider.settings or {}
    day = (settings.get("pickup_log") or {}).get(today) or {}
    day[uid] = int(day.get(uid) or 0) + 1
    # Keep only today's entries  yesterday's tally is irrelevant and unbounded
    # growth would bloat the row.
    settings["pickup_log"] = {today: day}
    provider.settings = settings
    provider.save(update_fields=["settings", "updated_at"])


def request_delhivery_pickup(
    site, user, orders, *, pickup_date, pickup_time, pickup_location, expected_package_count
) -> int:
    """Request one Delhivery pickup and move the given orders to Waiting for Pickup.

    A Delhivery pickup is warehouse-level: one API call brings a rider for every
    ready parcel, so this makes a single request and then flips each selected
    order's Delhivery shipment to ``PICKUP_REQUESTED``. Raises :class:`PickupError`
    (with an owner-safe message) when the store isn't on Delhivery, isn't
    connected, is outside the window, or has used today's slot; leaves every
    shipment untouched if the API call fails.
    """
    from commerce import shipping_delhivery as dl

    provider = enabled_provider(site)
    if not provider or (provider.provider or "").strip().lower() != "delhivery":
        raise PickupError(
            "Enable Delhivery in Shipping settings to request a pickup  Shiprocket "
            "schedules its own pickups automatically once an order is accepted."
        )
    if not dl.is_configured(provider):
        raise PickupError(
            "Delhivery isn't connected yet  add your API token in Shipping settings."
        )
    if not delhivery_pickup_window_open():
        raise PickupError(
            "Delhivery pickups can be requested only between 12:00 AM and 11:00 AM."
        )
    if delhivery_pickups_today(provider, user) >= DAILY_PICKUP_LIMIT:
        raise PickupError(
            "You've already requested a Delhivery pickup today  only one pickup "
            "request is allowed per day."
        )
    pickup_date = clean_pickup_date(pickup_date)

    try:
        result = dl.request_pickup(
            provider,
            pickup_location=pickup_location,
            pickup_date=pickup_date,
            pickup_time=pickup_time,
            expected_package_count=expected_package_count,
        )
    except dl.DelhiveryError as exc:
        raise PickupError(str(exc)) from exc

    _record_pickup(provider, user)

    now_iso = timezone.now().isoformat()
    today = timezone.localdate().isoformat()
    uid = str(getattr(user, "id", "") or "")
    moved = 0
    for order in orders:
        sh = (
            Shipment.objects.for_site(site)
            .filter(order=order, provider__provider="delhivery")
            .exclude(status=Shipment.Status.CANCELLED)
            .order_by("-created_at")
            .first()
        )
        if not sh:
            # Accepted while another courier was enabled: book it with
            # Delhivery now so the warehouse pickup covers it.
            sh = ensure_shipment(site, order)
            if not sh or sh.provider_key != "delhivery":
                continue
        sh.status = Shipment.Status.PICKUP_REQUESTED
        meta = sh.meta or {}
        meta["pickup_requested_at"] = now_iso
        meta["pickup_requested_date"] = today
        meta["pickup_date"] = pickup_date
        meta["pickup_requested_by"] = uid
        meta["delhivery_pickup"] = result if isinstance(result, dict) else {}
        sh.meta = meta
        sh.save(update_fields=["status", "meta"])
        moved += 1
    return moved


# ── Shiprocket manual pickup (assign AWB if needed → generate/pickup) ─────────
#
# Shiprocket's own flow is the mirror image of Delhivery's: it needs an AWB
# assigned to the shipment first, then a pickup generated per shipment. There is
# no morning window or daily cap  the merchant may schedule any date Shiprocket
# offers  so this shares Delhivery's PickupError but none of its time gates.

def shiprocket_courier_id(site) -> str:
    """The store's preferred Shiprocket courier id (from Shipping settings), or ""."""
    provider = enabled_provider(site)
    if not provider or (provider.provider or "").strip().lower() != "shiprocket":
        return ""
    return str((provider.settings or {}).get("shiprocket_courier_id") or "").strip()


# Most orders one popup prices at once (each is its own Shiprocket call).
MAX_RATE_QUOTES = 50


def _quote_payment(sr, order) -> dict:
    """How Shiprocket sees ``order``'s payment: the same rule create/adhoc
    uses, so the quote's COD flag always matches the booking's."""
    amounts = sr._payment_amounts(order)
    cod = amounts["payment_method"] == "COD"
    collect = round(amounts["sub_total"] + amounts["shipping_charges"] - amounts["total_discount"], 2)
    method = (order.payment_method or "").lower()
    if cod:
        label = "Partial COD" if (method == "partial" or getattr(order, "is_partial_payment", False)) else "COD"
        kind = "partial" if label == "Partial COD" else "cod"
    elif method == "store_credit":
        label, kind = "Store credit", "credit"
    else:
        label, kind = "Prepaid", "prepaid"
    return {"cod": cod, "collect": collect if cod else 0.0, "label": label, "kind": kind}


def shiprocket_rate_quotes(site, orders) -> dict:
    """Live Shiprocket couriers + rates for EACH order, for the bulk pickup popup.

    Shiprocket quotes one pickup → delivery pincode pair per call, priced by
    that parcel's weight and payment type (a COD parcel carries COD charges and
    only COD-capable couriers serve it). So every order is quoted on its own
    route; one order's list says nothing about another's. Calls run a few at a
    time so a batch of orders still answers quickly.

    Returns ``{"origin": pin, "orders": [...], "common": [...]}``. Each order row
    carries its destination, payment, weight, the courier already assigned (if
    any), its couriers and an ``error``. ``common`` lists the couriers that serve
    every quotable order, with the summed rate. Raises :class:`PickupError` when
    Shiprocket isn't connected or no origin pincode is known.
    """
    from concurrent.futures import ThreadPoolExecutor

    from django.db import connection

    from commerce import shipping_shiprocket as sr
    from commerce.models import Warehouse

    provider = enabled_provider(site)
    if not provider or (provider.provider or "").strip().lower() != "shiprocket" or not sr.is_configured(provider):
        raise PickupError("Shiprocket isn't connected. Connect it in Shipping settings.")
    try:
        # Refresh the token once here, never concurrently from the workers.
        sr.ensure_token(provider)
    except sr.ShiprocketError as exc:
        raise PickupError(str(exc)) from exc

    origin = sr.pickup_postcode(provider)
    if not origin:
        wh = (
            Warehouse.objects.for_site(site).filter(is_default=True).first()
            or Warehouse.objects.for_site(site).first()
        )
        origin = str((wh.pincode if wh else "") or "").strip()
    if not origin:
        raise PickupError(
            "Couldn't find your pickup pincode. Check the pickup location in Shipping settings."
        )

    rows, jobs = [], []
    for order in list(orders)[:MAX_RATE_QUOTES]:
        sh = (
            Shipment.objects.for_site(site)
            .filter(order=order, provider__provider="shiprocket")
            .exclude(status=Shipment.Status.CANCELLED)
            .order_by("-created_at")
            .first()
        )
        meta = (sh.meta or {}) if sh else {}
        pay = _quote_payment(sr, order)
        try:
            length, breadth, height, weight, _items = sr._dims_items_finance(order)
        except Exception:
            length = breadth = height = None
            weight = 0.5
        goods_value = float((order.subtotal or 0) + (getattr(order, "tax_amount", 0) or 0))
        row = {
            "id": str(order.pk),
            "number": str(order.display_number),
            "customer": order.customer_name or "",
            "city": order.shipping_city or "",
            "state": order.shipping_state or "",
            "pincode": order.shipping_pincode or "",
            "payment": pay["label"],
            "payment_kind": pay["kind"],
            "cod": pay["cod"],
            "collect": pay["collect"],
            "weight": round(float(weight or 0), 3),
            "assigned": (sh.courier_name or "Assigned") if (sh and (sh.awb or "").strip()) else "",
            "awb": (sh.awb or "") if sh else "",
            "couriers": [],
            "error": "",
        }
        rows.append(row)
        if row["assigned"]:
            continue  # courier already fixed; nothing to choose
        if not row["pincode"]:
            row["error"] = "This order has no delivery pincode."
            continue
        sr_order_id = str(meta.get("sr_order_id") or "").strip()
        jobs.append((row, {
            "pickup_postcode": origin,
            "delivery_postcode": row["pincode"],
            "cod": 1 if pay["cod"] else 0,
            "weight": row["weight"] or 0.5,
            # Shiprocket's own order (its stored weight and payment type) when
            # it exists; otherwise the package size and value we would book.
            "order_id": sr_order_id or None,
            "length": None if sr_order_id else length,
            "breadth": None if sr_order_id else breadth,
            "height": None if sr_order_id else height,
            "declared_value": None if sr_order_id else (goods_value or None),
        }))

    def quote(job):
        row, kwargs = job
        try:
            couriers = sr.check_serviceability(provider, **kwargs)
            if row["cod"]:
                # A COD parcel can only go with a courier that collects cash.
                couriers = [c for c in couriers if c.get("cod")]
            row["couriers"] = couriers
            if not couriers:
                row["error"] = (
                    "No courier collects cash on delivery to this pincode."
                    if row["cod"] else "No courier serves this pincode."
                )
        except sr.ShiprocketError as exc:
            row["error"] = str(exc)
        except Exception:
            logger.exception("shiprocket quote failed order=%s", row["id"])
            row["error"] = "Couldn't fetch rates for this order."
        finally:
            # Each worker thread gets its own DB connection (only opened if a
            # token refresh touched the provider row); never leak it.
            connection.close()

    if jobs:
        with ThreadPoolExecutor(max_workers=min(6, len(jobs))) as pool:
            list(pool.map(quote, jobs))

    # Couriers that can carry every order still needing one, with the total.
    open_rows = [r for r in rows if not r["assigned"]]
    common = []
    if open_rows and all(r["couriers"] for r in open_rows):
        by_id = [{str(c["id"]): c for c in r["couriers"]} for r in open_rows]
        shared = set(by_id[0])
        for m in by_id[1:]:
            shared &= set(m)
        for cid in shared:
            picks = [m[cid] for m in by_id]
            rates = [p["rate"] for p in picks]
            days = [p["days"] for p in picks if p["days"] is not None]
            common.append({
                "id": picks[0]["id"],
                "name": picks[0]["name"],
                "total": round(sum(rates), 2) if all(r is not None for r in rates) else None,
                "max_days": max(days) if days else None,
            })
        common.sort(key=lambda c: (c["total"] is None, c["total"] or 0, c["max_days"] or 99))

    return {"origin": origin, "orders": rows, "common": common}


def request_shiprocket_pickup(
    site, user, orders, *, pickup_date="", courier_id="", courier_map=None, failures=None
) -> int:
    """Assign AWBs where missing, schedule a Shiprocket pickup, and move the given
    orders to Pickup Scheduled.

    For each selected order's Shiprocket shipment: assign a courier/AWB if it has
    none (Shiprocket rejects a pickup otherwise), then call generate/pickup. The
    courier is chosen per order, in this order: the one picked for that order in
    the pickup popup (``courier_map``: order id → courier id), else one courier
    for the whole batch (``courier_id``), else the store's preferred courier from
    Shipping settings, else Shiprocket's own recommendation for that order. On
    success each shipment moves to ``PICKUP_REQUESTED``.

    Orders that fail are left in Ready to Ship; their reasons are appended to
    ``failures`` (a list of ``"Order #n: reason"``) when one is passed, so a
    partly successful batch can say which orders didn't go and why. Raises
    :class:`PickupError` (owner-safe message) when the store isn't on
    Shiprocket, isn't connected, or no order could be scheduled.
    """
    courier_map = {str(k): str(v or "").strip() for k, v in (courier_map or {}).items()}
    from commerce import shipping_shiprocket as sr

    provider = enabled_provider(site)
    if not provider or (provider.provider or "").strip().lower() != "shiprocket":
        raise PickupError("Enable Shiprocket in Shipping settings to request a Shiprocket pickup.")
    if not sr.is_configured(provider):
        raise PickupError(
            "Shiprocket isn't fully connected yet  add your API email, password "
            "and pickup location in Shipping settings."
        )

    # Collect each order's Shiprocket shipment and its Shiprocket shipment id.
    # An order in Ready to Ship that isn't in Shiprocket yet (accepted while
    # another courier was enabled, or moved to Ready to Ship by hand) is created
    # in Shiprocket right here, so the pickup never fails for that reason.
    targets = []  # (shipment, shipment_id)
    errors: list[str] = []
    for order in orders:
        sh = (
            Shipment.objects.for_site(site)
            .filter(order=order, provider__provider="shiprocket")
            .exclude(status=Shipment.Status.CANCELLED)
            .order_by("-created_at")
            .first()
        )
        sid = str((sh.meta or {}).get("shipment_id") or "").strip() if sh else ""
        if not sid:
            try:
                sh = ensure_shipment(site, order)
            except sr.ShiprocketError as exc:
                errors.append(f"Order #{order.display_number}: {exc}")
                continue
            sid = str((sh.meta or {}).get("shipment_id") or "").strip() if sh else ""
        if sh and sid:
            targets.append((sh, sid))
        elif not any(e.startswith(f"Order #{order.display_number}:") for e in errors):
            errors.append(f"Order #{order.display_number}: Shiprocket didn't return a shipment id for it.")
    if not targets:
        raise PickupError(errors[0] if errors else "Shiprocket couldn't create these orders.")

    # After 12:00 noon the earliest pickup is tomorrow, and never more than a
    # week out. A blank date resolves to the earliest allowed day.
    pickup_date = clean_pickup_date(pickup_date)

    # Courier: the popup choice wins, else the store's saved preference, else
    # Shiprocket's default (empty).
    chosen_courier = str(courier_id or "").strip() or str(
        (provider.settings or {}).get("shiprocket_courier_id") or ""
    ).strip()

    # Shiprocket takes one shipment per generate/pickup call and needs an AWB
    # already on it, so we assign the AWB (with the chosen courier) first, then
    # generate the pickup.
    now_iso = timezone.now().isoformat()
    uid = str(getattr(user, "id", "") or "")
    moved = 0
    for sh, sid in targets:
        order_courier = courier_map.get(str(sh.order_id)) or chosen_courier
        try:
            result = _shiprocket_pickup_one(sr, provider, sh, sid, pickup_date, order_courier)
        except PickupError as exc:
            errors.append(f"Order #{sh.order.display_number}: {exc}")
            continue
        sh.status = Shipment.Status.PICKUP_REQUESTED
        meta = sh.meta or {}
        result = result if isinstance(result, dict) else {}
        already = bool(result.get("already_queued"))
        meta["pickup_requested_at"] = now_iso
        meta["pickup_requested_by"] = uid
        meta["shiprocket_pickup"] = result
        # The date the rider comes: Shiprocket's confirmed date when it gave
        # one; for an already-queued shipment, the date recorded when it was
        # first queued, else the date asked for now.
        meta["pickup_date"] = (
            sr.pickup_scheduled_date(result)
            or (meta.get("pickup_date") if already else "")
            or pickup_date
        )
        meta["pickup_already_queued"] = already
        sh.meta = meta
        sh.save(update_fields=["status", "meta"])
        moved += 1

    if failures is not None:
        failures.extend(errors)
    if moved == 0:
        # Nothing scheduled: say why for every order (a few at most).
        if not errors:
            raise PickupError("Shiprocket couldn't schedule the pickup.")
        shown = errors[:3]
        more = len(errors) - len(shown)
        raise PickupError(" ".join(shown) + (f" (+{more} more)" if more > 0 else ""))
    return moved


def _shiprocket_pickup_one(sr, provider, sh, sid, pickup_date, courier_id=""):
    """Assign an AWB (if missing), then schedule one Shiprocket shipment's pickup.

    Follows Shiprocket's documented order  *Generate AWB for Shipment* precedes
    *Request for Shipment Pickup*  because a pickup for an unshipped ("NEW")
    order is refused with ``Awb not Assigned``. ``assign/awb`` uses ``courier_id``
    when given, else auto-selects the account's default courier; if it can't (no
    permission, no serviceable courier, low wallet balance), the reason is
    surfaced as-is.
    """
    if not (sh.awb or "").strip():
        try:
            res = sr.assign_awb(provider, sid, courier_id=courier_id or None)
        except sr.ShiprocketError as exc:
            # An earlier request may have assigned the AWB in Shiprocket without
            # our copy recording it; read it back instead of failing the pickup.
            res = _existing_awb(sr, provider, sid) if "already" in str(exc).lower() else None
            if not res:
                raise PickupError(str(exc)) from exc
        awb = (res.get("awb") or "").strip()
        if awb:
            sh.awb = awb
            fields = ["awb"]
            # The assigned courier's name, for the label and the tracking popup.
            raw = res.get("raw") if isinstance(res.get("raw"), dict) else {}
            block = raw.get("response") if isinstance(raw.get("response"), dict) else {}
            block = block.get("data") if isinstance(block.get("data"), dict) else block
            courier = str((block or {}).get("courier_name") or "").strip()
            if courier:
                sh.courier_name = courier[:120]
                fields.append("courier_name")
            # The courier's freight for this shipment (the logistics fee in
            # the orders export), when Shiprocket returns it with the AWB.
            charge = sr.freight_charge(raw)
            if charge is not None and sh.shipping_charge is None:
                sh.shipping_charge = charge
                fields.append("shipping_charge")
            sh.save(update_fields=fields)
            if sh.shipping_charge is None:
                record_freight_charge(sh)
            order = sh.order
            if awb != (order.tracking_id or ""):
                order.tracking_id = awb
                order.save(update_fields=["tracking_id"])

    try:
        return sr.generate_pickup(provider, sid, pickup_date=pickup_date)
    except sr.ShiprocketError as exc:
        raise PickupError(str(exc)) from exc


def record_freight_charge(sh) -> bool:
    """Look up and store a Shiprocket shipment's freight charge (the courier's
    fee for carrying it) on ``sh.shipping_charge``. Best-effort, never raises;
    returns True when a charge was stored.

    Shiprocket knows the freight once an AWB is assigned; it is read from the
    shipment details, else from the order details.
    """
    from commerce import shipping_shiprocket as sr

    if sh.shipping_charge is not None or not (sh.awb or "").strip():
        return False
    provider = sh.provider
    if provider is None or getattr(provider, "provider", "") != "shiprocket":
        return False
    meta = sh.meta or {}
    sr_meta = meta.get("shiprocket") if isinstance(meta.get("shiprocket"), dict) else {}
    charge = None
    try:
        if not sr.is_configured(provider):
            return False
        sid = str(meta.get("shipment_id") or "").strip()
        if sid:
            charge = sr.freight_charge(sr.get_shipment(provider, sid))
        sr_order_id = str(meta.get("sr_order_id") or sr_meta.get("order_id") or "").strip()
        if charge is None and sr_order_id:
            charge = sr.freight_charge(sr.get_order(provider, sr_order_id))
    except Exception:
        logger.warning("freight lookup failed shipment=%s", sh.pk)
        return False
    if charge is None:
        return False
    Shipment.objects.for_site(sh.site_id).filter(pk=sh.pk).update(shipping_charge=charge)
    sh.shipping_charge = charge
    return True


def logistics_fees(site, orders, *, lookup_budget_seconds: float = 12.0) -> dict:
    """``{order_id: Decimal}``: what the courier charged to carry each order.

    The sum of the stored freight on the order's shipments (a cancelled
    booking is not charged). Shiprocket shipments with an AWB but no stored
    freight yet are looked up live (and stored), within a small time budget so
    an export never hangs on a slow courier API; orders still unknown are left
    out of the result.
    """
    import time
    from decimal import Decimal

    ids = [getattr(o, "pk", o) for o in orders]
    if not ids:
        return {}
    shipments = list(
        Shipment.objects.for_site(site)
        .filter(order_id__in=ids)
        .exclude(status=Shipment.Status.CANCELLED)
        .select_related("provider")
        .order_by()
    )
    started = time.monotonic()
    out: dict = {}
    for sh in shipments:
        if sh.shipping_charge is None and (sh.awb or "").strip():
            if time.monotonic() - started < lookup_budget_seconds:
                record_freight_charge(sh)
        if sh.shipping_charge is not None:
            out[sh.order_id] = out.get(sh.order_id, Decimal("0.00")) + sh.shipping_charge
    return out


def _existing_awb(sr, provider, sid) -> dict | None:
    """The AWB Shiprocket already holds for shipment ``sid``, shaped like an
    ``assign_awb`` result, or ``None``."""
    try:
        data = sr.get_shipment(provider, sid)
    except sr.ShiprocketError:
        return None
    awb = str(data.get("awb") or data.get("awb_code") or "").strip()
    if not awb:
        return None
    courier = str(data.get("courier") or data.get("courier_name") or "").strip()
    return {"awb": awb, "raw": {"response": {"data": {"courier_name": courier}}}}


# ── Cancelling an order with the courier ─────────────────────────────────────

# Shipment states in which the parcel is still at the warehouse: cancelling the
# order cancels the shipment outright.
_CANCELLABLE_PRE_PICKUP = {
    Shipment.Status.PENDING,
    Shipment.Status.CREATED,
    Shipment.Status.BOOKED,
    Shipment.Status.PICKUP_REQUESTED,
}
# The courier has the parcel: a cancellation becomes a return to origin.
_WITH_COURIER = {
    Shipment.Status.PICKED_UP,
    Shipment.Status.IN_TRANSIT,
    Shipment.Status.OUT_FOR_DELIVERY,
    Shipment.Status.DELIVERY_FAILED,
}


def cancel_with_courier(site, order) -> str:
    """Propagate an owner's cancellation to the courier. Returns ``""`` or a
    warning safe to show the owner.

    Call it after the order itself is marked cancelled (so it sits in the
    Cancelled tab whatever the courier says):

    * **Before pickup** (Ready to Ship / Pickup Scheduled): the order is
      cancelled in Shiprocket too, which also drops its pickup, and the local
      shipment is marked cancelled.
    * **With the courier** (Shipped): Shiprocket is asked to cancel; the parcel
      then travels back, and the tracking webhook moves the order to RTO once
      Shiprocket reports it *returned* to origin. The shipment keeps its
      in-network status so those updates still apply.
    * Delivered / already returned / no shipment: nothing to do.

    Never raises: a courier refusal leaves the local cancellation in place and
    comes back as a warning so the owner can finish it in the courier dashboard.
    """
    shipments = list(
        Shipment.objects.for_site(site)
        .select_related("provider")
        .filter(order=order)
        .exclude(status__in=[Shipment.Status.CANCELLED, Shipment.Status.DELIVERED, Shipment.Status.RTO])
    )
    if not shipments:
        return ""
    now_iso = timezone.now().isoformat()
    warnings = []
    refused_in_transit = False
    for sh in shipments:
        meta = dict(sh.meta or {})
        if meta.get("owner_cancelled_at"):
            continue
        pre_pickup = sh.status in _CANCELLABLE_PRE_PICKUP
        error = ""
        if sh.provider_key == "shiprocket":
            error = _cancel_in_shiprocket(sh, meta)
        meta["owner_cancelled_at"] = now_iso
        if error:
            meta["courier_cancel_error"] = error[:300]
            warnings.append(error)
            refused_in_transit = refused_in_transit or sh.status in _WITH_COURIER
        else:
            meta.pop("courier_cancel_error", None)
        if pre_pickup and not error:
            sh.status = Shipment.Status.CANCELLED
        elif sh.status in _WITH_COURIER:
            # The parcel is on its way back; RTO is set by the courier's update.
            meta["return_expected"] = True
        sh.meta = meta
        sh.save(update_fields=["status", "meta", "updated_at"])
    if not warnings:
        return ""
    advice = (
        "The parcel is already with the courier: request its return (RTO) in your Shiprocket "
        "dashboard; it moves to the RTO tab once it is back."
        if refused_in_transit
        else "Cancel it in your Shiprocket dashboard so the courier doesn't pick it up."
    )
    return (
        f"Order #{order.display_number} is cancelled here, but Shiprocket didn't accept the cancellation "
        f"({warnings[0].rstrip('.')}). {advice}"
    )


def _cancel_in_shiprocket(sh, meta) -> str:
    """Cancel the shipment's order in Shiprocket. Returns ``""`` or the reason."""
    from commerce import shipping_shiprocket as sr

    provider = sh.provider
    if provider is None or not sr.is_configured(provider):
        return "Shiprocket isn't connected"
    sr_meta = meta.get("shiprocket") if isinstance(meta.get("shiprocket"), dict) else {}
    sr_order_id = str(meta.get("sr_order_id") or sr_meta.get("order_id") or "").strip()
    if not sr_order_id:
        # A local booking that never reached Shiprocket: nothing to cancel there.
        return ""
    try:
        result = sr.cancel_orders(provider, [sr_order_id])
    except sr.ShiprocketError as exc:
        text = str(exc)
        if "already" in text.lower() and "cancel" in text.lower():
            return ""
        logger.warning("shiprocket cancel refused order=%s: %s", sh.order_id, text)
        return text.replace("Shiprocket rejected the request: ", "")
    except Exception as exc:  # network etc.
        logger.warning("shiprocket cancel failed order=%s: %s", sh.order_id, exc)
        return "Shiprocket couldn't be reached"
    meta["shiprocket_cancel"] = result if isinstance(result, dict) else {}
    meta["shiprocket_cancelled_at"] = timezone.now().isoformat()
    return ""


# ── Applying a tracking update (webhook or manual sync) ──────────────────────

# Free-text courier status → our Shipment.Status. Kept liberal because couriers
# phrase the same milestone many ways.
_STATUS_MAP = [
    ("delivered", Shipment.Status.DELIVERED),
    ("rto", Shipment.Status.RTO),
    ("return to origin", Shipment.Status.RTO),
    ("returned", Shipment.Status.RTO),
    ("out for delivery", Shipment.Status.OUT_FOR_DELIVERY),
    ("undelivered", Shipment.Status.DELIVERY_FAILED),
    ("failed", Shipment.Status.DELIVERY_FAILED),
    ("in transit", Shipment.Status.IN_TRANSIT),
    ("intransit", Shipment.Status.IN_TRANSIT),
    ("shipped", Shipment.Status.IN_TRANSIT),
    ("picked up", Shipment.Status.PICKED_UP),
    ("pickup", Shipment.Status.PICKUP_REQUESTED),
    ("cancelled", Shipment.Status.CANCELLED),
    ("canceled", Shipment.Status.CANCELLED),
]


def map_courier_status(raw: str) -> str:
    s = (raw or "").strip().lower()
    for needle, mapped in _STATUS_MAP:
        if needle in s:
            return mapped
    return ""


# Shipment status → which order status it should drive.
_ORDER_STATUS_FOR_SHIPMENT = {
    Shipment.Status.PICKED_UP: Order.Status.SHIPPED,
    Shipment.Status.IN_TRANSIT: Order.Status.SHIPPED,
    Shipment.Status.OUT_FOR_DELIVERY: Order.Status.SHIPPED,
    Shipment.Status.DELIVERED: Order.Status.DELIVERED,
    Shipment.Status.CANCELLED: Order.Status.CANCELLED,
}


def normalise_payload(raw: dict) -> dict:
    """Coerce an arbitrary courier body into the shape the tracking popup wants:
    ``{current_status, awb, courier_name, etd, scans:[...]}``. Passes an already
    normalised body through untouched."""
    raw = raw or {}
    scans = raw.get("scans")
    if not isinstance(scans, list):
        # Shiprocket-style ``scan_detail`` / Delhivery-style ``Scans`` fallbacks.
        scans = raw.get("scan_detail") or raw.get("Scans") or []
        norm_scans = []
        for s in scans if isinstance(scans, list) else []:
            if not isinstance(s, dict):
                continue
            norm_scans.append(
                {
                    "status": s.get("status") or s.get("sr-status-label") or s.get("ScanType") or "",
                    "sr-status-label": s.get("sr-status-label") or s.get("status") or "",
                    "activity": s.get("activity") or s.get("Instructions") or s.get("remark") or "",
                    "location": s.get("location") or s.get("ScannedLocation") or s.get("city") or "",
                    "date": s.get("date") or s.get("ScanDateTime") or s.get("updated_at") or "",
                }
            )
        scans = norm_scans
    return {
        "current_status": raw.get("current_status")
        or raw.get("shipment_status")
        or raw.get("status")
        or "",
        "shipment_status": raw.get("shipment_status") or raw.get("current_status") or "",
        "awb": raw.get("awb") or raw.get("awb_code") or raw.get("waybill") or "",
        "courier_name": raw.get("courier_name") or raw.get("courier") or "",
        "etd": raw.get("etd") or raw.get("expected_delivery_date") or "",
        "scans": scans,
        "order_id": str(raw.get("order_id") or ""),
    }


def record_event(site, *, provider: str, raw: dict, signature_ok: bool = False) -> CourierWebhookEvent:
    """Store a courier update and advance the matching shipment/order.

    Matching mirrors the reference: by an ``order_id`` key in the body, else by
    the shipment AWB. The stored ``raw_payload`` is the normalised shape so
    :func:`order_tracking_json`'s response renders directly in the popup.
    """
    payload = normalise_payload(raw)
    awb = (payload.get("awb") or "").strip()
    status_label = payload.get("current_status") or ""

    order = None
    order_id = (payload.get("order_id") or "").strip()
    if order_id:
        order = _order_by_courier_ref(site, order_id)
    shipment = None
    if awb:
        shipment = Shipment.objects.for_site(site).filter(awb=awb).order_by("-created_at").first()
    if shipment and not order:
        order = shipment.order
    if order and not shipment:
        shipment = (
            Shipment.objects.for_site(site).filter(order=order).order_by("-created_at").first()
        )

    event = CourierWebhookEvent.objects.create(
        site=site,
        order=order,
        shipment=shipment,
        provider=(provider or "").lower(),
        topic=payload.get("current_status") or "",
        status=status_label,
        awb=awb,
        raw_payload=payload,
        signature_ok=bool(signature_ok),
    )

    mapped = map_courier_status(status_label)
    if shipment and mapped:
        shipment.status = mapped
        if payload.get("courier_name") and not shipment.courier_name:
            shipment.courier_name = payload["courier_name"]
        shipment.save(update_fields=["status", "courier_name"])
    if order and mapped in _ORDER_STATUS_FOR_SHIPMENT:
        new_status = _ORDER_STATUS_FOR_SHIPMENT[mapped]
        if order.status != new_status:
            order.status = new_status
            order.save(update_fields=["status", *_stamp_fields(new_status)])
    return event


def _stamp_fields(status: str) -> list[str]:
    # Order.save() stamps the transition timestamp; naming it in update_fields
    # keeps the write narrow.
    return {
        Order.Status.SHIPPED: ["shipped_at"],
        Order.Status.DELIVERED: ["delivered_at"],
        Order.Status.CANCELLED: ["cancelled_at"],
    }.get(status, [])


def _order_by_courier_ref(site, ref: str):
    """The order a courier's ``order_id`` names: the order number (an ordinary
    order), a replacement's ``<number>-RE`` / ``-RE<n>`` (or the older
    ``-R<n>``, see ``Order.courier_ref``) or the order's UUID."""
    ref = (ref or "").strip()
    parsed = Order.parse_courier_ref(ref)
    if parsed:
        number, seq = parsed
        return Order.objects.for_site(site).filter(number=number, replacement_seq=seq).first()
    if _looks_like_uuid(ref):
        return Order.objects.for_site(site).filter(pk=ref).first()
    return None


def _looks_like_uuid(value: str) -> bool:
    return len(value) >= 32 and "-" in value


def latest_event_for_order(site, order) -> CourierWebhookEvent | None:
    """The most recent tracking event for an order  by FK, then by AWB, then
    by an ``order_id`` inside the payload (mirrors ``order_tracking_json``)."""
    evt = (
        CourierWebhookEvent.objects.for_site(site)
        .filter(order=order)
        .order_by("-received_at")
        .first()
    )
    if evt:
        return evt
    shipment = (
        Shipment.objects.for_site(site).filter(order=order).order_by("-created_at").first()
    )
    if shipment and (shipment.awb or "").strip():
        evt = (
            CourierWebhookEvent.objects.for_site(site)
            .filter(awb=shipment.awb.strip())
            .order_by("-received_at")
            .first()
        )
        if evt:
            return evt
    for candidate in (
        CourierWebhookEvent.objects.for_site(site).order_by("-received_at")[:300]
    ):
        if str((candidate.raw_payload or {}).get("order_id") or "").strip() in order.courier_refs:
            return candidate
    return None


# ── Shiprocket wallet balance (Orders + Shipping pages, low-balance alert) ────

# Below this the owner is warned: on the page, by email and in the bell.
WALLET_LOW_THRESHOLD = 100
# A live balance is reused this long, so page loads don't each call Shiprocket.
_WALLET_CACHE_SECONDS = 120


def _wallet_cache_key(site) -> str:
    return f"shiprocket:wallet:{site.pk}"


def shiprocket_wallet(site, *, refresh: bool = False) -> dict:
    """The store's live Shiprocket wallet, for the balance box.

    Returns ``{"ok": True, "balance": Decimal, "low": bool, "threshold": int,
    "checked_at": datetime}`` or ``{"ok": False, "error": str,
    "needs_setup": bool}``. A fresh reading (not the 2-minute cache) also runs
    :func:`_check_wallet_low`, which emails and notifies the owner once when the
    balance drops below ₹100.
    """
    from decimal import Decimal

    from django.core.cache import cache

    from commerce import shipping_shiprocket as sr

    provider = (
        ShippingProvider.objects.for_site(site).filter(provider=ShippingProvider.Provider.SHIPROCKET).first()
    )
    if provider is None or not sr.has_credentials(provider):
        return {
            "ok": False,
            "needs_setup": True,
            "error": "Add your Shiprocket API email and password in Shipping settings to see your wallet balance.",
        }

    key = _wallet_cache_key(site)
    if not refresh:
        cached = cache.get(key)
        if cached:
            balance = Decimal(cached["balance"])
            return {
                "ok": True,
                "balance": balance,
                "low": balance < WALLET_LOW_THRESHOLD,
                "threshold": WALLET_LOW_THRESHOLD,
                "checked_at": datetime.fromisoformat(cached["checked_at"]),
            }
    try:
        balance = sr.wallet_balance(provider)
    except sr.ShiprocketError as exc:
        text = str(exc)
        if text.startswith("authentication failed"):
            text = "Shiprocket didn't accept the saved API email/password. Check them in Shipping settings."
        else:
            text = text.replace("Shiprocket rejected the request: ", "")
        return {"ok": False, "needs_setup": False, "error": text}
    except Exception:
        logger.exception("shiprocket wallet fetch failed site=%s", site.pk)
        return {"ok": False, "needs_setup": False, "error": "Couldn't reach Shiprocket. Try again in a minute."}

    now = timezone.now()
    cache.set(key, {"balance": str(balance), "checked_at": now.isoformat()}, _WALLET_CACHE_SECONDS)
    _check_wallet_low(site, provider, balance)
    return {
        "ok": True,
        "balance": balance,
        "low": balance < WALLET_LOW_THRESHOLD,
        "threshold": WALLET_LOW_THRESHOLD,
        "checked_at": now,
    }


def _check_wallet_low(site, provider, balance) -> None:
    """Alert the owner once per low spell: when the balance first drops below
    ₹100, send an email and a bell notification; when it is topped back up to
    ₹100 or more, re-arm the alert for the next time."""
    settings = dict(provider.settings or {})
    alerted = bool(settings.get("wallet_low_alerted_at"))
    if balance >= WALLET_LOW_THRESHOLD:
        if alerted:
            settings.pop("wallet_low_alerted_at", None)
            settings.pop("wallet_low_alerted_balance", None)
            provider.settings = settings
            provider.save(update_fields=["settings", "updated_at"])
        return
    if alerted:
        return
    # Mark first, so two concurrent readings can't both send the alert.
    settings["wallet_low_alerted_at"] = timezone.now().isoformat()
    settings["wallet_low_alerted_balance"] = str(balance)
    provider.settings = settings
    provider.save(update_fields=["settings", "updated_at"])
    _send_wallet_low_alert(site, balance)


def _send_wallet_low_alert(site, balance) -> None:
    """Bell notification + email to the store owner. Best effort."""
    from accounts import notify

    from commerce import bell_links

    amount = f"₹{balance:,.2f}"
    try:
        url = bell_links.page_url("shipping", site)
    except Exception:
        url = ""
    headline = "Shiprocket wallet balance is low"
    description = (
        f"Your Shiprocket wallet has {amount} left (below ₹{WALLET_LOW_THRESHOLD}). "
        "Recharge it in your Shiprocket dashboard so couriers can still be assigned and pickups scheduled."
    )
    notify.push_to_owner(
        site, headline=headline, description=description, kind="system", level="warning", action_url=url,
    )

    owner = getattr(site, "owner", None)
    to = getattr(owner, "effective_notification_email", "") if owner is not None else ""
    if not to:
        return
    try:
        from accounts.emails import _send

        _send(
            to,
            f"Low Shiprocket wallet balance · {site.name}",
            "notification/wallet_low",
            {
                "site_name": site.name,
                "balance": amount,
                "threshold": f"₹{WALLET_LOW_THRESHOLD}",
                "shipping_url": url,
                "when": timezone.now(),
                "recipient": to,
            },
        )
    except Exception:
        logger.exception("wallet low email failed site=%s", site.pk)


def check_all_shiprocket_wallets(limit: int = 500) -> int:
    """Beat: read every enabled Shiprocket store's wallet so a low balance is
    caught (and the owner alerted) even when nobody opens the dashboard.
    Returns how many stores were checked."""
    from commerce import shipping_shiprocket as sr

    from core.queries import sorted_rows

    checked = 0
    # Joined to each store's whole ``content`` JSON; the optimizer may drive
    # the join from Site and sort, so only the keys go through the sort.
    rows = sorted_rows(
        ShippingProvider.objects.across_all_sites()
        .select_related("site")
        .filter(provider=ShippingProvider.Provider.SHIPROCKET, enabled=True, site__is_deleted=False)
        .order_by("pk"),
        limit=limit,
    )
    for provider in rows:
        if not sr.has_credentials(provider):
            continue
        try:
            shiprocket_wallet(provider.site, refresh=True)
            checked += 1
        except Exception:
            logger.exception("wallet check failed site=%s", provider.site_id)
    return checked


# ── Pushing an edited order to the courier (order edit page) ────────────────

def sync_order_edit(site, order, *, items_changed: bool, address_changed: bool) -> list[str]:
    """Send the owner's edits of ``order`` to its Shiprocket order.

    * Items / quantities / prices / totals → ``update/adhoc``, which Shiprocket
      accepts only before an AWB (courier) is assigned.
    * Customer name / phone / email / delivery address → ``address/update``.
      Once the parcel is with the courier the address can't change any more.

    Returns owner-facing warnings for anything that couldn't be applied (empty
    when everything synced or there was nothing to sync). Never raises.
    """
    from commerce import shipping_shiprocket as sr

    if not (items_changed or address_changed):
        return []
    sh = (
        Shipment.objects.for_site(site)
        .select_related("provider")
        .filter(order=order, provider__provider="shiprocket")
        .exclude(status=Shipment.Status.CANCELLED)
        .order_by("-created_at")
        .first()
    )
    if sh is None:
        return []  # not booked with Shiprocket yet: it is sent fresh on booking
    provider = sh.provider
    if provider is None or not sr.is_configured(provider):
        return ["Shiprocket isn't connected, so these changes weren't sent to it. Update the order in your Shiprocket dashboard."]
    meta = dict(sh.meta or {})
    sr_meta = meta.get("shiprocket") if isinstance(meta.get("shiprocket"), dict) else {}
    sr_order_id = str(meta.get("sr_order_id") or sr_meta.get("order_id") or "").strip()
    with_courier = sh.status in _WITH_COURIER or sh.status in (Shipment.Status.DELIVERED, Shipment.Status.RTO)
    num = f"#{order.display_number}"
    warnings: list[str] = []

    if items_changed:
        if (sh.awb or "").strip() or with_courier:
            warnings.append(
                f"Order {num}: Shiprocket only accepts item, quantity and amount changes before a courier is "
                "assigned, and this order already has one. Its label and invoice here use the new details; "
                "update the order in your Shiprocket dashboard (or cancel and re-book it) to change it there."
            )
        else:
            try:
                result = sr.update_adhoc_order(provider, order)
                meta["shiprocket_updated_at"] = timezone.now().isoformat()
                new_sid = str((result or {}).get("shipment_id") or "").strip()
                if new_sid:
                    meta["shipment_id"] = new_sid
            except sr.ShiprocketError as exc:
                warnings.append(f"Order {num}: Shiprocket didn't accept the item changes ({exc}).")
            except Exception:
                logger.exception("shiprocket update/adhoc failed order=%s", order.pk)
                warnings.append(f"Order {num}: Shiprocket couldn't be reached, so the item changes weren't sent. Save again in a minute.")

    if address_changed:
        if with_courier:
            warnings.append(
                f"Order {num} is already with the courier, so Shiprocket can't change its delivery address. "
                "Contact the courier through your Shiprocket dashboard to redirect it."
            )
        elif not sr_order_id:
            warnings.append(f"Order {num}: no Shiprocket order id is stored, so the address wasn't sent to Shiprocket.")
        else:
            try:
                sr.update_delivery_address(provider, sr_order_id, order)
                meta["shiprocket_address_updated_at"] = timezone.now().isoformat()
            except sr.ShiprocketError as exc:
                warnings.append(f"Order {num}: Shiprocket didn't accept the new address ({exc}).")
            except Exception:
                logger.exception("shiprocket address/update failed order=%s", order.pk)
                warnings.append(f"Order {num}: Shiprocket couldn't be reached, so the address wasn't sent. Save again in a minute.")

    sh.meta = meta
    sh.save(update_fields=["meta", "updated_at"])
    return warnings
