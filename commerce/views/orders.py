"""Orders, fulfilment, post-order requests and contact enquiries.

The Orders page is a faithful port of the reference's D2C ``owner_orders`` tab:
status pills with live counts, provider-conditional tabs (Ready to Ship /
Waiting for Pickup for Delhivery-style couriers; Pickup Scheduled for
Shiprocket), per-row Accept/Reject before acceptance, bulk Accept/Cancel, an
Auto-create-shipping toggle that skips the Pending step, a courier tracking
popup fed by stored webhook events, and an order-edit page. Live courier API
calls are stubbed in :mod:`commerce.shipping`; the *flow* between tabs is real.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Exists, OuterRef, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from commerce import activity
from commerce import order_edit as order_edit_svc
from commerce import shipping as shipping_svc
from commerce.context import get_owned_site, store_context
from commerce.models import (
    Enquiry,
    Order,
    OrderRequest,
    Payment,
    Shipment,
)
from tenancy.models import Site

# Shipment statuses that mean the parcel has physically left the warehouse.
_IN_TRANSIT = [
    Shipment.Status.IN_TRANSIT,
    Shipment.Status.PICKED_UP,
    Shipment.Status.OUT_FOR_DELIVERY,
]
_RTO_STATUSES = [Shipment.Status.RTO]
# Once a parcel has left with the courier the order can't go back to Pending.
_WITH_COURIER = [
    Shipment.Status.PICKED_UP,
    Shipment.Status.IN_TRANSIT,
    Shipment.Status.OUT_FOR_DELIVERY,
    Shipment.Status.DELIVERY_FAILED,
    Shipment.Status.DELIVERED,
    Shipment.Status.RTO,
]

# UI tab → underlying order statuses (RTO is driven purely by shipment status).
_TAB_TO_STATUSES = {
    "pending": [Order.Status.CREATED, Order.Status.PAID],
    "ready_to_pack": [Order.Status.CONFIRMED],
    "waiting_pickup": [Order.Status.CONFIRMED],
    "pickup_scheduled": [Order.Status.CONFIRMED],
    "shipped": [Order.Status.SHIPPED],
    "delivered": [Order.Status.DELIVERED],
    "cancelled": [Order.Status.CANCELLED, Order.Status.ARCHIVED],
    "rto": [],
}


def _paginate(request, queryset, per_page=25):
    paginator = Paginator(queryset, per_page)
    return paginator.get_page(request.GET.get("page") or 1)


def _rto_q():
    """Orders whose parcel the courier has *returned* to the seller. A return
    still on its way back is not RTO yet (see courier_webhook.RTO_IN_PROGRESS)."""
    return Q(shipments__status=Shipment.Status.RTO)


# Tabs an order leaves once its parcel is back at origin: a returned order is
# shown only under RTO, whether it was shipped, delivered, or cancelled by the
# owner while with the courier.
_RTO_EXCLUSIVE_TABS = ("shipped", "delivered", "cancelled")


def _without_rto(site, qs):
    returned = Order.objects.for_site(site).filter(_rto_q()).values("pk")
    return qs.exclude(pk__in=returned)


def _order_display_num(order) -> str:
    return order.display_number or str(order.pk)


def _send_status_message(request, order, verb: str) -> None:
    messages.success(request, f"Order #{_order_display_num(order)} {verb}.")


# ── Orders tab ───────────────────────────────────────────────────────────────

@login_required
def orders(request, site_id):
    site = get_owned_site(request, site_id)
    if not site.is_d2c:
        messages.error(request, "Orders are available only for D2C stores.")
        return redirect("store:overview", site_id=site.id)

    if request.method == "POST":
        return _handle_order_action(request, site)

    is_shiprocket = shipping_svc.selected_provider_key(site) == "shiprocket"
    auto_ship = shipping_svc.auto_ship_enabled(site)

    tab = (request.GET.get("tab") or "pending").lower()
    q = (request.GET.get("q") or "").strip()

    # Keep the user on a tab that is actually visible for this provider/setting.
    # Both couriers use "Ready to Ship"; the post-pickup tab differs  Shiprocket
    # calls it "Pickup Scheduled", Delhivery "Waiting For Pickup".
    if is_shiprocket and tab == "waiting_pickup":
        tab = "pickup_scheduled"
    elif not is_shiprocket and tab == "pickup_scheduled":
        tab = "ready_to_pack"
    date_filter = _date_filter(request)
    counts = _tab_counts(site, date_filter["q"])
    # Whether the Pending tab exists is a property of the store, not of the
    # chosen date window, so judge it on the unfiltered count.
    pending_total = (
        _pending_count(Order.objects.for_site(site))
        if date_filter["active"]
        else counts["pending"]
    )
    if auto_ship and tab == "pending" and not pending_total:
        tab = "ready_to_pack"

    qs = _tab_queryset(site, tab)
    if q:
        qs = _apply_search(qs, _tab_queryset(site, tab), q)
    if date_filter["active"]:
        qs = qs.filter(date_filter["q"])

    qs = (
        qs.order_by("-created_at")
        .annotate(
            has_paid_txn=Exists(
                Payment.objects.filter(
                    site=site, order=OuterRef("pk"), status=Payment.Status.PAID
                )
            )
        )
        .select_related("collaboration")
        .prefetch_related("items__product__images", "shipments")
    )

    try:
        per_page = int(request.GET.get("per") or 25)
    except (TypeError, ValueError):
        per_page = 25
    if per_page not in (15, 25, 50, 100):
        per_page = 25

    paginator = Paginator(qs, per_page)
    try:
        page_obj = paginator.page(request.GET.get("page") or 1)
    except (PageNotAnInteger, EmptyPage):
        page_obj = paginator.page(1)

    # The day the courier comes, shown on each row while a pickup is pending.
    if tab in ("pickup_scheduled", "waiting_pickup"):
        for o in page_obj.object_list:
            o.pickup_on = None
            for sh in o.shipments.all():
                if sh.status == Shipment.Status.PICKUP_REQUESTED:
                    o.pickup_on = _parse_iso_date(str((sh.meta or {}).get("pickup_date") or "")[:10])
                    if o.pickup_on:
                        break

    pickup_min, pickup_max = shipping_svc.pickup_date_window()
    # Where an Edit link should bring the owner back to: this exact tab (as
    # resolved above), date window, search and page.
    return_qs = _clean_back(
        urlencode({**{k: request.GET.get(k) for k in _BACK_KEYS if request.GET.get(k)}, "tab": tab})
    )
    context = store_context(request, site, "orders")
    context.update(
        {
            "orders": page_obj.object_list,
            "return_qs": return_qs,
            "page_obj": page_obj,
            "paginator": paginator,
            "per_page": per_page,
            "active_tab": tab,
            "counts": counts,
            "pending_total": pending_total,
            "q": q,
            "date_range": date_filter["key"],
            "date_from": date_filter["from"],
            "date_to": date_filter["to"],
            "date_label": date_filter["label"],
            "date_qs": date_filter["qs"],
            "date_ranges": _DATE_RANGES,
            "today_iso": timezone.localdate().isoformat(),
            "selected_provider": shipping_svc.selected_provider_key(site),
            "has_delhivery": shipping_svc.selected_provider_key(site) == "delhivery",
            "auto_ship_enabled": auto_ship,
            "has_shipping_provider": shipping_svc.enabled_provider(site) is not None,
            "default_warehouse_name": _default_warehouse_name(site),
            "pickup_window_open": shipping_svc.delhivery_pickup_window_open(),
            "pickup_date_min": pickup_min.isoformat(),
            "pickup_date_max": pickup_max.isoformat(),
            "pickup_after_noon": pickup_min > timezone.localdate(),
            "shiprocket_courier_set": bool(shipping_svc.shiprocket_courier_id(site)),
            "currency": site.currency,
            "export_months": _export_months(site),
            "export_states": _export_states(site),
        }
    )
    return render(request, "store/orders.html", context)


def _export_months(site) -> dict:
    from commerce import order_export

    return order_export.months_for(site)


def _export_states(site) -> list[str]:
    from commerce import order_export

    return order_export.states_for(site)


@login_required
def orders_export(request, site_id):
    """Download one month's orders as an Excel workbook.

    One month per download, never in bulk: a month opens on the 2nd of the
    following month and the last three years are offered (see
    :mod:`commerce.order_export`).
    """
    from django.utils.text import slugify

    from commerce import order_export

    site = get_owned_site(request, site_id)
    first = order_export.parse_month(request.GET.get("month") or "")
    if first is None or not order_export.is_available(site, order_export.month_key(first)):
        messages.error(
            request,
            "That month's export isn't available. Each month opens on the 2nd of the "
            "following month, for the last three years.",
        )
        return redirect("store:orders", site_id=site.id)
    # Optional State filter: only orders shipped to that state (and their KPIs).
    state = order_export.clean_state(request.GET.get("state") or "")
    data = order_export.render_month_xlsx(site, first, state)
    suffix = f"-{slugify(state)}" if state and slugify(state) else ""
    filename = f"{slugify(site.name) or 'store'}-orders-{order_export.month_key(first)}{suffix}.xlsx"
    response = HttpResponse(
        data,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# Date-window presets shown beside the status pills: (key, label).
_DATE_RANGES = [
    ("all", "All time"),
    ("yesterday", "Yesterday"),
    ("7d", "Last 7 days"),
    ("30d", "Last 30 days"),
]


def _parse_iso_date(value) -> date | None:
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def _date_filter(request) -> dict:
    """Resolve the ``range`` / ``from`` / ``to`` query params into an order
    filter on ``created_at``, in the store's local calendar days.

    Bounds are aware datetimes (start inclusive, end exclusive) rather than a
    ``__date`` lookup, which needs timezone tables on MySQL.
    """
    key = (request.GET.get("range") or "all").lower()
    today = timezone.localdate()
    start = end = None
    d_from = d_to = None

    if key == "yesterday":
        start = end = today - timedelta(days=1)
    elif key == "7d":
        start, end = today - timedelta(days=6), today
    elif key == "30d":
        start, end = today - timedelta(days=29), today
    elif key == "custom":
        d_from = _parse_iso_date(request.GET.get("from"))
        d_to = _parse_iso_date(request.GET.get("to"))
        if d_from and d_to and d_from > d_to:
            d_from, d_to = d_to, d_from
        start, end = d_from, d_to
        if not (start or end):
            key = "all"
    else:
        key = "all"

    q_obj = Q()
    if start:
        q_obj &= Q(created_at__gte=timezone.make_aware(datetime.combine(start, time.min)))
    if end:
        q_obj &= Q(
            created_at__lt=timezone.make_aware(
                datetime.combine(end + timedelta(days=1), time.min)
            )
        )

    if key == "custom":
        fmt = lambda d: d.strftime("%d %b %Y")  # noqa: E731
        if start and end:
            label = fmt(start) if start == end else f"{fmt(start)} – {fmt(end)}"
        elif start:
            label = f"From {fmt(start)}"
        else:
            label = f"Up to {fmt(end)}"
        params = {"range": "custom"}
        if d_from:
            params["from"] = d_from.isoformat()
        if d_to:
            params["to"] = d_to.isoformat()
    else:
        label = dict(_DATE_RANGES)[key]
        params = {} if key == "all" else {"range": key}

    return {
        "key": key,
        "active": key != "all",
        "q": q_obj,
        "from": d_from.isoformat() if d_from else "",
        "to": d_to.isoformat() if d_to else "",
        "label": label,
        # Appended to the status-pill links so switching tab keeps the window.
        "qs": ("&" + urlencode(params)) if params else "",
    }


def _default_warehouse_name(site) -> str:
    """The store's default pickup warehouse name, to prefill the Delhivery pickup
    modal. Delhivery matches this against a registered Pickup Address nickname."""
    from commerce.models import Warehouse

    wh = (
        Warehouse.objects.for_site(site).filter(is_default=True).first()
        or Warehouse.objects.for_site(site).first()
    )
    return wh.name if wh else ""


# ── Delhivery label + invoice PDF ────────────────────────────────────────────

def _label_orders_data(site, orders):
    """Assemble the per-order dicts the label generator expects."""
    from commerce.cart import line_sku
    from commerce.label_pdf import payment_info
    from commerce.models import PaymentGatewayConfig, Warehouse
    from commerce.shipping_shiprocket import _to_cm, _to_kg

    wh = (
        Warehouse.objects.for_site(site).filter(is_default=True).first()
        or Warehouse.objects.for_site(site).first()
    )
    payment_config = PaymentGatewayConfig.objects.for_site(site).first()

    data = []
    for order in orders:
        sh = (
            Shipment.objects.for_site(site)
            .filter(order=order)
            .order_by("-created_at")
            .first()
        )
        items = list(order.items.for_site(site).select_related("product"))
        first = items[0] if items else None
        qty = sum(int(i.quantity or 1) for i in items) or 1
        # Package weight (kg, unit-aware: a 150 g product is 0.15 kg) summed
        # over quantities, and package L×B×H (cm) as the largest of the lines,
        # the same aggregation the Shiprocket shipment uses.
        weight = 0.0
        length = breadth = height = 0.0
        skus = []
        for i in items:
            p = i.product
            line_qty = max(1, int(i.quantity or 1))
            if p is not None:
                # A variation with its own dimensions / weight ships with those.
                from commerce.variant_profiles import line_measures

                p = line_measures(i) or p
                if p.package_weight_value is not None:
                    w = _to_kg(p.package_weight_value, p.package_weight_unit)
                else:
                    w = _to_kg(p.weight_value, p.weight_unit)
                weight += max(0.0, w) * line_qty
                if None not in (p.package_length, p.package_breadth, p.package_height):
                    dims = (p.package_length, p.package_breadth, p.package_height, p.package_unit)
                else:
                    dims = (p.product_length, p.product_breadth, p.product_height, p.product_unit)
                length = max(length, _to_cm(dims[0], dims[3]))
                breadth = max(breadth, _to_cm(dims[1], dims[3]))
                height = max(height, _to_cm(dims[2], dims[3]))
            skus.append({"sku": line_sku(i), "qty": line_qty})
        # Partial orders (part online, balance on delivery) are COD for the
        # courier: there is money to collect.
        cod = payment_info(order)["is_cod"]
        delhivery_data = {
            "wbn": (sh.awb if sh else "") or "",
            "oid": order.display_number,
            "name": order.customer_name,
            "address": order.shipping_address,
            "pin": order.shipping_pincode,
            "st": order.shipping_state,
            "destination_city": order.shipping_city,
            "pt": "COD" if cod else "Pre-paid",
            "qty": str(qty),
            "prd": (first.title if first else "Item"),
            "weight": round(weight, 3) if weight > 0 else 0.5,
            "hsn_code": (getattr(first.product, "hsn_code", "") if first and first.product else ""),
        }
        data.append(
            {
                "order": order,
                "shipment": sh,
                "items": items,
                "skus": skus,
                # Printed beside the AWB barcode; 0 / None render as "-".
                "package": {
                    "weight_kg": round(weight, 3),
                    "dims_cm": (length, breadth, height) if (length > 0 and breadth > 0 and height > 0) else None,
                },
                "courier_name": (sh.courier_name if sh else "") or "",
                "delhivery_data": delhivery_data,
                "website": site,
                "warehouse": wh,
                "payment_config": payment_config,
            }
        )
    return data


def _render_labels(site, orders, label_format):
    """Generate the combined label+invoice PDF and return it as a download."""
    from commerce.label_pdf import generate_label_invoice_pdf

    data = _label_orders_data(site, orders)
    try:
        pdf = generate_label_invoice_pdf(data, label_format=label_format)
    except Exception as exc:  # pragma: no cover - defensive
        import logging

        logging.getLogger("onewooz.audit").warning(
            "label generation failed site=%s: %s", getattr(site, "id", "?"), exc
        )
        return JsonResponse(
            {"success": False, "error": "Couldn't generate labels. Please try again."},
            status=500,
        )
    resp = HttpResponse(pdf, content_type="application/pdf")
    fname = f"labels-{label_format}-{timezone.now().strftime('%Y%m%d-%H%M%S')}.pdf"
    resp["Content-Disposition"] = f'attachment; filename="{fname}"'
    return resp


_LABEL_FORMATS = ("a4", "4x6", "courier")


def _clean_label_format(raw) -> str:
    value = (raw or "4x6").strip().lower()
    return value if value in _LABEL_FORMATS else "4x6"


def _courier_label_response(site, orders):
    """Shiprocket's own courier label PDF for ``orders``.

    Returns ``(response, "")`` on success, else ``(None, reason)``. The PDF is
    fetched server-side so the browser gets one download, like our own labels.
    """
    from commerce import shipping_shiprocket as sr

    provider = shipping_svc.enabled_provider(site)
    if not provider or (provider.provider or "").lower() != "shiprocket" or not sr.is_configured(provider):
        return None, "The courier label is available only with Shiprocket connected."
    shipment_ids = []
    for sh in Shipment.objects.for_site(site).filter(order__in=orders, provider__provider="shiprocket"):
        sid = str((sh.meta or {}).get("shipment_id") or "").strip()
        if sid and (sh.awb or "").strip() and sid not in shipment_ids:
            shipment_ids.append(sid)
    if not shipment_ids:
        return None, "These orders have no courier assigned yet, so Shiprocket can't print their label."
    try:
        url = sr.generate_label(provider, shipment_ids)
        pdf = sr.download_label(url)
    except sr.ShiprocketError as exc:
        return None, str(exc)
    resp = HttpResponse(pdf, content_type="application/pdf")
    fname = f"courier-labels-{timezone.now().strftime('%Y%m%d-%H%M%S')}.pdf"
    resp["Content-Disposition"] = f'attachment; filename="{fname}"'
    return resp, ""


def _generate_and_pickup(request, site):
    """Ready to Ship → one click: request the courier pickup, then generate the
    labels for every order whose pickup was scheduled.

    Answers an AJAX call: on success the label PDF streams back with an
    ``X-Redirect`` header naming the tab the orders moved to (the JS downloads
    the file, then follows it); a failed pickup returns a JSON error and leaves
    every order in Ready to Ship. If the pickup succeeds but the labels can't be
    built, the pickup still stands and the JSON response says so; labels can be
    reprinted from the next tab.
    """
    ids = _selected_uuids(request)
    orders = list(Order.objects.for_site(site).filter(pk__in=ids)) if ids else []
    if not orders:
        return JsonResponse(
            {"success": False, "error": "Select the orders to ship, then click Generate & Request Pickup."},
            status=400,
        )
    label_format = _clean_label_format(request.POST.get("label_format"))
    key = shipping_svc.selected_provider_key(site)
    failures: list[str] = []
    try:
        if key == "shiprocket":
            moved = shipping_svc.request_shiprocket_pickup(
                site,
                request.user,
                orders,
                pickup_date=(request.POST.get("pickup_date") or "").strip(),
                courier_id=(request.POST.get("courier_id") or "").strip(),
                courier_map=_courier_choices(request, orders),
                failures=failures,
            )
            next_tab, courier_label = "pickup_scheduled", "Shiprocket"
        elif key == "delhivery":
            try:
                expected = int(request.POST.get("expected_package_count") or len(orders))
            except (TypeError, ValueError):
                expected = len(orders)
            moved = shipping_svc.request_delhivery_pickup(
                site,
                request.user,
                orders,
                pickup_date=(request.POST.get("pickup_date") or "").strip(),
                pickup_time=(request.POST.get("pickup_time") or "").strip(),
                pickup_location=(request.POST.get("pickup_location") or "").strip(),
                expected_package_count=max(1, expected),
            )
            next_tab, courier_label = "waiting_pickup", "Delhivery"
        else:
            return JsonResponse(
                {
                    "success": False,
                    "error": "Connect Shiprocket or Delhivery in Shipping settings to request a pickup.",
                },
                status=400,
            )
    except shipping_svc.PickupError as exc:
        # Logged so a refused pickup is visible in the server logs too, not
        # only in the owner's popup.
        import logging

        logging.getLogger("onewooz.audit").warning(
            "generate+pickup refused site=%s orders=%s: %s",
            site.pk, [o.number for o in orders], exc,
        )
        return JsonResponse({"success": False, "error": str(exc)}, status=400)
    except Exception:
        import logging

        logging.getLogger(__name__).exception("generate+pickup failed site=%s", site.pk)
        return JsonResponse(
            {"success": False, "error": "The courier couldn't be reached. Please try again in a minute."},
            status=502,
        )

    activity.record(
        "store.shipping.pickup_requested", request=request, site=site,
        target=site, target_kind="Site", label=f"{courier_label} pickup + labels",
    )

    picked_shipments = list(
        Shipment.objects.for_site(site)
        .filter(order__in=orders, status=Shipment.Status.PICKUP_REQUESTED)
        .order_by("-created_at")
    )
    picked_ids = {sh.order_id for sh in picked_shipments}
    picked = [o for o in orders if o.pk in picked_ids]
    redirect_url = f"{_orders_url(site)}?tab={next_tab}"

    when = _pickup_dates_text(picked_shipments)
    queued = sorted(
        {sh.order.number for sh in picked_shipments if (sh.meta or {}).get("pickup_already_queued")}
    )
    if moved < len(orders):
        reasons = " ".join(failures[:5]) + (f" (+{len(failures) - 5} more)" if len(failures) > 5 else "")
        messages.warning(
            request,
            f"{courier_label} pickup requested{when} for {moved} of {len(orders)} order(s). "
            "The rest stay in Ready to Ship."
            + (f" Not booked: {reasons}" if reasons else " Open them to check the address/courier and try again."),
        )
    else:
        messages.success(
            request, f"{courier_label} pickup requested{when} for {moved} order(s) and labels generated."
        )
    if queued:
        nums = ", ".join(f"#{n}" for n in queued)
        messages.info(
            request,
            f"Order {nums} was already in {courier_label}'s pickup queue, so its pickup is confirmed"
            f"{when} and its label is included.",
        )

    if not picked:
        return JsonResponse({"success": True, "redirect": redirect_url, "labels": False})

    if label_format == "courier":
        response, error = _courier_label_response(site, picked)
        if response is None:
            # Fall back to our own thermal label so the parcel can still ship.
            messages.warning(request, f"Courier label unavailable ({error}). A 4×6 label was generated instead.")
            response = _render_labels(site, picked, "4x6")
    else:
        response = _render_labels(site, picked, label_format)

    if not isinstance(response, HttpResponse) or response.get("Content-Type", "").find("application/pdf") < 0:
        messages.warning(
            request,
            "The pickup is scheduled, but labels couldn't be generated. Reprint them from the next tab.",
        )
        return JsonResponse({"success": True, "redirect": redirect_url, "labels": False})
    response["X-Redirect"] = redirect_url
    return response


def _pickup_dates_text(shipments) -> str:
    """" for 07 Oct 2026" (or " for 07 Oct 2026 / 08 Oct 2026") from the pickup
    dates recorded on the shipments, or "" when none was recorded."""
    days = []
    for sh in shipments:
        d = _parse_iso_date(str((sh.meta or {}).get("pickup_date") or "")[:10])
        if d and d not in days:
            days.append(d)
    if not days:
        return ""
    return " for " + " / ".join(d.strftime("%d %b %Y") for d in sorted(days))


def _tab_queryset(site, tab):
    """The order queryset for one tab, mirroring the reference's filters."""
    base = Order.objects.for_site(site)
    statuses = _TAB_TO_STATUSES.get(tab, [])
    if statuses:
        base = base.filter(status__in=statuses)

    if tab == "pending":
        # Every created/paid order, including one that was accepted and then
        # moved back to Pending (its courier booking is kept and reused when it
        # is accepted again). Only a parcel already with the courier is left
        # out; those belong to Shipped/Delivered/RTO.
        base = base.exclude(shipments__status__in=_WITH_COURIER).distinct()
    elif tab == "ready_to_pack":
        base = base.exclude(
            shipments__status=Shipment.Status.PICKUP_REQUESTED
        ).distinct()
    elif tab == "waiting_pickup":
        base = base.filter(shipments__status=Shipment.Status.PICKUP_REQUESTED).distinct()
    elif tab == "pickup_scheduled":
        # Shiprocket orders whose pickup has been requested (an AWB is assigned
        # and the rider is scheduled), but that haven't yet entered the network.
        base = base.filter(
            shipments__provider__provider="shiprocket",
            shipments__status=Shipment.Status.PICKUP_REQUESTED,
        ).distinct()
    elif tab == "shipped":
        base = (
            Order.objects.for_site(site)
            .filter(
                Q(status=Order.Status.SHIPPED)
                | Q(status=Order.Status.CONFIRMED, shipments__status__in=_IN_TRANSIT)
            )
            .distinct()
        )
    elif tab == "rto":
        base = Order.objects.for_site(site).filter(_rto_q()).distinct()
    if tab in _RTO_EXCLUSIVE_TABS:
        base = _without_rto(site, base)
    return base


