"""Storefront analytics collection  native, privacy-light, first-party only.

Two halves work together:

* :func:`config_for` runs in the storefront middleware on every page render. It
  resolves (or mints) a first-party visitor id and a 30-minute session id, and
  hands the tracker script (``static/js/website/analytics.js``) the beacon URL
  and those ids via the ``#analytics-config`` node.
* :func:`ingest` receives the tracker's beacons at ``/api/track/`` and writes
  :class:`~commerce.models.AnalyticsSession` / :class:`~commerce.models.AnalyticsEvent`
  rows, which the dashboard (``commerce.views.analytics``) aggregates.

No cross-site identity, no third parties. Cookies are first-party, HttpOnly, and
SameSite=Lax; the session cookie is a sliding 30-minute window, so a fresh visit
after half an hour of inactivity is counted as a new session. Bots are dropped
before anything is written.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from commerce.models import AnalyticsEvent, AnalyticsSession, Product

VISITOR_COOKIE = "ow_vid"
SESSION_COOKIE = "ow_sid"
VISITOR_MAX_AGE = 60 * 60 * 24 * 730  # ~2 years
SESSION_MAX_AGE = 60 * 30             # 30-minute sliding session window

# Beacon event names → the model's funnel kinds. Anything else (performance,
# engagement, session summaries) is auxiliary telemetry merged onto the latest
# page view rather than stored as its own funnel row.
_KIND_MAP = {
    "page_view": AnalyticsEvent.Kind.PAGE_VIEW,
    "product_view": AnalyticsEvent.Kind.PRODUCT_VIEW,
    "add_to_cart": AnalyticsEvent.Kind.ADD_TO_CART,
    "checkout": AnalyticsEvent.Kind.CHECKOUT,
    "checkout_step": AnalyticsEvent.Kind.CHECKOUT,
    "purchase": AnalyticsEvent.Kind.PURCHASE,
}

_BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|bing|google|baidu|yandex|duckduck|facebookexternal|"
    r"embedly|quora|pinterest|preview|monitor|curl|wget|python-requests|okhttp|"
    r"headless|lighthouse|pingdom|uptime|semrush|ahrefs|mj12|dotbot",
    re.I,
)


def is_bot(ua: str) -> bool:
    return bool(_BOT_RE.search(ua or ""))


def _client_ip(request) -> str:
    """The visitor's public IP  leftmost X-Forwarded-For, else REMOTE_ADDR.

    Trustworthy here because the only thing in front of the application is our
    own edge (Caddy / load balancer), which sets the header.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or ""


def _geo_for(request) -> dict:
    """Resolve the visitor's location for a new session, best-effort.

    Prefers an accurate city/region/country from :mod:`accounts.geo` (offline
    GeoIP2 db or the free web service, cached per IP), and falls back to the
    edge's country header (Cloudflare's ``CF-IPCOUNTRY``) when no richer lookup
    is available. Never raises  analytics must never error a visitor's page.
    """
    # Behind the storefront CloudFront distribution the edge has already
    # geolocated the viewer (CloudFront-Viewer-* headers, forwarded by the
    # AllViewerAndCloudFrontHeaders origin request policy). Use that directly
    # it is free, instant and needs no third-party lookup.
    from urllib.parse import unquote

    cf_country = str(request.META.get("HTTP_CLOUDFRONT_VIEWER_COUNTRY", "") or "")[:2].upper()
    cf_city = unquote(str(request.META.get("HTTP_CLOUDFRONT_VIEWER_CITY", "") or ""))[:120]
    if cf_country and cf_city:
        cf_region = unquote(
            str(request.META.get("HTTP_CLOUDFRONT_VIEWER_COUNTRY_REGION_NAME", "") or "")
        )[:120]
        return {"country": cf_country, "region": cf_region, "city": cf_city}

    country = cf_country or str(request.META.get("HTTP_CF_IPCOUNTRY", "") or "")[:2].upper()
    if country in ("", "XX", "T1"):  # unknown / Tor per Cloudflare's convention
        country = ""
    city = region = ""
    try:
        from accounts import geo

        located = geo.locate(_client_ip(request))
        if located:
            city = (located.get("city") or "")[:120]
            region = (located.get("region") or "")[:120]
            country = (located.get("country_code") or country or "")[:2].upper()
    except Exception:
        pass
    return {"country": country, "region": region, "city": city}


