"""Store overview — the landing page for one store's management surface."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import render
from django.utils import timezone

from commerce.context import get_owned_site, store_context
from commerce.models import Order, Product


@login_required
def overview(request, site_id):
    site = get_owned_site(request, site_id)
    since = timezone.now() - timedelta(days=30)

    orders = Order.objects.for_site(site)
    paid_statuses = [Order.Status.PAID, Order.Status.SHIPPED, Order.Status.DELIVERED]

    revenue = (
        orders.filter(created_at__gte=since, status__in=paid_statuses).aggregate(
            total=Sum("total")
        )["total"]
        or Decimal("0.00")
    )
    order_count = orders.filter(created_at__gte=since).count()
    products = Product.objects.for_site(site)
    product_count = products.filter(is_deleted=False).count()
    open_orders = orders.filter(
        status__in=[Order.Status.CREATED, Order.Status.CONFIRMED, Order.Status.PAID]
    ).count()

    kpis = [
        {"label": "Revenue", "value": f"{site.currency} {revenue:,.0f}", "caption": "Last 30 days"},
        {"label": "Orders", "value": order_count, "caption": "Last 30 days"},
        {"label": "Open orders", "value": open_orders, "caption": "Awaiting fulfilment"},
        {"label": "Products", "value": product_count, "caption": "Live in catalogue"},
    ]

    recent_orders = list(orders.select_related("customer")[:8])
    # Low/out-of-stock computed in Python: stock spans a JSON variant blob, so
    # it can't be a single indexed column. Bounded scan of live products.
    live_products = products.filter(is_deleted=False, is_active=True)[:500]
    low_stock = [p for p in live_products if p.is_low_stock or p.is_out_of_stock][:6]

    context = store_context(request, site, "overview")
    context.update(
        {
            "kpis": kpis,
            "recent_orders": recent_orders,
            "low_stock": low_stock,
            "status_choices": Order.Status,
        }
    )
    return render(request, "store/overview.html", context)