def _apply_search(qs, base_qs, q):
    q_obj = (
        Q(items__product__sku__icontains=q)
        | Q(items__title__icontains=q)
        | Q(shipping_state__icontains=q)
        | Q(shipping_pincode__icontains=q)
        | Q(customer_name__icontains=q)
        | Q(customer_email__icontains=q)
        | Q(customer_phone__icontains=q)
        | Q(tracking_id__icontains=q)
    )
    if re.fullmatch(r"\d+", q):
        try:
            q_obj |= Q(number=int(q)) | Q(items__quantity=int(q))
        except ValueError:
            pass
    try:
        q_obj |= Q(total=Decimal(q))
    except (InvalidOperation, ValueError):
        pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            parsed = datetime.strptime(q, fmt).date()
            q_obj |= Q(created_at__date=parsed)
            break
        except ValueError:
            continue
    return base_qs.filter(q_obj).distinct()


def _pending_count(orders_qs) -> int:
    return (
        orders_qs.filter(status__in=_TAB_TO_STATUSES["pending"])
        .exclude(shipments__status__in=_WITH_COURIER)
        .distinct()
        .count()
    )


def _tab_counts(site, date_q=None):
    """Live count per status pill, narrowed to the chosen date window."""
    date_q = date_q if date_q is not None else Q()
    orders = lambda: Order.objects.for_site(site).filter(date_q)  # noqa: E731
    return {
        "pending": _pending_count(orders()),
        "ready_to_pack": orders()
        .filter(status__in=_TAB_TO_STATUSES["ready_to_pack"])
        .exclude(shipments__status=Shipment.Status.PICKUP_REQUESTED)
        .distinct()
        .count(),
        "waiting_pickup": orders()
        .filter(
            status__in=_TAB_TO_STATUSES["waiting_pickup"],
            shipments__status=Shipment.Status.PICKUP_REQUESTED,
        )
        .distinct()
        .count(),
        "pickup_scheduled": orders()
        .filter(
            status__in=_TAB_TO_STATUSES["pickup_scheduled"],
            shipments__provider__provider="shiprocket",
            shipments__status=Shipment.Status.PICKUP_REQUESTED,
        )
        .distinct()
        .count(),
        "shipped": _without_rto(
            site,
            orders().filter(
                Q(status=Order.Status.SHIPPED)
                | Q(status=Order.Status.CONFIRMED, shipments__status__in=_IN_TRANSIT)
            ),
        )
        .distinct()
        .count(),
        "delivered": _without_rto(
            site, orders().filter(status__in=_TAB_TO_STATUSES["delivered"])
        ).count(),
        "cancelled": _without_rto(
            site, orders().filter(status__in=_TAB_TO_STATUSES["cancelled"])
        ).count(),
        "rto": orders().filter(_rto_q()).distinct().count(),
    }


