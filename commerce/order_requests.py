"""Customer post-order requests: cancellation and replacement.

A shopper raises a request from their storefront account page; it lands on the
owner's **Requests** page, where the owner approves or rejects it. Approval is
what actually acts on the order:

* **Cancellation**  only while the order hasn't shipped (no parcel with the
  courier yet). Approving cancels the order exactly as the owner's own Cancel
  does: cancelled with the courier, stock put back, the shopper emailed.
* **Replacement**  a delivered order, within the replacement window of its
  products. Approving copies the order into a new order with the same order
  number, tagged *Replacement*, back in **Pending**; from there it is accepted,
  shipped and delivered like any other order (see :func:`create_replacement`).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from commerce import activity
from commerce.models import Order, OrderItem, OrderRequest, Shipment

# Shipment states meaning the parcel has left the warehouse with the courier.
_WITH_COURIER = (
    Shipment.Status.PICKED_UP,
    Shipment.Status.IN_TRANSIT,
    Shipment.Status.OUT_FOR_DELIVERY,
    Shipment.Status.DELIVERY_FAILED,
    Shipment.Status.DELIVERED,
    Shipment.Status.RTO,
)
# Order states before shipping: a cancellation can still be asked for.
_CANCELLABLE_STATUSES = (Order.Status.CREATED, Order.Status.PAID, Order.Status.CONFIRMED)
# Requests still in play (a second one of the same kind isn't accepted).
_OPEN = (OrderRequest.Status.PENDING, OrderRequest.Status.APPROVED)


class RequestError(Exception):
    """A request the shopper may not raise, or the owner may not approve."""


def has_shipped(order: Order) -> bool:
    """True once the order is shipped (or its parcel is with the courier)."""
    if order.status in (Order.Status.SHIPPED, Order.Status.DELIVERED):
        return True
    return Shipment.objects.for_site(order.site).filter(
        order=order, status__in=_WITH_COURIER
    ).exists()


def can_request_cancel(order: Order) -> bool:
    return order.status in _CANCELLABLE_STATUSES and not has_shipped(order)


def replacement_deadline(order: Order):
    """When the order's replacement window closes, or ``None`` (none applies).

    The window is the longest replacement period among the order's products,
    counted from delivery.
    """
    if order.status != Order.Status.DELIVERED or order.is_replacement:
        return None
    days = 0
    for item in OrderItem.objects.for_site(order.site).filter(order=order).select_related("product"):
        product = item.product
        window = getattr(product, "effective_replacement_days", None) if product else None
        days = max(days, int(window or 0))
    if not days:
        return None
    start = order.delivered_at or order.updated_at
    return start + timedelta(days=days)


def can_request_replacement(order: Order) -> bool:
    deadline = replacement_deadline(order)
    return bool(deadline and timezone.now() <= deadline)


def open_request(order: Order, request_type: str) -> OrderRequest | None:
    return (
        OrderRequest.objects.for_site(order.site)
        .filter(order=order, request_type=request_type, status__in=_OPEN)
        .first()
    )


def pending_support(order: Order) -> OrderRequest | None:
    """The order's support query still waiting on the store, if any."""
    return (
        OrderRequest.objects.for_site(order.site)
        .filter(
            order=order,
            request_type=OrderRequest.RequestType.SUPPORT,
            status=OrderRequest.Status.PENDING,
        )
        .first()
    )


def annotate_for_account(orders) -> None:
    """Attach what the account page shows per order: whether a cancellation /
    replacement may be requested, any request already raised, and the latest
    support query (with the store's reply). Support can be asked on every
    order, whatever its status, one open query at a time."""
    orders = list(orders)
    if not orders:
        return
    site = orders[0].site
    latest = {}
    for req in (
        OrderRequest.objects.for_site(site)
        .filter(order__in=orders)
        .order_by("created_at")
    ):
        latest[(req.order_id, req.request_type)] = req
    for o in orders:
        o.cancel_request = latest.get((o.pk, OrderRequest.RequestType.CANCEL))
        o.replace_request = latest.get((o.pk, OrderRequest.RequestType.REPLACE))
        o.support_request = latest.get((o.pk, OrderRequest.RequestType.SUPPORT))
        cancel_open = o.cancel_request is not None and o.cancel_request.status in _OPEN
        replace_open = o.replace_request is not None and o.replace_request.status in _OPEN
        o.can_cancel = not cancel_open and can_request_cancel(o)
        o.can_replace = not replace_open and can_request_replacement(o)
        o.can_support = not (
            o.support_request is not None
            and o.support_request.status == OrderRequest.Status.PENDING
        )
        o.support_reply = (
            str((o.support_request.meta or {}).get("reply") or "")
            if o.support_request is not None
            else ""
        )


