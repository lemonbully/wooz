"""Live Shiprocket integration for a store's own courier account.

Ported from the 1matrix reference (``website/shipping/providers/shiprocket.py``)
and adapted to OneWooz's :class:`~commerce.models.ShippingProvider` /
:class:`~commerce.models.Order` / :class:`~commerce.models.Product` models.

Booking an order creates it in the merchant's Shiprocket dashboard through the
``create/adhoc`` endpoint; the returned Shiprocket order id / shipment id / AWB
are stored on the OneWooz :class:`~commerce.models.Shipment` so later tracking,
label and pickup actions can reference them.

The API user's email + password live (encrypted) on the per-store provider row.
The bearer token they mint (~10 day life) is cached on the same row and refreshed
on expiry or a 401 ``token_expired``.
"""

from __future__ import annotations

import json
import logging

from django.utils import timezone

logger = logging.getLogger("onewooz.audit")

DEFAULT_BASE_URL = "https://apiv2.shiprocket.in"


class ShiprocketError(Exception):
    """A Shiprocket API call failed. The message is safe to log, not to show raw."""


# --------------------------------------------------------------------------
# Config / auth
# --------------------------------------------------------------------------
def _base_url(provider) -> str:
    return ((provider.base_url or "").strip() or DEFAULT_BASE_URL).rstrip("/")


def is_configured(provider) -> bool:
    """True when the provider carries everything an order booking needs.

    Shiprocket's ``create/adhoc`` needs three things, not two: the API email and
    password to authenticate, *and* a ``pickup_location`` that exactly matches a
    Pickup Address nickname in the merchant's Shiprocket dashboard. Treating a
    missing pickup location as "not configured" makes the Accept-time message
    name it, instead of the booking failing later with an opaque API error.
    """
    return bool(
        (provider.api_email or "").strip()
        and (provider.api_password or "").strip()
        and (provider.pickup_location or "").strip()
    )


def has_credentials(provider) -> bool:
    """True when the API email + password are saved: enough to sign in and read
    the account (pickup locations, wallet, couriers), before a pickup location
    has been chosen."""
    return bool((provider.api_email or "").strip() and (provider.api_password or "").strip())