# ── POST actions ─────────────────────────────────────────────────────────────

def _handle_order_action(request, site):
    action = (request.POST.get("action") or "").lower()
    oid = request.POST.get("order_id")

    if action == "accept_all":
        qs = Order.objects.for_site(site).filter(
            status__in=[Order.Status.CREATED, Order.Status.PAID]
        )
        updated, failure = _accept_many(site, qs)
        _message_bulk_accept(request, updated, failure, empty="No pending orders to accept.")
        return redirect("store:orders", site_id=site.id)

    if action == "accept_selected":
        ids = _selected_uuids(request)
        qs = Order.objects.for_site(site).filter(
            pk__in=ids, status__in=[Order.Status.CREATED, Order.Status.PAID]
        )
        updated, failure = _accept_many(site, qs)
        _message_bulk_accept(request, updated, failure, empty="No eligible orders in selection.")
        return redirect("store:orders", site_id=site.id)

    if action == "cancel_selected":
        ids = _selected_uuids(request)
        # A delivered order can't be cancelled (only replaced, from its page).
        qs = Order.objects.for_site(site).filter(pk__in=ids).exclude(
            status__in=[Order.Status.CANCELLED, Order.Status.DELIVERED]
        )
        updated = 0
        for order in qs:
            _cancel_order(request, site, order)
            updated += 1
        messages.success(
            request,
            f"Cancelled {updated} order(s)." if updated else "No eligible orders to cancel.",
        )
        return redirect("store:orders", site_id=site.id)

    if action in ("delhivery_generate_labels", "delhivery_label"):
        # Build the label + invoice PDF for the selected order(s) and stream it
        # back for download. The per-row icon posts a single order_id; the bulk
        # button posts selected_orders. AJAX (fetch → blob), so return the PDF
        # bytes / a JSON error rather than redirecting.
        label_format = _clean_label_format(request.POST.get("label_format"))
        ids = _selected_uuids(request) or ([oid] if oid else [])
        orders = list(Order.objects.for_site(site).filter(pk__in=ids)) if ids else []
        if not orders:
            return JsonResponse(
                {"success": False, "error": "Select at least one order to generate labels."},
                status=400,
            )
        if label_format == "courier":
            response, error = _courier_label_response(site, orders)
            if response is not None:
                return response
            return JsonResponse({"success": False, "error": error}, status=400)
        return _render_labels(site, orders, label_format)

    if action == "generate_and_pickup":
        return _generate_and_pickup(request, site)

    if action == "delhivery_ship_now":
        messages.info(
            request,
            "Ship-now needs a live Delhivery connection  connect Delhivery in "
            "Shipping settings to enable it.",
        )
        return redirect(f"{_orders_url(site)}?tab={request.POST.get('tab') or 'ready_to_pack'}")

    if action == "delhivery_pickup":
        ids = _selected_uuids(request)
        orders = list(Order.objects.for_site(site).filter(pk__in=ids)) if ids else []
        if not orders:
            messages.error(
                request, "Select the orders you're handing to Delhivery, then request the pickup."
            )
            return redirect(f"{_orders_url(site)}?tab=ready_to_pack")
        try:
            expected = int(request.POST.get("expected_package_count") or len(orders))
        except (TypeError, ValueError):
            expected = len(orders)
        try:
            moved = shipping_svc.request_delhivery_pickup(
                site,
                request.user,
                orders,
                pickup_date=(request.POST.get("pickup_date") or "").strip(),
                pickup_time=(request.POST.get("pickup_time") or "").strip(),
                pickup_location=(request.POST.get("pickup_location") or "").strip(),
                expected_package_count=expected,
            )
        except shipping_svc.PickupError as exc:
            messages.error(request, str(exc))
            return redirect(f"{_orders_url(site)}?tab=ready_to_pack")
        messages.success(
            request,
            f"Delhivery pickup requested for {moved} order(s)." if moved else "Delhivery pickup requested.",
        )
        activity.record(
            "store.shipping.pickup_requested", request=request, site=site,
            target=site, target_kind="Site", label="Delhivery pickup",
        )
        return redirect(f"{_orders_url(site)}?tab=waiting_pickup")

    if action == "shiprocket_pickup":
        ids = _selected_uuids(request)
        orders = list(Order.objects.for_site(site).filter(pk__in=ids)) if ids else []
        if not orders:
            messages.error(
                request, "Select the orders you want Shiprocket to pick up, then request the pickup."
            )
            return redirect(f"{_orders_url(site)}?tab=ready_to_pack")
        failures: list[str] = []
        try:
            moved = shipping_svc.request_shiprocket_pickup(
                site,
                request.user,
                orders,
                pickup_date=(request.POST.get("pickup_date") or "").strip(),
                courier_id=(request.POST.get("courier_id") or "").strip(),
                courier_map=_courier_choices(request, orders),
                failures=failures,
            )
        except shipping_svc.PickupError as exc:
            messages.error(request, str(exc))
            return redirect(f"{_orders_url(site)}?tab=ready_to_pack")
        messages.success(
            request,
            f"Shiprocket pickup requested for {moved} order(s)." if moved else "Shiprocket pickup requested.",
        )
        if failures:
            messages.warning(request, "Not booked, still in Ready to Ship: " + " ".join(failures[:5]))
        activity.record(
            "store.shipping.pickup_requested", request=request, site=site,
            target=site, target_kind="Site", label="Shiprocket pickup",
        )
        return redirect(f"{_orders_url(site)}?tab=pickup_scheduled")

    if oid and action in ("accept", "reject", "cancel"):
        order = Order.objects.for_site(site).filter(pk=oid).first()
        if order:
            if action == "accept" and order.status in (Order.Status.CREATED, Order.Status.PAID):
                error = _accept_order(site, order)
                if error:
                    messages.error(
                        request,
                        f"Couldn't accept order #{_order_display_num(order)}: {error}",
                    )
                else:
                    _send_status_message(request, order, "accepted")
                    activity.record(
                        "store.order.accepted", request=request, site=site,
                        target=order, target_kind="Order", label=f"#{order.number}",
                    )
            elif action == "reject" and order.status in (Order.Status.CREATED, Order.Status.PAID):
                _cancel_order(request, site, order)
                _send_status_message(request, order, "rejected")
                activity.record(
                    "store.order.cancelled", request=request, site=site,
                    target=order, target_kind="Order", label=f"#{order.number}",
                )
            elif action == "cancel" and order.status not in (Order.Status.CANCELLED, Order.Status.DELIVERED):
                _cancel_order(request, site, order)
                _send_status_message(request, order, "cancelled")
                activity.record(
                    "store.order.cancelled", request=request, site=site,
                    target=order, target_kind="Order", label=f"#{order.number}",
                )
        return redirect("store:orders", site_id=site.id)

    messages.error(request, "Unknown action.")
    return redirect("store:orders", site_id=site.id)