def raise_request(order: Order, request_type: str, *, reason: str = "", description: str = "") -> OrderRequest:
    """Record a shopper's request on ``order`` (it appears on the owner's
    Requests page and in the bell). Raises :class:`RequestError` when the
    order isn't eligible or the same request is already open."""
    if request_type == OrderRequest.RequestType.CANCEL:
        if not can_request_cancel(order):
            raise RequestError("This order has already shipped, so it can no longer be cancelled.")
    elif request_type == OrderRequest.RequestType.REPLACE:
        if not can_request_replacement(order):
            raise RequestError("This order isn't eligible for a replacement.")
    elif request_type == OrderRequest.RequestType.SUPPORT:
        # Any order, any status: the shopper describes the problem.
        if not (description or "").strip():
            raise RequestError("Please describe how the store can help.")
        if pending_support(order) is not None:
            raise RequestError(
                "You already have an open support request for this order. The store will reply by email."
            )
    else:
        raise RequestError("Unknown request.")
    if request_type != OrderRequest.RequestType.SUPPORT and open_request(order, request_type) is not None:
        raise RequestError("You have already raised this request. The store will get back to you.")

    req = OrderRequest.objects.create(
        site=order.site,
        order=order,
        request_type=request_type,
        reason=(reason or "").strip()[:255],
        description=(description or "").strip()[:2000],
        meta={"source": "customer_account"},
    )
    activity.record(
        "store.request.created", site=order.site, target=req, target_kind="OrderRequest",
        label=f"{req.get_request_type_display()} · Order #{order.display_number}"
        + (f" · {req.reason}" if request_type == OrderRequest.RequestType.SUPPORT and req.reason else ""),
        actor_kind="customer",
    )
    return req


def reply_to_support(req: OrderRequest, message: str, *, resolve: bool = True) -> bool:
    """Answer a shopper's support query: the reply is kept on the request
    (shown under the order on their account page), emailed to them from the
    store's address, and the query is marked processed. Returns whether the
    email went out (the reply is saved either way)."""
    message = (message or "").strip()[:4000]
    if not message:
        raise RequestError("Write a reply first.")
    now = timezone.now()
    req.meta = {**(req.meta or {}), "reply": message, "replied_at": now.isoformat()}
    fields = ["meta", "updated_at"]
    if resolve:
        req.status = OrderRequest.Status.PROCESSED
        req.processed_at = now
        fields += ["status", "processed_at"]
    req.save(update_fields=fields)
    return _email_support_reply(req, message)


def _email_support_reply(req: OrderRequest, message: str) -> bool:
    """Plain-text reply to the shopper, from the store's customer address
    (reply-to the store's support email when it has one). Never raises."""
    import logging

    from django.core.mail import EmailMessage

    from core.mail import store_mail_connection

    log = logging.getLogger(__name__)
    order = req.order
    to = (order.customer_email or "").strip()
    if not to:
        return False
    site = order.site
    brand = (getattr(site, "name", "") or "").strip() or "the store"
    name = (order.customer_name or "").strip().split(" ")[0] if order.customer_name else ""
    topic = f" ({req.reason})" if req.reason else ""
    lines = [
        f"Hi {name}," if name else "Hi,",
        "",
        f"{brand} has replied to your support request{topic} about order #{order.display_number}:",
        "",
        message,
        "",
    ]
    if (req.description or "").strip():
        lines += ["Your message:", req.description.strip(), ""]
    try:
        lines.append(f"See your orders: {site.storefront_url()}account/#orders")
    except Exception:
        pass
    lines += ["", f"Thank you for shopping with {brand}."]
    support_email = (getattr(site, "support_email", "") or "").strip()
    email = EmailMessage(
        subject=f"Re: your support request for {brand} order #{order.display_number}",
        body="\n".join(lines),
        from_email=site.customer_from_header,
        to=[to],
        reply_to=[support_email] if support_email else None,
        connection=store_mail_connection(fail_silently=True),
    )
    try:
        email.send(fail_silently=False)
    except Exception:
        log.exception("support reply email failed request=%s", req.pk)
        return False
    return True


# ── Owner decisions ──────────────────────────────────────────────────────────

