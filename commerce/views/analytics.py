"""Store analytics  an advanced, three-surface intelligence dashboard.

Reads the first-party events collected by ``storefront.analytics`` (page views,
product views, cart/checkout/purchase, plus auxiliary perf/engagement telemetry
merged onto page views) alongside the live catalogue, orders, payments,
customers and reviews, and turns them into three distinct analytical surfaces:

* **Overview**  traffic, audience, acquisition and the conversion funnel, each
  with a period-over-period delta so the owner sees direction, not just level.
* **Site performance**  technical quality: load time and first paint against
  web-vitals thresholds, engagement depth, session quality, bounce trend and a
  weekday×hour activity heatmap.
* **Sales analysis**  revenue, orders, AOV, product/category leaders, discount
  impact and payment mix, then a branch of **D2C-specific** cohorts (repeat
  rate, LTV, review sentiment, cart recovery) or **B2B-specific** intelligence
  (channel split, order size, GST/company share, enquiry pipeline, top accounts).

Every chart's geometry is computed here and drawn as inline SVG in the template
 no external chart library, in keeping with the hand-authored front end. Each
tab closes with an **insights** panel: prioritised, plain-language, outcome-
oriented recommendations generated from the numbers and tuned to the store's
business model.
"""

from __future__ import annotations

import math
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import (
    Avg,
    Count,
    DurationField,
    ExpressionWrapper,
    F,
    Q,
    Sum,
)
from django.db.models.functions import (
    ExtractHour,
    ExtractIsoWeekDay,
    TruncDate,
)
from django.shortcuts import render
from django.utils import timezone