def _cancel_order(request, site, order) -> None:
    """Cancel ``order`` (it moves to the Cancelled tab) and cancel it with the
    courier as well: before pickup that drops the Shiprocket order and its
    pickup; after pickup the parcel returns and the tracking webhook moves it
    to RTO once it is back. A courier refusal is shown as a warning."""
    order.status = Order.Status.CANCELLED
    order.save(update_fields=["status", "cancelled_at"])
    warning = shipping_svc.cancel_with_courier(site, order)
    if warning:
        messages.warning(request, warning)
    # Back on the shelf if it never left; a shipped parcel restocks on RTO.
    from commerce import order_notify, stock

    stock.release_if_not_dispatched(order)
    # Any store credit the order used goes back to the shopper.
    from commerce import credits

    credits.restore_for_order(order)
    order_notify.queue(order, "cancelled")


def _accept_order(site, order) -> str:
    """Book the order's shipment, then confirm it (moving it to the courier tab).

    Booking happens first: if the courier rejects it  Shiprocket not connected,
    a pickup location that doesn't match the dashboard, an API error  the order
    is left in Pending and the reason is returned, so the owner can fix it and
    retry rather than being stranded as "confirmed with no shipment". A
    non-Shiprocket stub never blocks acceptance. Returns "" on success.
    """
    error = _book_shipment(site, order)
    if error:
        return error
    order.status = Order.Status.CONFIRMED
    order.save(update_fields=["status", "confirmed_at"])
    return ""


