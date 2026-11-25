"""Turn a validated cart into an :class:`~commerce.models.Order`.

Prices are re-read from the live catalogue here (never trusted from the client),
every discount is re-evaluated against the shopper now identified by the order's
email  enforcing the checks that can only settle once the customer is known
(customer eligibility, one-use-per-customer, the total redemption cap)  and the
totals are recomputed. So the amount an order is created for is the amount the
payment gateway is then asked to charge.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import F

from commerce import credits
from commerce import discounts as engine
from commerce import quantity_pricing as qp
from commerce import stock
from commerce.cart import Cart
from commerce.models import Coupon, Order, OrderItem


class EmptyCartError(Exception):
    pass


class CouponError(Exception):
    """A discount that was in the cart can no longer be honoured at checkout."""


class QuantityLimitError(CouponError):
    """A line is above the store's quantity-discount limit (the owner chose
    "don't sell more than N" / "talk to us above N"). A :class:`CouponError` so
    every checkout caller already reports it to the shopper."""


# Orders that still count against a coupon's redemption limits.
_LIVE_ORDER_STATES = (
    Order.Status.CREATED,
    Order.Status.CONFIRMED,
    Order.Status.PAID,
    Order.Status.SHIPPED,
    Order.Status.DELIVERED,
)


def _sum_lines(items) -> Decimal:
    """Subtotal of already-resolved cart lines (same as ``Cart.subtotal``,
    without resolving the cart again)."""
    total = Decimal("0")
    for it in items:
        total += Decimal(it["line_total"])
    return total.quantize(Decimal("0.01"))


def _redemption_error(site, coupon: Coupon, email: str) -> str | None:
    """Per-customer / total redemption checks that need the shopper's identity."""
    if coupon.usage_limit is not None and coupon.times_used >= coupon.usage_limit:
        return f"“{coupon.display_name}” has reached its usage limit."
    if coupon.once_per_customer and email:
        already = (
            Order.objects.for_site(site)
            .filter(coupon=coupon, customer_email=email, status__in=_LIVE_ORDER_STATES)
            .exists()
        )
        if already:
            return f"You’ve already used “{coupon.display_name}”."
    return None


def create_order_from_cart(
    request, site, *, details: dict, cart: Cart | None = None, awaiting_payment: bool = False,
    credit_email: str = "",
) -> Order:
    """Create an ``Order`` and its line items from the session cart. Does not
    touch payment  the caller drives COD vs gateway next.

    ``credit_email`` is the *verified* shopper's email (their email-OTP
    session) when it matches the order's email: every store credit it holds
    comes off the total. Booked on placement, like the coupon redemptions.

    A COD order is placed at once (status CREATED). An online order is created
    ``awaiting_payment``: hidden from every listing, report and count, with no
    coupon redemption booked and no Customer row made. It only becomes a real
    order in :func:`place_order` once the gateway confirms the payment, and is
    discarded (:func:`discard_unpaid_order`) if the payment never happens.
    """
    cart = cart or Cart(request, site)
    items = cart.items()
    if not items:
        raise EmptyCartError("Your cart is empty.")

    # Never sell what isn't in stock: re-check every line against live stock
    # (the cart may have been filled before the item sold out).
    short = stock.shortages(items)
    if short:
        raise stock.OutOfStockError(stock.shortage_message(short))

    # The store's quantity-discount limit, re-checked from live rules (the
    # cart caps additions too; this stops a cart built before a rule changed).
    over = qp.lines_over_limit(items)
    if over:
        raise QuantityLimitError(qp.limit_message(site, over))

    subtotal = _sum_lines(items)
    email = (details.get("email") or "").strip().lower()
    customer = engine.resolve_customer(site, email)

    # Re-evaluate every discount strictly, now that we know who is buying.
    # Lines at a quantity price are left out when the store doesn't let
    # quantity prices combine with coupons.
    coupon_items, coupon_base, _left_out = qp.coupon_lines(site, items)
    result = engine.compute(
        site, lines=coupon_items, subtotal=coupon_base, code=cart.coupon_code,
        customer=customer, currency=getattr(site, "currency", "₹") or "₹", strict=True,
    )
    if cart.coupon_code and result.code_error:
        raise CouponError(result.code_error)

    for r in result.applied:
        err = _redemption_error(site, r.coupon, email)
        if err:
            raise CouponError(err)

    discount = result.amount
    # Store credit, after the coupon discounts, for a verified shopper only.
    credit_result = credits.CreditResult()
    credit_email = (credit_email or "").strip().lower()
    if credit_email and credit_email == email:
        credit_result = credits.compute(
            site, credit_email, lines=items, subtotal=subtotal, discount=discount
        )
    credit_amount = credit_result.amount
    # GST added on top for tax-exclusive products (nothing for tax-inclusive ones).
    # Computed from the same live lines, so the charged amount matches the cart.
    tax = cart.tax(items)
    total = (subtotal - discount - credit_amount)
    if total < Decimal("0.00"):
        total = Decimal("0.00")
    total = total + tax
    # The primary coupon recorded on the order: the coded one, else the top
    # automatic in effect.
    applied_coupons = [r.coupon for r in result.applied]
    primary = None
    for r in result.applied:
        if r.coupon.method == Coupon.Method.CODE:
            primary = r.coupon
            break
    if primary is None and applied_coupons:
        primary = applied_coupons[0]

    with transaction.atomic():
        order = Order.objects.create(
            site=site,
            channel=Order.Channel.B2B if site.is_b2b else Order.Channel.D2C,
            customer_name=(details.get("name") or "")[:120],
            customer_email=(details.get("email") or "")[:254],
            customer_phone=(details.get("phone") or "")[:20],
            shipping_address=(details.get("address") or ""),
            shipping_city=(details.get("city") or "")[:100],
            shipping_state=(details.get("state") or "")[:100],
            shipping_pincode=(details.get("pincode") or "")[:10],
            billing_address=(details.get("billing_address") or details.get("address") or ""),
            company_name=(details.get("company_name") or "")[:200],
            gstin=(details.get("gstin") or "")[:15],
            subtotal=subtotal,
            discount_amount=discount,
            tax_amount=tax,
            total=total,
            coupon=primary,
            applied_coupon_ids=[str(c.pk) for c in applied_coupons],
            credit=credit_result.primary,
            credit_discount_amount=credit_amount,
            credit_usage=credit_result.usage(),
            status=(Order.Status.AWAITING_PAYMENT if awaiting_payment else Order.Status.CREATED),
        )
        for it in items:
            product = it["product"]
            OrderItem.objects.create(
                site=site,
                order=order,
                product=product,
                title=it["title"],
                variant_label=it.get("variant_label", ""),
                # The variation combination's own SKU when the line is a
                # variation, else the product's (Cart.items resolves it).
                sku=(it.get("sku") or getattr(product, "sku", "") or "")[:100],
                quantity=it["quantity"],
                unit_price=it["unit_price"],
                line_total=it["line_total"],
                # Quantity discount: the regular price and the frozen deal, so
                # the order shows the saving and an owner edit re-prices fairly.
                regular_unit_price=(
                    it.get("regular_unit_price") if (it.get("qd") or {}).get("applied") else None
                ),
                quantity_discount=(it.get("qd") or {}).get("snapshot") or {},
            )
        if not awaiting_payment:
            # COD: take the units now, under row locks. A shortfall (someone
            # bought the last unit a moment ago) raises and rolls the order back.
            stock.deduct_for_order(order, strict=True)
            _book_placement(order, site)

    return order


