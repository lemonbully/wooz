"""Store analytics — sales and traffic at a glance.

Split by business model: a D2C store sees conversion and traffic; a B2B store
sees enquiries and order-value metrics; a "both" store sees each channel.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, Sum
from django.shortcuts import render
from django.utils import timezone

from commerce.context import get_owned_site, store_context
from commerce.models import AnalyticsEvent, Enquiry, Order


@login_required
def analytics(request, site_id):
    site = get_owned_site(request, site_id)

    try:
        days = max(1, min(365, int(request.GET.get("days") or 30)))
    except ValueError:
        days = 30
    since = timezone.now() - timedelta(days=days)

    orders = Order.objects.for_site(site).filter(created_at__gte=since)
    paid = orders.filter(status__in=[Order.Status.PAID, Order.Status.SHIPPED, Order.Status.DELIVERED])

    revenue = paid.aggregate(t=Sum("total"))["t"] or Decimal("0.00")
    order_count = orders.count()
    aov = paid.aggregate(a=Avg("total"))["a"] or Decimal("0.00")

    events = AnalyticsEvent.objects.for_site(site).filter(created_at__gte=since)
    sessions = (
        events.filter(kind=AnalyticsEvent.Kind.PAGE_VIEW)
        .values("session_id")
        .distinct()
        .count()
    )
    purchases = events.filter(kind=AnalyticsEvent.Kind.PURCHASE).count()
    conversion = (purchases / sessions * 100) if sessions else 0.0

    metrics = [
        {"label": "Revenue", "value": f"{site.currency} {revenue:,.0f}"},
        {"label": "Orders", "value": order_count},
        {"label": "Avg. order value", "value": f"{site.currency} {aov:,.0f}"},
    ]
    if site.is_d2c:
        metrics += [
            {"label": "Sessions", "value": sessions},
            {"label": "Conversion", "value": f"{conversion:.1f}%"},
        ]
    if site.is_b2b:
        metrics.append(
            {"label": "Enquiries", "value": Enquiry.objects.for_site(site).filter(created_at__gte=since).count()}
        )

    # Top products by units in the window.
    top_products = (
        Order.objects.for_site(site)
        .filter(created_at__gte=since)
        .values("items__product__title")
        .annotate(units=Sum("items__quantity"), revenue=Sum("items__line_total"))
        .order_by("-units")[:8]
    )

    # Orders by status, for a simple distribution bar.
    by_status = {row["status"]: row["n"] for row in orders.values("status").annotate(n=Count("id"))}

    context = store_context(request, site, "analytics")
    context.update(
        {
            "days": days,
            "metrics": metrics,
            "top_products": top_products,
            "by_status": by_status,
            "status_choices": Order.Status,
        }
    )
    return render(request, "store/analytics.html", context)
