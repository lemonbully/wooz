"""Best-effort, human-readable location for an IP address.

Used in two places that need to say *where* a request came from:

* the security page  every sign-in and every live session (see
  :mod:`accounts.sessions` / :mod:`accounts.views`);
* storefront analytics  each visit's city / country (see
  :mod:`storefront.analytics`).

Turning an IP into a place needs a lookup source; this uses, in order of
preference:

1. a configured MaxMind GeoIP2 database (Django's ``GEOIP_PATH`` +
   the ``geoip2`` package)  the accurate, offline, privacy-preserving option;
2. a **free** IP-geolocation web service (``ipwho.is`` by default, no API key),
   used only for public addresses, with the result cached so the same address is
   never looked up twice in a day and the service's free rate limit is never a
   concern;
3. the ``country_code`` already recorded on the row, mapped to a name;
4. a classification of private / loopback addresses (dev + LAN);
5. otherwise an honest "Location unavailable".

It never raises  a location label must never be able to break the page that
shows it  and it never calls the web service for a private / loopback / reserved
address. The web service can be turned off with ``settings.GEOIP_HTTP_FALLBACK =
False`` (it then behaves exactly as the offline-only version did).
"""

from __future__ import annotations

import ipaddress
import json
import urllib.request

from django.conf import settings
from django.core.cache import cache

__all__ = ["describe", "locate", "country_name"]

# A compact code→name map covering the markets OneWooz sees most; anything not
# listed falls back to the raw code, which is still more useful than nothing.
_COUNTRIES = {
    "IN": "India", "US": "United States", "GB": "United Kingdom", "AE": "UAE",
    "SG": "Singapore", "AU": "Australia", "CA": "Canada", "DE": "Germany",
    "FR": "France", "NL": "Netherlands", "JP": "Japan", "CN": "China",
    "BD": "Bangladesh", "PK": "Pakistan", "LK": "Sri Lanka", "NP": "Nepal",
    "MY": "Malaysia", "ID": "Indonesia", "PH": "Philippines", "TH": "Thailand",
    "SA": "Saudi Arabia", "QA": "Qatar", "KW": "Kuwait", "OM": "Oman",
    "ZA": "South Africa", "NG": "Nigeria", "KE": "Kenya", "BR": "Brazil",
    "IT": "Italy", "ES": "Spain", "IE": "Ireland", "SE": "Sweden",
    "CH": "Switzerland", "NZ": "New Zealand", "HK": "Hong Kong",
}

#: How long a resolved location is cached, keyed by IP. Locations change rarely,
#: so a day keeps the free service's request volume negligible.
_CACHE_TTL = 60 * 60 * 24
#: A miss (no location) is cached for a shorter window so a transient failure is
#: retried before too long, without hammering the service.
_MISS_TTL = 60 * 60
_CACHE_PREFIX = "owgeo:v1:"
#: A sentinel cached for a known miss, so a miss is distinguishable from "not yet
#: looked up" (a plain cache miss returns ``None`` too).
_MISS = "__miss__"


def country_name(code: str) -> str:
    code = (code or "").strip().upper()
    if not code:
        return ""
    return _COUNTRIES.get(code, code)


def _classify_private(ip: str) -> str:
    """A label for a non-public address, or "" if the address is public."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ""
    if addr.is_loopback:
        return "This device (localhost)"
    if addr.is_private:
        return "Local network"
    if addr.is_reserved or addr.is_link_local:
        return "Local network"
    return ""


def _is_public(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


def _label_from(city: str, region: str, country: str) -> str:
    """Assemble "City, Region, Country" from whatever parts are present."""
    parts = [p for p in (city, region, country) if p]
    # Drop a region that just repeats the city (common for city-states).
    if len(parts) >= 2 and city and region and city.lower() == region.lower():
        parts = [p for i, p in enumerate(parts) if not (i == 1 and p == region)]
    return ", ".join(parts)


def _geoip_db(ip: str) -> dict | None:
    """A location from a configured GeoIP2 database, or ``None`` if unavailable."""
    if not getattr(settings, "GEOIP_PATH", None):
        return None
    try:
        from django.contrib.gis.geoip2 import GeoIP2

        g = GeoIP2()
        data = g.city(ip)
    except Exception:
        # No database, package missing, or the IP is not in it  never fatal.
        return None
    code = (data.get("country_code") or "").upper()
    city = data.get("city") or ""
    region = data.get("region") or data.get("region_name") or ""
    country = data.get("country_name") or country_name(code)
    return {
        "city": city,
        "region": region,
        "country_code": code,
        "country_name": country,
        "label": _label_from(city, region, country),
    }


def _geoip_http(ip: str) -> dict | None:
    """A location from the free web service, or ``None`` if it can't be resolved.

    Best-effort: any network error, timeout, non-JSON body, or unsuccessful
    lookup returns ``None``. Only ever called for public addresses.
    """
    if not getattr(settings, "GEOIP_HTTP_FALLBACK", True):
        return None
    url_tmpl = getattr(settings, "GEOIP_HTTP_URL", "https://ipwho.is/{ip}")
    timeout = float(getattr(settings, "GEOIP_HTTP_TIMEOUT", 2.5))
    try:
        url = url_tmpl.format(ip=ip)
        req = urllib.request.Request(url, headers={"User-Agent": "OneWooz/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(20000)
        data = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    # ipwho.is uses ``success: false`` for a failed lookup; ip-api uses
    # ``status: "fail"``. Treat either as no result.
    if data.get("success") is False or data.get("status") == "fail":
        return None
    code = (data.get("country_code") or data.get("countryCode") or "").upper()[:2]
    city = str(data.get("city") or "")[:120]
    region = str(data.get("region") or data.get("regionName") or "")[:120]
    country = str(data.get("country") or country_name(code))[:120]
    label = _label_from(city, region, country)
    if not label:
        return None
    return {
        "city": city,
        "region": region,
        "country_code": code,
        "country_name": country,
        "label": label,
    }


def locate(ip: str) -> dict | None:
    """Resolve ``ip`` to a structured location, or ``None`` if it can't be.

    Returns ``{"city", "region", "country_code", "country_name", "label"}``.
    Private / loopback / reserved addresses always return ``None`` (they have no
    geographic location and are never sent to the web service). Results are cached
    per IP so repeated lookups (a security page listing many sessions, a burst of
    analytics beacons) cost at most one resolution per address per day.
    """
    ip = (ip or "").strip()
    if not ip or not _is_public(ip):
        return None

    key = _CACHE_PREFIX + ip
    cached = cache.get(key)
    if cached == _MISS:
        return None
    if isinstance(cached, dict):
        return cached

    result = _geoip_db(ip) or _geoip_http(ip)
    try:
        if result:
            cache.set(key, result, _CACHE_TTL)
        else:
            cache.set(key, _MISS, _MISS_TTL)
    except Exception:
        pass
    return result


def describe(ip: str, country_code: str = "") -> str:
    """A friendly location for ``ip``; ``country_code`` is a stored fallback."""
    ip = (ip or "").strip()
    if not ip:
        return "Unknown location"

    private = _classify_private(ip)
    if private:
        return private

    located = locate(ip)
    if located and located.get("label"):
        return located["label"]

    name = country_name(country_code)
    if name:
        return name

    return "Location unavailable"
