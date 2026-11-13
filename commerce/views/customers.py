"""Customers directory, per-customer history, and the row actions.

A customer is anyone who has ordered on (or enquired with) the store. Orders are
reconciled into Customer rows on every visit here (see
``commerce.customer_sync``), so the list always reflects who has actually
bought. Each row carries the same actions as the reference platform's Customers
tab  View, Edit, Credits, Notify, Delete  every one wired to the store's real
records so it stays in step with the shopper's own account.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import EmailMultiAlternatives
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from commerce import activity
from commerce.context import get_owned_site, store_context
from core.mail import store_mail_connection
from commerce.customer_sync import sync_customers_for_site
from commerce.models import (
    Category,
    Customer,
    CustomerAddress,
    CustomerCredit,
    Order,
    OrderItem,
    Product,
    display_ordered_categories,
)

ADDRESS_TYPES = (
    CustomerAddress.AddressType.BOTH,
    CustomerAddress.AddressType.SHIPPING,
    CustomerAddress.AddressType.BILLING,
)


def _orders_for(site, customer):
    """Every order that belongs to this customer  by the linked FK or, for
    resilience, by the snapshot email the storefront account also keys on."""
    return Order.objects.for_site(site).filter(
        Q(customer=customer) | Q(customer_email__iexact=customer.email)
    )


def _address_dict(a):
    return {
        "id": str(a.id),
        "address_type": a.address_type,
        "company_name": a.company_name,
        "gstin": a.gstin,
        "has_gst": a.has_gst,
        "house_no": a.house_no,
        "street_area": a.street_area,
        "city": a.city,
        "state": a.state,
        "pincode": a.pincode,
        "country_code": a.country_code,
        "is_default": a.is_default,
    }


def _iso(dt) -> str:
    return timezone.localtime(dt).isoformat() if dt else ""


def _order_detail_dict(site, o) -> dict:
    """One order, fully, for the customer popup's order accordion: lines,
    money breakdown, payment, delivery/billing details, courier and timeline.
    Relations are expected prefetched (items→product→images, shipments,
    payments); every read stays inside this store's rows."""
    from django.urls import reverse

    from commerce.label_pdf import payment_info

    items = []
    for it in o.items.all():
        image = ""
        product = it.product
        if product is not None:
            first = next(iter(product.images.all()), None)
            try:
                image = first.image.url if first and first.image else ""
            except Exception:  # missing file / storage misconfigured
                image = ""
        items.append(
            {
                "title": it.title or (product.title if product else "Item"),
                "variant": it.variant_label,
                "sku": it.sku or (getattr(product, "sku", "") if product else ""),
                "quantity": int(it.quantity or 0),
                "unit_price": str(it.unit_price),
                "line_total": str(it.line_total),
                "image": image,
            }
        )

    pay = payment_info(o)
    paid_online = any(p.status == "paid" for p in o.payments.all())
    shipment = next(iter(o.shipments.all()), None)  # newest first (Meta.ordering)
    courier = {}
    if shipment is not None:
        meta = shipment.meta or {}
        courier = {
            "name": shipment.courier_name,
            "awb": shipment.awb or o.tracking_id,
            "status": shipment.get_status_display(),
            "tracking_url": shipment.tracking_url,
            "pickup_date": str(meta.get("pickup_date") or "")[:10],
            "returning": bool(meta.get("return_in_progress")),
        }
    elif o.tracking_id:
        courier = {"awb": o.tracking_id}

    return {
        "id": str(o.id),
        "number": o.number,
        "status": o.status,
        "status_display": o.get_status_display(),
        "total": str(o.total),
        "created_at": _iso(o.created_at),
        "item_count": sum(i["quantity"] for i in items) or len(items),
        "items": items,
        "amounts": {
            "subtotal": str(o.subtotal),
            "discount": str(o.discount_amount),
            "credit_discount": str(o.credit_discount_amount),
            "shipping": str(o.shipping_amount),
            "tax": str(o.tax_amount),
            "total": str(o.total),
        },
        "coupon_code": (o.coupon.code if o.coupon_id and o.coupon else "") or "",
        "payment": {
            "label": pay["label"],
            "mode": pay["mode"],
            "paid": str(pay["paid"]),
            "collect": str(pay["collect"]),
            "paid_online": paid_online,
        },
        "shipping": {
            "name": o.customer_name,
            "phone": o.customer_phone,
            "email": o.customer_email,
            "address": o.shipping_address,
            "city": o.shipping_city,
            "state": o.shipping_state,
            "pincode": o.shipping_pincode,
        },
        "billing_address": o.billing_address if o.billing_address != o.shipping_address else "",
        "company_name": o.company_name,
        "gstin": o.gstin,
        "courier": courier,
        "timeline": [
            {"label": label, "at": _iso(at)}
            for label, at in (
                ("Placed", o.created_at),
                ("Confirmed", o.confirmed_at),
                ("Shipped", o.shipped_at),
                ("Delivered", o.delivered_at),
                ("Cancelled", o.cancelled_at),
            )
            if at
        ],
        "edit_url": reverse("store:order_edit", args=[site.id, o.id]),
    }