def _book_shipment(site, order) -> str:
    """Book (or reuse) the order's courier shipment. Returns "" or the reason
    the courier refused, which the caller shows the owner."""
    from commerce.shipping_shiprocket import ShiprocketError

    try:
        shipping_svc.ensure_shipment(site, order)
    except ShiprocketError as exc:
        return str(exc)
    except Exception:  # a local stub courier must never block acceptance
        pass
    return ""


def _set_owner_status(request, site, order, next_status) -> bool:
    """Apply a status the owner picked by hand (order edit page / status menu).

    Keeps the Orders tabs consistent with the courier booking:

    * **→ Pending** from an accepted order: allowed while the parcel is still
      at the warehouse; its courier booking is kept and reused when the order
      is accepted again. A paid order goes back to "paid" (it still sits in
      Pending) so the payment isn't forgotten. Refused once the courier has
      the parcel.
    * **Pending → Ready to Ship**: books the courier shipment first, exactly
      like Accept, so the order can actually be picked up; a courier refusal
      leaves it in Pending with the reason.

    Sets ``order.status`` (the caller saves) and returns whether it changed.
    """
    current = order.status
    pending = (Order.Status.CREATED, Order.Status.PAID)
    if next_status == current or (next_status == Order.Status.CREATED and current in pending):
        return False

    if current == Order.Status.DELIVERED:
        # Delivered is final. The only way forward is a replacement, created
        # from the order page ("Create replacement"), which is a new order.
        messages.error(
            request,
            f"Order #{_order_display_num(order)} is delivered, so its status can't be changed. "
            "Choose “Create replacement” to send the items again.",
        )
        return False

    if next_status == Order.Status.CREATED:
        with_courier = (
            Shipment.objects.for_site(site)
            .filter(order=order, status__in=_WITH_COURIER)
            .exists()
        )
        if with_courier:
            messages.error(
                request,
                f"Order #{_order_display_num(order)} is already with the courier, so it can't go back to Pending.",
            )
            return False
        paid = (
            Payment.objects.for_site(site)
            .filter(order=order, status=Payment.Status.PAID)
            .exists()
        )
        order.status = Order.Status.PAID if paid else Order.Status.CREATED
        if Shipment.objects.for_site(site).filter(order=order, status=Shipment.Status.PICKUP_REQUESTED).exists():
            messages.warning(
                request,
                f"Order #{_order_display_num(order)} is back in Pending. A courier pickup was already "
                "scheduled for it; cancel that pickup in your courier dashboard if the rider shouldn't come.",
            )
        return True

    if next_status == Order.Status.CANCELLED:
        # Picked by hand on the order page: cancel with the courier too, the
        # same as the Orders page's Cancel action.
        order.status = next_status
        warning = shipping_svc.cancel_with_courier(site, order)
        if warning:
            messages.warning(request, warning)
        from commerce import stock

        stock.release_if_not_dispatched(order)
        # Any store credit the order used goes back to the shopper.
        from commerce import credits

        credits.restore_for_order(order)
        # Emailed by the caller once the cancellation is saved.
        order._pending_customer_mail = "cancelled"
        return True

    if next_status == Order.Status.CONFIRMED and current in pending:
        error = _book_shipment(site, order)
        if error:
            messages.error(
                request,
                f"Order #{_order_display_num(order)} stays in Pending: the courier refused the booking. {error}",
            )
            return False

    order.status = next_status
    # Marked shipped / delivered by hand: the shopper is emailed, the same as
    # when the courier reports it (each at most once per order). The caller
    # sends it after saving, so it carries e.g. a tracking number typed in the
    # same edit.
    if next_status in (Order.Status.SHIPPED, Order.Status.DELIVERED) and current != next_status:
        order._pending_customer_mail = "shipped" if next_status == Order.Status.SHIPPED else "delivered"
    return True


def _send_pending_customer_mail(order) -> None:
    """Email the shopper about a status the owner just set and saved
    (cancelled / shipped / delivered), if :func:`_set_owner_status` set one."""
    kind = getattr(order, "_pending_customer_mail", "")
    if not kind:
        return
    order._pending_customer_mail = ""
    from commerce import order_notify

    order_notify.queue(order, kind)


def _accept_many(site, queryset) -> tuple[int, str]:
    """Accept every order in ``queryset``. Returns (accepted_count, last_error)."""
    accepted, last_error = 0, ""
    for order in queryset:
        error = _accept_order(site, order)
        if error:
            last_error = error
        else:
            accepted += 1
    return accepted, last_error


def _message_bulk_accept(request, accepted: int, last_error: str, *, empty: str) -> None:
    if accepted:
        messages.success(request, f"Accepted {accepted} order(s).")
    if last_error:
        # The courier refused at least one  surface why so it can be fixed.
        messages.error(request, f"Some orders couldn't be booked with the courier: {last_error}")
    elif not accepted:
        messages.info(request, empty)


def _selected_uuids(request):
    return [v for v in request.POST.getlist("selected_orders") if v]


def _courier_choices(request, orders) -> dict:
    """``{order id: courier id}`` from the pickup popup's per-order pickers
    (``courier_for_<order id>``). A blank choice means "let Shiprocket pick"."""
    out = {}
    for o in orders:
        raw = (request.POST.get(f"courier_for_{o.pk}") or "").strip()
        if raw.isdigit():
            out[str(o.pk)] = raw
    return out


def _orders_url(site) -> str:
    from django.urls import reverse

    return reverse("store:orders", args=[site.id])


# The Orders-page state an Edit link carries to the order page (``?back=``) so
# its Back / Cancel links and the post-save redirect return the owner to the
# same tab, date window, search and page they left.
_BACK_KEYS = ("tab", "range", "from", "to", "q", "page", "per")


def _clean_back(raw: str) -> str:
    """Re-encode only the known Orders-page params from ``raw`` (a query
    string), so ``?back=`` can never smuggle anything else into a URL."""
    from django.http import QueryDict

    qd = QueryDict(raw or "")
    params = {}
    for key in _BACK_KEYS:
        value = (qd.get(key) or "").strip()
        if value:
            params[key] = value[:100]
    return urlencode(params)


def _orders_back_url(site, back_qs: str) -> str:
    return _orders_url(site) + (f"?{back_qs}" if back_qs else "")


def _order_edit_url(site, order, back_qs: str = "") -> str:
    from django.urls import reverse

    url = reverse("store:order_edit", args=[site.id, order.id])
    return f"{url}?{urlencode({'back': back_qs})}" if back_qs else url


# ── Auto-ship toggle ─────────────────────────────────────────────────────────