def device_of(ua: str) -> str:
    ua = (ua or "").lower()
    if "ipad" in ua or ("tablet" in ua and "mobile" not in ua):
        return "tablet"
    if "mobi" in ua or "android" in ua or "iphone" in ua or "ipod" in ua:
        return "mobile"
    return "desktop"


def traffic_source(referrer: str, utm_source: str) -> str:
    """Coarse channel bucket, derived at read time from referrer + utm."""
    if utm_source:
        return utm_source.lower()[:40]
    host = re.sub(r"^https?://", "", referrer or "").split("/", 1)[0].lower()
    if not host:
        return "direct"
    if any(s in host for s in ("google", "bing", "yahoo", "duckduck", "baidu", "ecosia")):
        return "search"
    if any(s in host for s in ("facebook", "instagram", "t.co", "twitter", "x.com",
                               "linkedin", "pinterest", "youtube", "tiktok", "whatsapp")):
        return "social"
    return "referral"


def page_type_for(path: str) -> str:
    p = (path or "/").rstrip("/") or "/"
    if p == "/":
        return "home"
    if p.startswith("/products/") or p.startswith("/product/"):
        return "product"
    if p.startswith("/category"):
        return "category"
    if p in ("/shop", "/products"):
        return "shop"
    if p.startswith("/about"):
        return "about"
    if p.startswith("/blog"):
        return "blog"
    if p.startswith("/contact") or p.startswith("/enquiry"):
        return "contact"
    if p.startswith("/cart") or p.startswith("/checkout"):
        return "checkout"
    return "page"


# --------------------------------------------------------------------------
# Config for the tracker (middleware side)
# --------------------------------------------------------------------------
def config_for(request, site) -> dict:
    """Build the ``#analytics-config`` payload and stash cookie intent on the
    request for the middleware to apply to the response."""
    vid = request.COOKIES.get(VISITOR_COOKIE) or uuid.uuid4().hex
    sid = request.COOKIES.get(SESSION_COOKIE) or uuid.uuid4().hex
    request._ow_analytics_cookies = {
        "vid": vid,
        "sid": sid,
        "new_visitor": VISITOR_COOKIE not in request.COOKIES,
    }
    g = request.GET
    try:
        from django.urls import reverse

        track_url = reverse("public_track", urlconf=getattr(request, "urlconf", None))
    except Exception:
        track_url = "/api/track/"

    page_type = page_type_for(request.path)
    page_meta = {
        "path": request.path,
        "utm_source": (g.get("utm_source") or "")[:120],
        "utm_medium": (g.get("utm_medium") or "")[:120],
        "utm_campaign": (g.get("utm_campaign") or "")[:120],
    }
    # On a product page, resolve the slug → id once (indexed) so the tracker can
    # fire a product_view without the template carrying the id.
    if page_type == "product":
        m = re.search(r"/products/([^/]+)/?$", request.path)
        if m:
            try:
                pid = (
                    Product.objects.for_site(site)
                    .filter(slug=m.group(1))
                    .values_list("id", flat=True)
                    .first()
                )
                if pid:
                    page_meta["product_id"] = str(pid)
            except Exception:
                pass

    return {
        "track_url": track_url,
        "session_id": sid,
        "visitor_id": vid,
        "website_id": str(site.id),
        "page_type": page_type,
        "page_meta": page_meta,
    }


def apply_cookies(request, response) -> None:
    """Set/refresh the first-party analytics cookies on the response."""
    ck = getattr(request, "_ow_analytics_cookies", None)
    if not ck:
        return
    common = {"httponly": True, "samesite": "Lax", "secure": request.is_secure()}
    if ck.get("new_visitor"):
        response.set_cookie(VISITOR_COOKIE, ck["vid"], max_age=VISITOR_MAX_AGE, **common)
    # Refresh the session cookie every request so the 30-minute window slides.
    response.set_cookie(SESSION_COOKIE, ck["sid"], max_age=SESSION_MAX_AGE, **common)


