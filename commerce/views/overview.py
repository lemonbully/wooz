"""Store overview  the landing page for one store's management surface."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from commerce.context import get_owned_site, store_context
from commerce.models import Order, Product

# The performance metrics are shown over a selectable time window. Presets are
# whole-day counts back from now; "custom" takes an explicit from/to date range.
_WINDOW_PRESETS = {"7": 7, "15": 15, "30": 30}
_DEFAULT_WINDOW = "30"


def _parse_date(raw: str):
    """Parse a ``YYYY-MM-DD`` string from a date input, or return ``None``."""
    try:
        return datetime.strptime((raw or "").strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _resolve_window(request):
    """Resolve the selected metrics window into a since/until range.

    Returns a dict the view uses for filtering and the template uses to render
    the filter control. Invalid or incomplete input falls back to 30 days.
    """
    now = timezone.now()
    window = (request.GET.get("window") or _DEFAULT_WINDOW).strip()

    if window == "custom":
        d_from = _parse_date(request.GET.get("from"))
        d_to = _parse_date(request.GET.get("to"))
        if d_from and d_to and d_from <= d_to:
            since = timezone.make_aware(datetime.combine(d_from, time.min))
            until = timezone.make_aware(datetime.combine(d_to, time.max))
            return {
                "window": "custom",
                "since": since,
                "until": until,
                "label": f"{d_from:%d %b %Y} – {d_to:%d %b %Y}",
                "custom_from": d_from.isoformat(),
                "custom_to": d_to.isoformat(),
            }
        # Incomplete/invalid custom range  fall through to the default preset.
        window = _DEFAULT_WINDOW

    days = _WINDOW_PRESETS.get(window, _WINDOW_PRESETS[_DEFAULT_WINDOW])
    return {
        "window": str(days),
        "since": now - timedelta(days=days),
        "until": now,
        "label": f"Last {days} days",
        "custom_from": "",
        "custom_to": "",
    }


@login_required
def overview(request, site_id):
    site = get_owned_site(request, site_id)
    win = _resolve_window(request)
    since, until = win["since"], win["until"]

    orders = Order.objects.for_site(site)
    in_window = {"created_at__gte": since, "created_at__lte": until}

    # Revenue counts every placed order (a COD order never reaches "paid"); only
    # cancelled/archived orders are excluded. Mirrors the Analytics page.
    revenue = (
        orders.filter(**in_window, status__in=Order.REVENUE_STATUSES, replacement_seq=0).aggregate(
            total=Sum("total")
        )["total"]
        or Decimal("0.00")
    )
    order_count = orders.filter(**in_window).count()
    products = Product.objects.for_site(site)
    product_count = products.filter(is_deleted=False).count()
    open_orders = orders.filter(
        status__in=[Order.Status.CREATED, Order.Status.CONFIRMED, Order.Status.PAID]
    ).count()
    cancelled_count = orders.filter(
        **in_window, status__in=[Order.Status.CANCELLED, Order.Status.ARCHIVED]
    ).count()

    recent_orders = list(orders.select_related("customer")[:8])
    # Low/out-of-stock computed in Python: stock spans a JSON variant blob, so
    # it can't be a single indexed column. Bounded scan of live products, reused
    # for both the out-of-stock KPI and the restock list.
    live_products = list(products.filter(is_deleted=False, is_active=True)[:500])
    low_stock = [p for p in live_products if p.is_low_stock or p.is_out_of_stock][:6]
    # Same rows as the Out of stock page it links to: a sold-out variation of
    # a product that still has other stock counts too.
    from commerce.views.catalog import _stock_rows

    out_of_stock_count = len(_stock_rows(live_products)[0])

    # Each KPI links through to the page that owns that metric.
    def _url(name):
        return reverse(f"store:{name}", args=[site.id])

    # Windowed metrics carry the selected range label; state snapshots keep their
    # own captions (they describe a point in time, not the window).
    win_caption = win["label"]
    # Revenue opens Analytics on its "Sales analysis" tab, over the same number
    # of days when a preset window is selected (a custom range keeps the
    # Analytics default, since that page only takes a day count).
    revenue_url = f"{_url('analytics')}?tab=sales"
    if win["window"] != "custom":
        revenue_url += f"&days={win['window']}"
    kpis = [
        {"label": "Revenue", "value": f"{site.currency} {revenue:,.0f}", "caption": win_caption, "url": revenue_url},
        {"label": "Orders", "value": order_count, "caption": win_caption, "url": _url("orders")},
        {"label": "Pending orders", "value": open_orders, "caption": "Awaiting fulfilment", "url": f"{_url('orders')}?tab=pending"},
        {"label": "Products", "value": product_count, "caption": "Live in catalogue", "url": _url("products")},
        {"label": "Cancelled orders", "value": cancelled_count, "caption": win_caption, "url": f"{_url('orders')}?tab=cancelled"},
        {"label": "Out of stock", "value": out_of_stock_count, "caption": "Needs restock", "url": _url("out_of_stock")},
    ]

    context = store_context(request, site, "overview")
    context.update(
        {
            "kpis": kpis,
            "recent_orders": recent_orders,
            "low_stock": low_stock,
            "status_choices": Order.owner_statuses(),
            # Metrics window filter state for the control in the header.
            "window": win["window"],
            "window_label": win["label"],
            "window_presets": [("7", "7 days"), ("15", "15 days"), ("30", "30 days")],
            "custom_from": win["custom_from"],
            "custom_to": win["custom_to"],
        }
    )
    return render(request, "store/overview.html", context)