def approve_cancellation(req: OrderRequest) -> list[str]:
    """Cancel the request's order. Returns warnings for the owner (e.g. a
    courier that refused the cancellation). Raises :class:`RequestError` if the
    order has shipped since the request was raised."""
    from commerce import order_notify, stock
    from commerce import shipping as shipping_svc

    order = req.order
    warnings: list[str] = []
    if order.status == Order.Status.CANCELLED:
        return warnings
    if has_shipped(order):
        raise RequestError(
            f"Order #{order.display_number} has already shipped, so it can't be cancelled. Reject the request instead."
        )
    order.status = Order.Status.CANCELLED
    order.save(update_fields=["status", "cancelled_at"])
    warning = shipping_svc.cancel_with_courier(order.site, order)
    if warning:
        warnings.append(warning)
    stock.release_if_not_dispatched(order)
    # Any store credit the order used goes back to the shopper.
    from commerce import credits

    credits.restore_for_order(order)
    order_notify.queue(order, "cancelled")
    activity.record(
        "store.order.cancelled", site=order.site, target=order, target_kind="Order",
        label=f"#{order.display_number} (customer request)",
    )
    return warnings


def pending_replacement(order: Order) -> Order | None:
    """A replacement of ``order`` (or of its original) still waiting in
    Pending, i.e. not accepted yet. Guards against creating the same
    replacement twice (a double click, two tabs)."""
    root_id = order.replacement_of_id if order.replacement_seq and order.replacement_of_id else order.pk
    return (
        Order.objects.for_site(order.site)
        .filter(replacement_of_id=root_id, status__in=(Order.Status.CREATED, Order.Status.PAID))
        .first()
    )


def create_replacement(order: Order) -> Order:
    """Copy ``order`` into a replacement order and return it.

    The copy keeps the order number, the shopper, the address and every line
    (same products, variations, SKUs and quantities), is tagged as a
    replacement (``replacement_of`` / ``replacement_seq``) and starts in
    **Pending** (status *created*) so the owner accepts it and it flows through
    ship → deliver like any order. It is a **₹0 order**: every price, the
    shipping and the total are zero, so the courier booking goes as prepaid with
    nothing to collect and it never counts as new revenue. It isn't attributed
    to a UGC collaborator (no second commission) and carries no commerce fee of
    its own. A replacement of a replacement still points at the first,
    original order, so the two always read together.
    """
    from commerce import stock

    zero = Decimal("0.00")
    root = order
    if order.replacement_seq and order.replacement_of_id:
        root = Order.all_objects.filter(pk=order.replacement_of_id).first() or order
    with transaction.atomic():
        # Lock the original so two approvals can't both take the same sequence.
        Order.all_objects.select_for_update().filter(pk=root.pk).first()
        seq = (
            Order.all_objects.filter(site_id=order.site_id, number=order.number)
            .aggregate(m=Max("replacement_seq"))["m"]
            or 0
        ) + 1
        copy = Order.objects.create(
            site=order.site,
            number=order.number,
            replacement_of=root,
            replacement_seq=seq,
            channel=order.channel,
            customer=order.customer,
            customer_name=order.customer_name,
            customer_email=order.customer_email,
            customer_phone=order.customer_phone,
            shipping_address=order.shipping_address,
            shipping_city=order.shipping_city,
            shipping_state=order.shipping_state,
            shipping_pincode=order.shipping_pincode,
            billing_address=order.billing_address,
            company_name=order.company_name,
            gstin=order.gstin,
            status=Order.Status.CREATED,
            subtotal=zero,
            discount_amount=zero,
            tax_amount=zero,
            shipping_amount=zero,
            total=zero,
            payment_method="replacement",
        )
        for item in OrderItem.objects.for_site(order.site).filter(order=order).order_by("created_at"):
            OrderItem.objects.create(
                site=order.site,
                order=copy,
                product_id=item.product_id,
                title=item.title,
                variant_label=item.variant_label,
                sku=item.sku,
                quantity=item.quantity,
                unit_price=zero,
                line_total=zero,
            )
        # The replacement units leave the shelf like any order's.
        stock.deduct_for_order(copy, strict=False)
    # Timeline only: the owner made this themselves, so no bell or email.
    activity.record(
        "store.order.replacement_created", site=order.site, target=copy, target_kind="Order",
        label=f"#{copy.display_number} (replacement of #{copy.number})",
    )
    return copy


def approve_replacement(req: OrderRequest) -> Order:
    """Create the replacement order for an approved request (once) and link it
    on the request's ``meta``."""
    existing = (req.meta or {}).get("replacement_order_id")
    if existing:
        found = Order.all_objects.filter(pk=existing).first()
        if found is not None:
            return found
    copy = create_replacement(req.order)
    req.meta = {**(req.meta or {}), "replacement_order_id": str(copy.pk)}
    OrderRequest.objects.for_site(req.site).filter(pk=req.pk).update(meta=req.meta)
    return copy
