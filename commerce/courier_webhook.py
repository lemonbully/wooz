"""Inbound courier tracking webhooks  Shiprocket parity.

A port of the reference platform's Shiprocket webhook (1matrix ``website``:
``courier_webhook`` + ``_extract_shiprocket_updates`` + the status maps), adapted
to OneWooz's models (:class:`commerce.models.Shipment`,
:class:`commerce.models.CourierWebhookEvent`, :class:`commerce.models.ShippingProvider`).
Behaviour matches the reference:

* the store is resolved from the request host (its own storefront domain), the
  provider config must exist and be enabled;
* the request is authenticated by a token (header / query / path) matched against
  the provider's configured inbound token, or an HMAC-SHA256 of the body against
  a configured secret; with neither configured it is accepted (as the reference
  did) so a store can go live before setting a secret;
* the payload is parsed, each update's status is resolved (numeric Shiprocket
  ``status_id`` preferred over fragile text), the shipment is matched by AWB /
  Shiprocket ``shipment_id`` / our local order id, and the shipment + order
  statuses are advanced  **never downgraded or cancelled** beyond the reference
  rules;
* every event is written to :class:`CourierWebhookEvent` for the tracking popup,
  and the owner gets a dashboard notification via :func:`commerce.activity.record`.

Processing is synchronous (per-store courier volume is low), so the endpoint
returns after the update  no Celery hop, unlike the reference's task enqueue.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from commerce import activity
from commerce.models import CourierWebhookEvent, Order, Shipment, ShippingProvider

logger = logging.getLogger(__name__)

# Shiprocket numeric status_id → OneWooz Shipment.Status. Ported from the
# reference; the reference's "manifested" (pre-pickup) maps to our
# "pickup_requested" since OneWooz has no separate manifested state.
_MANIFEST = Shipment.Status.PICKUP_REQUESTED
# A return to origin that is still on its way back (RTO Initiated, RTO In
# Transit, RTO OFD/NDR, Reached Back at Seller City...). Not a stored status:
# the shipment stays "in transit" with ``meta.return_in_progress`` set, and the
# order only reaches the RTO tab once the parcel is actually *returned*
# (RTO Delivered / RTO Acknowledged → ``rto``).
RTO_IN_PROGRESS = "rto_in_transit"
# An update that must not move the shipment (e.g. "Cancellation Requested").
_NO_CHANGE = ""
_SHIPROCKET_STATUS_ID_MAP = {
    1: _MANIFEST, 2: _MANIFEST, 3: _MANIFEST, 4: _MANIFEST, 5: _MANIFEST,
    11: _MANIFEST, 13: _MANIFEST, 15: _MANIFEST, 19: _MANIFEST, 20: _MANIFEST,
    27: _MANIFEST, 52: _MANIFEST,
    10: "rto", 14: "rto",
    9: RTO_IN_PROGRESS, 29: RTO_IN_PROGRESS, 31: RTO_IN_PROGRESS, 36: RTO_IN_PROGRESS,
    40: RTO_IN_PROGRESS, 41: RTO_IN_PROGRESS, 46: RTO_IN_PROGRESS, 75: RTO_IN_PROGRESS,
    78: RTO_IN_PROGRESS,
    42: "picked_up",
    6: "in_transit", 18: "in_transit", 22: "in_transit", 28: "in_transit",
    32: "in_transit", 38: "in_transit", 39: "in_transit",
    47: "in_transit", 48: "in_transit",
    50: "in_transit", 51: "in_transit", 53: "in_transit", 54: "in_transit",
    55: "in_transit", 56: "in_transit",
    17: "out_for_delivery",
    7: "delivered", 23: "delivered", 26: "delivered", 33: "delivered", 43: "delivered",
    8: "cancelled", 34: "cancelled", 35: "cancelled", 37: "cancelled", 45: "cancelled",
    16: _NO_CHANGE,
    12: "delivery_failed", 21: "delivery_failed", 24: "delivery_failed", 25: "delivery_failed",
}

# Shipment statuses that mean "in the courier network" → set shipped timestamp.
_IN_NETWORK = {"picked_up", "in_transit", "out_for_delivery", "delivered", "pickup_requested"}


def _canon_shiprocket_status_id(status_id):
    try:
        return _SHIPROCKET_STATUS_ID_MAP.get(int(status_id))
    except (TypeError, ValueError):
        return None


def _canon_shipment_status(raw: str | None) -> str:
    """Normalise a provider status string to a OneWooz Shipment.Status value."""
    s = (raw or "").strip().lower()
    if not s:
        return "in_transit"
    s2 = s.replace(" ", "_").replace("-", "_").replace("/", "_").replace("__", "_")
    # out_for_delivery must precede delivered ("out_for_delivery" contains it).
    # RTO first: "RTO Delivered" / "RTO In Transit" contain later keywords.
    # Only a parcel actually back at the seller is "rto"; anything earlier in
    # the return journey is still travelling.
    if "rto" in s2 or "return_to_origin" in s2 or "returned" in s2 or s2.startswith("return_"):
        if (
            "returned" in s2
            or "rto_delivered" in s2
            or "rto_received" in s2
            or "rto_acknowledged" in s2
            or "rto_completed" in s2
        ):
            return "rto"
        return RTO_IN_PROGRESS
    if "cancellation_requested" in s2:
        return _NO_CHANGE
    if "out_for_delivery" in s2 or "outfordelivery" in s2 or s2 == "ofd":
        return "out_for_delivery"
    if "undelivered" in s2:
        return "delivery_failed"
    if "delivered" in s2 or s2 in ("dlv", "del"):
        return "delivered"
    if "picked_up" in s2:
        return "picked_up"
    if "pickup" in s2 or "manifest" in s2:
        return _MANIFEST
    if "cancel" in s2:
        return "cancelled"
    if "undelivered" in s2 or "delivery_failed" in s2 or s2.endswith("_failed") or s2 == "failed":
        return "delivery_failed"
    if "in_transit" in s2 or "transit" in s2 or s2 == "shipped":
        return "in_transit"
    if s2 in ("created", "pending", "generated", "ready"):
        return "created"
    return s2[:50] or "in_transit"


def _first_str(d, keys) -> str:
    for k in keys:
        try:
            v = d.get(k)
        except Exception:
            v = None
        if v is None:
            continue
        if isinstance(v, (int, float)):
            v = str(v)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def extract_shiprocket_updates(payload) -> list[dict]:
    """Port of the reference extractor: a list of normalised update dicts."""
    entries: list[dict] = []

    def _add(obj):
        if isinstance(obj, dict):
            entries.append(obj)

    if isinstance(payload, list):
        for it in payload:
            _add(it)
    elif isinstance(payload, dict):
        for key in ("data", "payload", "shipments", "shipment", "event", "events", "result", "results"):
            v = payload.get(key)
            if isinstance(v, list):
                for it in v:
                    _add(it)
            elif isinstance(v, dict):
                entries.append(v)
        if not entries:
            _add(payload)
    else:
        return []

    out: list[dict] = []
    for e in entries:
        obj = e
        try:
            for wrap_key in ("data", "shipment", "payload"):
                inner = obj.get(wrap_key) if isinstance(obj, dict) else None
                if isinstance(inner, dict):
                    obj = inner
        except Exception:
            obj = e

        awb = _first_str(obj, ["awb", "awb_code", "waybill"])
        shipment_id = _first_str(obj, ["shipment_id", "shipmentId", "shiprocket_shipment_id"])
        local_order_id = _first_str(obj, ["order_id", "orderId", "channel_order_id", "channelOrderId"])
        sr_order_id = _first_str(obj, ["sr_order_id", "srOrderId"])

        status = None
        sid_raw = _first_str(obj, ["shipment_status_id", "shipmentStatusId", "current_status_id", "currentStatusId"])
        if sid_raw:
            status = _canon_shiprocket_status_id(sid_raw)
        if status is None:
            raw_status = _first_str(obj, [
                "current_status", "Current_Status", "shipment_status", "Shipment_Status",
                "status", "Status", "event", "Event",
            ])
            if not raw_status:
                try:
                    scans = obj.get("scans") or []
                    if isinstance(scans, list) and scans:
                        last = scans[-1] if isinstance(scans[-1], dict) else {}
                        raw_status = _first_str(last, ["activity"])
                except Exception:
                    pass
            status = _canon_shipment_status(raw_status) if raw_status else "in_transit"

        candidates = [v for v in (awb, shipment_id) if v]
        if not candidates and not local_order_id:
            continue
        out.append({
            "candidates": candidates,
            "awb": awb,
            "shipment_id": shipment_id,
            "local_order_id": local_order_id,
            "sr_order_id": sr_order_id,
            "status": status,
            "raw": obj,
        })
    return out


def verify(provider: ShippingProvider, *, raw_body: bytes, headers: dict, token: str) -> bool:
    """Return whether the request is authenticated for ``provider``.

    Mirrors the reference: a configured inbound token matched against a header /
    query / path token, or an HMAC of the body against a configured secret. With
    neither configured, accept  so a store can receive tracking before it has
    set a secret. Config lives on ``provider.settings`` (a JSON blob).
    """
    cfg = provider.settings if isinstance(provider.settings, dict) else {}
    expected = (cfg.get("webhook_token") or cfg.get("inbound_api_key") or "").strip()
    secret = (cfg.get("webhook_hmac_secret") or "").strip()

    hdr_token = (
        (headers.get("x-shiprocket-webhook-token") or "").strip()
        or (headers.get("x-shiprocket-token") or "").strip()
        or (headers.get("x-webhook-token") or "").strip()
        or (headers.get("x-api-key") or "").strip()
    )
    auth = (headers.get("authorization") or "").strip()

    if expected:
        for candidate in (token, hdr_token):
            if candidate and hmac.compare_digest(candidate, expected):
                return True
        if auth and (
            hmac.compare_digest(auth, expected)
            or hmac.compare_digest(auth, f"Token {expected}")
            or hmac.compare_digest(auth, f"Bearer {expected}")
        ):
            return True
        # A token is configured but none matched → reject.
        return False

    if secret:
        sent = (
            headers.get("x-signature")
            or headers.get("x-hub-signature-256")
            or headers.get("x-shiprocket-signature")
            or headers.get("x-webhook-signature")
            or ""
        )
        mac = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        return bool(sent) and (sent.endswith(mac) or sent == mac)

    # Nothing configured  accept (reference behaviour).
    return True



# ── Inbound webhook token / URL (Shipping settings → Tracking webhook card) ───

def ensure_webhook_token(provider: ShippingProvider, *, rotate: bool = False) -> str:
    """The provider's inbound webhook token, generated on first use.

    Hex only on purpose: Shiprocket's dashboard rejects webhook URLs containing
    the substrings ``sr`` / ``kr`` / ``shiprocket`` / ``kartrocket``, and a
    URL-safe base64 token could contain them by chance. ``rotate`` replaces it
    (the old token stops working immediately).
    """
    import secrets

    cfg = provider.settings if isinstance(provider.settings, dict) else {}
    token = (cfg.get("webhook_token") or "").strip()
    if token and not rotate:
        return token
    token = secrets.token_hex(20)
    cfg = {**cfg, "webhook_token": token}
    cfg.pop("inbound_api_key", None)
    provider.settings = cfg
    if provider.pk:
        provider.save(update_fields=["settings", "updated_at"])
    return token


def webhook_site_key(site) -> str:
    """The site's identifier in the public webhook path: UUID hex without
    dashes, so only 0-9a-f appear and no Shiprocket-forbidden keyword can."""
    return site.pk.hex


def webhook_urls(request, site, provider: ShippingProvider) -> dict:
    """Absolute webhook URLs for the Shipping settings page.

    ``plain`` expects the token in the ``x-api-key`` header (Shiprocket's
    "Token" field); ``with_token`` carries it in the path, for dashboards that
    can't send headers. Both live on the dashboard host, never a storefront
    host, so a store slug containing a forbidden keyword can't break them.
    """
    from django.conf import settings as dj_settings
    from django.urls import reverse

    key = webhook_site_key(site)
    token = ensure_webhook_token(provider)

    def _abs(path: str) -> str:
        url = request.build_absolute_uri(path)
        if not dj_settings.DEBUG and url.startswith("http://"):
            url = "https://" + url[len("http://"):]
        return url

    return {
        "plain": _abs(reverse("courier_tracking_webhook", args=[key])),
        "with_token": _abs(reverse("courier_tracking_webhook_token", args=[key, token])),
        "token": token,
    }


def resolve_site(site_key: str):
    """Find a live store by the key used in its webhook URL (hex or dashed UUID)."""
    import uuid

    from tenancy.models import Site

    try:
        pk = uuid.UUID(str(site_key or "").strip())
    except (TypeError, ValueError, AttributeError):
        return None
    return Site.objects.filter(pk=pk, is_deleted=False).first()


# ── Applying one normalised update ────────────────────────────────────────────

_VALID_SHIPMENT_STATUSES = set(Shipment.Status.values)
_IN_NETWORK_ONLY = {"picked_up", "in_transit", "out_for_delivery", "delivery_failed"}
_PRE_NETWORK = {"pending", "created", "booked", "pickup_requested"}


def _allowed_transition(current: str, new: str) -> bool:
    """Guard against out-of-order or stale courier pushes.

    Couriers retry and Shiprocket can deliver events out of sequence, so a late
    "Pickup Scheduled" must not drag an in-transit parcel back to Ready to Ship,
    and a delivered/returned/cancelled shipment is final (delivered may still
    become RTO, which happens when a delivery is reversed).
    """
    current = (current or "").strip().lower()
    if current == new:
        return False
    if current == "rto":
        return False
    if current == "cancelled":
        # A cancelled shipment can still come back to the seller (an order
        # cancelled after pickup); nothing else revives it.
        return new == "rto"
    if current == "delivered":
        return new == "rto"
    if current in _IN_NETWORK_ONLY and new in _PRE_NETWORK:
        return False
    return True


def _find_shipment(site, upd: dict):
    """Match an update to one of this store's shipments.

    AWB first, then the order id we sent Shiprocket at booking (our order
    *number*), then Shiprocket's own shipment / order ids kept in ``meta``.
    """
    base = Shipment.objects.for_site(site).select_related("order", "provider")
    candidates = [str(x).strip() for x in (upd.get("candidates") or []) if str(x).strip()]
    if candidates:
        shipment = base.filter(awb__in=candidates).order_by("-created_at").first()
        if shipment is not None:
            return shipment

    local = str(upd.get("local_order_id") or "").strip()
    if local:
        order_q = None
        # The order number, or a replacement's "12-RE" / older "12-R1" ref
        # (see Order.courier_ref).
        parsed = Order.parse_courier_ref(local)
        if parsed:
            order_q = Q(order__number=parsed[0], order__replacement_seq=parsed[1])
        else:
            import uuid

            try:
                order_q = Q(order__id=uuid.UUID(local))
            except ValueError:
                order_q = None
        if order_q is not None:
            shipment = base.filter(order_q).order_by("-created_at").first()
            if shipment is not None:
                return shipment

    needles = {
        str(v).strip()
        for v in (upd.get("shipment_id"), upd.get("sr_order_id"))
        if v is not None and str(v).strip()
    }
    if needles:
        for cand in base.filter(provider__provider="shiprocket").order_by("-created_at")[:300]:
            meta = cand.meta or {}
            sr = meta.get("shiprocket") if isinstance(meta.get("shiprocket"), dict) else {}
            stored = {
                str(v).strip()
                for v in (
                    meta.get("shipment_id"),
                    meta.get("sr_order_id"),
                    sr.get("shipment_id"),
                    sr.get("order_id"),
                )
                if v is not None and str(v).strip()
            }
            if needles & stored:
                return cand
    return None


def apply_update(site, upd: dict, now=None):
    """Apply one normalised update to its shipment + order. Returns the matched
    shipment (or ``None`` when no shipment of this store matches)."""
    now = now or timezone.now()
    shipment = _find_shipment(site, upd)
    if shipment is None:
        return None

    fields = []
    awb_val = (upd.get("awb") or "").strip()
    if awb_val and not (shipment.awb or "").strip():
        shipment.awb = awb_val[:120]
        fields.append("awb")
        order = shipment.order
        if order is not None and not (order.tracking_id or "").strip():
            order.tracking_id = awb_val[:120]
            order.save(update_fields=["tracking_id"])

    raw = upd.get("raw") if isinstance(upd.get("raw"), dict) else {}
    courier = str(raw.get("courier_name") or "").strip()
    if courier and courier.lower() != (shipment.courier_name or "").strip().lower():
        shipment.courier_name = courier[:120]
        fields.append("courier_name")
    track_url = str(raw.get("track_url") or "").strip()
    if track_url.lower().startswith("https://") and track_url != shipment.tracking_url and len(track_url) <= 200:
        shipment.tracking_url = track_url
        fields.append("tracking_url")

    status = (upd.get("status") or "").strip().lower()
    meta = dict(shipment.meta or {})
    if status == RTO_IN_PROGRESS:
        # On its way back to the seller: still a parcel in the network (it
        # stays in Shipped, or in Cancelled when the owner cancelled it) until
        # the courier reports it returned.
        meta["return_in_progress"] = True
        meta.setdefault("rto_initiated_at", now.isoformat())
        status = "in_transit"
    elif status == "rto":
        meta.pop("return_in_progress", None)
        meta.setdefault("rto_returned_at", now.isoformat())
    status_changed = False
    if status in _VALID_SHIPMENT_STATUSES and _allowed_transition(shipment.status, status):
        shipment.status = status
        fields.append("status")
        status_changed = True

    meta["last_courier_status"] = str(raw.get("current_status") or raw.get("shipment_status") or status)[:80]
    meta["last_courier_update_at"] = now.isoformat()
    if shipment.status in _IN_NETWORK and not meta.get("shipped_at"):
        meta["shipped_at"] = now.isoformat()
    shipment.meta = meta
    fields.append("meta")
    shipment.save(update_fields=sorted(set(fields)) + ["updated_at"])

    if status_changed:
        _advance_order(shipment.order, shipment.status, now)
        if shipment.status in ("rto", "cancelled") and shipment.order is not None:
            # Returned to origin, or cancelled before pickup: the units are
            # back on the shelf. Idempotent (Order.stock_deducted).
            from commerce import stock

            from commerce import order_notify

            if shipment.status == "rto":
                stock.release_for_order(shipment.order)
                # Tell the shopper their parcel went back to the store (once).
                order_notify.queue(shipment.order, "returned")
            elif shipment.order.status == Order.Status.CANCELLED:
                stock.release_if_not_dispatched(shipment.order)
                # Idempotent: a no-op when the cancel already returned it.
                from commerce import credits

                credits.restore_for_order(shipment.order)
    return shipment


def _record(site, provider_key: str, payload, updates, *, signature_ok: bool, topic: str = ""):
    """Store one courier event (pushed or synced) for the popup and the log."""
    first_raw = updates[0].get("raw") if updates and isinstance(updates[0].get("raw"), dict) else {}
    if not topic and isinstance(payload, dict):
        topic = str(payload.get("topic") or payload.get("event") or "")
    return CourierWebhookEvent.objects.create(
        site=site,
        provider=provider_key,
        topic=(topic or "")[:80],
        status=str(first_raw.get("current_status") or (updates[0]["status"] if updates else ""))[:60],
        awb=(updates[0]["awb"] if updates else "")[:120],
        raw_payload=payload if isinstance(payload, (dict, list)) else {},
        signature_ok=signature_ok,
    )


def process(site, provider_key: str, raw_body: bytes, headers: dict) -> dict:
    """Authenticate, record, and apply a courier webhook. Returns a small dict.

    ``site`` is the resolved store; ``provider_key`` is e.g. ``"shiprocket"``.
    Never raises; the view turns the returned dict into a JSON response.
    """
    provider_key = (provider_key or "").strip().lower() or "shiprocket"
    cfg = ShippingProvider.objects.for_site(site).filter(provider=provider_key).first()
    if cfg is None or not cfg.enabled:
        return {"status": "ignored", "code": 200}

    token = (headers.pop("_path_token", "") or "").strip()
    signature_ok = verify(cfg, raw_body=raw_body, headers=headers, token=token)

    try:
        payload = json.loads((raw_body or b"{}").decode("utf-8") or "{}")
    except Exception:
        payload = {}

    # Delhivery-style bodies share the keys the Shiprocket extractor reads.
    updates = extract_shiprocket_updates(payload)

    # Always record the event, even when rejected or unmatched: the tracking
    # popup and the Shipping page's webhook log need it visible.
    event = _record(site, provider_key, payload, updates, signature_ok=signature_ok)

    if not signature_ok:
        return {"status": "forbidden", "code": 403}
    if not updates:
        # A dashboard "test" ping or an unrelated topic. Acknowledge it so the
        # courier marks the URL as working.
        return {"status": "ok", "code": 200}

    now = timezone.now()
    applied = 0
    matched = None
    with transaction.atomic():
        for upd in updates:
            shipment = apply_update(site, upd, now)
            if shipment is None:
                continue
            applied += 1
            if matched is None:
                matched = shipment
                event.shipment = shipment
                event.order = shipment.order
                event.save(update_fields=["shipment", "order"])

    if matched is not None:
        _notify(site, matched)
    return {"status": "processed" if applied else "no_match", "code": 200, "updates": applied}


def _notify(site, shipment) -> None:
    order = shipment.order
    label = f"#{order.display_number} → {shipment.get_status_display()}" if order else shipment.get_status_display()
    try:
        activity.record(
            "store.shipment.updated", site=site, target=shipment,
            target_kind="Shipment", label=label,
        )
    except Exception:
        pass


# ── Pull: live sync from the courier API ("Sync status" button) ───────────────

class SyncError(Exception):
    """A live tracking sync failed. The message is safe to show the owner."""


def sync_shipment(site, shipment) -> CourierWebhookEvent:
    """Fetch the shipment's live tracking from its courier and apply it.

    Shiprocket shipments are tracked through the API (by AWB, else by
    Shiprocket shipment id); the result is stored as a courier event in the
    webhook payload shape, so the tracking popup renders it like a push, and
    applied through the same rules. Couriers without a tracking client fall
    back to re-applying the latest stored event. Raises :class:`SyncError`.
    """
    from commerce import shipping_shiprocket as sr

    provider = shipment.provider
    key = (provider.provider if provider else "") or ""
    if key != "shiprocket":
        evt = _latest_event(site, shipment)
        if evt is None:
            raise SyncError("No tracking update has been received for this order yet.")
        for upd in extract_shiprocket_updates(evt.raw_payload or {}):
            apply_update(site, upd)
        return evt

    if not sr.is_configured(provider):
        raise SyncError("Shiprocket isn't connected. Add your API email and password in Shipping settings.")
    meta = shipment.meta or {}
    sr_meta = meta.get("shiprocket") if isinstance(meta.get("shiprocket"), dict) else {}
    shipment_id = str(meta.get("shipment_id") or sr_meta.get("shipment_id") or "").strip()
    try:
        payload = sr.track(provider, awb=shipment.awb, shipment_id=shipment_id)
    except sr.ShiprocketError as exc:
        raise SyncError(str(exc)) from exc

    # Make sure the update matches *this* shipment even if Shiprocket omitted ids.
    if not payload.get("order_id") and shipment.order_id:
        payload["order_id"] = shipment.order.courier_ref
    if not payload.get("awb") and not payload.get("shipment_id"):
        payload["shipment_id"] = shipment_id
    updates = extract_shiprocket_updates(payload)
    event = _record(site, "shiprocket", payload, updates, signature_ok=True, topic="sync")
    before = shipment.status
    with transaction.atomic():
        matched = None
        for upd in updates:
            matched = apply_update(site, upd) or matched
        target = matched or shipment
        event.shipment = target
        event.order = target.order
        event.save(update_fields=["shipment", "order"])
    target.refresh_from_db()
    if target.status != before:
        _notify(site, target)
    return event


def sync_open_shipments(site, *, limit: int = 50) -> dict:
    """Sync every in-flight Shiprocket shipment of the store (Shipping page
    "Sync now"). Returns ``{"synced", "failed", "changed"}``."""
    open_statuses = [
        Shipment.Status.CREATED, Shipment.Status.BOOKED, Shipment.Status.PICKUP_REQUESTED,
        Shipment.Status.PICKED_UP, Shipment.Status.IN_TRANSIT,
        Shipment.Status.OUT_FOR_DELIVERY, Shipment.Status.DELIVERY_FAILED,
    ]
    qs = (
        Shipment.objects.for_site(site)
        .select_related("order", "provider")
        .filter(provider__provider="shiprocket", status__in=open_statuses)
        .exclude(awb="")
        .order_by("-updated_at")[:limit]
    )
    synced = failed = changed = 0
    for shipment in qs:
        before = shipment.status
        try:
            sync_shipment(site, shipment)
        except SyncError:
            failed += 1
            continue
        except Exception:
            logger.exception("tracking sync failed shipment=%s", shipment.pk)
            failed += 1
            continue
        synced += 1
        shipment.refresh_from_db(fields=["status"])
        if shipment.status != before:
            changed += 1
    return {"synced": synced, "failed": failed, "changed": changed}


def _latest_event(site, shipment):
    evt = (
        CourierWebhookEvent.objects.for_site(site)
        .filter(shipment=shipment)
        .order_by("-received_at")
        .first()
    )
    if evt is None and (shipment.awb or "").strip():
        evt = (
            CourierWebhookEvent.objects.for_site(site)
            .filter(awb=shipment.awb.strip())
            .order_by("-received_at")
            .first()
        )
    return evt


def _advance_order(order, ship_status: str, now) -> None:
    """Advance the order to match the shipment, never downgrading or over-cancelling.

    Mirrors the reference: delivered → delivered; picked_up / in_transit /
    out_for_delivery → shipped (only from created/confirmed/paid); cancelled →
    cancelled. A delivered/cancelled/archived order is never touched.
    """
    if order is None:
        return
    cur = (order.status or "").strip().lower()
    if cur in ("delivered", "cancelled", "archived"):
        return
    target = None
    if ship_status == "delivered":
        target = Order.Status.DELIVERED
    elif ship_status in ("picked_up", "in_transit", "out_for_delivery"):
        if cur in ("created", "confirmed", "paid"):
            target = Order.Status.SHIPPED
    elif ship_status == "cancelled":
        target = Order.Status.CANCELLED
    if target is None or cur == target:
        return
    order.status = target
    # Tell the shopper (each at most once): shipped (with tracking), delivered,
    # or cancelled from the courier's side (e.g. in the Shiprocket dashboard).
    mail_kind = {
        Order.Status.SHIPPED: "shipped",
        Order.Status.DELIVERED: "delivered",
        Order.Status.CANCELLED: "cancelled",
    }.get(target)
    if mail_kind:
        from commerce import order_notify

        order_notify.queue(order, mail_kind)
    stamp = {
        Order.Status.SHIPPED: "shipped_at",
        Order.Status.DELIVERED: "delivered_at",
        Order.Status.CANCELLED: "cancelled_at",
    }.get(target)
    fields = ["status", "updated_at"] + ([stamp] if stamp else [])
    try:
        order.save(update_fields=fields)
    except Exception:
        logger.exception("Could not advance order %s from courier webhook", getattr(order, "pk", "?"))
