"""Email marketing  the store dashboard surface.

Owner-facing pages for the :mod:`outreach` engine: an overview with live
analytics, the campaign composer (broadcast or drip), audience lists (built by
hand, imported, or drawn from the store's customers), drip sequences, and the
suppression list. Sending itself is never done here  the composer/"Send now"
button only flips a campaign live and hands off to Celery (the beat + worker
pair), so a slow send never blocks a request.

Every route is gated behind the ``email_marketing`` feature (see
``commerce.features``), and every query is tenant-scoped with ``.for_site``.
"""

from __future__ import annotations

import json
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import TruncDate
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.http import require_POST

from commerce import activity
from commerce.context import get_owned_site, store_context
from commerce.customer_sync import sync_customers_for_site
from commerce.models import Customer
from outreach.models import (
    AudienceList,
    AudienceMember,
    Campaign,
    CampaignSendLog,
    DripSequence,
    EmailDelivery,
    EmailTemplate,
    Suppression,
)
from outreach.services.imports import parse_recipients
from outreach.services.limits import limits_for_site

_SENDABLE = (Campaign.Status.DRAFT, Campaign.Status.PAUSED, Campaign.Status.SCHEDULED)


# ── helpers ─────────────────────────────────────────────────────────────────

def _campaign_stats(site, campaign):
    d = EmailDelivery.objects.for_site(site).filter(campaign=campaign)
    return {
        "recipients": d.count(),
        "sent": d.filter(send_status=EmailDelivery.SendStatus.SENT).count(),
        "opened": d.filter(open_count__gt=0).count(),
        "clicked": d.filter(clicked_at__isnull=False).count(),
        "unsubscribed": d.filter(unsubscribed_at__isnull=False).count(),
    }


def _parse_start(raw: str, *, scheduling: str):
    """Parse a start value honouring the plan's scheduling mode.

    Returns ``(start_at, error)``. ``simple`` accepts a future date only;
    ``datetime`` accepts a date-time no earlier than now (small clock buffer).
    """
    raw = (raw or "").strip()
    if not raw:
        return None, None  # no schedule → immediate when sent

    if scheduling == "simple":
        d = parse_date(raw)
        if not d:
            # A datetime was submitted on a simple plan  take its date.
            dt = parse_datetime(raw)
            d = timezone.localtime(dt).date() if dt else None
        if not d:
            return None, "Enter a valid date."
        if d <= timezone.localdate():
            return None, "Choose a future date (not today)."
        from datetime import datetime as _dt

        return timezone.make_aware(_dt.combine(d, _dt.min.time()), timezone.get_current_timezone()), None

    dt = parse_datetime(raw)
    if dt is None:
        d = parse_date(raw)
        if d is None:
            return None, "Enter a valid date and time."
        from datetime import datetime as _dt

        dt = _dt.combine(d, _dt.min.time())
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    if dt < timezone.now() - timedelta(seconds=30):
        return None, "Choose a time in the future."
    return dt, None


def _apply_audience(request, site, campaign):
    ids = request.POST.getlist("audience_lists")
    if ids:
        campaign.audience_lists.set(AudienceList.objects.for_site(site).filter(pk__in=ids))
    else:
        campaign.audience_lists.clear()
    campaign.send_to_all_customers = request.POST.get("send_to_all_customers") == "on"
    campaign.save(update_fields=["send_to_all_customers", "updated_at"])


def _has_recipients(campaign) -> bool:
    if campaign.send_to_all_customers:
        return True
    return campaign.audience_lists.exists()


def _validate_ready(campaign) -> str | None:
    if not _has_recipients(campaign):
        return "Choose at least one audience list, or send to all customers."
    if campaign.is_drip:
        if not campaign.drip_id or not (campaign.drip.steps or []):
            return "Pick a drip sequence with at least one step."
    else:
        if not (campaign.subject or "").strip():
            return "Add a subject line."
        if not (campaign.html or "").strip() and campaign.design != "promo":
            return "Choose an email design."
    return None


# ── overview + analytics ────────────────────────────────────────────────────

def _pct(n, of) -> int:
    return int(round((n / of) * 100)) if of else 0


def _build_funnel(recipients, sent, opened, clicked):
    """A recipients → sent → opened → clicked funnel for ``_an_funnel``."""
    steps = [
        ("Recipients", recipients),
        ("Delivered", sent),
        ("Opened", opened),
        ("Clicked", clicked),
    ]
    top = recipients or 1
    out = []
    prev = None
    for label, value in steps:
        row = {"label": label, "value": value, "pct": _pct(value, top), "drop": None, "step_pct": 100}
        if prev is not None:
            row["step_pct"] = _pct(value, prev) if prev else 0
            row["drop"] = max(0, 100 - row["step_pct"])
        out.append(row)
        prev = value
    return out


@login_required
def email_marketing(request, site_id):
    site = get_owned_site(request, site_id)
    deliveries = EmailDelivery.objects.for_site(site)
    S = EmailDelivery.SendStatus

    sent = deliveries.filter(send_status=S.SENT).count()
    opened = deliveries.filter(open_count__gt=0).count()
    clicked = deliveries.filter(clicked_at__isnull=False).count()
    unsubscribed = deliveries.filter(unsubscribed_at__isnull=False).count()
    recipients = deliveries.count()

    kpi_tiles = [
        {"label": "Emails sent", "value": sent, "sub": "delivered to inboxes"},
        {"label": "Open rate", "value": f"{_pct(opened, sent)}%", "sub": f"{opened} opened"},
        {"label": "Click rate", "value": f"{_pct(clicked, sent)}%", "sub": f"{clicked} clicked"},
        {"label": "Unsubscribes", "value": unsubscribed, "sub": f"{_pct(unsubscribed, sent)}% of sent"},
    ]

    pending = deliveries.filter(send_status__in=[S.PENDING, S.SENDING]).count()
    failed = deliveries.filter(send_status=S.FAILED).count()
    status_total = max(1, sent + pending + failed)
    status_bars = [
        {"label": "Sent", "value": sent, "pct": _pct(sent, status_total), "accent": True},
        {"label": "In progress", "value": pending, "pct": _pct(pending, status_total)},
        {"label": "Failed", "value": failed, "pct": _pct(failed, status_total)},
    ]

    from core.queries import sorted_rows

    # Campaign rows carry their full email HTML and no index serves this order
    # (the status exclusion), so only keys are sorted (MySQL error 1038).
    campaigns = sorted_rows(
        Campaign.objects.for_site(site)
        .filter(is_deleted=False)
        .exclude(status=Campaign.Status.ARCHIVED)
        .order_by("-created_at"),
        limit=25,
    )
    campaign_rows = [{"c": c, "stats": _campaign_stats(site, c)} for c in campaigns]

    lists = AudienceList.objects.for_site(site).filter(is_archived=False)
    context = store_context(request, site, "email_marketing")
    context.update(
        {
            "kpis": kpi_tiles,
            "funnel": _build_funnel(recipients, sent, opened, clicked),
            "status_bars": status_bars,
            "has_activity": recipients > 0,
            "campaign_rows": campaign_rows,
            "audience_count": lists.count(),
            "member_count": AudienceMember.objects.for_site(site).count(),
            "customer_count": Customer.objects.for_site(site).filter(is_deleted=False).count(),
            "suppression_count": Suppression.objects.for_site(site).count(),
            "limits": limits_for_site(site),
        }
    )
    return render(request, "store/email_marketing.html", context)


