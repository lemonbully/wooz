"""Live Delhivery pickup requests for a store's own courier account.

Delhivery pickups are **warehouse-level**, not per-waybill: one request tells
Delhivery to send a rider to a registered pickup address on a given date/time to
collect however many packages are ready. That is the opposite of Shiprocket,
which schedules its own pickup the moment an order is created  so OneWooz only
needs this one call from the reference's Delhivery client
(``website/shipping/providers/delhivery.py``:``request_pickup``); order and label
creation stay on the local stub until their live clients are ported.

The API key lives (encrypted) on the per-store provider row as ``api_token``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("onewooz.audit")

# Delhivery's pickup-request endpoint is served from the production tracking host
# regardless of environment; a store can override it via ``provider.base_url``.
DEFAULT_BASE_URL = "https://track.delhivery.com"


class DelhiveryError(Exception):
    """A Delhivery API call failed. The message is safe to log, not to show raw."""


def _base_url(provider) -> str:
    return ((provider.base_url or "").strip() or DEFAULT_BASE_URL).rstrip("/")


def is_configured(provider) -> bool:
    """True when the provider carries a usable Delhivery API token."""
    return bool((provider.api_token or "").strip())


def request_pickup(
    provider,
    *,
    pickup_location: str,
    pickup_date: str = "",
    pickup_time: str = "",
    expected_package_count: int = 1,
) -> dict:
    """Ask Delhivery to schedule a pickup at ``pickup_location``.

    ``pickup_location`` must exactly match a registered Pickup Address (warehouse)
    nickname in the merchant's Delhivery account  the reference defaulted a blank
    to ``"Default"``, which only ever worked for accounts that happened to keep
    that label. Here a blank is a configuration error the caller reports plainly.

    Returns Delhivery's parsed JSON. Raises :class:`DelhiveryError` on any failure.
    """
    import requests

    from django.utils import timezone

    token = (provider.api_token or "").strip()
    if not token:
        raise DelhiveryError(
            "Delhivery isn't connected yet  add your API token in Shipping settings."
        )
    location = (pickup_location or "").strip()
    if not location:
        raise DelhiveryError(
            "A pickup warehouse name is required  it must match a registered "
            "Pickup Address in your Delhivery account."
        )
    try:
        packages = max(1, int(expected_package_count or 1))
    except (TypeError, ValueError):
        packages = 1

    payload = {
        "pickup_time": (pickup_time or "").strip() or "14:00",
        "pickup_date": (pickup_date or "").strip() or timezone.localdate().isoformat(),
        "pickup_location": location,
        "expected_package_count": packages,
    }

    url = f"{_base_url(provider)}/fm/request/new/"
    headers = {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    logger.info("delhivery pickup request location=%s date=%s", location, payload["pickup_date"])
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
    except Exception as exc:
        raise DelhiveryError(f"request failed: {exc}") from exc

    if resp.status_code >= 400:
        body = getattr(resp, "text", "") or ""
        logger.warning("delhivery pickup -> %s: %s", resp.status_code, body[:500])
        raise DelhiveryError(_error_message(resp, body))
    try:
        return resp.json()
    except ValueError:
        return {"ok": True}


def check_pincode_serviceability(provider, pincode) -> dict:
    """Whether Delhivery services a delivery PIN code (and prepaid/COD support).

    Uses Delhivery's public Pincode Serviceability API. Returns
    ``{serviceable, prepaid, cod}``; raises :class:`DelhiveryError` on a
    transport/HTTP error so the caller can fall back gracefully.
    """
    import requests

    token = (provider.api_token or "").strip()
    if not token:
        raise DelhiveryError("Delhivery API token not set")
    pin = "".join(ch for ch in str(pincode or "") if ch.isdigit())[:10]
    if not pin:
        return {"serviceable": False, "prepaid": False, "cod": False}

    url = f"{_base_url(provider)}/c/api/pin-codes/json/"
    try:
        resp = requests.get(
            url,
            params={"filter_codes": pin, "token": token},
            headers={"Accept": "application/json"},
            timeout=20,
        )
    except Exception as exc:
        raise DelhiveryError(f"request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise DelhiveryError(f"Delhivery API returned {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        return {"serviceable": False, "prepaid": False, "cod": False}

    codes = data.get("delivery_codes") if isinstance(data, dict) else None
    prepaid = cod = False
    for entry in codes or []:
        pc = (entry or {}).get("postal_code") or entry or {}
        pre = str(pc.get("pre") or pc.get("pre_paid") or pc.get("prepaid") or "").upper()
        c = str(pc.get("cod") or "").upper()
        if pre in ("Y", "YES", "1"):
            prepaid = True
        if c in ("Y", "YES", "1"):
            cod = True
    serviceable = prepaid or cod or bool(codes)
    return {"serviceable": serviceable, "prepaid": prepaid, "cod": cod}


def _error_message(resp, body: str) -> str:
    """Turn a Delhivery error response into a message safe to show the owner.

    The most common ``/fm/request/new/`` failure is a pickup location that isn't a
    registered warehouse; catching that points the owner at the warehouse-name
    field instead of a bare status code. Anything else surfaces Delhivery's own
    error text so the reason is visible rather than swallowed.
    """
    detail = ""
    try:
        data = resp.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        detail = str(
            data.get("error") or data.get("message") or data.get("rmk") or ""
        ).strip()
        errs = data.get("errors") or data.get("pickup_location")
        if not detail and isinstance(errs, (list, tuple)) and errs:
            detail = "; ".join(str(e) for e in errs)

    haystack = f"{detail} {body}".lower()
    if ("pickup" in haystack or "warehouse" in haystack or "client" in haystack) and (
        "location" in haystack
        or "register" in haystack
        or "not found" in haystack
        or "does not exist" in haystack
    ):
        return (
            "Delhivery rejected the pickup location  it must exactly match a "
            "registered Pickup Address (warehouse) in your Delhivery account. Fix "
            "the warehouse name and try again."
            + (f" Delhivery said: {detail}" if detail else "")
        )
    if detail:
        return f"Delhivery rejected the pickup request: {detail}"
    return f"Delhivery API returned {resp.status_code}"