@login_required
@require_POST
def toggle_auto_ship(request, site_id):
    site = get_owned_site(request, site_id)
    raw = request.POST.get("enabled")
    enabled = str(raw).strip().lower() in ("1", "true", "yes", "on")
    shipping_svc.set_auto_ship(site, enabled)
    provider = shipping_svc.selected_provider_key(site)
    return JsonResponse({"success": True, "enabled": enabled, "provider": provider})


# ── Shiprocket courier serviceability (feeds the pickup popup's rate picker) ──

@login_required
def shiprocket_rate_quotes(request, site_id):
    """Live Shiprocket couriers + rates for EVERY selected order (``?order=<id>``
    repeated), for the bulk pickup popup.

    Each order is priced on its own route, weight and payment type, because a
    courier and price quoted for one destination says nothing about another
    (Delhi → Delhi vs Delhi → West Bengal, prepaid vs COD).
    """
    site = get_owned_site(request, site_id)
    ids = [v for v in request.GET.getlist("order") if v][: shipping_svc.MAX_RATE_QUOTES]
    valid = []
    for v in ids:
        try:
            valid.append(str(uuid.UUID(v)))
        except ValueError:
            continue
    orders = list(Order.objects.for_site(site).filter(pk__in=valid).order_by()) if valid else []
    if not orders:
        return JsonResponse({"success": False, "error": "Select the orders to ship first."}, status=400)
    # Keep the owner's row order.
    pos = {v: i for i, v in enumerate(valid)}
    orders.sort(key=lambda o: pos.get(str(o.pk), 0))
    try:
        data = shipping_svc.shiprocket_rate_quotes(site, orders)
    except shipping_svc.PickupError as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=400)
    return JsonResponse({"success": True, "currency": site.currency, **data})


@login_required
def shiprocket_serviceability(request, site_id, order_id):
    """Live Shiprocket couriers + rates for one order (kept for old links;
    the pickup popup uses :func:`shiprocket_rate_quotes`)."""
    site = get_owned_site(request, site_id)
    order = get_object_or_404(Order.objects.for_site(site), pk=order_id)
    try:
        data = shipping_svc.shiprocket_rate_quotes(site, [order])
    except shipping_svc.PickupError as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=400)
    row = (data.get("orders") or [{}])[0]
    if row.get("error") and not row.get("couriers"):
        return JsonResponse({"success": False, "error": row["error"], "couriers": []}, status=400)
    return JsonResponse({"success": True, "couriers": row.get("couriers") or [], "currency": site.currency})


# ── Courier tracking JSON (feeds the popup) ──────────────────────────────────

_OPEN_SHIPMENT_STATUSES = {
    Shipment.Status.CREATED,
    Shipment.Status.BOOKED,
    Shipment.Status.PICKUP_REQUESTED,
    Shipment.Status.PICKED_UP,
    Shipment.Status.IN_TRANSIT,
    Shipment.Status.OUT_FOR_DELIVERY,
    Shipment.Status.DELIVERY_FAILED,
}


def _latest_shipment(site, order):
    return (
        Shipment.objects.for_site(site)
        .select_related("provider", "order")
        .filter(order=order)
        .order_by("-created_at")
        .first()
    )


@login_required
def order_tracking_json(request, site_id, order_id):
    """Tracking popup data: the order's latest courier event (pushed by the
    webhook or pulled by a sync). For an open Shiprocket shipment whose last
    update is missing or older than 15 minutes, a live pull from Shiprocket
    runs first, so the popup shows the courier's current journey."""
    from core import ratelimit

    from commerce import courier_webhook

    site = get_owned_site(request, site_id)
    order = get_object_or_404(Order.objects.for_site(site), pk=order_id)
    shipment = _latest_shipment(site, order)
    evt = shipping_svc.latest_event_for_order(site, order)

    stale = evt is None or (
        evt.received_at and timezone.now() - evt.received_at > timezone.timedelta(minutes=15)
    )
    if (
        stale
        and shipment is not None
        and shipment.provider_key == "shiprocket"
        and shipment.status in _OPEN_SHIPMENT_STATUSES
        and ((shipment.awb or "").strip() or (shipment.meta or {}).get("shipment_id"))
        and ratelimit.consume("shipping.order_sync", str(site.pk)).allowed
    ):
        try:
            evt = courier_webhook.sync_shipment(site, shipment)
            order.refresh_from_db()
        except Exception:
            # Best effort; fall back to whatever was stored.
            evt = shipping_svc.latest_event_for_order(site, order)

    if not evt:
        return JsonResponse(
            {
                "success": False,
                "error": (
                    "No tracking data yet. It appears once the courier picks up the parcel "
                    "(or connect the tracking webhook in Shipping settings)."
                ),
            },
            status=404,
        )
    payload = evt.raw_payload if isinstance(evt.raw_payload, dict) else {}
    if shipment is not None:
        payload = {**payload}
        payload.setdefault("awb", shipment.awb)
        payload.setdefault("courier_name", shipment.courier_name)
        if not payload.get("track_url") and shipment.tracking_url:
            payload["track_url"] = shipment.tracking_url
    return JsonResponse(
        {
            "success": True,
            "provider": evt.provider,
            "received_at": timezone.localtime(evt.received_at).strftime("%d %b %Y, %I:%M %p")
            if evt.received_at
            else "",
            "shipment_status": shipment.get_status_display() if shipment else "",
            "order_status": order.get_status_display(),
            "payload": payload,
        }
    )


@login_required
@require_POST
def sync_shipment_status(request, site_id, order_id):
    """The per-order "Sync status" button: pull the live status from the
    courier (Shiprocket API) and apply it, moving the order between tabs."""
    from core import ratelimit

    from commerce import courier_webhook

    site = get_owned_site(request, site_id)
    order = get_object_or_404(Order.objects.for_site(site), pk=order_id)
    shipment = _latest_shipment(site, order)
    if shipment is None:
        return JsonResponse(
            {"success": False, "error": "This order has no courier shipment yet."},
            status=404,
        )
    if not ratelimit.consume("shipping.order_sync", str(site.pk)).allowed:
        return JsonResponse(
            {"success": False, "error": "Too many syncs in a short time. Please wait a few minutes."},
            status=429,
        )
    before_order, before_ship = order.status, shipment.status
    try:
        courier_webhook.sync_shipment(site, shipment)
    except courier_webhook.SyncError as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=400)
    except Exception:  # pragma: no cover - defensive
        import logging

        logging.getLogger(__name__).exception("order sync failed order=%s", order.pk)
        return JsonResponse({"success": False, "error": "Couldn't reach the courier. Try again shortly."}, status=502)
    order.refresh_from_db()
    shipment.refresh_from_db()
    changed = order.status != before_order or shipment.status != before_ship
    return JsonResponse(
        {
            "success": True,
            "changed": changed,
            "order_status": order.status,
            "shipment_status": shipment.status,
            "message": (
                f"Order #{order.display_number}: {shipment.get_status_display()}."
                if changed
                else f"Order #{order.display_number} is up to date ({shipment.get_status_display()})."
            ),
        }
    )


# ── Courier webhook receiver (public, token-authenticated) ───────────────────

def _handle_courier_push(request, site, provider: str, token: str = ""):
    """Shared body of every public courier webhook route."""
    import logging

    from core import ratelimit

    from commerce import courier_webhook

    if site is None:
        return JsonResponse({"status": "error", "message": "unknown store"}, status=404)
    if request.method != "POST":
        # A GET (someone opening the URL in a browser, or a courier dashboard's
        # reachability probe) just confirms the endpoint is live.
        return JsonResponse({"status": "ok", "message": "Tracking webhook is live. Couriers POST updates here."})
    if not ratelimit.consume("webhook.courier", ratelimit.client_identity(request)).allowed:
        return JsonResponse({"status": "rate_limited"}, status=429)
    headers = {k.lower(): v for k, v in request.headers.items()}
    if token:
        headers["_path_token"] = token
    try:
        result = courier_webhook.process(site, provider, request.body or b"{}", headers)
    except Exception:
        # Never 500 a courier; it would retry forever. Log and acknowledge.
        logging.getLogger(__name__).exception("courier webhook failed site=%s", site.pk)
        return JsonResponse({"status": "error"}, status=200)
    code = result.pop("code", 200)
    return JsonResponse(result, status=code)