@login_required
def email_analytics_json(request, site_id):
    site = get_owned_site(request, site_id)
    try:
        days = max(7, min(int(request.GET.get("days") or 30), 120))
    except ValueError:
        days = 30
    campaign_id = (request.GET.get("campaign_id") or "").strip()

    deliveries = EmailDelivery.objects.for_site(site)
    if campaign_id and campaign_id != "all":
        deliveries = deliveries.filter(campaign_id=campaign_id)

    S = EmailDelivery.SendStatus
    delivery_stats = {
        "sent": deliveries.filter(send_status=S.SENT).count(),
        "pending": deliveries.filter(send_status__in=[S.PENDING, S.SENDING]).count(),
        "failed": deliveries.filter(send_status=S.FAILED).count(),
    }
    opened = deliveries.filter(open_count__gt=0).count()
    clicked = deliveries.filter(clicked_at__isnull=False).count()
    unsubscribed = deliveries.filter(unsubscribed_at__isnull=False).count()

    end = timezone.localdate()
    start = end - timedelta(days=days - 1)
    labels = [(start + timedelta(days=i)).isoformat() for i in range(days)]

    def series(qs, field):
        rows = (
            qs.filter(**{f"{field}__date__gte": start, f"{field}__date__lte": end})
            .exclude(**{field: None})
            .annotate(d=TruncDate(field))
            .values("d")
            .annotate(c=Count("id"))
        )
        by = {str(r["d"]): int(r["c"]) for r in rows}
        return [by.get(day, 0) for day in labels]

    payload = {
        "ok": True,
        "kpis": {
            "sent": delivery_stats["sent"],
            "opened": opened,
            "clicked": clicked,
            "unsubscribed": unsubscribed,
        },
        "delivery": delivery_stats,
        "funnel": {
            "recipients": deliveries.count(),
            "sent": delivery_stats["sent"],
            "opened": opened,
            "clicked": clicked,
            "unsubscribed": unsubscribed,
        },
        "series": {
            "labels": labels,
            "sent": series(deliveries.filter(send_status=S.SENT), "sent_at"),
            "opened": series(deliveries.filter(open_count__gt=0), "first_opened_at"),
            "clicked": series(deliveries.filter(clicked_at__isnull=False), "clicked_at"),
            "unsubscribed": series(deliveries.filter(unsubscribed_at__isnull=False), "unsubscribed_at"),
        },
    }
    resp = JsonResponse(payload)
    resp["Cache-Control"] = "no-store"
    return resp


# ── engagement segments (per campaign) ───────────────────────────────────────

# The three engagement slices a store can act on, keyed for the URL. Computed
# over the campaign's EmailDelivery rows, deduped per recipient email so a drip
# recipient (many day-rows) counts once.
SEGMENTS = {
    "all": "All recipients",
    "non_openers": "Never opened",
    "not_clicked": "Opened, not clicked",
}


def _campaign_recipient_rows(site, campaign):
    """One row per recipient email for a campaign, with engagement flags.

    Aggregated across every delivery (all drip days): opened if any open was
    recorded, clicked / unsubscribed if ever, sent-at = the latest send.
    """
    agg = (
        EmailDelivery.objects.for_site(site)
        .filter(campaign=campaign)
        .exclude(recipient_email="")
        .values("recipient_email")
        .annotate(
            name=Max("recipient_name"),
            opens=Sum("open_count"),
            last_sent=Max("sent_at"),
            clicked=Max("clicked_at"),
            unsub=Max("unsubscribed_at"),
        )
        .order_by("-last_sent", "recipient_email")
    )
    rows = []
    for r in agg:
        opens = int(r["opens"] or 0)
        email = r["recipient_email"]
        rows.append(
            {
                "name": (r["name"] or "").strip() or email.split("@")[0],
                "email": email,
                "sent_at": r["last_sent"],
                "opens": opens,
                "opened": opens > 0,
                "clicked": r["clicked"] is not None,
                "unsubscribed": r["unsub"] is not None,
            }
        )
    return rows


def _filter_segment(rows, seg):
    if seg == "non_openers":
        return [r for r in rows if not r["opened"]]
    if seg == "not_clicked":
        return [r for r in rows if r["opened"] and not r["clicked"]]
    return rows


@login_required
def email_campaign_segments(request, site_id, campaign_id):
    site = get_owned_site(request, site_id)
    campaign = get_object_or_404(
        Campaign.objects.for_site(site).filter(is_deleted=False), pk=campaign_id
    )
    seg = request.GET.get("segment") or "all"
    if seg not in SEGMENTS:
        seg = "all"

    rows = _campaign_recipient_rows(site, campaign)
    counts = {
        "all": len(rows),
        "non_openers": sum(1 for r in rows if not r["opened"]),
        "not_clicked": sum(1 for r in rows if r["opened"] and not r["clicked"]),
    }
    filtered = _filter_segment(rows, seg)
    page = Paginator(filtered, 50).get_page(request.GET.get("page") or 1)

    context = store_context(request, site, "email_marketing")
    context.update(
        {
            "campaign": campaign,
            "segment": seg,
            "seg_label": SEGMENTS[seg],
            "seg_count": counts[seg],
            "segments": [{"key": k, "label": v, "count": counts[k]} for k, v in SEGMENTS.items()],
            "rows_page": page,
            "total_recipients": counts["all"],
        }
    )
    return render(request, "store/email_campaign_segments.html", context)