def _headers(provider) -> dict:
    token = (provider.access_token or "").strip()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _login(provider) -> tuple[str, int]:
    """Exchange email/password for a bearer token; returns (token, ttl_seconds)."""
    import requests

    url = f"{_base_url(provider)}/v1/external/auth/login"
    try:
        resp = requests.post(
            url,
            json={"email": provider.api_email, "password": provider.api_password},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    except Exception as exc:
        raise ShiprocketError(f"authentication failed: {exc}") from exc
    token = data.get("token") or data.get("access_token") or ""
    if not token:
        raise ShiprocketError("authentication succeeded but no token was returned")
    # Shiprocket tokens are typically valid for ~10 days.
    expires_in = int(data.get("expires_in") or 10 * 24 * 3600)
    return token, expires_in


def ensure_token(provider, *, force: bool = False) -> None:
    """Make sure the provider holds a valid cached token, refreshing if needed."""
    if not force and provider.is_access_token_valid():
        return
    if not has_credentials(provider):
        raise ShiprocketError("Shiprocket credentials are not set")
    token, expires_in = _login(provider)
    provider.store_access_token(
        token, timezone.now() + timezone.timedelta(seconds=int(expires_in or 0))
    )


def _api(provider, method: str, path: str, *, json_payload=None, params=None, _retry: bool = True):
    """Authenticated Shiprocket call with a single 401/token_expired re-auth retry."""
    import requests

    ensure_token(provider)
    url = f"{_base_url(provider)}{path}"
    try:
        resp = requests.request(
            method.upper(), url, headers=_headers(provider), json=json_payload, params=params, timeout=30
        )
    except Exception as exc:
        raise ShiprocketError(f"request failed: {exc}") from exc

    if resp.status_code == 401 and _retry:
        body = (getattr(resp, "text", "") or "").lower()
        if "token_expired" in body or "unauthor" in body:
            ensure_token(provider, force=True)
            return _api(provider, method, path, json_payload=json_payload, params=params, _retry=False)

    if resp.status_code >= 400:
        body = (getattr(resp, "text", "") or "")
        logger.warning("shiprocket %s %s -> %s: %s", method, path, resp.status_code, body[:500])
        raise ShiprocketError(_error_message(resp, body))
    return resp


def _error_message(resp, body: str) -> str:
    """Turn a Shiprocket error response into a message safe to show the owner.

    Shiprocket phrases a wrong pickup location as e.g. "Wrong Pickup location
    entered" / "Pickup location not added"; catching that lets us point the owner
    straight at the warehouse-name field instead of a bare status code. Any other
    error surfaces Shiprocket's own ``message`` (and per-field ``errors``) so the
    reason is visible rather than swallowed behind "returned 422".
    """
    detail = ""
    try:
        data = resp.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        detail = str(data.get("message") or "").strip()
        errors = data.get("errors")
        if isinstance(errors, dict) and errors:
            parts = []
            for field, msgs in errors.items():
                text = ", ".join(msgs) if isinstance(msgs, (list, tuple)) else str(msgs)
                parts.append(f"{field}: {text}" if field else text)
            joined = "; ".join(p for p in parts if p)
            detail = f"{detail} ({joined})" if detail and joined else (detail or joined)

    haystack = f"{detail} {body}".lower()
    if resp.status_code == 403 or "you do not have permission" in haystack or (
        "unauthor" in haystack and "permission" in haystack
    ):
        return (
            "Your Shiprocket API user isn't allowed to perform this action "
            "(assigning a courier or scheduling a pickup). Give the API user those "
            "permissions in Shiprocket (Settings → API → Configure API User), or "
            "open the order in your Shiprocket dashboard and use “Ship Now” to "
            "assign a courier and schedule the pickup there  the order is already "
            "in your Shiprocket account."
        )
    if "pickup" in haystack and ("location" in haystack or "address" in haystack):
        return (
            "Shiprocket rejected the pickup location  it must exactly match a "
            "Pickup Address nickname saved in your Shiprocket dashboard "
            "(Settings → Pickup Addresses). Update it in Shipping settings and "
            "accept the order again."
            + (f" Shiprocket said: {detail}" if detail else "")
        )
    if "awb" in haystack and ("assign" in haystack or "courier" in haystack):
        return (
            "Shiprocket needs a courier/AWB assigned before it can schedule a "
            "pickup. Assign a courier to the order in your Shiprocket dashboard, "
            "then request the pickup again."
            + (f" Shiprocket said: {detail}" if detail else "")
        )
    if detail:
        return f"Shiprocket rejected the request: {detail}"
    return f"Shiprocket API returned {resp.status_code}"


# --------------------------------------------------------------------------
# Payload building (dimensions in cm, weight in kg  as Shiprocket expects)
# --------------------------------------------------------------------------
def _to_cm(val, unit) -> float:
    try:
        v = float(val or 0)
    except (TypeError, ValueError):
        return 0.0
    u = (unit or "").strip().lower()
    if u in ("mm", "millimeter", "millimetre"):
        return v / 10.0
    if u in ("in", "inch", "inches"):
        return v * 2.54
    if u in ("ft", "feet", "foot"):
        return v * 30.48
    return v  # cm or unknown -> assume cm


def _to_kg(val, unit) -> float:
    try:
        v = float(val or 0)
    except (TypeError, ValueError):
        return 0.0
    u = (unit or "").strip().lower()
    if u in ("g", "gram", "grams"):
        return v / 1000.0
    return v  # kg or unknown -> assume kg


def _dims_items_finance(order):
    """Aggregate package L/B/H (cm, max), total weight (kg), and item lines."""
    from commerce.cart import line_sku

    length = breadth = height = weight = 0.0
    items = []
    from commerce.variant_profiles import line_measures

    for it in order.items.select_related("product").all():
        product = it.product
        qty = int(it.quantity or 1)
        if product is not None:
            # A variation with its own dimensions / weight ships with those.
            product = line_measures(it) or product
            if product.package_length is not None and product.package_breadth is not None and product.package_height is not None:
                l = _to_cm(product.package_length, product.package_unit)
                b = _to_cm(product.package_breadth, product.package_unit)
                h = _to_cm(product.package_height, product.package_unit)
            else:
                l = _to_cm(product.product_length, product.product_unit)
                b = _to_cm(product.product_breadth, product.product_unit)
                h = _to_cm(product.product_height, product.product_unit)
            length, breadth, height = max(length, l), max(breadth, b), max(height, h)
            if product.package_weight_value is not None:
                w = _to_kg(product.package_weight_value, product.package_weight_unit)
            else:
                w = _to_kg(product.weight_value, product.weight_unit)
            weight += max(0.0, w) * max(1, qty)
        hsn = getattr(product, "hsn_code", "") or ""
        items.append({
            "name": (it.title or (product.title if product else "Item"))[:100],
            "sku": (line_sku(it) or "GENERIC")[:100],
            "units": qty,
            "selling_price": float(it.unit_price or 0),
            "discount": "",
            "tax": "",
            "hsn": int(hsn) if str(hsn).isdigit() else "",
        })

    if length <= 0 and breadth <= 0 and height <= 0:
        length, breadth, height = 10.0, 10.0, 5.0  # last-resort minimums
    weight = max(0.1, float(weight or 0))
    if not items:
        items = [{"name": "Item", "sku": "GENERIC", "units": 1, "selling_price": float(order.total or 0)}]
    return length, breadth, height, weight, items


def _pickup_location(provider) -> str:
    """The exact Pickup Address nickname the merchant saved in Shipping settings.

    Never guessed. The reference defaulted an empty value to ``"Primary"``, which
    only works for accounts that happen to have kept Shiprocket's default label 
    every other account had its booking rejected with an opaque error. Here an
    empty pickup location is a configuration error the caller reports plainly, so
    we send the owner's real warehouse name or nothing at all.
    """
    return (provider.pickup_location or "").strip()[:120]


# --------------------------------------------------------------------------
# Create the order in Shiprocket
# --------------------------------------------------------------------------
def create_adhoc_order(provider, order) -> dict:
    """Create ``order`` in the merchant's Shiprocket account (create/adhoc).

    Returns a normalized dict: ``{sr_order_id, shipment_id, awb, courier_name,
    tracking_url, raw}``. Raises :class:`ShiprocketError` on any failure.
    """
    payload = _adhoc_payload(provider, order)
    logger.info("shiprocket create/adhoc order=%s: %s", payload["order_id"], json.dumps(payload, ensure_ascii=False))
    resp = _api(provider, "POST", "/v1/external/orders/create/adhoc", json_payload=payload)
    try:
        data = resp.json()
    except ValueError as exc:
        raise ShiprocketError("Shiprocket returned a non-JSON response") from exc

    inner = data.get("data") if isinstance(data.get("data"), dict) else {}
    sr_order_id = data.get("order_id") or inner.get("order_id")
    shipment_id = data.get("shipment_id") or inner.get("shipment_id") or ""
    awb = str(data.get("awb_code") or data.get("awb") or inner.get("awb_code") or "")
    return {
        "sr_order_id": sr_order_id,
        "shipment_id": str(shipment_id or ""),
        "awb": awb,
        "courier_name": str(data.get("courier_name") or inner.get("courier_name") or "Shiprocket"),
        "tracking_url": str(data.get("tracking_url") or inner.get("tracking_url") or ""),
        "raw": data,
    }


def update_adhoc_order(provider, order) -> dict:
    """Push an edited order's items, amounts and package to Shiprocket
    (``POST /v1/external/orders/update/adhoc``).

    Same payload as create/adhoc, keyed by the ``order_id`` we sent then (our
    order number). Shiprocket accepts item changes (add/remove lines, quantity,
    price, tax/discount, totals, dimensions/weight) only **before an AWB is
    assigned**, and ignores address / customer / payment-method fields here
    (those go through :func:`update_delivery_address`). Returns the response
    body; raises :class:`ShiprocketError` when refused.
    """
    payload = _adhoc_payload(provider, order)
    logger.info("shiprocket update/adhoc order=%s: %s", payload["order_id"], json.dumps(payload, ensure_ascii=False))
    resp = _api(provider, "POST", "/v1/external/orders/update/adhoc", json_payload=payload)
    try:
        data = resp.json()
    except ValueError:
        return {"ok": True}
    if isinstance(data, dict) and data.get("success") is False:
        raise ShiprocketError(str(data.get("message") or "Shiprocket didn't accept the order update."))
    return data if isinstance(data, dict) else {"ok": True}


def update_delivery_address(provider, sr_order_id, order) -> dict:
    """Update the customer's name, phone, email and delivery address on a
    Shiprocket order (``POST /v1/external/orders/address/update``, keyed by
    Shiprocket's own order id). Raises :class:`ShiprocketError` when refused."""
    try:
        oid = int(str(sr_order_id).strip())
    except (TypeError, ValueError) as exc:
        raise ShiprocketError("This order has no Shiprocket order id yet.") from exc
    phone = "".join(ch for ch in str(order.customer_phone or "") if ch.isdigit())[-10:]
    payload = {
        "order_id": oid,
        "shipping_customer_name": (order.customer_name or "Customer")[:100],
        "shipping_phone": phone,
        "shipping_address": order.shipping_address or "",
        "shipping_address_2": "",
        "shipping_city": order.shipping_city or "",
        "shipping_state": order.shipping_state or "",
        "shipping_country": "India",
        "shipping_pincode": order.shipping_pincode or "",
        "shipping_email": order.customer_email or "",
    }
    logger.info("shiprocket address/update order=%s", oid)
    resp = _api(provider, "POST", "/v1/external/orders/address/update", json_payload=payload)
    try:
        data = resp.json()
    except ValueError:
        return {"ok": True}
    return data if isinstance(data, dict) else {"ok": True}


def _payment_amounts(order) -> dict:
    """Payment method and money fields for create/update adhoc, so the courier
    collects exactly what the customer still owes.

    Shiprocket's order value (and, for COD, the amount the courier collects) is
    ``sub_total + shipping_charges - total_discount``; it has no field for an
    amount already paid online. So:

    * ``sub_total`` is the lines plus any GST added on top for tax-exclusive
      products (``tax_amount``), so that GST is collected too;
    * ``total_discount`` is whatever makes the value equal the amount due:
      the coupon discount and store credit and, for a **partial-payment**
      order, the part already paid online;
    * a partial order goes as **COD** (the courier collects its balance,
      ``partial_cod_amount``); a fully paid order as Prepaid.
    """
    from decimal import Decimal

    zero = Decimal("0.00")

    def d(v):
        try:
            return Decimal(str(v or 0)).quantize(Decimal("0.01"))
        except Exception:
            return zero

    method = (order.payment_method or "").lower()
    partial = bool(getattr(order, "is_partial_payment", False)) or method == "partial"
    sub_total = d(order.subtotal) + d(getattr(order, "tax_amount", 0))
    shipping = d(order.shipping_amount)
    total = d(order.total)
    if partial:
        balance = d(getattr(order, "partial_cod_amount", 0))
        if balance <= zero:
            balance = max(zero, total - d(getattr(order, "partial_paid_amount", 0)))
        due = balance
        payment_method = "COD" if balance > zero else "Prepaid"
    else:
        due = total
        payment_method = "COD" if "cod" in method else "Prepaid"
    # The order value must equal what is due; anything above it is "discount".
    total_discount = max(zero, sub_total + shipping - due)
    if due > sub_total + shipping:
        # Charges we don't itemise to Shiprocket (rare): fold them into sub_total.
        sub_total = due - shipping
        total_discount = zero
    return {
        "payment_method": payment_method,
        "sub_total": float(sub_total),
        "shipping_charges": float(shipping),
        "total_discount": float(total_discount),
    }


def _adhoc_payload(provider, order) -> dict:
    """The create/update adhoc body for ``order`` (dimensions in cm, weight in
    kg). Raises :class:`ShiprocketError` when no pickup location is set."""
    pickup_location = _pickup_location(provider)
    if not pickup_location:
        raise ShiprocketError(
            "Set your Shiprocket pickup location in Shipping settings first  it "
            "must exactly match a Pickup Address nickname saved in your Shiprocket "
            "dashboard (Settings → Pickup Addresses)."
        )

    length, breadth, height, weight, order_items = _dims_items_finance(order)
    amounts = _payment_amounts(order)

    payload = {
        # A replacement goes as "<number>-R<n>": Shiprocket refuses an order id
        # it has already seen (the original's booking).
        "order_id": getattr(order, "courier_ref", None) or str(order.number or order.pk),
        "order_date": timezone.now().strftime("%Y-%m-%d %H:%M"),
        "pickup_location": pickup_location,
        "billing_customer_name": (order.customer_name or "Customer")[:100],
        "billing_last_name": "",
        "billing_address": order.shipping_address or "",
        "billing_address_2": "",
        "billing_city": order.shipping_city or "",
        "billing_pincode": order.shipping_pincode or "",
        "billing_state": order.shipping_state or "",
        "billing_country": "India",
        "billing_email": order.customer_email or "",
        "billing_phone": order.customer_phone or "",
        "shipping_is_billing": True,
        "order_items": order_items,
        "payment_method": amounts["payment_method"],
        "shipping_charges": amounts["shipping_charges"],
        "giftwrap_charges": 0,
        "transaction_charges": 0,
        "total_discount": amounts["total_discount"],
        "sub_total": amounts["sub_total"],
        "length": length,
        "breadth": breadth,
        "height": height,
        "weight": weight,
    }
    return payload


# --------------------------------------------------------------------------
# Assign a courier / AWB, then schedule the pickup
# --------------------------------------------------------------------------
def _extract_awb(data) -> str:
    """Pull an AWB code out of Shiprocket's several response shapes."""
    if not isinstance(data, dict):
        return ""
    resp = data.get("response") if isinstance(data.get("response"), dict) else {}
    inner = resp.get("data") if isinstance(resp.get("data"), dict) else {}
    for src in (data, resp, inner):
        if isinstance(src, dict):
            awb = src.get("awb_code") or src.get("awb")
            if awb:
                return str(awb)
    return ""


def assign_awb(provider, shipment_id, *, courier_id=None) -> dict:
    """Assign a courier / AWB to a Shiprocket shipment.

    Shiprocket won't schedule a pickup for a shipment that has no AWB, so the
    pickup flow calls this first whenever one is missing. With no ``courier_id``
    Shiprocket auto-picks its recommended courier. Returns ``{"awb", "raw"}`` and
    raises :class:`ShiprocketError` when no courier could be assigned.
    """
    try:
        sid = int(str(shipment_id).strip())
    except (TypeError, ValueError) as exc:
        raise ShiprocketError("This order has no valid Shiprocket shipment id yet.") from exc

    payload = {"shipment_id": [sid]}
    if courier_id:
        try:
            payload["courier_id"] = int(courier_id)
        except (TypeError, ValueError):
            pass
    resp = _api(provider, "POST", "/v1/external/courier/assign/awb", json_payload=payload)
    try:
        data = resp.json()
    except ValueError as exc:
        raise ShiprocketError("Shiprocket returned a non-JSON response") from exc

    awb = _extract_awb(data)
    if not awb:
        # A 200 with no AWB means the auto-assignment itself failed (e.g. no
        # serviceable courier); surface Shiprocket's own reason.
        detail = ""
        resp_obj = data.get("response") if isinstance(data, dict) else {}
        if isinstance(resp_obj, dict):
            block = resp_obj.get("data") if isinstance(resp_obj.get("data"), dict) else resp_obj
            detail = str(
                (block or {}).get("awb_assign_error")
                or resp_obj.get("awb_assign_error")
                or data.get("message")
                or ""
            ).strip()
        raise ShiprocketError(
            "Shiprocket couldn't assign a courier/AWB automatically"
            + (f": {detail}" if detail else "")
            + ". Assign a courier to this order in your Shiprocket dashboard, then request the pickup."
        )
    return {"awb": awb, "raw": data}


def generate_pickup(provider, shipment_id, *, pickup_date: str = "") -> dict:
    """Schedule a Shiprocket pickup for ONE shipment (generate/pickup).

    Shiprocket accepts a single ``shipment_id`` per call and needs an AWB already
    generated for it. ``pickup_date`` is optional (``YYYY-MM-DD``); a pickup_date
    that falls on a holiday or Sunday is rolled to the next available date. Raises
    :class:`ShiprocketError` on an HTTP error *and* on a 200 body whose
    ``pickup_status`` is 0 (Shiprocket's soft-failure shape)  except an
    already-queued shipment, which is treated as success.
    """
    try:
        sid = int(str(shipment_id).strip())
    except (TypeError, ValueError) as exc:
        raise ShiprocketError(
            "No Shiprocket shipment id is available for this order yet  accept it "
            "so it's created in Shiprocket, then request the pickup."
        ) from exc

    payload = {"shipment_id": [sid]}
    if (pickup_date or "").strip():
        payload["pickup_date"] = [pickup_date.strip()]
    logger.info("shiprocket generate/pickup: %s", json.dumps(payload, ensure_ascii=False))
    try:
        resp = _api(provider, "POST", "/v1/external/courier/generate/pickup", json_payload=payload)
    except ShiprocketError as exc:
        # Shiprocket answers a shipment whose pickup was already requested (an
        # earlier click, or the same order sent again with a later one) with a
        # 400 "Already in Pickup Queue". The pickup *is* scheduled in their
        # dashboard, so this is a success: the caller records it and the labels
        # are generated as usual.
        if _is_already_queued(str(exc)):
            return {"already_queued": True, "pickup_status": 1, "response": {"data": str(exc)}}
        raise
    try:
        data = resp.json()
    except ValueError:
        return {"ok": True}

    if isinstance(data, dict) and data.get("pickup_status") in (0, "0", False):
        text = _pickup_response_text(data)
        if _is_already_queued(text):
            return {**data, "already_queued": True}
        raise ShiprocketError(text or "Shiprocket couldn't schedule the pickup.")
    return data


def _is_already_queued(text: str) -> bool:
    """True for Shiprocket's "Already in Pickup Queue" / "pickup already
    scheduled" refusals, which mean the pickup is in fact booked."""
    low = (text or "").lower()
    return "already" in low and ("queue" in low or "pickup" in low or "schedul" in low)


def pickup_scheduled_date(data) -> str:
    """The pickup date Shiprocket confirmed (``YYYY-MM-DD``), or ``""``.

    A generate/pickup success carries ``response.pickup_scheduled_date``
    (``"2026-10-07 12:39:55"``); an already-queued answer carries none.
    """
    if not isinstance(data, dict):
        return ""
    resp = data.get("response") if isinstance(data.get("response"), dict) else {}
    raw = str(resp.get("pickup_scheduled_date") or data.get("pickup_scheduled_date") or "").strip()
    return raw[:10] if len(raw) >= 10 and raw[4:5] == "-" else ""


def get_shipment(provider, shipment_id) -> dict:
    """One Shiprocket shipment's details (``GET /v1/external/shipments/<id>``).

    Used to recover an AWB Shiprocket already assigned when our copy missed it
    (e.g. a pickup requested once whose response never reached us). Returns the
    ``data`` block, or ``{}``.
    """
    try:
        sid = int(str(shipment_id).strip())
    except (TypeError, ValueError):
        return {}
    resp = _api(provider, "GET", f"/v1/external/shipments/{sid}")
    try:
        data = resp.json()
    except ValueError:
        return {}
    block = data.get("data") if isinstance(data, dict) else None
    return block if isinstance(block, dict) else {}


def get_order(provider, sr_order_id) -> dict:
    """One Shiprocket order's details (``GET /v1/external/orders/show/<id>``).

    Carries ``awb_data.charges`` (the courier's freight for the shipment) once
    an AWB is assigned. Returns the ``data`` block, or ``{}``.
    """
    try:
        oid = int(str(sr_order_id).strip())
    except (TypeError, ValueError):
        return {}
    resp = _api(provider, "GET", f"/v1/external/orders/show/{oid}")
    try:
        data = resp.json()
    except ValueError:
        return {}
    block = data.get("data") if isinstance(data, dict) else None
    return block if isinstance(block, dict) else {}


# Keys Shiprocket uses for the courier's forward freight on a shipment, most
# specific first. (RTO freight comes under ``*_rto`` keys, which never match.)
_FREIGHT_KEYS = (
    "freight_charges",
    "freight_charge",
    "total_freight_charges",
    "applied_weight_amount",
    "charged_weight_amount",
)


def freight_charge(data):
    """The courier's freight charge for a shipment, found anywhere in a
    Shiprocket response (assign AWB, shipment or order details), as a
    ``Decimal``; ``None`` when the response carries none."""
    from decimal import Decimal, InvalidOperation

    found: dict = {}

    def walk(node, depth=0):
        if depth > 6:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                lk = str(key).lower()
                if isinstance(value, (dict, list)):
                    walk(value, depth + 1)
                elif lk in _FREIGHT_KEYS and lk not in found:
                    try:
                        amount = Decimal(str(value).replace(",", "").strip()).quantize(Decimal("0.01"))
                    except (InvalidOperation, ValueError):
                        continue
                    if amount > 0:
                        found[lk] = amount
        elif isinstance(node, list):
            for value in node[:25]:
                walk(value, depth + 1)

    walk(data)
    for key in _FREIGHT_KEYS:
        if key in found:
            return found[key]
    return None


# --------------------------------------------------------------------------
# Cancel an order (owner cancelled it on OneWooz)
# --------------------------------------------------------------------------
def cancel_orders(provider, sr_order_ids) -> dict:
    """Cancel orders in the merchant's Shiprocket account.

    ``POST /v1/external/orders/cancel`` with ``{"ids": [<Shiprocket order id>]}``
    (Shiprocket's own order id, the one create/adhoc returned, not our order
    number). Before pickup this cancels the order and its pickup; once the
    courier has the parcel Shiprocket turns the cancellation into a return to
    origin, which then arrives through the tracking webhook. Raises
    :class:`ShiprocketError` with Shiprocket's reason when it refuses.
    """
    ids = []
    for oid in sr_order_ids or []:
        try:
            ids.append(int(str(oid).strip()))
        except (TypeError, ValueError):
            continue
    if not ids:
        raise ShiprocketError("This order has no Shiprocket order id, so it can't be cancelled there.")
    logger.info("shiprocket orders/cancel: %s", ids)
    resp = _api(provider, "POST", "/v1/external/orders/cancel", json_payload={"ids": ids})
    try:
        data = resp.json()
    except ValueError:
        return {"ok": True}
    if isinstance(data, dict) and str(data.get("status_code") or "") in ("400", "422"):
        raise ShiprocketError(str(data.get("message") or "Shiprocket refused the cancellation."))
    return data if isinstance(data, dict) else {"ok": True}


def _pickup_response_text(data) -> str:
    """Human-readable text from a generate/pickup body (``response`` can be a
    string or a dict with ``data``/``message``)."""
    if not isinstance(data, dict):
        return ""
    resp = data.get("response")
    if isinstance(resp, str):
        return resp.strip()
    if isinstance(resp, dict):
        return str(resp.get("data") or resp.get("message") or "").strip()
    return str(data.get("message") or "").strip()


# --------------------------------------------------------------------------
# Account: pickup locations + wallet balance
# --------------------------------------------------------------------------
def _parse_pickup_locations(data) -> list[dict]:
    """``GET settings/company/pickup`` → ``[{name, address, city, state,
    pincode, phone, primary, verified}]``, primary location first.

    ``name`` is the ``pickup_location`` nickname, the exact string create/adhoc
    expects. Duplicates (same nickname) are dropped.
    """
    block = data.get("data") if isinstance(data, dict) else None
    rows = block.get("shipping_address") if isinstance(block, dict) else None
    out, seen = [], set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("pickup_location") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        address = ", ".join(
            p for p in (str(row.get("address") or "").strip(), str(row.get("address_2") or "").strip()) if p
        )
        out.append(
            {
                "name": name,
                "address": address,
                "city": str(row.get("city") or "").strip(),
                "state": str(row.get("state") or "").strip(),
                "pincode": str(row.get("pin_code") or "").strip(),
                "phone": str(row.get("phone") or "").strip(),
                "primary": str(row.get("is_primary_location") or "0") in ("1", "True", "true"),
                "verified": str(row.get("phone_verified") or "0") in ("1", "True", "true"),
            }
        )
    out.sort(key=lambda r: (not r["primary"], r["name"].lower()))
    return out


def list_pickup_locations(provider) -> list[dict]:
    """Every pickup location saved in the merchant's Shiprocket account
    (``GET /v1/external/settings/company/pickup``). Needs only the API email +
    password. Raises :class:`ShiprocketError` on an API failure."""
    resp = _api(provider, "GET", "/v1/external/settings/company/pickup")
    try:
        data = resp.json()
    except ValueError as exc:
        raise ShiprocketError("Shiprocket returned a non-JSON pickup-location response") from exc
    return _parse_pickup_locations(data)


def pickup_locations_for_credentials(email: str, password: str, base_url: str = "") -> list[dict]:
    """Pickup locations for credentials that aren't saved yet (typed into the
    Shipping form). Signs in once without caching the token anywhere, so
    nothing is written to the store's provider row."""
    import requests
    from types import SimpleNamespace

    probe = SimpleNamespace(api_email=(email or "").strip(), api_password=password or "", base_url=base_url or "")
    if not (probe.api_email and probe.api_password):
        raise ShiprocketError("Enter your Shiprocket API email and password first.")
    token, _ttl = _login(probe)
    try:
        resp = requests.get(
            f"{_base_url(probe)}/v1/external/settings/company/pickup",
            headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
            timeout=20,
        )
    except Exception as exc:
        raise ShiprocketError(f"request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise ShiprocketError(_error_message(resp, getattr(resp, "text", "") or ""))
    try:
        return _parse_pickup_locations(resp.json())
    except ValueError as exc:
        raise ShiprocketError("Shiprocket returned a non-JSON pickup-location response") from exc


def wallet_balance(provider):
    """The Shiprocket wallet balance as a :class:`~decimal.Decimal` (rupees;
    may be negative). ``GET /v1/external/account/details/wallet-balance`` →
    ``{"data": {"balance_amount": "123.45"}}``."""
    from decimal import Decimal, InvalidOperation

    resp = _api(provider, "GET", "/v1/external/account/details/wallet-balance")
    try:
        data = resp.json()
    except ValueError as exc:
        raise ShiprocketError("Shiprocket returned a non-JSON wallet response") from exc
    block = data.get("data") if isinstance(data, dict) else None
    raw = block.get("balance_amount") if isinstance(block, dict) else None
    if raw is None and isinstance(data, dict):
        raw = data.get("balance_amount")
    try:
        return Decimal(str(raw).replace(",", "").strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ShiprocketError("Shiprocket didn't return a wallet balance.") from exc


# --------------------------------------------------------------------------
# Courier catalogue + serviceability (for courier selection & live rates)
# --------------------------------------------------------------------------
def list_couriers(provider, *, active_only: bool = True) -> list[dict]:
    """The account's courier partners (courierListWithCounts), company-level.

    Used to populate the "preferred courier" dropdown in Shipping settings.
    Returns ``[{"id", "name"}]``; an API failure raises :class:`ShiprocketError`.
    """
    params = {"type": "active"} if active_only else None
    resp = _api(provider, "GET", "/v1/external/courier/courierListWithCounts", params=params)
    try:
        data = resp.json()
    except ValueError:
        return []
    rows = data.get("courier_data") if isinstance(data, dict) else None
    out = []
    for c in rows or []:
        if not isinstance(c, dict):
            continue
        cid = c.get("id")
        name = (c.get("name") or "").strip()
        if cid and name:
            out.append({"id": cid, "name": name})
    # De-duplicate by id, keep first name seen; sort by name for a stable dropdown.
    seen, uniq = set(), []
    for c in out:
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        uniq.append(c)
    uniq.sort(key=lambda c: c["name"].lower())
    return uniq


def pickup_postcode(provider) -> str:
    """The pincode of the Shiprocket pickup address this store books from (the
    nickname saved in Shipping settings), or "".

    Rates must be priced from the same origin the booking uses; the store's
    local default warehouse can be a different address. Read from Shiprocket's
    pickup-location list and cached for an hour; any failure returns "" so the
    caller falls back to the warehouse pincode.
    """
    from django.core.cache import cache

    nickname = _pickup_location(provider)
    if not nickname:
        return ""
    key = f"sr:pickup-pin:{provider.pk}:{nickname}"
    pin = cache.get(key)
    if pin is not None:
        return pin
    pin = ""
    try:
        for loc in list_pickup_locations(provider):
            if loc.get("name") == nickname:
                pin = str(loc.get("pincode") or "").strip()
                break
    except ShiprocketError:
        return ""
    cache.set(key, pin, 3600)
    return pin


def _money(value):
    """A rate/charge from Shiprocket as a float, or ``None`` when absent."""
    try:
        if value is None or value == "":
            return None
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def check_serviceability(
    provider,
    *,
    pickup_postcode,
    delivery_postcode,
    cod: int = 0,
    weight: float = 0.5,
    order_id=None,
    length=None,
    breadth=None,
    height=None,
    declared_value=None,
) -> list[dict]:
    """Couriers that can carry this parcel, with live rates and ETDs.

    Shiprocket prices ONE pickup → delivery pincode pair per call; there is no
    multi-destination quote, so a batch is rated order by order. Shiprocket
    needs either ``order_id`` (its own order id: it then uses that order's
    weight and payment type) or ``cod`` + ``weight``; the package size and
    declared value refine a quote made without an order id.

    Returns ``[{"id","name","rate","freight","cod_charges","etd","days","cod"}]``
    sorted by rate (``rate`` is the total Shiprocket quotes; ``freight`` /
    ``cod_charges`` are its parts when Shiprocket itemises them). An
    unserviceable route returns ``[]`` rather than raising.
    """
    pin_from = str(pickup_postcode or "").strip()
    pin_to = str(delivery_postcode or "").strip()
    if not pin_from or not pin_to:
        raise ShiprocketError(
            "A pickup warehouse pincode and the order's delivery pincode are both "
            "needed to fetch courier rates. Add a warehouse with a pincode in "
            "Shipping settings."
        )
    params = {"pickup_postcode": pin_from, "delivery_postcode": pin_to, "weight": weight, "cod": int(cod or 0)}
    if order_id:
        params["order_id"] = order_id
    for name, value in (("length", length), ("breadth", breadth), ("height", height)):
        if value:
            params[name] = max(1, int(round(float(value))))
    if declared_value:
        params["declared_value"] = max(1, int(round(float(declared_value))))
    try:
        resp = _api(provider, "GET", "/v1/external/courier/serviceability/", params=params)
    except ShiprocketError:
        # Shiprocket answers an unserviceable route with a 4xx; treat as "none".
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    companies = (((data or {}).get("data") or {}).get("available_courier_companies")) or []
    out = []
    for c in companies:
        if not isinstance(c, dict):
            continue
        cid = c.get("courier_company_id")
        name = (c.get("courier_name") or "").strip()
        if not cid or not name:
            continue
        freight = _money(c.get("freight_charge"))
        cod_charges = _money(c.get("cod_charges"))
        rate = _money(c.get("rate"))
        if rate is None:
            rate = round((freight or 0) + (cod_charges or 0), 2) if freight is not None else None
        try:
            cod_ok = int(c.get("cod") or 0)
        except (TypeError, ValueError):
            cod_ok = 0
        try:
            days = int(str(c.get("estimated_delivery_days") or "").strip())
        except (TypeError, ValueError):
            days = None
        out.append(
            {
                "id": cid,
                "name": name,
                "rate": rate,
                "freight": freight,
                "cod_charges": cod_charges,
                "etd": str(c.get("etd") or ""),
                "days": days,
                "cod": cod_ok,
            }
        )
    out.sort(key=lambda c: (c["rate"] is None, c["rate"] or 0, c["days"] if c["days"] is not None else 99))
    return out


# --------------------------------------------------------------------------
# Live tracking (feeds the "Sync status" button and the tracking popup)
# --------------------------------------------------------------------------
def _find_tracking_data(data):
    """Locate the ``tracking_data`` block in a track response.

    ``track/awb`` answers ``{"tracking_data": {...}}``; ``track/shipment`` may
    wrap it per shipment id (``{"<id>": {"tracking_data": {...}}}``) or return a
    list of such objects. Returns ``{}`` when none is present.
    """
    if isinstance(data, list):
        for item in data:
            found = _find_tracking_data(item)
            if found:
                return found
        return {}
    if not isinstance(data, dict):
        return {}
    td = data.get("tracking_data")
    if isinstance(td, dict):
        return td
    for value in data.values():
        if isinstance(value, (dict, list)):
            found = _find_tracking_data(value)
            if found:
                return found
    return {}


def track(provider, *, awb: str = "", shipment_id: str = "") -> dict:
    """Fetch a shipment's live tracking from Shiprocket.

    Returns the payload in the same shape Shiprocket's tracking webhook pushes 
    ``{awb, courier_name, current_status, shipment_status_id, etd, track_url,
    order_id, shipment_id, scans:[{date, status, activity, location,
    "sr-status", "sr-status-label"}]}``  so a synced update is stored and
    rendered exactly like a pushed one. Raises :class:`ShiprocketError` when
    nothing can be tracked yet (no AWB assigned, unknown shipment).
    """
    awb = (awb or "").strip()
    shipment_id = str(shipment_id or "").strip()
    if awb:
        resp = _api(provider, "GET", f"/v1/external/courier/track/awb/{awb}")
    elif shipment_id:
        resp = _api(provider, "GET", f"/v1/external/courier/track/shipment/{shipment_id}")
    else:
        raise ShiprocketError(
            "This shipment has no AWB or Shiprocket shipment id yet, so it can't be tracked."
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise ShiprocketError("Shiprocket returned a non-JSON tracking response") from exc

    td = _find_tracking_data(data)
    if not td:
        raise ShiprocketError("Shiprocket has no tracking data for this shipment yet.")
    if td.get("error") and not td.get("shipment_track") and not td.get("shipment_track_activities"):
        raise ShiprocketError(f"Shiprocket: {str(td.get('error')).strip()}")

    tracks = td.get("shipment_track") if isinstance(td.get("shipment_track"), list) else []
    head = tracks[0] if tracks and isinstance(tracks[0], dict) else {}
    activities = td.get("shipment_track_activities")
    scans = []
    for a in activities if isinstance(activities, list) else []:
        if not isinstance(a, dict):
            continue
        scans.append(
            {
                "date": a.get("date") or "",
                "status": a.get("status") or "",
                "activity": a.get("activity") or "",
                "location": a.get("location") or "",
                "sr-status": str(a.get("sr-status") or ""),
                "sr-status-label": a.get("sr-status-label") or "",
            }
        )
    # Shiprocket lists activities newest first; the webhook sends them oldest
    # first. Store oldest first so both sources read the same way.
    scans.reverse()

    current = str(head.get("current_status") or "").strip()
    if not current and scans:
        current = str(scans[-1].get("sr-status-label") or scans[-1].get("activity") or "").strip()
    return {
        "awb": str(head.get("awb_code") or awb or ""),
        "courier_name": str(head.get("courier_name") or ""),
        "current_status": current,
        "shipment_status": current,
        "shipment_status_id": td.get("shipment_status") or "",
        "etd": td.get("etd") or head.get("edd") or "",
        "track_url": td.get("track_url") or "",
        # The track API's order_id is Shiprocket's own id, not the order number
        # we sent at booking (which is what a webhook's ``order_id`` carries),
        # so it travels as sr_order_id and the caller fills order_id.
        "order_id": "",
        "sr_order_id": str(head.get("order_id") or ""),
        "shipment_id": str(head.get("shipment_id") or shipment_id or ""),
        "origin": head.get("origin") or "",
        "destination": head.get("destination") or "",
        "delivered_date": head.get("delivered_date") or "",
        "pickup_date": head.get("pickup_date") or "",
        "scans": scans,
        "source": "sync",
    }


# --------------------------------------------------------------------------
# Courier label (Shiprocket's own printable label PDF)
# --------------------------------------------------------------------------
def generate_label(provider, shipment_ids) -> str:
    """Generate Shiprocket's courier label for one or more shipments.

    Returns the ``label_url`` of a single PDF covering every shipment id passed.
    Each shipment must already have an AWB (i.e. a courier assigned), which the
    pickup step guarantees. Raises :class:`ShiprocketError` otherwise.
    """
    ids = []
    for sid in shipment_ids or []:
        try:
            ids.append(int(str(sid).strip()))
        except (TypeError, ValueError):
            continue
    if not ids:
        raise ShiprocketError("No Shiprocket shipment ids to generate labels for.")
    resp = _api(provider, "POST", "/v1/external/courier/generate/label", json_payload={"shipment_id": ids})
    try:
        data = resp.json()
    except ValueError as exc:
        raise ShiprocketError("Shiprocket returned a non-JSON label response") from exc
    url = str((data or {}).get("label_url") or "").strip() if isinstance(data, dict) else ""
    if not url or str((data or {}).get("label_created")) in ("0", "False", "false"):
        detail = ""
        if isinstance(data, dict):
            detail = str(data.get("response") or data.get("message") or "").strip()
            failed = data.get("not_created")
            if failed and not detail:
                detail = f"labels not created for shipment(s) {failed}"
        raise ShiprocketError(
            "Shiprocket couldn't generate the courier label" + (f": {detail}" if detail else ".")
        )
    return url


def download_label(url: str) -> bytes:
    """Fetch a Shiprocket label PDF (served from Shiprocket's own storage)."""
    import requests

    # The URL comes from Shiprocket's response; still refuse anything that isn't
    # plain HTTPS so a malformed value can't point the server at an internal host.
    if not (url or "").lower().startswith("https://"):
        raise ShiprocketError("Shiprocket returned an invalid label link.")
    try:
        resp = requests.get(url, timeout=30, allow_redirects=False)
        resp.raise_for_status()
    except Exception as exc:
        raise ShiprocketError(f"Couldn't download the Shiprocket label: {exc}") from exc
    return resp.content
