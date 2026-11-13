"""Turn whatever an owner pastes as their "Google Maps link" into a map that
actually renders inside an ``<iframe>``.

Owners paste all sorts of things: a share link (``maps.app.goo.gl/…``), the
long ``google.com/maps/place/…`` URL from the address bar, the ``<iframe>``
code from Share → Embed a map, or just an address. Only Google's embed URLs may
be framed (every other Maps URL refuses with X-Frame-Options), so each form is
converted to one:

* an embed URL / embed code  used as-is;
* a place / search / directions / coordinates URL  rebuilt as a keyless
  ``google.com/maps?q=…&output=embed`` URL;
* a short share link  resolved to its long URL (one bounded request at save
  time, Google hosts only) and then converted;
* plain text  treated as an address to search for.

Anything pointing at a non-Google host is refused, so the storefront can never
be made to frame an arbitrary site.
"""

from __future__ import annotations

import html
import re
import urllib.error
import urllib.request
from urllib.parse import parse_qs, quote_plus, unquote_plus, urlencode, urlparse

EMBED_BASE = "https://www.google.com/maps"

# Share-link hosts that only redirect to a real Maps URL.
_SHORT_HOSTS = {"maps.app.goo.gl", "goo.gl", "g.co"}
_IFRAME_SRC = re.compile(r"""<iframe[^>]*?\ssrc\s*=\s*["']([^"']+)["']""", re.I)
_AT_COORDS = re.compile(r"@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)(?:,(\d+(?:\.\d+)?)z)?")
_DATA_COORDS = re.compile(r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)")
_MAX_LEN = 2000


_GOOGLE_HOST = re.compile(
    r"^(?:www\.|maps\.|consent\.)?google\.(?:com|[a-z]{2}|co\.[a-z]{2}|com\.[a-z]{2})$"
)


def _is_google_host(host: str) -> bool:
    """google.com / google.co.in / maps.google.de / www.google.com.au …"""
    return bool(_GOOGLE_HOST.match((host or "").lower().split(":")[0]))


def _is_short_host(host: str) -> bool:
    return (host or "").lower().split(":")[0] in _SHORT_HOSTS


def _build(q: str, ll: str = "", zoom: str = "") -> str:
    params = {"q": q}
    if ll:
        params["ll"] = ll
    params["z"] = zoom or "15"
    params["output"] = "embed"
    return f"{EMBED_BASE}?{urlencode(params)}"


def _clean_input(raw) -> str:
    s = html.unescape(str(raw or "")).strip()[:_MAX_LEN]
    m = _IFRAME_SRC.search(s)
    if m:
        s = html.unescape(m.group(1)).strip()
    return s


def _as_url(s: str):
    """A parsed URL when ``s`` looks like one (scheme optional), else None."""
    if not s or any(c.isspace() for c in s):
        return None
    candidate = s if "://" in s else ("https://" + s if "." in s.split("/")[0] else "")
    if not candidate:
        return None
    u = urlparse(candidate)
    if u.scheme not in ("http", "https") or not u.netloc:
        return None
    return u


def to_embed_url(raw) -> str:
    """The embeddable URL for ``raw``, or "" when it can't be converted without
    a network request (a short link) or isn't a Google Maps location."""
    s = _clean_input(raw)
    if not s:
        return ""
    u = _as_url(s)
    if u is None:
        # Plain text: an address, a place name, or "lat,lng".
        return _build(s)

    host = u.netloc.lower()
    if _is_short_host(host) or not _is_google_host(host):
        return ""

    qs = parse_qs(u.query)
    # Google's cookie-consent interstitial wraps the real URL in ?continue=.
    if host.startswith("consent.") and qs.get("continue"):
        return to_embed_url(qs["continue"][0])

    # Embed URLs are served from www.google.com whatever country domain they
    # were copied from (the one host the storefront CSP admits for maps).
    path = u.path or ""
    if path.startswith("/maps/embed"):
        return "https://www.google.com" + path + (("?" + u.query) if u.query else "")
    if "output=embed" in u.query and path.rstrip("/") in ("", "/maps"):
        return "https://www.google.com/maps?" + u.query

    at = _AT_COORDS.search(path)
    data = _DATA_COORDS.search(u.path + "?" + u.query)
    zoom = ""
    ll = ""
    if data:
        ll = f"{data.group(1)},{data.group(2)}"
    elif at:
        ll = f"{at.group(1)},{at.group(2)}"
    if at and at.group(3):
        zoom = str(int(float(at.group(3))))

    for key in ("q", "query", "daddr", "destination", "ll"):
        val = (qs.get(key) or [""])[0].strip()
        if val:
            return _build(val, "" if key == "ll" else ll, zoom)

    segments = [unquote_plus(p) for p in path.split("/") if p]
    # /maps/place/<name>/@lat,lng/…  and  /maps/search/<term>/…
    for marker in ("place", "search"):
        if marker in segments:
            i = segments.index(marker)
            if i + 1 < len(segments) and not segments[i + 1].startswith("@"):
                return _build(segments[i + 1], ll, zoom)
    # /maps/dir/<from>/<to>/…  shows the destination.
    if "dir" in segments:
        stops = [p for p in segments[segments.index("dir") + 1:] if p and not p.startswith(("@", "data="))]
        if stops:
            return _build(stops[-1], ll, zoom)
    if ll:
        return _build(ll, "", zoom)
    return ""