@csrf_exempt
def tracking_webhook(request, site_key, token=""):
    """Public courier tracking webhook: ``/webhooks/tracking/<site>/[<token>/]``.

    No login: the courier authenticates with the store's webhook token, sent in
    the path or the ``x-api-key`` header (see
    :func:`commerce.courier_webhook.verify`). The path is free of the words
    Shiprocket's dashboard rejects, and ``?provider=`` selects a non-Shiprocket
    courier.
    """
    from commerce import courier_webhook

    provider = (request.GET.get("provider") or "shiprocket").strip().lower()[:20]
    return _handle_courier_push(request, courier_webhook.resolve_site(site_key), provider, token)


@csrf_exempt
def courier_webhook(request, site_id, provider, token=""):
    """Older public route (``store/<site>/webhooks/courier/<provider>/<token>/``),
    kept working for URLs already pasted into a courier dashboard."""
    site = Site.objects.filter(pk=site_id, is_deleted=False).first()
    return _handle_courier_push(request, site, (provider or "shiprocket").lower()[:20], token)


# ── Order edit ───────────────────────────────────────────────────────────────

_STATUS_UI_TO_ORDER = {
    "pending": Order.Status.CREATED,
    "ready_to_pack": Order.Status.CONFIRMED,
    "shipped": Order.Status.SHIPPED,
    "delivered": Order.Status.DELIVERED,
    "cancel": Order.Status.CANCELLED,
    "archive": Order.Status.ARCHIVED,
}


@login_required
def order_edit(request, site_id, order_id):
    site = get_owned_site(request, site_id)
    order = get_object_or_404(
        Order.objects.for_site(site).prefetch_related("items__product__images"),
        pk=order_id,
    )

    back_qs = _clean_back(request.POST.get("back") or request.GET.get("back") or "")
    if request.method == "POST":
        return _save_order_edit(request, site, order, back_qs)

    from commerce import order_requests

    shipments = order.shipments.for_site(site)
    returned = shipments.filter(status=Shipment.Status.RTO).exists()
    context = store_context(request, site, "orders")
    context.update(
        {
            "order": order,
            "items": order.items.for_site(site).select_related("product"),
            "shipments": shipments,
            "currency": site.currency,
            "product_choices": order_edit_svc.product_choices(site),
            "back_qs": back_qs,
            "back_url": _orders_back_url(site, back_qs),
            # Delivered is final: the status can only become a replacement.
            "status_locked": order.status == Order.Status.DELIVERED,
            "can_create_replacement": order.status == Order.Status.DELIVERED and not returned,
            "pending_replacement": (
                order_requests.pending_replacement(order)
                if order.status == Order.Status.DELIVERED
                else None
            ),
        }
    )
    return render(request, "store/order_edit.html", context)


# Customer / delivery fields that Shiprocket's address/update carries.
_ADDRESS_FIELDS = (
    "customer_name", "customer_phone", "customer_email",
    "shipping_address", "shipping_city", "shipping_state", "shipping_pincode",
)


def _save_order_edit(request, site, order, back_qs: str = ""):
    """Apply the order edit form so the whole order stays consistent.

    Lines (quantity / price / remove / add) are applied with stock re-taken
    under locks, the totals are re-derived from them, the status change runs,
    and the result is pushed to the order's Shiprocket order (items before a
    courier is assigned, address until the parcel is picked up). Anything
    Shiprocket can't take is reported, not silently dropped.
    """
    num = _order_display_num(order)
    edit_url = _order_edit_url(site, order, back_qs)
    status_ui = (request.POST.get("status_ui") or "").strip().lower()
    want_replacement = status_ui == "create_replacement"
    if want_replacement:
        # Checked before anything is saved, so a refusal changes nothing.
        from commerce import order_requests

        if order.status != Order.Status.DELIVERED:
            messages.error(request, f"Order #{num} isn't delivered, so a replacement can't be created for it.")
            return redirect(edit_url)
        if Shipment.objects.for_site(site).filter(order=order, status=Shipment.Status.RTO).exists():
            messages.error(request, f"Order #{num} was returned to you (RTO), so it can't be replaced.")
            return redirect(edit_url)
        if order_requests.pending_replacement(order) is not None:
            messages.error(
                request,
                f"A replacement for order #{num} is already waiting in Orders \u2192 Pending. Accept it there.",
            )
            return redirect(edit_url)
    elif order.status == Order.Status.DELIVERED and status_ui and status_ui != "delivered":
        messages.error(
            request,
            f"Order #{num} is delivered, so its status can't be changed. "
            "Choose “Create replacement” to send the items again.",
        )
        return redirect(edit_url)
    try:
        plan = order_edit_svc.build_plan(site, order, request.POST)
    except order_edit_svc.OrderEditError as exc:
        messages.error(request, str(exc))
        return redirect(edit_url)

    before = {f: (getattr(order, f) or "").strip() for f in _ADDRESS_FIELDS}
    # What the shopper can see, before the edit (their "order updated" email
    # lists exactly what changed).
    from commerce import order_notify

    shown_before = order_notify.snapshot(site, order)
    status_before = order.status

    order.customer_name = (request.POST.get("customer_name") or order.customer_name).strip()
    order.customer_email = (request.POST.get("customer_email") or "").strip()
    order.customer_phone = (request.POST.get("customer_phone") or order.customer_phone).strip()
    order.shipping_address = (request.POST.get("shipping_address") or order.shipping_address).strip()
    order.shipping_state = (request.POST.get("shipping_state") or "").strip()
    order.shipping_city = (request.POST.get("shipping_city") or "").strip()
    order.shipping_pincode = (request.POST.get("shipping_pincode") or "").strip()
    order.gstin = (request.POST.get("gstin") or "").strip()
    order.company_name = (request.POST.get("company_name") or "").strip()
    billing = (request.POST.get("billing_address") or "").strip()
    order.billing_address = billing or (order.shipping_address or "")
    # Tracking / AWB and payment method are read-only on this page: the
    # courier booking and the checkout / payment gateway own them.
    address_changed = any((getattr(order, f) or "").strip() != before[f] for f in _ADDRESS_FIELDS)

    old_total = order.total
    items_changed = plan.changed
    try:
        order_edit_svc.apply_plan(site, order, plan)
    except order_edit_svc.OrderEditError as exc:
        messages.error(request, f"Order #{num} wasn't changed: {exc}")
        return redirect(edit_url)

    # Totals always follow the lines; a typed total only counts when the owner
    # actually changed the box (it is pre-filled with the current total).
    typed_total = (request.POST.get("total_amount") or "").strip()
    original_total = (request.POST.get("total_amount_original") or "").strip()
    override = typed_total if typed_total and typed_total != original_total else ""
    order_edit_svc.recompute_totals(site, order, override_total=override)
    amounts_changed = order.total != old_total

    next_status = _STATUS_UI_TO_ORDER.get(status_ui)
    if next_status:
        _set_owner_status(request, site, order, next_status)

    order.save()
    _send_pending_customer_mail(order)

    if amounts_changed and not order.is_partial_payment:
        # A prepaid order's customer paid the old amount online; a higher total
        # isn't collected by anyone unless the owner does it.
        from django.db.models import Sum

        paid = (
            Payment.objects.for_site(site)
            .filter(order=order, status=Payment.Status.PAID)
            .aggregate(s=Sum("amount"))["s"]
        )
        if paid is not None and order.total > paid:
            messages.warning(
                request,
                f"Order #{num} was paid online ({site.currency} {paid}); the new total is "
                f"{site.currency} {order.total}. Collect the {site.currency} {order.total - paid} "
                "difference from the customer separately.",
            )

    for warning in shipping_svc.sync_order_edit(
        site, order,
        items_changed=items_changed or amounts_changed,
        address_changed=address_changed,
    ):
        messages.warning(request, warning)

    # Tell the shopper what changed (a cancellation sends its own email from
    # _set_owner_status instead).
    if not (order.status == Order.Status.CANCELLED and status_before != Order.Status.CANCELLED):
        from storefront.templatetags.website_filters import currency_symbol

        changes = order_notify.diff(
            shown_before, order_notify.snapshot(site, order), currency_symbol(site.currency)
        )
        if order.status == Order.Status.SHIPPED and status_before != Order.Status.SHIPPED:
            # The "shipped" email already carries the tracking number.
            changes = [c for c in changes if c["label"] != "Tracking number"]
        if changes:
            order_notify.queue(order, "updated", changes)

    activity.record(
        "store.order.updated", request=request, site=site,
        target=order, target_kind="Order", label=f"#{num}",
    )

    if want_replacement:
        # Delivered -> "Create replacement": a zero-value copy of the order
        # (same number, tagged Replacement) lands in Pending; the owner accepts
        # it there to book it with the courier. The original stays delivered.
        from commerce import order_requests

        copy = order_requests.create_replacement(order)
        messages.success(
            request,
            f"Replacement order #{copy.display_number} created at {site.currency} 0 for order #{order.number}, "
            "which stays Delivered. It is in Pending: accept it to book it with your courier.",
        )
        return redirect(f"{_orders_url(site)}?tab=pending")

    messages.success(request, f"Order #{num} updated." + (
        f" New total {site.currency} {order.total}." if amounts_changed else ""
    ))
    return redirect(edit_url)


