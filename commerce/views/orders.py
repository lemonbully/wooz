"""Orders, post-order requests and contact enquiries."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from commerce.context import get_owned_site, store_context
from commerce.models import Enquiry, Order, OrderRequest


def _paginate(request, queryset, per_page=25):
    paginator = Paginator(queryset, per_page)
    return paginator.get_page(request.GET.get("page") or 1)


@login_required
def orders(request, site_id):
    site = get_owned_site(request, site_id)
    qs = Order.objects.for_site(site).select_related("customer")

    status = request.GET.get("status") or ""
    channel = request.GET.get("channel") or ""
    q = (request.GET.get("q") or "").strip()

    if status:
        qs = qs.filter(status=status)
    if channel and site.business_model == site.BusinessModel.BOTH:
        qs = qs.filter(channel=channel)
    if q:
        qs = qs.filter(
            Q(customer_name__icontains=q)
            | Q(customer_email__icontains=q)
            | Q(customer_phone__icontains=q)
            | Q(tracking_id__icontains=q)
        )

    # Status tabs with live counts.
    counts = {c.value: 0 for c in Order.Status}
    for row in Order.objects.for_site(site).values("status"):
        counts[row["status"]] = counts.get(row["status"], 0) + 1

    context = store_context(request, site, "orders")
    context.update(
        {
            "orders": _paginate(request, qs),
            "status_choices": Order.Status,
            "counts": counts,
            "active_status": status,
            "active_channel": channel,
            "q": q,
            "show_channel": site.business_model == site.BusinessModel.BOTH,
        }
    )
    return render(request, "store/orders.html", context)


@login_required
def order_detail(request, site_id, order_id):
    site = get_owned_site(request, site_id)
    order = get_object_or_404(
        Order.objects.for_site(site).select_related("customer", "coupon"), pk=order_id
    )
    context = store_context(request, site, "orders")
    context.update(
        {
            "order": order,
            "items": order.items.for_site(site).select_related("product"),
            "payments": order.payments.for_site(site),
            "requests": order.requests.for_site(site),
            "shipments": order.shipments.for_site(site),
            "status_choices": Order.Status,
        }
    )
    return render(request, "store/order_detail.html", context)


@login_required
@require_POST
def update_status(request, site_id, order_id):
    site = get_owned_site(request, site_id)
    order = get_object_or_404(Order.objects.for_site(site), pk=order_id)
    new_status = request.POST.get("status") or ""
    if new_status in Order.Status.values:
        order.status = new_status
        order.save()
        messages.success(request, f"Order #{order.number} marked {order.get_status_display().lower()}.")
    else:
        messages.error(request, "Unknown status.")
    return redirect("store:order_detail", site_id=site.id, order_id=order.id)


@login_required
def requests(request, site_id):
    site = get_owned_site(request, site_id)
    qs = OrderRequest.objects.for_site(site).select_related("order")

    rtype = request.GET.get("type") or ""
    status = request.GET.get("status") or ""
    if rtype:
        qs = qs.filter(request_type=rtype)
    if status:
        qs = qs.filter(status=status)

    context = store_context(request, site, "requests")
    context.update(
        {
            "requests": _paginate(request, qs),
            "type_choices": OrderRequest.RequestType,
            "status_choices": OrderRequest.Status,
            "active_type": rtype,
            "active_status": status,
        }
    )
    return render(request, "store/requests.html", context)


@login_required
@require_POST
def resolve_request(request, site_id, request_id):
    site = get_owned_site(request, site_id)
    req = get_object_or_404(OrderRequest.objects.for_site(site), pk=request_id)
    decision = request.POST.get("decision") or ""
    if decision in OrderRequest.Status.values:
        req.status = decision
        if decision in (OrderRequest.Status.PROCESSED, OrderRequest.Status.APPROVED, OrderRequest.Status.REJECTED):
            req.processed_at = timezone.now()
        req.save()
        messages.success(request, "Request updated.")
    return redirect("store:requests", site_id=site.id)


@login_required
def enquiries(request, site_id):
    site = get_owned_site(request, site_id)
    qs = Enquiry.objects.for_site(site).filter(is_deleted=False).prefetch_related("products", "categories")
    unread_only = request.GET.get("unread") == "1"
    if unread_only:
        qs = qs.filter(is_read=False)

    context = store_context(request, site, "enquiries")
    context.update(
        {
            "enquiries": _paginate(request, qs),
            "unread_only": unread_only,
            "is_b2b": site.is_b2b,
        }
    )
    return render(request, "store/enquiries.html", context)