def _valid_id(value, allowed) -> str:
    """``value`` if it is one of ``allowed`` (this store's ids), else ""."""
    value = (value or "").strip()
    return value if value in allowed else ""


def _category_with_descendants(categories, root_id):
    """``root_id`` plus every category nested under it."""
    children = {}
    for c in categories:
        children.setdefault(str(c.parent_id or ""), []).append(str(c.id))
    out, stack = [], [root_id]
    while stack:
        cid = stack.pop()
        if cid in out:
            continue
        out.append(cid)
        stack.extend(children.get(cid, []))
    return out


@login_required
def customers(request, site_id):
    site = get_owned_site(request, site_id)
    # Turn everyone who has ordered into a Customer row before listing.
    sync_customers_for_site(site)

    # Shoppers who deleted their own account stay listed by name (their orders
    # still count), marked "Account deleted" with no contact actions.
    qs = Customer.objects.for_site(site).filter(
        Q(is_deleted=False) | Q(account_deleted_at__isnull=False)
    )
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q))

    categories_all = list(
        display_ordered_categories(Category.objects.for_site(site).filter(is_deleted=False))
    )
    products_all = list(
        Product.objects.for_site(site).filter(is_deleted=False).order_by("title").only("id", "title")
    )

    # Filters: customers who bought a given product, or anything in a category
    # (its sub-categories and products tagged into it included). Narrowed via a
    # subquery so the order count / spend annotations below stay per customer.
    product_id = _valid_id(request.GET.get("product"), {str(p.id) for p in products_all})
    category_id = _valid_id(request.GET.get("category"), {str(c.id) for c in categories_all})
    if product_id or category_id:
        items = OrderItem.objects.for_site(site)
        if product_id:
            items = items.filter(product_id=product_id)
        if category_id:
            cat_ids = _category_with_descendants(categories_all, category_id)
            items = items.filter(
                Q(product__category_id__in=cat_ids) | Q(product__categories__in=cat_ids)
            )
        qs = qs.filter(pk__in=items.exclude(order__customer__isnull=True).values("order__customer_id"))

    qs = qs.annotate(order_count=Count("orders", distinct=True), spent=Sum("orders__total"))

    page = Paginator(qs, 30).get_page(request.GET.get("page") or 1)
    context = store_context(request, site, "customers")
    context.update(
        {
            "customers": page,
            "q": q,
            "categories_all": categories_all,
            "products_all": products_all,
            "product_filter": product_id,
            "category_filter": category_id,
            "filtered": bool(q or product_id or category_id),
        }
    )
    return render(request, "store/customers.html", context)


_POPUP_SOURCE = "newsletter_popup"


def _filter_subscribers(request, qs):
    """Apply the Subscribers page's search (``q``) and source tab (``source``:
    ``newsletter`` = signed up through the popup, ``other`` = everywhere else)."""
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(email__icontains=q)
    source = (request.GET.get("source") or "").strip()
    if source == "newsletter":
        qs = qs.filter(source=_POPUP_SOURCE)
    elif source == "other":
        qs = qs.exclude(source=_POPUP_SOURCE)
    else:
        source = ""
    return qs, q, source