def _book_placement(order: Order, site) -> None:
    """The side effects of an order actually being placed."""
    # Book a redemption against every discount that applied (code + any
    # automatics), not just the one recorded on the order.
    for coupon_id in order.applied_coupon_ids or []:
        Coupon.objects.for_site(site).filter(pk=coupon_id).update(
            times_used=F("times_used") + 1
        )
    # Draw the shopper's store credits down by what this order used.
    credits.book_for_order(order)
    # Placing an order makes this shopper a customer of the store: create (or
    # reuse) their Customer row and link the order, so the owner's Customers
    # page lists everyone who has ordered.
    from commerce.customer_sync import upsert_customer_for_order

    upsert_customer_for_order(order, site=site)


def place_order(order: Order) -> None:
    """Turn a paid ``awaiting_payment`` order into a real one.

    Called by the payment confirmation (browser verify, return URL or webhook)
    right after it has atomically moved the order out of ``awaiting_payment``,
    so this runs exactly once per order. Books the coupon redemptions, links the
    Customer and tells the owner a new order arrived. Best-effort per step 
    the shopper has already paid, so nothing here may undo that.
    """
    import logging

    site = order.site
    try:
        with transaction.atomic():
            _book_placement(order, site)
    except Exception:
        logging.getLogger("onewooz.audit").exception(
            "placing paid order failed: order=%s", order.pk
        )
    # Paid: take the units off stock now (checked at checkout; never refused
    # after payment, so a rare race floors stock at 0 instead).
    try:
        stock.deduct_for_order(order, strict=False)
    except Exception:
        logging.getLogger("onewooz.audit").exception(
            "stock deduction failed for paid order=%s", order.pk
        )

    from commerce import activity

    activity.record(
        "store.order.received", site=site,
        target=order, target_kind="Order", label=f"#{order.number}",
        actor_kind="customer",
    )

    # Email the shopper their itemised confirmation (with variations). Best-effort
    # and after payment has settled, so a mail failure never disturbs the order.
    try:
        from commerce.emails import send_order_confirmation

        send_order_confirmation(order)
    except Exception:
        logging.getLogger("onewooz.audit").exception(
            "order confirmation email failed: order=%s", order.pk
        )


def discard_unpaid_order(order: Order) -> bool:
    """Delete an online order whose payment never completed.

    Only ever removes an order still ``awaiting_payment``  nothing about it
    was booked or shown, so deleting it leaves no trace (its items and payment
    attempts cascade). Returns True if it was deleted.
    """
    deleted, _ = Order.all_objects.filter(
        pk=order.pk, status=Order.Status.AWAITING_PAYMENT
    ).delete()
    return bool(deleted)