@login_required
def email_campaign_segment_export(request, site_id, campaign_id):
    """Download the chosen engagement segment as an .xlsx (dependency-free)."""
    site = get_owned_site(request, site_id)
    campaign = get_object_or_404(
        Campaign.objects.for_site(site).filter(is_deleted=False), pk=campaign_id
    )
    seg = request.GET.get("segment") or "all"
    if seg not in SEGMENTS:
        seg = "all"

    rows = _filter_segment(_campaign_recipient_rows(site, campaign), seg)

    from commerce import xlsx

    wb = xlsx.Workbook()
    ws = wb.add_sheet(SEGMENTS[seg])
    ws.write(0, 0, f"{campaign.name}  {SEGMENTS[seg]}", xlsx.STYLE_TITLE)
    ws.merge(0, 0, 0, 5)
    ws.write(
        1, 0,
        f"{len(rows)} recipient(s) · exported {timezone.localtime():%d %b %Y, %H:%M}",
        xlsx.STYLE_SUBTITLE,
    )
    ws.merge(1, 0, 1, 5)

    header_row = 3
    headers = ["Name", "Email", "Sent at", "Opens", "Clicked", "Unsubscribed"]
    ws.write_row(header_row, 0, headers[:5], xlsx.STYLE_HEADER)
    ws.write(header_row, 3, "Opens", xlsx.STYLE_HEADER_NUM)
    ws.write(header_row, 5, "Unsubscribed", xlsx.STYLE_HEADER)
    ws.freeze_row = header_row + 1
    for i, w in enumerate([26, 34, 20, 9, 11, 14]):
        ws.set_column_width(i, w)

    for i, r in enumerate(rows):
        rr = header_row + 1 + i
        ws.write(rr, 0, r["name"], xlsx.STYLE_CELL)
        ws.write(rr, 1, r["email"], xlsx.STYLE_CELL)
        ws.write(
            rr, 2,
            (timezone.localtime(r["sent_at"]).strftime("%d %b %Y, %H:%M") if r["sent_at"] else ""),
            xlsx.STYLE_CELL,
        )
        ws.write(rr, 3, r["opens"], xlsx.STYLE_CELL_INT)
        ws.write(rr, 4, "Yes" if r["clicked"] else "No", xlsx.STYLE_CELL_CENTER)
        ws.write(rr, 5, "Yes" if r["unsubscribed"] else "No", xlsx.STYLE_CELL_CENTER)

    resp = HttpResponse(
        wb.to_bytes(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    safe = "".join(c for c in campaign.name if c.isalnum() or c in " -_").strip()[:40] or "campaign"
    resp["Content-Disposition"] = f'attachment; filename="{safe} - {SEGMENTS[seg]}.xlsx"'
    return resp


# ── analytics page (charts) ──────────────────────────────────────────────────

# A restrained multi-hue palette for the charts (accent + tints + neutrals),
# used for donut segments and series so the page reads as one system.
_CHART_COLORS = ["#4b2bf5", "#6a44fb", "#9a86ff", "#c9bdff", "#9a9aa0", "#c0392b"]


def _donut(segments):
    """Turn ``[(label, value, color)]`` into SVG stroke segments (r = 54).

    Each returned item carries the ``dash``/``offset`` for a stroked <circle>
    drawn on a 120×120 viewBox rotated -90° (so it starts at 12 o'clock).
    """
    import math

    r = 54.0
    circ = 2 * math.pi * r
    total = sum(max(0, v) for _l, v, _c in segments) or 0
    out = []
    prefix = 0.0
    for label, value, color in segments:
        value = max(0, value)
        frac = (value / total) if total else 0
        seg_len = frac * circ
        out.append(
            {
                "label": label,
                "value": value,
                "pct": int(round(frac * 100)),
                "color": color,
                "dash": f"{seg_len:.2f} {circ:.2f}",
                "offset": f"{-prefix:.2f}",
            }
        )
        prefix += seg_len
    return {"circ": round(circ, 2), "total": total, "segments": out}


@login_required
def email_analytics(request, site_id):
    site = get_owned_site(request, site_id)
    try:
        days = max(7, min(int(request.GET.get("days") or 30), 120))
    except ValueError:
        days = 30

    d = EmailDelivery.objects.for_site(site)
    S = EmailDelivery.SendStatus
    recipients = d.count()
    sent = d.filter(send_status=S.SENT).count()
    pending = d.filter(send_status__in=[S.PENDING, S.SENDING]).count()
    failed = d.filter(send_status=S.FAILED).count()
    opened = d.filter(open_count__gt=0).count()
    clicked = d.filter(clicked_at__isnull=False).count()
    unsub = d.filter(unsubscribed_at__isnull=False).count()
    opened_not_clicked = d.filter(open_count__gt=0, clicked_at__isnull=True).count()
    never_opened = max(0, recipients - opened)

    kpis = [
        {"label": "Emails sent", "value": sent, "sub": f"{recipients} recipients"},
        {"label": "Open rate", "value": f"{_pct(opened, sent)}%", "sub": f"{opened} opened"},
        {"label": "Click rate", "value": f"{_pct(clicked, sent)}%", "sub": f"{clicked} clicked"},
        {"label": "Click-to-open", "value": f"{_pct(clicked, opened)}%", "sub": "of openers clicked"},
        {"label": "Unsubscribes", "value": unsub, "sub": f"{_pct(unsub, sent)}% of sent"},
    ]

    delivery_donut = _donut([
        ("Sent", sent, _CHART_COLORS[0]),
        ("In progress", pending, _CHART_COLORS[3]),
        ("Failed", failed, _CHART_COLORS[5]),
    ])
    engagement_donut = _donut([
        ("Clicked", clicked, _CHART_COLORS[0]),
        ("Opened, not clicked", opened_not_clicked, _CHART_COLORS[2]),
        ("Never opened", never_opened, _CHART_COLORS[4]),
    ])

    # Time-series over the window.
    end = timezone.localdate()
    start = end - timedelta(days=days - 1)
    labels = [(start + timedelta(days=i)) for i in range(days)]
    label_keys = [dt.isoformat() for dt in labels]

    def series(qs, field):
        rows = (
            qs.filter(**{f"{field}__date__gte": start, f"{field}__date__lte": end})
            .exclude(**{field: None})
            .annotate(day=TruncDate(field))
            .values("day")
            .annotate(c=Count("id"))
        )
        by = {str(r["day"]): int(r["c"]) for r in rows}
        return [by.get(k, 0) for k in label_keys]

    s_sent = series(d.filter(send_status=S.SENT), "sent_at")
    s_open = series(d.filter(open_count__gt=0), "first_opened_at")
    s_click = series(d.filter(clicked_at__isnull=False), "clicked_at")

    series_max = max([1] + s_sent + s_open + s_click)
    W, H = 100.0, 40.0
    n = len(label_keys)

    def points(values):
        if n <= 1:
            y = H - (values[0] / series_max) * H if values else H
            return f"0,{y:.2f} {W},{y:.2f}"
        step = W / (n - 1)
        return " ".join(
            f"{i * step:.2f},{H - (v / series_max) * H:.2f}" for i, v in enumerate(values)
        )

    trend_lines = [
        {"label": "Sent", "color": _CHART_COLORS[4], "points": points(s_sent)},
        {"label": "Opened", "color": _CHART_COLORS[2], "points": points(s_open)},
        {"label": "Clicked", "color": _CHART_COLORS[0], "points": points(s_click)},
    ]
    trend_axis = {
        "max": series_max,
        "start": start.strftime("%d %b"),
        "mid": labels[n // 2].strftime("%d %b"),
        "end": end.strftime("%d %b"),
        "totals": {"sent": sum(s_sent), "opened": sum(s_open), "clicked": sum(s_click)},
    }

    # Opens by weekday (decomposition), Mon..Sun.
    weekday_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    wk_counts = [0] * 7
    for opened_at in d.filter(first_opened_at__isnull=False).values_list("first_opened_at", flat=True):
        wk_counts[timezone.localtime(opened_at).weekday()] += 1
    wk_max = max([1] + wk_counts)
    weekday_bars = [
        {"label": weekday_names[i], "value": wk_counts[i], "pct": _pct(wk_counts[i], wk_max)}
        for i in range(7)
    ]

    # Per-campaign comparison (open % / click %) for recent campaigns.
    per_campaign = []
    from core.queries import sorted_rows

    for c in sorted_rows(
        Campaign.objects.for_site(site)
        .filter(is_deleted=False)
        .exclude(status=Campaign.Status.ARCHIVED)
        .order_by("-created_at"),
        limit=8,
    ):
        st = _campaign_stats(site, c)
        per_campaign.append(
            {
                "name": c.name,
                "sent": st["sent"],
                "open_pct": _pct(st["opened"], st["sent"]),
                "click_pct": _pct(st["clicked"], st["sent"]),
            }
        )

    context = store_context(request, site, "email_marketing")
    context.update(
        {
            "kpis": kpis,
            "funnel": _build_funnel(recipients, sent, opened, clicked),
            "delivery_donut": delivery_donut,
            "engagement_donut": engagement_donut,
            "trend_lines": trend_lines,
            "trend_axis": trend_axis,
            "weekday_bars": weekday_bars,
            "per_campaign": per_campaign,
            "days": days,
            "day_ranges": [7, 30, 90],
            "has_activity": recipients > 0,
        }
    )
    return render(request, "store/email_analytics.html", context)


# ── campaign composer ───────────────────────────────────────────────────────

@login_required
def email_campaign_new(request, site_id):
    site = get_owned_site(request, site_id)
    if request.method == "POST":
        return _save_campaign(request, site, None)
    return _render_composer(request, site, None)


@login_required
def email_campaign_edit(request, site_id, campaign_id):
    site = get_owned_site(request, site_id)
    campaign = get_object_or_404(
        Campaign.objects.for_site(site).filter(is_deleted=False), pk=campaign_id
    )
    if request.method == "POST":
        return _save_campaign(request, site, campaign)
    return _render_composer(request, site, campaign)


def _render_composer(request, site, campaign):
    from commerce.models import Product
    from core.queries import sorted_rows

    # Content is chosen by selecting a published design (no raw-HTML entry). Match
    # the campaign's saved HTML back to its template so an edit preselects it.
    # Template rows carry full email HTML: sort keys only (MySQL error 1038).
    # The admin-published (AI-built) designs are switched off for now: only the
    # built-in, field-driven designs are offered. Flip
    # ``OUTREACH_SHOW_PUBLISHED_TEMPLATES`` to bring them back.
    from django.conf import settings as dj_settings

    from outreach import promo_design

    if getattr(dj_settings, "OUTREACH_SHOW_PUBLISHED_TEMPLATES", False):
        templates = sorted_rows(EmailTemplate.objects.filter(status=EmailTemplate.Status.PUBLISHED))
    else:
        templates = []
    selected_template_id = ""
    if campaign is None or campaign.design == promo_design.DESIGN_KEY:
        selected_template_id = "builtin:promo"
    elif campaign and (campaign.html or "").strip():
        target = (campaign.html or "").strip()
        for t in templates:
            if (t.html or "").strip() == target:
                selected_template_id = str(t.id)
                break

    # Products the campaign can feature in the {{products}} grid (single or up to
    # four). A lightweight list keeps the custom picker cheap to render.
    products = [
        {"id": str(p.id), "title": p.title, "price": p.price}
        for p in sorted_rows(
            Product.objects.for_site(site)
            .filter(is_active=True, is_deleted=False)
            .order_by("title")
        )
    ]

    from django.templatetags.static import static

    promo = promo_design.clean(
        (campaign.design_data if campaign and campaign.design == promo_design.DESIGN_KEY else None)
        or promo_design.defaults(site),
        site,
    )

    context = store_context(request, site, "email_marketing")
    context.update(
        {
            "campaign": campaign,
            "is_edit": bool(campaign),
            "lists": AudienceList.objects.for_site(site).filter(is_archived=False),
            "selected_list_ids": (
                set(str(x) for x in campaign.audience_lists.values_list("id", flat=True))
                if campaign
                else set()
            ),
            "sequences": DripSequence.objects.for_site(site).filter(is_archived=False),
            "templates": templates,
            "selected_template_id": selected_template_id,
            "promo": promo,
            "promo_name": promo_design.DESIGN_NAME,
            "promo_description": promo_design.DESIGN_DESCRIPTION,
            "promo_highlights": [
                {"n": n, "icon": promo[f"hl{n}_icon"], "label": promo[f"hl{n}_label"], "url": promo[f"hl{n}_url"]}
                for n in (1, 2, 3)
            ],
            "promo_icon_choices": [
                {"key": k, "label": label, "src": static(f"email/icons/{k}.png")}
                for k, label in promo_design.ICONS
            ],
            "promo_max_products": promo_design.MAX_PRODUCTS,
            "store_url": promo_design.store_base_url(site),
            "products": products,
            "product_mode": (campaign.product_mode if campaign else Campaign.ProductMode.MULTI),
            "selected_product_ids": (
                [str(x) for x in (campaign.product_ids or [])] if campaign else []
            ),
            "limits": limits_for_site(site),
            "kinds": Campaign.Kind.choices,
        }
    )
    return render(request, "store/email_campaign_form.html", context)


def _product_limit(mode: str, design: str = "") -> int:
    """How many products a campaign's grid may feature."""
    if mode == Campaign.ProductMode.SINGLE:
        return 1
    if design == "promo":
        from outreach import promo_design

        return promo_design.MAX_PRODUCTS
    return 4


def _save_campaign(request, site, campaign):
    limits = limits_for_site(site)
    action = request.POST.get("action") or "save"  # save | send
    kind = request.POST.get("kind") or Campaign.Kind.BROADCAST
    if kind not in Campaign.Kind.values:
        kind = Campaign.Kind.BROADCAST

    if campaign is None:
        campaign = Campaign(site=site, kind=kind, status=Campaign.Status.DRAFT)
    campaign.kind = kind
    campaign.name = (request.POST.get("name") or "").strip() or "Untitled campaign"
    campaign.subject = (request.POST.get("subject") or "").strip()
    campaign.preheader = (request.POST.get("preheader") or "").strip()
    campaign.from_name = (request.POST.get("from_name") or "").strip()
    campaign.reply_to = (request.POST.get("reply_to") or "").strip()
    campaign.builder = (
        Campaign.Builder.AI if request.POST.get("builder") == "ai" else Campaign.Builder.HTML
    )
    from outreach import promo_design

    if request.POST.get("template_choice") == "builtin:promo":
        # Built-in design: the email is rendered from these fields at send time.
        campaign.design = promo_design.DESIGN_KEY
        campaign.design_data = promo_design.from_post(request.POST, site)
        campaign.html = ""
    else:
        campaign.design = ""
        campaign.html = request.POST.get("html") or ""
    campaign.cta_label = (request.POST.get("cta_label") or "").strip()
    campaign.cta_url = (request.POST.get("cta_url") or "").strip()
    if campaign.design == promo_design.DESIGN_KEY:
        # The design's main button doubles as the campaign's fallback click target.
        campaign.cta_label = campaign.design_data.get("hero_button_label", "")[:60]
        hero_url = campaign.design_data.get("hero_button_url", "")
        campaign.cta_url = hero_url if hero_url.lower().startswith(("https://", "http://")) else ""

    # Featured products for the {{products}} grid: single = one, multi = up to
    # four. The picker submits a JSON array of product ids; keep only ids that
    # belong to this store, in the chosen order, capped to the mode's limit.
    mode = request.POST.get("product_mode")
    if mode not in Campaign.ProductMode.values:
        mode = Campaign.ProductMode.MULTI
    campaign.product_mode = mode
    try:
        raw_ids = json.loads(request.POST.get("product_ids") or "[]")
    except (ValueError, TypeError):
        raw_ids = []
    chosen = [str(x) for x in raw_ids if isinstance(x, (str, int))] if isinstance(raw_ids, list) else []
    if chosen:
        from commerce.models import Product

        valid = set(
            str(pid)
            for pid in Product.objects.for_site(site)
            .filter(is_active=True, is_deleted=False, id__in=chosen)
            .values_list("id", flat=True)
        )
        ordered = [pid for pid in chosen if pid in valid]
        limit = _product_limit(mode, campaign.design)
        campaign.product_ids = ordered[:limit]
    else:
        campaign.product_ids = []

    if kind == Campaign.Kind.DRIP:
        seq_id = request.POST.get("drip")
        campaign.drip = (
            DripSequence.objects.for_site(site).filter(pk=seq_id).first() if seq_id else None
        )
    else:
        campaign.drip = None

    start_at, err = _parse_start(request.POST.get("start_at"), scheduling=limits.get("scheduling", "datetime"))
    if err:
        messages.error(request, err)
        return _render_composer(request, site, campaign if campaign.pk else None)
    campaign.start_at = start_at
    if campaign.status == Campaign.Status.SCHEDULED and start_at is None and action != "send":
        # Clearing the date of a scheduled campaign un-schedules it. Left as
        # SCHEDULED with no date, the next beat tick would send it at once.
        campaign.status = Campaign.Status.DRAFT
        messages.info(request, "The send date was cleared, so the campaign is back to draft.")

    campaign.save()
    _apply_audience(request, site, campaign)

    if action == "send":
        return _launch(request, site, campaign)

    activity.record(
        "store.email_campaign.updated" if campaign.pk else "store.email_campaign.created",
        request=request, site=site, target=campaign, target_kind="EmailCampaign", label=campaign.name,
    )
    messages.success(request, f"Campaign “{campaign.name}” saved.")
    return redirect("store:email_campaign_edit", site_id=site.id, campaign_id=campaign.id)


def _launch(request, site, campaign):
    """Flip a campaign live and hand off to the worker (never send inline)."""
    err = _validate_ready(campaign)
    if err:
        messages.error(request, err)
        return redirect("store:email_campaign_edit", site_id=site.id, campaign_id=campaign.id)

    now = timezone.now()
    scheduled = campaign.start_at and campaign.start_at > now
    campaign.status = Campaign.Status.SCHEDULED if scheduled else Campaign.Status.ACTIVE
    if not scheduled and not campaign.start_at:
        campaign.start_at = now
    if not scheduled:
        campaign.started_at = campaign.started_at or now
    campaign.save(update_fields=["status", "start_at", "started_at", "updated_at"])

    if not scheduled:
        from outreach import tasks

        tasks.dispatch_campaign.delay(str(campaign.id))

    activity.record(
        "store.email_campaign.launched", request=request, site=site,
        target=campaign, target_kind="EmailCampaign", label=campaign.name,
    )
    if scheduled:
        messages.success(request, f"Campaign scheduled for {timezone.localtime(campaign.start_at):%d %b %Y, %H:%M}.")
    else:
        messages.success(request, "Campaign is live  emails are going out in the background.")
    return redirect("store:email_marketing", site_id=site.id)


@login_required
@require_POST
def email_campaign_send(request, site_id, campaign_id):
    site = get_owned_site(request, site_id)
    campaign = get_object_or_404(
        Campaign.objects.for_site(site).filter(is_deleted=False), pk=campaign_id
    )
    if campaign.status not in _SENDABLE:
        messages.error(request, "This campaign can't be sent from its current state.")
        return redirect("store:email_marketing", site_id=site.id)
    return _launch(request, site, campaign)


@login_required
@require_POST
def email_campaign_pause(request, site_id, campaign_id):
    site = get_owned_site(request, site_id)
    Campaign.objects.for_site(site).filter(
        pk=campaign_id, status__in=[Campaign.Status.ACTIVE, Campaign.Status.SCHEDULED]
    ).update(status=Campaign.Status.PAUSED)
    messages.success(request, "Campaign paused.")
    return redirect("store:email_marketing", site_id=site.id)


@login_required
@require_POST
def email_campaign_resume(request, site_id, campaign_id):
    site = get_owned_site(request, site_id)
    campaign = get_object_or_404(Campaign.objects.for_site(site), pk=campaign_id)
    if campaign.status != Campaign.Status.PAUSED:
        return redirect("store:email_marketing", site_id=site.id)
    now = timezone.now()
    scheduled = campaign.start_at and campaign.start_at > now
    campaign.status = Campaign.Status.SCHEDULED if scheduled else Campaign.Status.ACTIVE
    campaign.save(update_fields=["status", "updated_at"])
    if not scheduled:
        from outreach import tasks

        tasks.resume_campaign.delay(str(campaign.id))
    messages.success(request, "Campaign resumed.")
    return redirect("store:email_marketing", site_id=site.id)


@login_required
@require_POST
def email_campaign_delete(request, site_id, campaign_id):
    site = get_owned_site(request, site_id)
    campaign = get_object_or_404(Campaign.objects.for_site(site), pk=campaign_id)
    campaign.soft_delete()
    activity.record(
        "store.email_campaign.deleted", request=request, site=site,
        target=campaign, target_kind="EmailCampaign", label=campaign.name,
    )
    messages.success(request, "Campaign removed.")
    return redirect("store:email_marketing", site_id=site.id)


# ── audiences ────────────────────────────────────────────────────────────────

#: Where a new audience list draws its recipients from.
AUDIENCE_SOURCES = ("subscribers", "customers", "empty")


def _fill_list_from(site, lst, source: str) -> int:
    """Add the store's subscribers or customers to ``lst``; returns how many
    new members were added (addresses already on the list are skipped)."""
    existing = set(m.lower() for m in lst.members.values_list("email", flat=True))
    to_create = []
    if source == "customers":
        sync_customers_for_site(site)
        for c in Customer.objects.for_site(site).filter(is_deleted=False):
            e = (c.email or "").strip().lower()
            if not e or e in existing:
                continue
            existing.add(e)
            to_create.append(
                AudienceMember(site=site, audience_list=lst, name=c.name or "", email=e, phone=c.phone or "", customer=c)
            )
    elif source == "subscribers":
        from commerce.models import NewsletterSubscriber

        emails = (
            NewsletterSubscriber.objects.for_site(site)
            .filter(is_active=True)
            .values_list("email", flat=True)
        )
        # Link a subscriber to their customer record when they have one.
        customers = {}
        for c in Customer.objects.for_site(site).filter(is_deleted=False).only("id", "email", "name", "phone"):
            if c.email:
                customers.setdefault(c.email.strip().lower(), c)
        for raw in emails:
            e = (raw or "").strip().lower()
            if not e or "@" not in e or e in existing:
                continue
            existing.add(e)
            c = customers.get(e)
            to_create.append(
                AudienceMember(
                    site=site, audience_list=lst, email=e, customer=c,
                    name=(c.name or "") if c else "", phone=(c.phone or "") if c else "",
                )
            )
    if to_create:
        AudienceMember.objects.bulk_create(to_create, ignore_conflicts=True)
    return len(to_create)


@login_required
def email_audiences(request, site_id):
    site = get_owned_site(request, site_id)
    if request.method == "POST":
        source = request.POST.get("source") or "empty"
        if source not in AUDIENCE_SOURCES:
            source = "empty"
        default_name = {"subscribers": "All subscribers", "customers": "All customers"}.get(source, "")
        name = (request.POST.get("name") or "").strip() or default_name
        if not name:
            messages.error(request, "Give the list a name.")
            return redirect("store:email_audiences", site_id=site.id)
        if source == "empty":
            lst = AudienceList.objects.create(site=site, name=name)
            messages.success(request, f"List “{lst.name}” created.")
        else:
            lst, _ = AudienceList.objects.for_site(site).get_or_create(
                name=name, is_archived=False, defaults={"site": site}
            )
            added = _fill_list_from(site, lst, source)
            who = "subscriber" if source == "subscribers" else "customer"
            messages.success(
                request,
                f"“{lst.name}” built from your {who}s: {added} added, "
                f"{lst.members.count()} recipient(s) in total.",
            )
        return redirect("store:email_audience_detail", site_id=site.id, list_id=lst.id)

    lists = (
        AudienceList.objects.for_site(site)
        .filter(is_archived=False)
        .annotate(members_count=Count("members"))
        .order_by("-created_at")
    )
    context = store_context(request, site, "email_marketing")
    context.update(
        {
            "lists": lists,
            "customer_count": Customer.objects.for_site(site).filter(is_deleted=False).count(),
            "subscriber_count": _subscriber_count(site),
            "suppressions": Suppression.objects.for_site(site).order_by("-created_at")[:100],
            "suppression_count": Suppression.objects.for_site(site).count(),
        }
    )
    return render(request, "store/email_audiences.html", context)


def _subscriber_count(site) -> int:
    from commerce.models import NewsletterSubscriber

    return NewsletterSubscriber.objects.for_site(site).filter(is_active=True).count()


@login_required
def email_audience_detail(request, site_id, list_id):
    site = get_owned_site(request, site_id)
    lst = get_object_or_404(AudienceList.objects.for_site(site), pk=list_id)
    members = lst.members.order_by("-created_at")
    context = store_context(request, site, "email_marketing")
    context.update({"list": lst, "members": members, "member_count": members.count()})
    return render(request, "store/email_audience_detail.html", context)


@login_required
@require_POST
def email_audience_add(request, site_id, list_id):
    site = get_owned_site(request, site_id)
    lst = get_object_or_404(AudienceList.objects.for_site(site), pk=list_id)

    # The recipients editor adds many people at once and saves them together: it
    # posts a JSON array of {name, email, phone}. A single name/email/phone POST
    # is still accepted (backward compatible).
    rows = []
    raw = request.POST.get("recipients")
    if raw:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            data = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            rows.append({
                "name": (item.get("name") or "").strip(),
                "email": (item.get("email") or "").strip().lower(),
                "phone": (item.get("phone") or "").strip(),
            })
    else:
        rows.append({
            "name": (request.POST.get("name") or "").strip(),
            "email": (request.POST.get("email") or "").strip().lower(),
            "phone": (request.POST.get("phone") or "").strip(),
        })

    valid = [r for r in rows if r["email"] and "@" in r["email"]]
    if not valid:
        messages.error(request, "Enter at least one valid email address.")
        return redirect("store:email_audience_detail", site_id=site.id, list_id=lst.id)

    added = 0
    seen = set()
    for r in valid:
        if r["email"] in seen:
            continue
        seen.add(r["email"])
        _, created = AudienceMember.objects.for_site(site).get_or_create(
            audience_list=lst,
            email=r["email"],
            defaults={"site": site, "name": r["name"], "phone": r["phone"]},
        )
        if created:
            added += 1

    if added:
        messages.success(request, f"Added {added} recipient{'' if added == 1 else 's'}.")
    else:
        messages.info(request, "No new recipients  those addresses are already on the list.")
    return redirect("store:email_audience_detail", site_id=site.id, list_id=lst.id)


@login_required
@require_POST
def email_audience_import(request, site_id, list_id):
    site = get_owned_site(request, site_id)
    lst = get_object_or_404(AudienceList.objects.for_site(site), pk=list_id)
    f = request.FILES.get("file")
    if not f:
        messages.error(request, "Choose a .csv or .xlsx file to import.")
        return redirect("store:email_audience_detail", site_id=site.id, list_id=lst.id)

    rows, error = parse_recipients(f)
    if error:
        messages.error(request, error)
        return redirect("store:email_audience_detail", site_id=site.id, list_id=lst.id)

    existing = set(m.lower() for m in lst.members.values_list("email", flat=True))
    added = 0
    to_create = []
    for r in rows:
        e = r["email"]
        if e in existing:
            continue
        existing.add(e)
        to_create.append(
            AudienceMember(site=site, audience_list=lst, name=r["name"], email=e, phone=r["phone"])
        )
    if to_create:
        AudienceMember.objects.bulk_create(to_create, ignore_conflicts=True)
        added = len(to_create)
    messages.success(request, f"Imported {added} new recipient(s).")
    return redirect("store:email_audience_detail", site_id=site.id, list_id=lst.id)


@login_required
@require_POST
def email_audience_from_customers(request, site_id):
    """Create (or refresh) a list from the store's customers or subscribers
    (``source``; customers by default, for older links/forms)."""
    site = get_owned_site(request, site_id)
    source = request.POST.get("source")
    if source not in ("customers", "subscribers"):
        source = "customers"
    fallback = "All subscribers" if source == "subscribers" else "All customers"
    name = (request.POST.get("name") or fallback).strip() or fallback
    lst, _ = AudienceList.objects.for_site(site).get_or_create(
        name=name, is_archived=False, defaults={"site": site}
    )
    _fill_list_from(site, lst, source)
    messages.success(request, f"“{lst.name}” now has {lst.members.count()} recipient(s).")
    return redirect("store:email_audience_detail", site_id=site.id, list_id=lst.id)


@login_required
@require_POST
def email_audience_member_delete(request, site_id, list_id, member_id):
    site = get_owned_site(request, site_id)
    AudienceMember.objects.for_site(site).filter(pk=member_id, audience_list_id=list_id).delete()
    return redirect("store:email_audience_detail", site_id=site.id, list_id=list_id)


@login_required
@require_POST
def email_audience_delete(request, site_id, list_id):
    site = get_owned_site(request, site_id)
    AudienceList.objects.for_site(site).filter(pk=list_id).delete()
    messages.success(request, "List removed.")
    return redirect("store:email_audiences", site_id=site.id)


@login_required
@require_POST
def email_suppression_delete(request, site_id, suppression_id):
    """Re-subscribe an address the store wants to contact again."""
    site = get_owned_site(request, site_id)
    Suppression.objects.for_site(site).filter(pk=suppression_id).delete()
    messages.success(request, "Removed from the suppression list.")
    return redirect("store:email_audiences", site_id=site.id)


# ── drip sequences ───────────────────────────────────────────────────────────

@login_required
def email_sequences(request, site_id):
    site = get_owned_site(request, site_id)
    sequences = (
        DripSequence.objects.for_site(site).filter(is_archived=False).order_by("-created_at")
    )
    context = store_context(request, site, "email_marketing")
    context.update({"sequences": sequences})
    return render(request, "store/email_sequences.html", context)


@login_required
def email_sequence_new(request, site_id):
    site = get_owned_site(request, site_id)
    if request.method == "POST":
        return _save_sequence(request, site, None)
    return _render_sequence(request, site, None)


@login_required
def email_sequence_edit(request, site_id, sequence_id):
    site = get_owned_site(request, site_id)
    seq = get_object_or_404(DripSequence.objects.for_site(site), pk=sequence_id)
    if request.method == "POST":
        return _save_sequence(request, site, seq)
    return _render_sequence(request, site, seq)


def _render_sequence(request, site, seq):
    # Each step's content is a published design, chosen in the editor. Match every
    # saved step's HTML back to its template so an edit preselects it per step.
    from core.queries import sorted_rows

    # Template rows carry full email HTML: sort keys only (MySQL error 1038).
    templates = sorted_rows(EmailTemplate.objects.filter(status=EmailTemplate.Status.PUBLISHED))
    by_html = {}
    for t in templates:
        by_html.setdefault((t.html or "").strip(), str(t.id))
    enriched_steps = []
    for step in (seq.steps if seq else []) or []:
        if not isinstance(step, dict):
            continue
        item = dict(step)
        item["template_id"] = by_html.get((item.get("html") or "").strip(), "")
        enriched_steps.append(item)

    context = store_context(request, site, "email_marketing")
    context.update(
        {
            "sequence": seq,
            "is_edit": bool(seq),
            "steps_json": json.dumps(enriched_steps),
            "templates": templates,
        }
    )
    return render(request, "store/email_sequence_form.html", context)


def _save_sequence(request, site, seq):
    name = (request.POST.get("name") or "").strip() or "Untitled sequence"
    try:
        steps = json.loads(request.POST.get("steps") or "[]")
    except (ValueError, TypeError):
        steps = []
    # Normalise/clean the steps.
    clean = []
    seen_days = set()
    for item in steps if isinstance(steps, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            day = int(item.get("day"))
        except (TypeError, ValueError):
            continue
        if day < 1 or day > 365 or day in seen_days:
            continue
        subject = (item.get("subject") or "").strip()
        if not subject:
            continue
        seen_days.add(day)
        clean.append({"day": day, "subject": subject, "html": item.get("html") or "", "message": (item.get("message") or "").strip()})
    clean.sort(key=lambda s: s["day"])

    if not clean:
        messages.error(request, "Add at least one step with a day and a subject.")
        return _render_sequence(request, site, seq)

    if seq is None:
        seq = DripSequence(site=site)
    seq.name = name
    seq.steps = clean
    seq.save()
    messages.success(request, f"Sequence “{seq.name}” saved.")
    return redirect("store:email_sequences", site_id=site.id)


@login_required
@require_POST
def email_sequence_delete(request, site_id, sequence_id):
    site = get_owned_site(request, site_id)
    DripSequence.objects.for_site(site).filter(pk=sequence_id).update(is_archived=True)
    messages.success(request, "Sequence removed.")
    return redirect("store:email_sequences", site_id=site.id)


# ── AI email builder ─────────────────────────────────────────────────────────

_AI_SYSTEM = (
    "You are an expert email template designer for an online store's marketing. "
    "Return ONE complete, self-contained HTML email document (<!DOCTYPE html> … "
    "</html>) using table-based layout and INLINE CSS only (no <style>, no external "
    "stylesheets), web-safe fonts, and squared corners. It must render across Gmail, "
    "Outlook, Apple Mail and Yahoo. Include a prominent call-to-action anchor whose "
    "href is exactly {{cta_url}}, an unsubscribe link in the footer whose href is "
    "exactly {{unsubscribe_url}}, and use {{recipient_name}} and {{store_name}} where "
    "natural. Return ONLY the raw HTML  no markdown fences, no commentary."
)


@login_required
@require_POST
def email_ai_generate(request, site_id):
    site = get_owned_site(request, site_id)

    limits = limits_for_site(site)
    if not limits.get("allow_ai_builder"):
        return JsonResponse(
            {"ok": False, "error": "The AI email builder isn’t included in your plan."}, status=403
        )

    from core import ratelimit

    try:
        ratelimit.check("ai.generate", str(getattr(request.user, "id", "anon")))
    except ratelimit.RateLimitExceeded as exc:
        return JsonResponse(
            {"ok": False, "error": "You’ve made a lot of requests  please wait a moment."},
            status=429,
        )

    try:
        body = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Invalid request."}, status=400)

    brief = (body.get("message") or "").strip()
    if not brief:
        return JsonResponse({"ok": False, "error": "Describe the email you want."}, status=400)

    from ud import ai

    if not ai.text_available():
        return JsonResponse(
            {"ok": False, "error": "The AI builder isn’t configured. Please contact support."},
            status=503,
        )

    prompt = f"{_AI_SYSTEM}\n\nStore name: {site.name}\n\nBrief: {brief}"
    result = ai.generate_text(prompt)
    if not result.get("ok"):
        return JsonResponse({"ok": False, "error": result.get("error") or "Generation failed."}, status=502)

    html = (result.get("text") or "").strip()
    if html.startswith("```"):
        lines = html.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        html = "\n".join(lines).strip()
    return JsonResponse({"ok": True, "html": html})


# ── admin-authored templates (picker in the composer) ────────────────────────

@login_required
def email_template_json(request, site_id, template_id):
    """Return a published template's HTML so the composer can start from it."""
    get_owned_site(request, site_id)  # ownership check
    t = EmailTemplate.objects.filter(
        pk=template_id, status=EmailTemplate.Status.PUBLISHED
    ).first()
    if not t:
        return JsonResponse({"ok": False, "error": "Template not found."}, status=404)
    return JsonResponse({"ok": True, "html": t.html or "", "name": t.name})


@login_required
def email_template_preview(request, site_id, template_id):
    """Standalone demo preview of a template  opened in its own page (eye icon)."""
    from django.http import Http404, HttpResponse

    from outreach.services import rendering

    get_owned_site(request, site_id)  # ownership check
    t = EmailTemplate.objects.filter(
        pk=template_id, status=EmailTemplate.Status.PUBLISHED
    ).first()
    if not t:
        raise Http404("Template not found")
    return HttpResponse(rendering.preview_html(t.html or ""))


# ── built-in design preview (live, inside the composer) ─────────────────────

@login_required
def email_design_preview(request, site_id):
    """Render the built-in promo design for the composer's preview frame.

    GET shows the defaults (the design list's eye icon); POST renders exactly
    what the owner has typed so far, with the products they picked. The page is
    framed same-origin by the composer, so it carries its own CSP (inline email
    styles, remote images, no script at all) and SAMEORIGIN framing.
    """
    from outreach import promo_design

    site = get_owned_site(request, site_id)
    if request.method == "POST":
        data = promo_design.from_post(request.POST, site)
        mode = request.POST.get("product_mode") or Campaign.ProductMode.MULTI
        try:
            raw_ids = json.loads(request.POST.get("product_ids") or "[]")
        except (ValueError, TypeError):
            raw_ids = []
        ids = [str(x) for x in raw_ids if isinstance(x, (str, int))] if isinstance(raw_ids, list) else []
        preheader = (request.POST.get("preheader") or "").strip()
    else:
        data = promo_design.defaults(site)
        mode = Campaign.ProductMode.MULTI
        ids = []
        preheader = ""

    limit = _product_limit(mode, promo_design.DESIGN_KEY)
    products = promo_design.load_products(site, ids=ids[:limit], limit=limit, demo=True)
    html = promo_design.render(
        site, data, products=products, preheader=preheader, absolute=False, demo=True
    )
    html = (
        html.replace("{{unsubscribe_url}}", "#unsubscribe")
        .replace("{{tracking_pixel}}", "")
        .replace("<head>", '<head><base target="_blank">', 1)
    )

    response = HttpResponse(html)
    response["X-Frame-Options"] = "SAMEORIGIN"
    response["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com data:; img-src 'self' data: https: http:; "
        "base-uri 'self'; form-action 'none'; frame-ancestors 'self'"
    )
    return response