# ── Legacy detail (kept for links) ───────────────────────────────────────────

@login_required
def order_detail(request, site_id, order_id):
    # The edit page is now the canonical single-order view.
    return redirect("store:order_edit", site_id=site_id, order_id=order_id)


@login_required
@require_POST
def update_status(request, site_id, order_id):
    site = get_owned_site(request, site_id)
    order = get_object_or_404(Order.objects.for_site(site), pk=order_id)
    new_status = request.POST.get("status") or ""
    if new_status in {s.value for s in Order.owner_statuses()}:
        if not _set_owner_status(request, site, order, new_status):
            return redirect("store:order_edit", site_id=site.id, order_id=order.id)
        order.save()
        _send_pending_customer_mail(order)
        activity.record(
            "store.order.updated", request=request, site=site,
            target=order, target_kind="Order", label=f"#{order.display_number}",
            changes={"status": order.get_status_display()},
        )
        messages.success(
            request, f"Order #{order.display_number} marked {order.get_status_display().lower()}."
        )
    else:
        messages.error(request, "Unknown status.")
    return redirect("store:order_edit", site_id=site.id, order_id=order.id)


# ── Post-order requests ──────────────────────────────────────────────────────

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

    page = _paginate(request, qs)
    page.object_list = list(page.object_list)
    # Link each approved replacement request to the order it created.
    replacement_ids = [
        (r.meta or {}).get("replacement_order_id")
        for r in page.object_list
        if (r.meta or {}).get("replacement_order_id")
    ]
    replacements = {
        str(o.pk): o for o in Order.objects.for_site(site).filter(pk__in=replacement_ids)
    } if replacement_ids else {}
    for r in page.object_list:
        r.replacement_order = replacements.get(str((r.meta or {}).get("replacement_order_id") or ""))

    context = store_context(request, site, "requests")
    context.update(
        {
            "requests": page,
            "type_choices": OrderRequest.RequestType,
            "status_choices": OrderRequest.Status,
            "active_type": rtype,
            "active_status": status,
            "pending_count": OrderRequest.objects.for_site(site)
            .filter(status=OrderRequest.Status.PENDING)
            .count(),
        }
    )
    return render(request, "store/requests.html", context)


@login_required
@require_POST
def resolve_request(request, site_id, request_id):
    site = get_owned_site(request, site_id)
    req = get_object_or_404(
        OrderRequest.objects.for_site(site).select_related("order"), pk=request_id
    )
    decision = request.POST.get("decision") or ""
    if decision == "reply":
        # Answer a shopper's support query: saved on the request (they see it
        # under the order on their account page), emailed, marked processed.
        from commerce import order_requests

        try:
            sent = order_requests.reply_to_support(req, request.POST.get("reply") or "")
        except order_requests.RequestError as exc:
            messages.error(request, str(exc))
            return redirect(_requests_back(request, site))
        activity.record(
            "store.request.resolved", request=request, site=site,
            target=req, target_kind="OrderRequest",
            label=f"Support · Order #{req.order.display_number}",
            changes={"decision": "Replied"},
        )
        if sent:
            messages.success(request, f"Reply sent to {req.order.customer_email} and the request marked processed.")
        else:
            messages.warning(
                request,
                "Reply saved and shown on the customer's account page, but the email couldn't be sent"
                + ("" if req.order.customer_email else " (the order has no email address)")
                + ".",
            )
        return redirect(_requests_back(request, site))
    if decision in OrderRequest.Status.values:
        # Approving a cancellation cancels the order; approving a replacement
        # creates the replacement order (same order number, back in Pending).
        if decision == OrderRequest.Status.APPROVED and req.status == OrderRequest.Status.PENDING:
            from commerce import order_requests

            try:
                if req.request_type == OrderRequest.RequestType.CANCEL:
                    for warning in order_requests.approve_cancellation(req):
                        messages.warning(request, warning)
                    messages.success(request, f"Order #{req.order.display_number} cancelled as requested.")
                elif req.request_type == OrderRequest.RequestType.REPLACE:
                    copy = order_requests.approve_replacement(req)
                    messages.success(
                        request,
                        f"Replacement order #{copy.display_number} created for order #{req.order.number}. It is in Orders → Pending.",
                    )
            except order_requests.RequestError as exc:
                messages.error(request, str(exc))
                return redirect("store:requests", site_id=site.id)
        req.status = decision
        if decision in (
            OrderRequest.Status.PROCESSED,
            OrderRequest.Status.APPROVED,
            OrderRequest.Status.REJECTED,
        ):
            req.processed_at = timezone.now()
        req.save()
        activity.record(
            "store.request.resolved", request=request, site=site,
            target=req, target_kind="OrderRequest",
            label=req.get_request_type_display() if hasattr(req, "get_request_type_display") else "Request",
            changes={"decision": req.get_status_display() if hasattr(req, "get_status_display") else decision},
        )
        messages.success(request, "Request updated.")
    return redirect(_requests_back(request, site))


def _requests_back(request, site) -> str:
    """The Requests page with the filters the owner was on (type / status /
    page), so acting on a request doesn't reset the list."""
    from django.urls import reverse

    params = {}
    for key in ("type", "status", "page"):
        value = (request.POST.get(f"back_{key}") or "").strip()
        if value and re.fullmatch(r"[a-z0-9_]{1,20}", value):
            params[key] = value
    url = reverse("store:requests", args=[site.id])
    return f"{url}?{urlencode(params)}" if params else url


# ── Enquiries ────────────────────────────────────────────────────────────────

@login_required
def enquiries(request, site_id):
    site = get_owned_site(request, site_id)
    qs = (
        Enquiry.objects.for_site(site)
        .filter(is_deleted=False)
        .prefetch_related("products", "categories")
    )
    unread_only = request.GET.get("unread") == "1"
    if unread_only:
        qs = qs.filter(is_read=False)

    # ?focus=<id> (the bell's "Open" on a new enquiry): land on the page that
    # holds it, highlighted and scrolled into view, and mark it read.
    focus_id = ""
    focused = None
    raw_focus = (request.GET.get("focus") or "").strip()
    if raw_focus:
        import uuid as _uuid

        try:
            focused = qs.filter(pk=_uuid.UUID(raw_focus)).first()
        except ValueError:
            focused = None
    if focused is not None:
        focus_id = str(focused.pk)
        if not request.GET.get("page"):
            newer = qs.filter(created_at__gt=focused.created_at).count()
            request.GET = request.GET.copy()
            request.GET["page"] = str(newer // 25 + 1)
        # (Left unread on the Unread tab, where reading it would hide it.)
        if not focused.is_read and not unread_only:
            Enquiry.objects.for_site(site).filter(pk=focused.pk).update(is_read=True)

    context = store_context(request, site, "enquiries")
    context.update(
        {
            "enquiries": _paginate(request, qs),
            "unread_only": unread_only,
            "is_b2b": site.is_b2b,
            "focus_id": focus_id,
        }
    )
    return render(request, "store/enquiries.html", context)


def _enquiries_redirect(request, site):
    """Back to the enquiries list, keeping the caller on the tab they were on."""
    resp = redirect("store:enquiries", site_id=site.id)
    if request.POST.get("unread") == "1":
        resp["Location"] += "?unread=1"
    return resp


@login_required
@require_POST
def enquiry_mark_read(request, site_id, enquiry_id):
    """Mark one enquiry as read (idempotent)."""
    site = get_owned_site(request, site_id)
    enq = get_object_or_404(
        Enquiry.objects.for_site(site).filter(is_deleted=False), pk=enquiry_id
    )
    if not enq.is_read:
        enq.is_read = True
        enq.save(update_fields=["is_read", "updated_at"])
    return _enquiries_redirect(request, site)


@login_required
@require_POST
def enquiry_delete(request, site_id, enquiry_id):
    """Soft-delete one enquiry so it drops out of every tab."""
    site = get_owned_site(request, site_id)
    enq = get_object_or_404(
        Enquiry.objects.for_site(site).filter(is_deleted=False), pk=enquiry_id
    )
    enq.soft_delete()
    messages.success(request, "Enquiry deleted.")
    return _enquiries_redirect(request, site)