# --------------------------------------------------------------------------
# Ingest (beacon side)
# --------------------------------------------------------------------------
def _to_decimal(val):
    if val in (None, ""):
        return None
    try:
        return Decimal(str(val)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _resolve_product(site, product_id):
    if not product_id:
        return None
    try:
        return Product.objects.for_site(site).filter(pk=product_id).first()
    except Exception:
        # A malformed id (not a valid UUID) must not error the beacon.
        return None


def _augment_latest(site, session, event_type, payload):
    """Merge auxiliary telemetry (perf, engagement) onto the session's most
    recent page view so the dashboard can average it without extra rows."""
    ev = (
        AnalyticsEvent.objects.for_site(site)
        .filter(session=session, kind=AnalyticsEvent.Kind.PAGE_VIEW)
        .order_by("-created_at")
        .first()
    )
    if ev is None:
        return
    meta = dict(ev.meta or {})
    if event_type == "performance":
        for k in ("page_load_ms", "fcp_ms"):
            if payload.get(k) is not None:
                meta[k] = payload.get(k)
    elif event_type == "product_engagement":
        if payload.get("score") is not None:
            meta["engagement"] = payload.get("score")
        if payload.get("max_scroll_percent") is not None:
            meta["max_scroll"] = payload.get("max_scroll_percent")
    elif event_type == "session_summary":
        if payload.get("max_scroll_percent") is not None:
            meta["max_scroll"] = payload.get("max_scroll_percent")
    ev.meta = meta
    ev.save(update_fields=["meta"])


def ingest(request, site, body: dict) -> None:
    """Persist one beacon. Silent on anything malformed  telemetry must never
    error a visitor's page."""
    ua = request.META.get("HTTP_USER_AGENT", "")
    if is_bot(ua):
        return

    event_type = str(body.get("event_type") or "").strip()[:60]
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
    page_meta = body.get("page_meta") if isinstance(body.get("page_meta"), dict) else {}
    sid = (request.COOKIES.get(SESSION_COOKIE) or str(body.get("session_id") or "")).strip()[:64]
    if not sid or not event_type:
        return

    now = timezone.now()
    vid = (request.COOKIES.get(VISITOR_COOKIE) or str(body.get("visitor_id") or "")).strip()[:64]
    scoped = AnalyticsSession.objects.for_site(site)
    session = scoped.filter(session_key=sid).order_by("-started_at").first()
    if session is None:
        referrer = str(payload.get("referrer") or request.META.get("HTTP_REFERER") or "")[:200]
        # A returning visitor is one we've already seen a session from.
        is_new = not (vid and scoped.filter(visitor_key=vid).exists())
        geo = _geo_for(request)
        session = AnalyticsSession.objects.create(
            site=site,
            session_key=sid,
            visitor_key=vid,
            is_new_visitor=is_new,
            referrer=referrer,
            utm_source=str(page_meta.get("utm_source") or "")[:120],
            utm_medium=str(page_meta.get("utm_medium") or "")[:120],
            utm_campaign=str(page_meta.get("utm_campaign") or "")[:120],
            device=device_of(ua),
            country=geo["country"],
            region=geo["region"],
            city=geo["city"],
            last_seen_at=now,
        )
    else:
        session.last_seen_at = now
        session.save(update_fields=["last_seen_at"])

    kind = _KIND_MAP.get(event_type)
    if kind is None:
        _augment_latest(site, session, event_type, payload)
        return

    keep = {k: payload[k] for k in ("title", "referrer", "step", "quantity") if k in payload}
    AnalyticsEvent.objects.create(
        site=site,
        session=session,
        kind=kind,
        path=str(payload.get("path") or page_meta.get("path") or "")[:255],
        product=_resolve_product(site, payload.get("product_id")),
        value=_to_decimal(payload.get("value")),
        meta=keep,
    )