def resolve_short_link(raw, timeout: float = 4.0) -> str:
    """Follow a Google short share link to its long Maps URL ("" on failure).

    Only ever requests Google short-link / Maps hosts over HTTPS, reads no body
    and stops after a few hops, so an owner-supplied value can't be used to make
    the server fetch anything else.
    """
    s = _clean_input(raw)
    u = _as_url(s)
    if u is None or not _is_short_host(u.netloc):
        return ""

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):  # noqa: D401 - stdlib hook
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    url = "https://" + u.netloc + u.path + (("?" + u.query) if u.query else "")
    for _ in range(5):
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "Mozilla/5.0 (OneWooz map resolver)"})
        try:
            with opener.open(req, timeout=timeout) as resp:  # 2xx: no redirect left
                return url if _is_google_host(urlparse(url).netloc) else ""
        except urllib.error.HTTPError as exc:
            location = exc.headers.get("Location") if exc.headers else None
            if exc.code not in (301, 302, 303, 307, 308) or not location:
                return ""
        except Exception:
            return ""
        nxt = urlparse(location)
        if nxt.scheme != "https" or not (_is_short_host(nxt.netloc) or _is_google_host(nxt.netloc)):
            return ""
        url = location
        if _is_google_host(nxt.netloc):
            return url
    return ""


def embed_for_link(raw) -> str:
    """Offline conversion first; a short share link is resolved over the
    network. Use at save time / in the editor preview, never while rendering."""
    embed = to_embed_url(raw)
    if embed:
        return embed
    long_url = resolve_short_link(raw)
    return to_embed_url(long_url) if long_url else ""


def _address_query(ci: dict) -> str:
    parts = [
        str(ci.get(k) or "").strip()
        for k in ("address", "city", "state", "postal_code", "country")
    ]
    return ", ".join(" ".join(p.split()) for p in parts if p)


def open_link(raw, ci: dict | None = None) -> str:
    """A link that opens the location in Google Maps (for "Get directions")."""
    s = _clean_input(raw)
    u = _as_url(s)
    if u is not None and (_is_google_host(u.netloc) or _is_short_host(u.netloc)):
        if "/maps/embed" in u.path:
            q = _address_query(ci or {})
            return f"https://www.google.com/maps/search/?api=1&query={quote_plus(q)}" if q else ""
        return "https://" + u.netloc + u.path + (("?" + u.query) if u.query else "")
    if s and u is None:
        return f"https://www.google.com/maps/search/?api=1&query={quote_plus(s)}"
    return ""


def contact_map(ci) -> dict:
    """``{"embed", "link"}`` for a store's ``contact_info`` (empty strings when
    no map is set). Render-time safe: never makes a network request  it uses
    the embed URL resolved at save time, else converts offline, else (a short
    link saved before resolution existed) falls back to the store's address."""
    if not isinstance(ci, dict):
        return {"embed": "", "link": ""}
    raw = str(ci.get("map_location") or "").strip()
    if not raw:
        return {"embed": "", "link": ""}
    embed = ""
    if ci.get("map_embed_source") == raw:
        embed = str(ci.get("map_embed_url") or "")
    if not embed:
        embed = to_embed_url(raw)
    if not embed:
        q = _address_query(ci)
        embed = _build(q) if q else ""
    return {"embed": embed, "link": open_link(raw, ci)}


def apply_to_contact_info(ci) -> None:
    """Store the resolved embed URL beside ``map_location`` (called on save)."""
    if not isinstance(ci, dict):
        return
    raw = str(ci.get("map_location") or "").strip()
    if not raw:
        ci.pop("map_embed_url", None)
        ci.pop("map_embed_source", None)
        return
    if ci.get("map_embed_source") == raw:
        # Already handled for this exact link; never re-hit the network on every
        # autosave (a link that failed to resolve falls back to the address).
        if not ci.get("map_embed_url"):
            ci["map_embed_url"] = to_embed_url(raw)
        return
    ci["map_embed_url"] = embed_for_link(raw)
    ci["map_embed_source"] = raw