from commerce.context import get_owned_site, store_context
from commerce.models import (
    AnalyticsEvent,
    AnalyticsSession,
    Customer,
    Enquiry,
    Order,
    OrderItem,
    Payment,
    Review,
    WishlistItem,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PAID_STATUSES = (Order.Status.PAID, Order.Status.SHIPPED, Order.Status.DELIVERED)

# Fixed SVG canvas for the trend chart (viewBox units).
_CW, _CH, _PAD = 720.0, 240.0, 28.0

# A restrained categorical ramp: the one accent, then ink shades  on-brand for
# a design system with a single accent and warm neutrals.
CHART_COLORS = ["#4b2bf5", "#26262a", "#6a44fb", "#5b5b62", "#8f8f96", "#c3c3c8"]

_CURRENCY_SYMBOL = {"INR": "₹", "USD": "$", "EUR": "€"}

# Web-vitals-style thresholds (milliseconds). Good / needs-improvement / poor.
_LOAD_GOOD, _LOAD_POOR = 2500, 4000
_FCP_GOOD, _FCP_POOR = 1800, 3000

VALID_TABS = ("overview", "performance", "sales")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _pct(part, whole):
    return round((part / whole) * 100, 1) if whole else 0.0


def _money(symbol, amount) -> str:
    return f"{symbol}{amount:,.0f}"


def _fmt_duration(seconds: int) -> str:
    if seconds <= 0:
        return "0s"
    m, s = divmod(int(seconds), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _delta(current, previous) -> dict:
    """Period-over-period change as a rendered dict.

    ``dir`` is one of up/down/flat; the template colours up green, down red and
    flat grey. ``new`` flags the case where there was nothing to compare to.
    """
    cur = float(current or 0)
    prev = float(previous or 0)
    if prev == 0:
        if cur == 0:
            return {"dir": "flat", "label": "no change", "pct": 0.0, "new": False}
        return {"dir": "up", "label": "new activity", "pct": 100.0, "new": True}
    change = (cur - prev) / prev * 100
    direction = "flat" if abs(change) < 0.05 else ("up" if change > 0 else "down")
    return {
        "dir": direction,
        "label": f"{change:+.1f}% vs prev.",
        "pct": round(change, 1),
        "new": False,
    }


def _percentile(sorted_values, p: float):
    """Nearest-rank percentile of a pre-sorted list; 0 for an empty list."""
    if not sorted_values:
        return 0
    k = max(0, min(len(sorted_values) - 1, int(round((p / 100) * (len(sorted_values) - 1)))))
    return sorted_values[k]


# ---------------------------------------------------------------------------
# SVG geometry
# ---------------------------------------------------------------------------
def _line_points(rows, key, maximum):
    """Polyline points string for a daily series scaled to ``maximum``."""
    n = len(rows)
    if n == 0 or maximum <= 0:
        return ""
    span = _CW - 2 * _PAD
    step = span / max(1, n - 1)
    pts = []
    for i, row in enumerate(rows):
        x = _PAD + i * step
        y = (_CH - _PAD) - (float(row[key]) / maximum) * (_CH - 2 * _PAD)
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


def _line_chart(rows, series, unit="", money_symbol=""):
    """Build a full multi-series line chart context from daily ``rows``.

    ``series`` is a list of ``(key, css)`` pairs; the y-axis is shared. Returns
    the geometry the template needs plus formatted min/max/last labels.
    """
    all_vals = [float(r[k]) for k, _ in series for r in rows] or [0.0]
    maximum = max([1.0] + all_vals)
    lines = []
    for key, css in series:
        lines.append({"css": css, "key": key, "points": _line_points(rows, key, maximum)})
    # The first (primary) series also gets a filled area beneath it.
    area = ""
    if lines and lines[0]["points"]:
        area = f"{_PAD},{_CH - _PAD} {lines[0]['points']} {_CW - _PAD},{_CH - _PAD}"

    def _fmt(v):
        if money_symbol:
            return _money(money_symbol, v)
        return f"{v:,.0f}{unit}"

    return {
        "lines": lines,
        "area": area,
        "cw": _CW,
        "ch": _CH,
        "pad": _PAD,
        "baseline": _CH - _PAD,
        "left": _PAD,
        "right": _CW - _PAD,
        "max_label": _fmt(maximum),
        "first_label": rows[0]["label"] if rows else "",
        "last_label": rows[-1]["label"] if rows else "",
    }


_DONUT_R = 54.0
_DONUT_C = 2 * math.pi * _DONUT_R


def _donut(items, total):
    """Stroke-dash segments for a donut chart.

    Each item gets ``dash`` (``len gap``) and ``offset`` so segments stack
    clockwise from 12 o'clock (the SVG group is rotated -90°). Colours cycle
    through the restrained ramp.
    """
    segs = []
    cursor = 0.0
    for i, it in enumerate(items):
        frac = (it["value"] / total) if total else 0.0
        length = frac * _DONUT_C
        segs.append({
            "label": it["label"],
            "value": it["value"],
            "pct": round(frac * 100, 1),
            "color": CHART_COLORS[i % len(CHART_COLORS)],
            "dash": f"{length:.2f} {_DONUT_C - length:.2f}",
            "offset": f"{-cursor:.2f}",
        })
        cursor += length
    return {"r": _DONUT_R, "c": _DONUT_C, "segments": segs}


def _bars(items, value_key="value", label_key="label", fmt=None, accent_first=False):
    """Horizontal bar rows scaled to the largest value."""
    top = max([1] + [it[value_key] for it in items])
    out = []
    for i, it in enumerate(items):
        out.append({
            "label": it[label_key],
            "value": fmt(it[value_key]) if fmt else it[value_key],
            "raw": it[value_key],
            "pct": _pct(it[value_key], top),
            "accent": accent_first and i == 0,
            "extra": it.get("extra", ""),
        })
    return out


def _geo_bars(sessions, limit=8):
    """Top visitor locations, labelled "City, Country" where the city is known.

    Groups sessions by their resolved city + country, builds a human label
    (city + full country name, falling back to just the country when no city was
    resolved), merges rows that collapse to the same label, and returns the top
    ``limit`` as ready-to-render bar items. Rows with no location at all are
    dropped rather than shown as "Unknown".
    """
    from accounts.geo import country_name

    tallies: dict[str, int] = {}
    rows = (
        sessions.values("city", "country")
        .annotate(n=Count("id"))
        .order_by("-n")
    )
    for r in rows:
        city = (r.get("city") or "").strip()
        country = country_name((r.get("country") or "").strip())
        if city and country:
            label = f"{city}, {country}"
        else:
            label = city or country
        if not label:
            continue
        tallies[label] = tallies.get(label, 0) + r["n"]

    ordered = sorted(tallies.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"label": label, "value": n} for label, n in ordered]


# ---------------------------------------------------------------------------
# Daily-series scaffolding
# ---------------------------------------------------------------------------
def _day_axis(now, days):
    start = (now - timedelta(days=days - 1)).date()
    return [start + timedelta(days=i) for i in range(days)]


def _daily_map(qs, date_field):
    return {
        r["d"]: r["n"]
        for r in qs.annotate(d=TruncDate(date_field)).values("d").annotate(n=Count("id"))
    }


def _daily_sum(qs, date_field, sum_field):
    return {
        r["d"]: (r["s"] or Decimal("0.00"))
        for r in qs.annotate(d=TruncDate(date_field)).values("d").annotate(s=Sum(sum_field))
    }


# ---------------------------------------------------------------------------
# Shared window metrics (used for period-over-period deltas)
# ---------------------------------------------------------------------------
def _window_metrics(site, since, until) -> dict:
    events = AnalyticsEvent.objects.for_site(site).filter(
        created_at__gte=since, created_at__lt=until
    )
    sessions = AnalyticsSession.objects.for_site(site).filter(
        started_at__gte=since, started_at__lt=until
    )
    orders = Order.objects.for_site(site).filter(created_at__gte=since, created_at__lt=until)
    # Revenue counts every order that was actually placed (a COD order never
    # reaches "paid"); only cancelled/archived orders are excluded.
    counted = orders.filter(status__in=Order.REVENUE_STATUSES, replacement_seq=0)

    pageviews = events.filter(kind=AnalyticsEvent.Kind.PAGE_VIEW).count()
    session_count = sessions.count()
    visitors = sessions.exclude(visitor_key="").values("visitor_key").distinct().count()
    purchases = events.filter(kind=AnalyticsEvent.Kind.PURCHASE).count()
    order_count = counted.count()
    revenue = counted.aggregate(t=Sum("total"))["t"] or Decimal("0.00")
    aov = counted.aggregate(a=Avg("total"))["a"] or Decimal("0.00")
    atc = events.filter(kind=AnalyticsEvent.Kind.ADD_TO_CART).count()

    bounced = (
        sessions.annotate(
            pv=Count("events", filter=Q(events__kind=AnalyticsEvent.Kind.PAGE_VIEW))
        )
        .filter(pv__lte=1)
        .count()
    )

    return {
        "pageviews": pageviews,
        "sessions": session_count,
        "visitors": visitors,
        "purchases": purchases,
        "orders": order_count,
        "revenue": revenue,
        "aov": aov,
        "add_to_cart": atc,
        "bounce_rate": _pct(bounced, session_count),
        "conversion": _pct(purchases or order_count, session_count),
    }


# ---------------------------------------------------------------------------
# Traffic-source bucketing (read-time, from referrer + utm)
# ---------------------------------------------------------------------------
def _source_bucket(referrer: str, utm_source: str) -> str:
    from storefront.analytics import traffic_source

    return traffic_source(referrer or "", utm_source or "")


# ===========================================================================
# TAB 1  OVERVIEW
# ===========================================================================
def _overview_context(site, now, days, since, events, sessions, cur_metrics, prev_metrics):
    symbol = _CURRENCY_SYMBOL.get(site.currency, "")
    session_count = cur_metrics["sessions"]

    new_visitors = sessions.filter(is_new_visitor=True).count()
    returning = max(0, session_count - new_visitors)

    dur = sessions.filter(last_seen_at__isnull=False).aggregate(
        d=Avg(ExpressionWrapper(F("last_seen_at") - F("started_at"), output_field=DurationField()))
    )["d"]
    avg_duration = int(dur.total_seconds()) if dur else 0

    engaged = max(0, session_count - _bounced_count(sessions))
    engagement_rate = _pct(engaged, session_count)

    # KPI tiles with deltas ------------------------------------------------
    kpis = [
        _kpi("Visitors", f"{cur_metrics['visitors']:,}", f"{session_count:,} sessions",
             _delta(cur_metrics["visitors"], prev_metrics["visitors"])),
        _kpi("Page views", f"{cur_metrics['pageviews']:,}",
             f"{(cur_metrics['pageviews'] / session_count):.1f} / session" if session_count else "",
             _delta(cur_metrics["pageviews"], prev_metrics["pageviews"])),
        _kpi("Engagement", f"{engagement_rate:.0f}%", "engaged sessions",
             _delta(engagement_rate, 100 - prev_metrics["bounce_rate"])),
        _kpi("Avg. visit", _fmt_duration(avg_duration), "time on site", None),
        _kpi("Conversion", f"{cur_metrics['conversion']:.2f}%", "visit → order",
             _delta(cur_metrics["conversion"], prev_metrics["conversion"])),
        _kpi("Revenue", _money(symbol, cur_metrics["revenue"]), f"{cur_metrics['orders']:,} orders",
             _delta(cur_metrics["revenue"], prev_metrics["revenue"])),
    ]
    if site.is_b2b:
        enq = Enquiry.objects.for_site(site).filter(created_at__gte=since).count()
        kpis.append(_kpi("Enquiries", f"{enq:,}", "leads captured", None))

    # Daily trend (page views + sessions) ----------------------------------
    axis = _day_axis(now, days)
    pv_map = _daily_map(events.filter(kind=AnalyticsEvent.Kind.PAGE_VIEW), "created_at")
    se_map = _daily_map(sessions, "started_at")
    rows = [
        {"label": d.strftime("%d %b"), "pv": pv_map.get(d, 0), "se": se_map.get(d, 0)}
        for d in axis
    ]
    trend = _line_chart(rows, [("pv", "pv"), ("se", "se")])

    # Acquisition  traffic sources (donut) --------------------------------
    src_counts = {}
    for s in sessions.values("referrer", "utm_source"):
        b = _source_bucket(s["referrer"], s["utm_source"])
        src_counts[b] = src_counts.get(b, 0) + 1
    src_items = [
        {"label": k.title(), "value": v}
        for k, v in sorted(src_counts.items(), key=lambda kv: kv[1], reverse=True)
    ]
    sources_donut = _donut(src_items[:6], session_count)

    # Campaigns (utm_campaign) ---------------------------------------------
    campaigns = [
        {"label": r["utm_campaign"], "value": r["n"]}
        for r in sessions.exclude(utm_campaign="")
        .values("utm_campaign").annotate(n=Count("id")).order_by("-n")[:6]
    ]

    # Devices + new/returning ----------------------------------------------
    devices = [
        {"label": (r["device"] or "unknown").title(), "value": r["n"]}
        for r in sessions.values("device").annotate(n=Count("id")).order_by("-n")
    ]
    devices_donut = _donut(devices, session_count)

    # Geography ------------------------------------------------------------
    # Prefer the accurate city (resolved at session start), falling back to the
    # country name, so the chart reads "Mumbai, India" where we have it and
    # "India" where we only have the country. Rows with neither are ignored.
    geo = _bars(_geo_bars(sessions), accent_first=True)

    # Conversion funnel with step-to-step drop-off -------------------------
    steps = [
        ("Sessions", session_count),
        ("Product views", events.filter(kind=AnalyticsEvent.Kind.PRODUCT_VIEW).count()),
        ("Add to cart", cur_metrics["add_to_cart"]),
        ("Checkout", events.filter(kind=AnalyticsEvent.Kind.CHECKOUT).count()),
        ("Purchase", cur_metrics["purchases"] or cur_metrics["orders"]),
    ]
    funnel = _funnel(steps)

    # Top pages ------------------------------------------------------------
    top_pages = _bars([
        {"label": r["path"], "value": r["n"]}
        for r in events.filter(kind=AnalyticsEvent.Kind.PAGE_VIEW).exclude(path="")
        .values("path").annotate(n=Count("id")).order_by("-n")[:8]
    ], accent_first=True)

    insights = _overview_insights(site, cur_metrics, prev_metrics, engagement_rate, funnel, src_items)

    return {
        "kpis": kpis,
        "trend": trend,
        "sources_donut": sources_donut,
        "campaigns": campaigns,
        "devices_donut": devices_donut,
        "geo": geo,
        "funnel": funnel,
        "top_pages": top_pages,
        "new_visitors": new_visitors,
        "returning_visitors": returning,
        "new_pct": _pct(new_visitors, session_count),
        "insights": insights,
    }


def _bounced_count(sessions):
    return (
        sessions.annotate(
            pv=Count("events", filter=Q(events__kind=AnalyticsEvent.Kind.PAGE_VIEW))
        )
        .filter(pv__lte=1)
        .count()
    )


def _kpi(label, value, sub, delta):
    return {"label": label, "value": value, "sub": sub, "delta": delta}


def _funnel(steps):
    top = max([1] + [v for _, v in steps])
    out = []
    prev = None
    for label, value in steps:
        drop = None if prev in (None, 0) else round((1 - value / prev) * 100, 1)
        out.append({
            "label": label,
            "value": value,
            "pct": _pct(value, top),
            "step_pct": _pct(value, prev) if prev else 100.0,
            "drop": drop,
        })
        prev = value
    return out


# ===========================================================================
# TAB 2  SITE PERFORMANCE
# ===========================================================================
def _performance_context(site, now, days, since, events, sessions, cur_metrics):
    session_count = cur_metrics["sessions"]

    # Pull page-view telemetry (perf + engagement live in meta, merged there
    # by the ingest). One pass in Python, capped so a very busy window can't
    # blow up the request.
    loads, fcps, scrolls, engagements = [], [], [], []
    per_path_load = {}
    for ev in (
        events.filter(kind=AnalyticsEvent.Kind.PAGE_VIEW)
        .values("path", "meta")[:20000]
    ):
        meta = ev["meta"] or {}
        pl = _num(meta.get("page_load_ms"))
        if pl is not None:
            loads.append(pl)
            path = ev["path"] or "/"
            per_path_load.setdefault(path, []).append(pl)
        fcp = _num(meta.get("fcp_ms"))
        if fcp is not None:
            fcps.append(fcp)
        sc = _num(meta.get("max_scroll"))
        if sc is not None:
            scrolls.append(sc)
        en = _num(meta.get("engagement"))
        if en is not None:
            engagements.append(en)

    loads_sorted = sorted(loads)
    avg_load = sum(loads) / len(loads) if loads else 0
    p75_load = _percentile(loads_sorted, 75)
    avg_fcp = sum(fcps) / len(fcps) if fcps else 0
    avg_scroll = sum(scrolls) / len(scrolls) if scrolls else 0
    avg_engagement = sum(engagements) / len(engagements) if engagements else 0

    # Web-vitals-style rating buckets on load time.
    good = sum(1 for v in loads if v <= _LOAD_GOOD)
    needs = sum(1 for v in loads if _LOAD_GOOD < v <= _LOAD_POOR)
    poor = sum(1 for v in loads if v > _LOAD_POOR)
    perf_score = _pct(good, len(loads)) if loads else 0.0

    vitals = [
        _vital("Load time", p75_load, _LOAD_GOOD, _LOAD_POOR, "ms", "p75"),
        _vital("First paint", avg_fcp, _FCP_GOOD, _FCP_POOR, "ms", "avg"),
    ]
    vitals_dist = _donut([
        {"label": "Fast", "value": good},
        {"label": "Moderate", "value": needs},
        {"label": "Slow", "value": poor},
    ], len(loads))

    # Engagement quality tiles.
    bounce = cur_metrics["bounce_rate"]
    pages_per = (cur_metrics["pageviews"] / session_count) if session_count else 0
    dur = sessions.filter(last_seen_at__isnull=False).aggregate(
        d=Avg(ExpressionWrapper(F("last_seen_at") - F("started_at"), output_field=DurationField()))
    )["d"]
    avg_duration = int(dur.total_seconds()) if dur else 0

    quality = [
        _kpi("Performance score", f"{perf_score:.0f}", "fast page loads", None),
        _kpi("Load time (p75)", f"{p75_load:,}ms" if loads else "", f"avg {avg_load:,.0f}ms" if loads else "no data", None),
        _kpi("First paint", f"{avg_fcp:,.0f}ms" if fcps else "", "avg FCP", None),
        _kpi("Scroll depth", f"{avg_scroll:.0f}%" if scrolls else "", "avg reach", None),
        _kpi("Bounce rate", f"{bounce:.0f}%", "single-page visits", None),
        _kpi("Pages / session", f"{pages_per:.1f}", "browse depth", None),
    ]

    # Bounce-rate trend over time. One query returns per-session (day, page-view
    # count); we tally in Python, so a 365-day window costs one query, not 365.
    axis = _day_axis(now, days)
    total_by_day, bounced_by_day = {}, {}
    for r in (
        sessions.annotate(
            pv=Count("events", filter=Q(events__kind=AnalyticsEvent.Kind.PAGE_VIEW)),
            d=TruncDate("started_at"),
        ).values("id", "d", "pv")
    ):
        d = r["d"]
        total_by_day[d] = total_by_day.get(d, 0) + 1
        if (r["pv"] or 0) <= 1:
            bounced_by_day[d] = bounced_by_day.get(d, 0) + 1
    rows = [
        {"label": d.strftime("%d %b"),
         "bounce": _pct(bounced_by_day.get(d, 0), total_by_day.get(d, 0))}
        for d in axis
    ]
    bounce_trend = _line_chart(rows, [("bounce", "pv")], unit="%")

    # Session-duration distribution.
    duration_buckets = _duration_distribution(sessions)

    # Slowest pages (by avg load), min sample of 1.
    slowest = sorted(
        ({"label": p, "value": round(sum(v) / len(v)), "extra": f"{len(v)} views"}
         for p, v in per_path_load.items()),
        key=lambda x: x["value"], reverse=True,
    )[:8]
    slowest = _bars(slowest, fmt=lambda v: f"{v:,}ms")

    # Activity heatmap: ISO weekday (1=Mon..7=Sun) × hour-of-day.
    heatmap = _activity_heatmap(sessions)

    # Device performance split (avg load per device from session→events).
    device_perf = _device_performance(events)

    insights = _performance_insights(perf_score, p75_load, avg_fcp, bounce, pages_per, avg_scroll, avg_engagement)

    return {
        "vitals": vitals,
        "vitals_dist": vitals_dist,
        "vitals_total": len(loads),
        "quality": quality,
        "bounce_trend": bounce_trend,
        "duration_buckets": duration_buckets,
        "slowest": slowest,
        "heatmap": heatmap,
        "device_perf": device_perf,
        "avg_engagement": round(avg_engagement, 1),
        "insights": insights,
    }


def _num(v):
    try:
        if v is None or v == "":
            return None
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _vital(label, value, good, poor, unit, note):
    if value <= 0:
        rating = "none"
    elif value <= good:
        rating = "good"
    elif value <= poor:
        rating = "needs"
    else:
        rating = "poor"
    # Position of the marker on a 0..(poor*1.5) scale for the gauge bar.
    scale = poor * 1.5
    pos = min(100, (value / scale) * 100) if scale else 0
    good_pct = round((good / scale) * 100, 1) if scale else 0
    poor_pct = round((poor / scale) * 100, 1) if scale else 0
    return {
        "label": label,
        "value": f"{value:,.0f}{unit}" if value else "",
        "rating": rating,
        "note": note,
        "pos": round(pos, 1),
        "good_pct": good_pct,
        "needs_pct": round(poor_pct - good_pct, 1),
    }


def _duration_distribution(sessions):
    buckets = [
        ("0–10s", 0, 10),
        ("10–30s", 10, 30),
        ("30s–1m", 30, 60),
        ("1–3m", 60, 180),
        ("3–10m", 180, 600),
        ("10m+", 600, 10 ** 9),
    ]
    counts = [0] * len(buckets)
    for s in sessions.filter(last_seen_at__isnull=False).annotate(
        secs=ExpressionWrapper(F("last_seen_at") - F("started_at"), output_field=DurationField())
    ).values("secs"):
        secs = s["secs"].total_seconds() if s["secs"] else 0
        for i, (_, lo, hi) in enumerate(buckets):
            if lo <= secs < hi:
                counts[i] += 1
                break
    return _bars([{"label": b[0], "value": c} for b, c in zip(buckets, counts)])


def _activity_heatmap(sessions):
    grid = {}
    for r in (
        sessions.annotate(wd=ExtractIsoWeekDay("started_at"), hr=ExtractHour("started_at"))
        .values("wd", "hr").annotate(n=Count("id"))
    ):
        if r["wd"] is None or r["hr"] is None:
            continue
        grid[(r["wd"], r["hr"])] = r["n"]
    peak = max([1] + list(grid.values()))
    day_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    rows = []
    for wd in range(1, 8):
        cells = []
        for hr in range(24):
            n = grid.get((wd, hr), 0)
            cells.append({"n": n, "intensity": round(n / peak, 3), "hr": hr})
        rows.append({"day": day_labels[wd - 1], "cells": cells})
    return {"rows": rows, "peak": peak, "hours": list(range(0, 24, 3))}


def _device_performance(events):
    # Average load time by device, joined through the session.
    out = {}
    for ev in (
        events.filter(kind=AnalyticsEvent.Kind.PAGE_VIEW)
        .values("session__device", "meta")[:20000]
    ):
        pl = _num((ev["meta"] or {}).get("page_load_ms"))
        if pl is None:
            continue
        dev = (ev["session__device"] or "unknown").title()
        out.setdefault(dev, []).append(pl)
    items = [
        {"label": d, "value": round(sum(v) / len(v)), "extra": f"{len(v)} views"}
        for d, v in out.items()
    ]
    items.sort(key=lambda x: x["value"])
    return _bars(items, fmt=lambda v: f"{v:,}ms")


# ===========================================================================
# TAB 3  SALES ANALYSIS
# ===========================================================================
def _sales_context(site, now, days, since, events, sessions, cur_metrics, prev_metrics):
    symbol = _CURRENCY_SYMBOL.get(site.currency, "")
    orders = Order.objects.for_site(site).filter(created_at__gte=since)
    # Every placed order counts as revenue (COD orders never reach "paid"); only
    # cancelled/archived are left out. "paid_count" is kept purely as a secondary
    # figure for the KPI sub-labels.
    counted = orders.filter(status__in=Order.REVENUE_STATUSES, replacement_seq=0)
    paid = orders.filter(status__in=PAID_STATUSES)
    order_items = OrderItem.objects.for_site(site).filter(order__in=counted)

    revenue = cur_metrics["revenue"]
    order_count = cur_metrics["orders"]
    counted_count = counted.count()
    paid_count = paid.count()
    aov = cur_metrics["aov"]
    units = order_items.aggregate(q=Sum("quantity"))["q"] or 0
    discount_total = counted.aggregate(d=Sum("discount_amount"))["d"] or Decimal("0.00")
    tax_total = counted.aggregate(t=Sum("tax_amount"))["t"] or Decimal("0.00")

    # Revenue per visit  a north-star efficiency metric.
    rpv = (float(revenue) / cur_metrics["sessions"]) if cur_metrics["sessions"] else 0

    kpis = [
        _kpi("Revenue", _money(symbol, revenue), f"{counted_count:,} orders",
             _delta(revenue, prev_metrics["revenue"])),
        _kpi("Orders", f"{order_count:,}", f"{paid_count:,} paid",
             _delta(order_count, prev_metrics["orders"])),
        _kpi("Avg. order value", _money(symbol, aov), "per order",
             _delta(aov, prev_metrics["aov"])),
        _kpi("Units sold", f"{units:,}", f"{(units / counted_count):.1f} / order" if counted_count else "", None),
        _kpi("Revenue / visit", _money(symbol, rpv), "monetisation", None),
        _kpi("Discounts", _money(symbol, discount_total),
             f"{_pct(float(discount_total), float(revenue) + float(discount_total)):.0f}% of gross", None),
    ]

    # Revenue trend ---------------------------------------------------------
    axis = _day_axis(now, days)
    rev_map = _daily_sum(counted, "created_at", "total")
    ord_map = _daily_map(orders, "created_at")
    rev_rows = [
        {"label": d.strftime("%d %b"), "rev": float(rev_map.get(d, 0) or 0)}
        for d in axis
    ]
    revenue_trend = _line_chart(rev_rows, [("rev", "pv")], money_symbol=symbol)
    ord_rows = [{"label": d.strftime("%d %b"), "ord": ord_map.get(d, 0)} for d in axis]
    orders_trend = _line_chart(ord_rows, [("ord", "se")])

    # Orders by status (donut) ---------------------------------------------
    status_labels = dict(Order.Status.choices)
    status_items = [
        {"label": status_labels.get(r["status"], r["status"]), "value": r["n"]}
        for r in orders.values("status").annotate(n=Count("id")).order_by("-n")
    ]
    status_donut = _donut(status_items, order_count)

    # Top products by revenue ----------------------------------------------
    top_products = _bars([
        {"label": r["title"], "value": float(r["rev"] or 0),
         "extra": f"{r['qty'] or 0} units"}
        for r in order_items.values("title").annotate(
            rev=Sum("line_total"), qty=Sum("quantity")).order_by("-rev")[:8]
    ], fmt=lambda v: _money(symbol, v), accent_first=True)

    # Revenue by category ---------------------------------------------------
    cat_items = [
        {"label": r["product__category__name"] or "Uncategorised", "value": float(r["rev"] or 0)}
        for r in order_items.values("product__category__name").annotate(
            rev=Sum("line_total")).order_by("-rev")[:8]
    ]
    category_bars = _bars(cat_items, fmt=lambda v: _money(symbol, v), accent_first=True)

    # Payment-method mix ----------------------------------------------------
    pay_items = [
        {"label": (r["gateway"] or "unknown").title(), "value": r["n"]}
        for r in Payment.objects.for_site(site).filter(
            created_at__gte=since, status=Payment.Status.PAID)
        .values("gateway").annotate(n=Count("id")).order_by("-n")
    ]
    payment_donut = _donut(pay_items, sum(p["value"] for p in pay_items))

    # Coupon / discount performance ----------------------------------------
    coupon_rows = [
        {"label": r["coupon__code"], "value": r["n"], "extra": _money(symbol, r["disc"] or 0)}
        for r in counted.exclude(coupon__isnull=True)
        .values("coupon__code").annotate(n=Count("id"), disc=Sum("discount_amount")).order_by("-n")[:6]
    ]
    coupons = _bars(coupon_rows, accent_first=True)

    # Sales funnel with revenue attribution.
    funnel = _funnel([
        ("Sessions", cur_metrics["sessions"]),
        ("Add to cart", cur_metrics["add_to_cart"]),
        ("Checkout", events.filter(kind=AnalyticsEvent.Kind.CHECKOUT).count()),
        ("Purchase", cur_metrics["purchases"] or counted_count),
    ])

    ctx = {
        "kpis": kpis,
        "revenue_trend": revenue_trend,
        "orders_trend": orders_trend,
        "status_donut": status_donut,
        "top_products": top_products,
        "category_bars": category_bars,
        "payment_donut": payment_donut,
        "coupons": coupons,
        "funnel": funnel,
        "tax_total": _money(symbol, tax_total),
        "symbol": symbol,
    }

    # Business-model branch -------------------------------------------------
    if site.is_d2c:
        ctx["d2c"] = _d2c_sales(site, symbol, since, counted, order_items, events, sessions, cur_metrics)
    if site.is_b2b:
        ctx["b2b"] = _b2b_sales(site, symbol, since, orders, counted, order_items)

    ctx["insights"] = _sales_insights(
        site, symbol, cur_metrics, prev_metrics, aov, rpv, discount_total, revenue,
        ctx.get("d2c"), ctx.get("b2b"),
    )
    return ctx


def _d2c_sales(site, symbol, since, counted, order_items, events, sessions, cur_metrics):
    """Consumer cohorts: repeat rate, LTV, review sentiment, cart recovery."""
    # Repeat purchase rate (lifetime, all realised orders per customer).
    per_customer = list(
        Order.objects.for_site(site).filter(status__in=Order.REVENUE_STATUSES, replacement_seq=0, customer__isnull=False)
        .values("customer").annotate(n=Count("id"), rev=Sum("total"))
    )
    buyers = len(per_customer)
    repeat_buyers = sum(1 for c in per_customer if c["n"] > 1)
    repeat_rate = _pct(repeat_buyers, buyers)
    ltv = (sum(float(c["rev"] or 0) for c in per_customer) / buyers) if buyers else 0
    avg_orders_per = (sum(c["n"] for c in per_customer) / buyers) if buyers else 0

    # Review sentiment.
    reviews = Review.objects.for_site(site).filter(created_at__gte=since)
    review_count = reviews.count()
    avg_rating = reviews.aggregate(a=Avg("rating"))["a"] or 0
    rating_dist = [
        {"label": f"{star}★", "value": reviews.filter(rating=star).count()}
        for star in (5, 4, 3, 2, 1)
    ]
    positive = reviews.filter(rating__gte=4).count()
    sentiment = _pct(positive, review_count)

    # Cart recovery opportunity.
    atc = cur_metrics["add_to_cart"]
    purchases = cur_metrics["purchases"] or counted.count()
    abandonment = _pct(max(0, atc - purchases), atc) if atc else 0.0

    # Wishlist signal.
    wishlist = WishlistItem.objects.for_site(site).filter(created_at__gte=since).count()

    # New vs returning customers in window.
    new_customers = Customer.objects.for_site(site).filter(created_at__gte=since).count()

    return {
        "repeat_rate": repeat_rate,
        "repeat_buyers": repeat_buyers,
        "buyers": buyers,
        "ltv": _money(symbol, ltv),
        "ltv_raw": ltv,
        "avg_orders_per": round(avg_orders_per, 2),
        "review_count": review_count,
        "avg_rating": round(float(avg_rating), 2),
        "rating_dist": _bars(rating_dist, accent_first=True),
        "sentiment": sentiment,
        "abandonment": abandonment,
        "recoverable": max(0, atc - purchases),
        "wishlist": wishlist,
        "new_customers": new_customers,
    }


def _b2b_sales(site, symbol, since, orders, counted, order_items):
    """Business intelligence: channel split, order size, GST share, pipeline."""
    # Channel split (for stores that are "both").
    channel_labels = dict(Order.Channel.choices)
    channel_items = [
        {"label": channel_labels.get(r["channel"], r["channel"]), "value": r["n"],
         "extra": _money(symbol, r["rev"] or 0)}
        for r in orders.values("channel").annotate(n=Count("id"), rev=Sum("total")).order_by("-n")
    ]

    counted_count = counted.count()
    units = order_items.aggregate(q=Sum("quantity"))["q"] or 0
    avg_units = (units / counted_count) if counted_count else 0
    avg_line_value = order_items.aggregate(a=Avg("line_total"))["a"] or Decimal("0.00")

    # GST / company orders share (B2B invoicing signal).
    gst_orders = counted.exclude(gstin="").count()
    company_orders = counted.exclude(company_name="").count()
    gst_share = _pct(gst_orders, counted_count)

    # Enquiry / quote pipeline.
    enquiries = Enquiry.objects.for_site(site).filter(created_at__gte=since)
    enq_total = enquiries.count()
    enq_unread = enquiries.filter(is_read=False).count()
    enq_response = _pct(enq_total - enq_unread, enq_total)
    # Enquiry → order conversion proxy (business orders vs enquiries in window).
    b2b_orders = orders.filter(channel=Order.Channel.B2B).count()
    enq_conversion = _pct(b2b_orders, enq_total) if enq_total else 0.0

    # Top business accounts by revenue.
    top_accounts = _bars([
        {"label": r["company_name"] or r["customer_name"] or "",
         "value": float(r["rev"] or 0), "extra": f"{r['n']} orders"}
        for r in counted.values("company_name", "customer_name").annotate(
            rev=Sum("total"), n=Count("id")).order_by("-rev")[:8]
    ], fmt=lambda v: _money(symbol, v), accent_first=True)

    # High-value orders (top decile threshold).
    large_orders = counted.filter(total__gte=(avg_line_value or 0)).count()

    return {
        "channel_items": channel_items,
        "avg_units": round(avg_units, 1),
        "avg_line_value": _money(symbol, avg_line_value),
        "gst_orders": gst_orders,
        "company_orders": company_orders,
        "gst_share": gst_share,
        "enq_total": enq_total,
        "enq_unread": enq_unread,
        "enq_response": enq_response,
        "enq_conversion": enq_conversion,
        "top_accounts": top_accounts,
        "large_orders": large_orders,
    }


# ===========================================================================
# INSIGHTS ENGINES  outcome-oriented, business-model-aware recommendations
# ===========================================================================
def _insight(level, title, body):
    """level: good | warn | tip | risk."""
    return {"level": level, "title": title, "body": body}


def _overview_insights(site, cur, prev, engagement_rate, funnel, sources):
    out = []
    d = _delta(cur["visitors"], prev["visitors"])
    if d["dir"] == "up" and not d["new"]:
        out.append(_insight("good", "Traffic is growing",
            f"Visitors are up {d['pct']:+.0f}% versus the previous period. Keep the acquisition channels that are working funded, and make sure your best-performing pages convert."))
    elif d["dir"] == "down":
        out.append(_insight("warn", "Traffic is slipping",
            f"Visitors fell {d['pct']:.0f}% versus the previous period. Check whether a paid campaign ended or a referral source dropped off, and refresh your top landing pages."))

    if cur["conversion"] < 1.0 and cur["sessions"] >= 50:
        out.append(_insight("risk", "Conversion is below benchmark",
            f"At {cur['conversion']:.2f}%, visit-to-order conversion is under the ~1–2% storefront norm. The funnel below shows exactly where visitors leave  tighten the biggest drop-off first."))
    elif cur["conversion"] >= 2.5:
        out.append(_insight("good", "Strong conversion",
            f"{cur['conversion']:.2f}% of visits convert  above the typical storefront band. Scaling traffic now compounds directly into revenue."))

    if engagement_rate < 45 and cur["sessions"] >= 50:
        out.append(_insight("warn", "Visitors are bouncing early",
            f"Only {engagement_rate:.0f}% of sessions are engaged. Sharpen your above-the-fold value proposition and page speed (see Site performance) to hold attention."))

    # Biggest funnel drop.
    worst = None
    for step in funnel[1:]:
        if step["drop"] is not None and (worst is None or step["drop"] > worst["drop"]):
            worst = step
    if worst and worst["drop"] and worst["drop"] >= 60:
        out.append(_insight("tip", f"Biggest leak: {worst['label']}",
            f"{worst['drop']:.0f}% of visitors drop off before reaching “{worst['label']}”. A focused fix here moves conversion more than anything upstream."))

    # Channel concentration risk.
    if sources:
        total = sum(s["value"] for s in sources)
        top = sources[0]
        if total and top["value"] / total > 0.7:
            out.append(_insight("tip", "Traffic is concentrated",
                f"{_pct(top['value'], total):.0f}% of sessions come from {top['label']}. Diversify a second channel so a single algorithm change can't halve your traffic."))
    if not out:
        out.append(_insight("good", "Steady baseline",
            "Metrics are holding steady in this window. Use the funnel and top-pages panels to find the single highest-leverage improvement."))
    return out


def _performance_insights(perf_score, p75_load, avg_fcp, bounce, pages_per, scroll, engagement):
    out = []
    if p75_load == 0:
        out.append(_insight("tip", "Waiting on performance data",
            "Load-time telemetry appears once visitors browse with the tracker running. Come back after some traffic to see web-vitals ratings."))
        return out
    if p75_load > _LOAD_POOR:
        out.append(_insight("risk", "Pages load slowly",
            f"Your p75 load time is {p75_load:,}ms  above the {_LOAD_POOR/1000:.1f}s “poor” threshold. Slow pages depress both conversion and search ranking. Compress hero images and defer non-critical scripts."))
    elif p75_load <= _LOAD_GOOD:
        out.append(_insight("good", "Fast storefront",
            f"p75 load time is {p75_load:,}ms  inside the good band. Fast pages lift conversion; protect this as you add content and pixels."))
    else:
        out.append(_insight("warn", "Load time needs work",
            f"p75 load time is {p75_load:,}ms. Getting under {_LOAD_GOOD/1000:.1f}s typically lifts conversion a few percent. Start with the slowest pages listed."))

    if avg_fcp and avg_fcp > _FCP_POOR:
        out.append(_insight("warn", "Slow first paint",
            f"First contentful paint averages {avg_fcp:,.0f}ms, so visitors stare at a blank screen too long. Inline critical CSS and preconnect to your image host."))
    if bounce >= 60:
        out.append(_insight("warn", "High bounce rate",
            f"{bounce:.0f}% of visits are single-page. Add clear next steps  related products, a sticky category nav  to pull visitors deeper."))
    if scroll and scroll < 40:
        out.append(_insight("tip", "Shoppers aren't scrolling",
            f"Average scroll depth is {scroll:.0f}%. Move your strongest proof and CTAs higher up the page."))
    if pages_per >= 3 and bounce < 45:
        out.append(_insight("good", "Deep browsing",
            f"Visitors view {pages_per:.1f} pages per session with a low bounce  your merchandising and internal links are working."))
    return out


def _sales_insights(site, symbol, cur, prev, aov, rpv, discount_total, revenue, d2c, b2b):
    out = []
    dr = _delta(revenue, prev["revenue"])
    if dr["dir"] == "up" and not dr["new"]:
        out.append(_insight("good", "Revenue is growing",
            f"Revenue is up {dr['pct']:+.0f}% versus the previous period. Reinvest into the products and channels driving it before demand cools."))
    elif dr["dir"] == "down":
        out.append(_insight("warn", "Revenue is down",
            f"Revenue fell {dr['pct']:.0f}%. Check whether it's a traffic problem (Overview) or a conversion/AOV problem  the levers are different."))

    da = _delta(aov, prev["aov"])
    if da["dir"] == "down" and not da["new"]:
        out.append(_insight("tip", "Average order value is falling",
            f"AOV dropped {da['pct']:.0f}%. Bundle complementary products or set a free-shipping threshold just above current AOV to nudge basket size up."))
    elif da["dir"] == "up" and not da["new"]:
        out.append(_insight("good", "Bigger baskets",
            f"AOV rose {da['pct']:+.0f}%. Whatever you changed in merchandising or bundling is working  extend it to more categories."))

    # Discount dependence.
    gross = float(revenue) + float(discount_total)
    if gross and float(discount_total) / gross > 0.2:
        out.append(_insight("risk", "Heavy discount dependence",
            f"Discounts are {_pct(float(discount_total), gross):.0f}% of gross sales. Test whether trimming blanket codes and steering budget to targeted offers protects margin without denting volume."))

    if d2c:
        if d2c["repeat_rate"] < 20 and d2c["buyers"] >= 10:
            out.append(_insight("tip", "Few customers come back",
                f"Only {d2c['repeat_rate']:.0f}% of buyers have ordered more than once. A post-purchase email flow and a first-repeat incentive are the cheapest revenue you can add."))
        elif d2c["repeat_rate"] >= 35:
            out.append(_insight("good", "Loyal customer base",
                f"{d2c['repeat_rate']:.0f}% of buyers are repeat  well above average. A referral or subscription offer can turn this loyalty into growth."))
        if d2c["abandonment"] >= 65 and d2c["recoverable"]:
            out.append(_insight("risk", "Carts are being abandoned",
                f"{d2c['abandonment']:.0f}% of carts don't convert (~{d2c['recoverable']} recoverable). Turn on abandoned-cart emails and simplify checkout to win a share back."))
        if d2c["review_count"] and d2c["sentiment"] < 70:
            out.append(_insight("warn", "Review sentiment is mixed",
                f"Only {d2c['sentiment']:.0f}% of reviews are 4★+. Read the low ratings for recurring complaints  fixing the top one lifts both conversion and repeat rate."))
        elif d2c["review_count"] >= 5 and d2c["avg_rating"] >= 4.3:
            out.append(_insight("good", "Customers love the product",
                f"A {d2c['avg_rating']:.1f}★ average across {d2c['review_count']} reviews is a strong trust signal  surface it on product pages and ads."))

    if b2b:
        if b2b["enq_total"] and b2b["enq_response"] < 80:
            out.append(_insight("risk", "Leads are going cold",
                f"{b2b['enq_unread']} of {b2b['enq_total']} enquiries are still unread ({b2b['enq_response']:.0f}% responded). In B2B, speed-to-lead is the single biggest win-rate lever  clear the queue."))
        if b2b["enq_total"] and b2b["enq_conversion"] < 15:
            out.append(_insight("tip", "Enquiries aren't converting",
                f"Only {b2b['enq_conversion']:.0f}% of enquiries turn into business orders. Add quantity-break pricing and a faster quote turnaround to close more."))
        if b2b["gst_share"] >= 50:
            out.append(_insight("good", "Strong B2B invoicing base",
                f"{b2b['gst_share']:.0f}% of orders carry a GSTIN  a healthy registered-business customer base. Offer credit terms or bulk tiers to grow account value."))

    if not out:
        out.append(_insight("good", "Sales are stable",
            "No red flags this window. Use the product and category leaders to decide where to deepen inventory and marketing."))
    return out


# ===========================================================================
# Dispatcher
# ===========================================================================
@login_required
def analytics(request, site_id):
    site = get_owned_site(request, site_id)

    tab = request.GET.get("tab") or "overview"
    if tab not in VALID_TABS:
        tab = "overview"

    try:
        days = max(1, min(365, int(request.GET.get("days") or 30)))
    except ValueError:
        days = 30

    now = timezone.now()
    since = now - timedelta(days=days)
    prev_since = since - timedelta(days=days)

    events = AnalyticsEvent.objects.for_site(site).filter(created_at__gte=since)
    sessions = AnalyticsSession.objects.for_site(site).filter(started_at__gte=since)

    cur_metrics = _window_metrics(site, since, now)
    prev_metrics = _window_metrics(site, prev_since, since)

    has_data = bool(
        cur_metrics["pageviews"] or cur_metrics["sessions"] or cur_metrics["orders"]
    )

    context = store_context(request, site, "analytics")
    context.update({
        "tab": tab,
        "days": days,
        "range_options": [7, 30, 90, 365],
        "has_data": has_data,
        "tabs": [
            {"key": "overview", "label": "Overview"},
            {"key": "performance", "label": "Site performance"},
            {"key": "sales", "label": "Sales analysis"},
        ],
    })

    if has_data:
        if tab == "performance":
            context.update(_performance_context(
                site, now, days, since, events, sessions, cur_metrics))
        elif tab == "sales":
            context.update(_sales_context(
                site, now, days, since, events, sessions, cur_metrics, prev_metrics))
        else:
            context.update(_overview_context(
                site, now, days, since, events, sessions, cur_metrics, prev_metrics))

    return render(request, "store/analytics.html", context)