@login_required
def subscribers(request, site_id):
    """Newsletter subscribers captured from the storefront footer form."""
    from commerce.models import NewsletterSubscriber

    site = get_owned_site(request, site_id)
    base = NewsletterSubscriber.objects.for_site(site).filter(is_active=True)
    qs, q, source = _filter_subscribers(request, base)
    qs = qs.order_by("-created_at")

    total = base.count()
    popup_total = base.filter(source=_POPUP_SOURCE).count()
    page = Paginator(qs, 30).get_page(request.GET.get("page") or 1)
    context = store_context(request, site, "subscribers")
    context.update({
        "subscribers": page, "q": q, "total_subscribers": total,
        "source": source, "popup_total": popup_total, "other_total": total - popup_total,
    })
    return render(request, "store/subscribers.html", context)


@login_required
def subscribers_export(request, site_id):
    """Download every newsletter subscriber as a styled ``.xlsx``.

    Exports the whole active list in one file (honouring the current search
    filter if the owner exported from a filtered view). Uses the dependency-free
    writer in :mod:`commerce.xlsx`, so the download works without any extra
    package installed.
    """
    from commerce import xlsx
    from commerce.models import NewsletterSubscriber

    site = get_owned_site(request, site_id)
    qs, _q, _source = _filter_subscribers(
        request, NewsletterSubscriber.objects.for_site(site).filter(is_active=True)
    )
    rows = list(qs.order_by("-created_at"))

    wb = xlsx.Workbook()
    ws = wb.add_sheet("Subscribers")
    ws.write(0, 0, f"{site.name}  Newsletter subscribers", xlsx.STYLE_TITLE)
    ws.merge(0, 0, 0, 2)
    ws.write(
        1, 0,
        f"{len(rows)} subscriber(s) · exported {timezone.localtime():%d %b %Y, %H:%M}",
        xlsx.STYLE_SUBTITLE,
    )
    ws.merge(1, 0, 1, 2)

    header_row = 3
    ws.write_row(header_row, 0, ["Email", "Source", "Subscribed"], xlsx.STYLE_HEADER)
    ws.freeze_row = header_row + 1
    for i, w in enumerate([40, 28, 22]):
        ws.set_column_width(i, w)

    for i, sub in enumerate(rows):
        rr = header_row + 1 + i
        ws.write(rr, 0, sub.email, xlsx.STYLE_CELL)
        ws.write(rr, 1, sub.source_label, xlsx.STYLE_CELL)
        ws.write(
            rr, 2,
            timezone.localtime(sub.created_at).strftime("%d %b %Y, %H:%M"),
            xlsx.STYLE_CELL_CENTER,
        )

    if not rows:
        ws.write(header_row + 1, 0, "No subscribers yet.", xlsx.STYLE_CELL)
        ws.merge(header_row + 1, 0, header_row + 1, 2)

    resp = HttpResponse(
        wb.to_bytes(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    safe = "".join(c for c in site.name if c.isalnum() or c in " -_").strip()[:40] or "store"
    stamp = timezone.localtime().strftime("%Y%m%d")
    resp["Content-Disposition"] = f'attachment; filename="{safe} - subscribers {stamp}.xlsx"'
    return resp


@login_required
@require_POST
def subscriber_delete(request, site_id, subscriber_id):
    from commerce.models import NewsletterSubscriber

    site = get_owned_site(request, site_id)
    sub = get_object_or_404(NewsletterSubscriber.objects.for_site(site), pk=subscriber_id)
    sub.delete()
    messages.success(request, "Subscriber removed.")
    return redirect("store:subscribers", site_id=site.id)


@login_required
def customer_detail(request, site_id, customer_id):
    site = get_owned_site(request, site_id)
    customer = get_object_or_404(
        Customer.objects.for_site(site).filter(is_deleted=False), pk=customer_id
    )
    orders = _orders_for(site, customer)
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


# --------------------------------------------------------------------------
# Row action: View (JSON)
# --------------------------------------------------------------------------
@login_required
def customer_json(request, site_id, customer_id):
    site = get_owned_site(request, site_id)
    customer = get_object_or_404(
        Customer.objects.for_site(site).filter(is_deleted=False), pk=customer_id
    )
    orders_qs = _orders_for(site, customer).order_by("-created_at")
    agg = orders_qs.aggregate(n=Count("id"), spent=Sum("total"))

    addresses = [_address_dict(a) for a in customer.addresses.for_site(site)]

    orders = [
        _order_detail_dict(site, o)
        for o in orders_qs.select_related("coupon").prefetch_related(
            "items__product__images", "shipments", "payments"
        )[:50]
    ]

    return JsonResponse(
        {
            "success": True,
            "customer": {
                "id": str(customer.id),
                "name": customer.name,
                "email": customer.email,
                "phone": customer.phone,
                "created_at": customer.created_at.isoformat(),
                "updated_at": customer.updated_at.isoformat(),
                "order_count": agg["n"] or 0,
                "spent": str(agg["spent"] or Decimal("0.00")),
                "currency": site.currency,
                "addresses": addresses,
                "orders": orders,
            },
        }
    )


# --------------------------------------------------------------------------
# Row action: Edit (form POST → redirect)
# --------------------------------------------------------------------------
@require_POST
@login_required
def customer_edit(request, site_id, customer_id):
    site = get_owned_site(request, site_id)
    customer = get_object_or_404(
        Customer.objects.for_site(site).filter(is_deleted=False), pk=customer_id
    )

    name = (request.POST.get("name") or "").strip()[:120]
    phone = (request.POST.get("phone") or "").strip()[:20]
    email = (request.POST.get("email") or "").strip().lower()[:254]

    if not email:
        messages.error(request, "A customer needs an email address.")
        return redirect("store:customers", site_id=site.id)
    # Email is the customer's key within the store; a change must not collide
    # with an existing customer. Orders stay linked by the FK, so history is kept.
    clash = (
        Customer.objects.for_site(site)
        .filter(email=email)
        .exclude(pk=customer.pk)
        .exists()
    )
    if clash:
        messages.error(request, "Another customer already uses that email.")
        return redirect("store:customers", site_id=site.id)

    customer.name = name
    customer.phone = phone
    customer.email = email
    customer.save(update_fields=["name", "phone", "email", "updated_at"])

    _save_addresses(request, site, customer)

    activity.record(
        "store.customer.updated", request=request, site=site,
        target=customer, target_kind="Customer", label=customer.name or customer.email,
    )
    messages.success(request, "Customer updated.")
    return redirect("store:customers", site_id=site.id)


def _one_of(value, allowed, default):
    value = (value or "").strip()
    return value if value in allowed else default


def _save_addresses(request, site, customer):
    """Reconcile the customer's addresses against the submitted JSON: update
    existing rows, create new ones, and delete any the owner removed."""
    raw = request.POST.get("addresses_json") or ""
    if not raw:
        return
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError:
        return
    if not isinstance(rows, list):
        return

    existing = {str(a.id): a for a in customer.addresses.for_site(site)}
    seen = set()
    default_taken = False

    for row in rows:
        if not isinstance(row, dict):
            continue
        fields = {
            "address_type": _one_of(row.get("address_type"), ADDRESS_TYPES, CustomerAddress.AddressType.BOTH),
            "company_name": (row.get("company_name") or "").strip()[:200],
            "gstin": (row.get("gstin") or "").strip()[:15],
            "house_no": (row.get("house_no") or "").strip()[:120],
            "street_area": (row.get("street_area") or "").strip()[:200],
            "city": (row.get("city") or "").strip()[:120],
            "state": (row.get("state") or "").strip()[:120],
            "pincode": (row.get("pincode") or "").strip()[:10],
        }
        fields["has_gst"] = bool(fields["gstin"])
        # Ignore a card the owner left entirely blank.
        if not any(v for k, v in fields.items() if k not in ("address_type", "has_gst")):
            continue
        want_default = bool(row.get("is_default")) and not default_taken
        if want_default:
            default_taken = True

        rid = str(row.get("id") or "")
        if rid and rid in existing:
            addr = existing[rid]
            for key, val in fields.items():
                setattr(addr, key, val)
            addr.is_default = want_default
            addr.save()
            seen.add(rid)
        else:
            CustomerAddress(
                site=site, customer=customer, is_default=want_default, **fields
            ).save()

    # Anything present before but absent from the submission was removed.
    for rid, addr in existing.items():
        if rid not in seen:
            addr.delete()


# --------------------------------------------------------------------------
# Row action: Delete (form POST → redirect). Soft delete  the shopper's
# orders keep their snapshot, exactly as the model intends.
# --------------------------------------------------------------------------
@require_POST
@login_required
def customer_delete(request, site_id, customer_id):
    site = get_owned_site(request, site_id)
    customer = get_object_or_404(
        Customer.objects.for_site(site).filter(is_deleted=False), pk=customer_id
    )
    customer.soft_delete()
    activity.record(
        "store.customer.deleted", request=request, site=site,
        target=customer, target_kind="Customer", label=customer.name or customer.email,
    )
    messages.success(request, "Customer removed.")
    return redirect("store:customers", site_id=site.id)


# --------------------------------------------------------------------------
# Row action: Credits  list history (JSON) and add (JSON)
# --------------------------------------------------------------------------
@login_required
def customer_credits_list(request, site_id, customer_id):
    site = get_owned_site(request, site_id)
    customer = get_object_or_404(
        Customer.objects.for_site(site).filter(is_deleted=False), pk=customer_id
    )
    names = {str(c.id): c.name for c in Category.objects.for_site(site)}
    credits = (
        CustomerCredit.objects.for_site(site)
        .filter(Q(customer=customer) | Q(customer_email__iexact=customer.email))
        .order_by("-created_at")
    )
    out = []
    for cr in credits:
        out.append(
            {
                "id": str(cr.id),
                "credit_type": cr.credit_type,
                "credit_value": str(cr.credit_value),
                "remaining_amount": (str(cr.remaining_amount) if cr.remaining_amount is not None else None),
                "scope": cr.scope,
                "category_names": [names.get(str(cid), "") for cid in (cr.category_ids or [])],
                "is_active": cr.is_active,
                "valid_until": cr.valid_until.isoformat() if cr.valid_until else None,
                "note": cr.note,
                "created_at": cr.created_at.isoformat(),
                "last_used_at": cr.last_used_at.isoformat() if cr.last_used_at else None,
                "from_bulk": bool(cr.batch_id),
            }
        )
    return JsonResponse({"success": True, "credits": out})


def _credit_label(site, credit) -> str:
    """"INR 250.00 credit" / "10% credit", for the activity feed."""
    if credit.get("credit_type") == CustomerCredit.CreditType.PERCENT:
        return f"{credit.get('credit_value')}% credit"
    return f"{site.currency} {credit.get('credit_value')} credit"


@require_POST
@login_required
def customer_credit_delete(request, site_id, customer_id, credit_id):
    """Delete one customer's credit (before or after its expiry). Orders that
    already used it keep their discount; see commerce.credit_batches."""
    site = get_owned_site(request, site_id)
    customer = get_object_or_404(
        Customer.objects.for_site(site).filter(is_deleted=False), pk=customer_id
    )
    credit = (
        CustomerCredit.objects.for_site(site)
        .filter(Q(customer=customer) | Q(customer_email__iexact=customer.email))
        .filter(pk=credit_id)
        .first()
    )
    if credit is None:
        return JsonResponse({"success": False, "error": "That credit no longer exists."}, status=404)
    label = _credit_label(site, {"credit_type": credit.credit_type, "credit_value": credit.credit_value})
    credit.delete()
    activity.record(
        "store.customer.credit_deleted", request=request, site=site,
        target=customer, target_kind="Customer",
        label=f"{label} · {customer.name or customer.email}",
    )
    return JsonResponse({"success": True, "message": "Credit deleted."})


@login_required
def customers_credit_bulk_list(request, site_id):
    """Every universal (Bulk credits) grant, for the "Given to all" tab."""
    from commerce import credit_batches

    site = get_owned_site(request, site_id)
    return JsonResponse({"success": True, "batches": credit_batches.batches(site)})


@require_POST
@login_required
def customers_credit_bulk_delete(request, site_id):
    """Delete one universal grant: that credit, from every customer it went to."""
    from commerce import credit_batches

    site = get_owned_site(request, site_id)
    deleted, summary = credit_batches.delete_batch(site, request.POST.get("batch") or "")
    if not deleted:
        return JsonResponse(
            {"success": False, "error": "That bulk credit no longer exists."}, status=404
        )
    activity.record(
        "store.customer.credit_bulk_deleted", request=request, site=site,
        target_kind="CustomerCredit",
        label=f"{_credit_label(site, summary)} · {deleted} customer(s)",
    )
    return JsonResponse(
        {"success": True, "message": f"Credit removed from {deleted} customer(s)."}
    )


def _apply_credit(site, customer, data, batch_id=None):
    """Validate a credit payload and create a CustomerCredit. Returns
    ``(credit, None)`` on success or ``(None, error_message)``. ``batch_id``
    tags every credit of one "Bulk credits" run (see commerce.credit_batches)."""
    credit_type = (data.get("credit_type") or "amount").strip()
    if credit_type not in (CustomerCredit.CreditType.AMOUNT, CustomerCredit.CreditType.PERCENT):
        return None, "Choose a valid credit type."
    try:
        value = Decimal(str(data.get("credit_value") or "").strip())
    except (InvalidOperation, TypeError):
        return None, "Enter a valid value greater than 0."
    if value <= 0:
        return None, "Enter a valid value greater than 0."
    if credit_type == CustomerCredit.CreditType.PERCENT and value > 100:
        return None, "Percentage cannot exceed 100."

    scope = (data.get("scope") or "all").strip()
    if scope not in (CustomerCredit.Scope.ALL, CustomerCredit.Scope.CATEGORIES):
        scope = CustomerCredit.Scope.ALL
    category_ids = []
    if scope == CustomerCredit.Scope.CATEGORIES:
        raw = data.get("category_ids")
        raw = raw if isinstance(raw, list) else data.getlist("category_ids") if hasattr(data, "getlist") else []
        valid = {str(c.id) for c in Category.objects.for_site(site)}
        category_ids = [str(x) for x in raw if str(x) in valid]
        if not category_ids:
            return None, "Select at least one category or choose All."

    from django.utils.dateparse import parse_date

    valid_until_raw = (data.get("valid_until") or "").strip()
    valid_until = parse_date(valid_until_raw) if valid_until_raw else None

    credit = CustomerCredit.objects.create(
        site=site,
        customer=customer,
        customer_email=customer.email,
        credit_type=credit_type,
        credit_value=value,
        remaining_amount=(value if credit_type == CustomerCredit.CreditType.AMOUNT else None),
        scope=scope,
        category_ids=category_ids,
        valid_until=valid_until,
        note=(data.get("note") or "").strip()[:255],
        is_active=True,
        batch_id=batch_id,
    )
    return credit, None


@require_POST
@login_required
def customer_credit_add(request, site_id, customer_id):
    site = get_owned_site(request, site_id)
    customer = get_object_or_404(
        Customer.objects.for_site(site).filter(is_deleted=False), pk=customer_id
    )
    credit, error = _apply_credit(site, customer, request.POST)
    if error:
        return JsonResponse({"success": False, "error": error}, status=400)
    _send_credit_email(site, customer, credit)
    activity.record(
        "store.customer.credited", request=request, site=site,
        target=customer, target_kind="Customer", label=customer.name or customer.email,
    )
    return JsonResponse({"success": True})


@require_POST
@login_required
def customers_credit_bulk(request, site_id):
    """Distribute one credit to every current customer of the store."""
    site = get_owned_site(request, site_id)
    everyone = Customer.objects.for_site(site).filter(is_deleted=False)
    # One id for the whole run, so the grant can later be deleted as a unit.
    from core.ids import uuid7

    batch_id = uuid7()
    made = 0
    error = None
    for customer in everyone:
        credit, err = _apply_credit(site, customer, request.POST, batch_id=batch_id)
        if err:
            error = err
            break
        _send_credit_email(site, customer, credit)
        made += 1
    if error and made == 0:
        return JsonResponse({"success": False, "error": error}, status=400)
    return JsonResponse({"success": True, "message": f"Credit added for {made} customer(s)."})


# --------------------------------------------------------------------------
# Email helpers (shared by Notify and Credits)
# --------------------------------------------------------------------------
MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB each


def _read_attachments(request):
    """Read uploaded notification attachments once into (name, bytes, mime)
    tuples, so the same files can be attached to many recipients' emails."""
    out = []
    for f in request.FILES.getlist("attachments")[:MAX_ATTACHMENTS]:
        if f.size and f.size > MAX_ATTACHMENT_BYTES:
            continue
        try:
            out.append((f.name, f.read(), f.content_type or "application/octet-stream"))
        except Exception:
            continue
    return out


def _send_customer_email(site, to_email, title, message, link, attachments=None):
    if not to_email:
        return False
    body = message
    if link:
        body = f"{message}\n\n{link}"
    body = f"{body}\n\n {site.name}"
    try:
        # Store→customer mail leaves from the store's own branded address on the
        # shared SES-verified domain (e.g. jackandjones@via.onewooz.com), not
        # from OneWooz's own DEFAULT_FROM_EMAIL. Replies go to the owner's
        # support inbox when they have set one.
        msg = EmailMultiAlternatives(
            subject=title[:255],
            body=body,
            from_email=site.customer_from_header,
            to=[to_email],
            reply_to=[site.support_email] if site.support_email else None,
            connection=store_mail_connection(fail_silently=True),
        )
        # Branded HTML alternative  the store's name in the header, the shared
        # OneWooz email design (email/base.html). Best-effort: if rendering ever
        # fails the plain-text body still sends.
        try:
            from django.template.loader import render_to_string

            html = render_to_string(
                "email/store/message.html",
                {
                    "brand": site.name,
                    "title": title,
                    "message": message,
                    "link": link,
                    "reply_to": bool(site.support_email),
                    "powered_by": True,
                },
            )
            msg.attach_alternative(html, "text/html")
        except Exception:
            pass
        for name, content, mime in attachments or []:
            msg.attach(name, content, mime)
        msg.send(fail_silently=True)
        return True
    except Exception:
        return False


def _send_credit_email(site, customer, credit):
    """Tell the shopper a store credit was added to their account."""
    if credit.credit_type == CustomerCredit.CreditType.PERCENT:
        desc = f"{credit.credit_value}% discount"
    else:
        desc = f"{site.currency} {credit.credit_value} store credit"
    lines = [f"Good news  {site.name} has added {desc} to your account."]
    if credit.valid_until:
        lines.append(f"Valid until: {credit.valid_until.isoformat()}")
    if credit.note:
        lines.append(f"Note: {credit.note}")
    lines.append(
        "It comes off your order automatically at checkout when you order with this email: "
        "sign in to your account first, or verify this email at checkout with the one-time "
        "code we send you. You can also see it on your account page."
    )
    _send_customer_email(
        site, customer.email, f"You’ve received store credit at {site.name}", "\n".join(lines), ""
    )


# --------------------------------------------------------------------------
# Row action: Notify  email one customer, or all of them (with attachments)
# --------------------------------------------------------------------------
@require_POST
@login_required
def customer_notify(request, site_id, customer_id):
    site = get_owned_site(request, site_id)
    customer = get_object_or_404(
        Customer.objects.for_site(site).filter(is_deleted=False), pk=customer_id
    )
    title = (request.POST.get("title") or "").strip()
    message = (request.POST.get("message") or "").strip()
    link = (request.POST.get("link") or "").strip()
    if not title or not message:
        return JsonResponse({"success": False, "error": "Title and message are required."}, status=400)
    attachments = _read_attachments(request)
    _send_customer_email(site, customer.email, title, message, link, attachments)
    return JsonResponse({"success": True, "message": "Notification was sent."})


@require_POST
@login_required
def customers_notify_all(request, site_id):
    site = get_owned_site(request, site_id)
    title = (request.POST.get("title") or "").strip()
    message = (request.POST.get("message") or "").strip()
    link = (request.POST.get("link") or "").strip()
    if not title or not message:
        return JsonResponse({"success": False, "error": "Title and message are required."}, status=400)
    attachments = _read_attachments(request)
    sent = 0
    for customer in Customer.objects.for_site(site).filter(is_deleted=False):
        if _send_customer_email(site, customer.email, title, message, link, attachments):
            sent += 1
    return JsonResponse({"success": True, "message": f"Notification was sent to {sent} customer(s)."})
