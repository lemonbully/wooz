"""Customers directory and per-customer history."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, render

from commerce.context import get_owned_site, store_context
from commerce.models import Customer, Order


@login_required
def customers(request, site_id):
    site = get_owned_site(request, site_id)
    qs = Customer.objects.for_site(site).filter(is_deleted=False)
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q))
    qs = qs.annotate(order_count=Count("orders"), spent=Sum("orders__total"))

    page = Paginator(qs, 30).get_page(request.GET.get("page") or 1)
    context = store_context(request, site, "customers")
    context.update({"customers": page, "q": q})
    return render(request, "store/customers.html", context)


@login_required
def customer_detail(request, site_id, customer_id):
    site = get_owned_site(request, site_id)
    customer = get_object_or_404(
        Customer.objects.for_site(site).filter(is_deleted=False), pk=customer_id
    )
    orders = Order.objects.for_site(site).filter(customer=customer)
    spent = orders.aggregate(t=Sum("total"))["t"] or Decimal("0.00")

    context = store_context(request, site, "customers")
    context.update(
        {
            "customer": customer,
            "orders": orders[:50],
            "order_count": orders.count(),
            "spent": spent,
            "addresses": customer.addresses.for_site(site),
        }
    )
    return render(request, "store/customer_detail.html", context)
