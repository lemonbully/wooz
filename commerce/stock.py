"""Stock enforcement for storefront orders.

Stock lives on :class:`~commerce.models.Product`: ``inventory`` for a simple
product, ``inventory`` inside each ``variant_combinations`` row for a product
with variations. A blank inventory counts as 0, the same way the storefront's
"In stock / Out of stock" label and the dashboard's Out of stock page read it.

Three checkpoints keep an out-of-stock item from being ordered:

* **Add to cart** (:func:`available_for`): an out-of-stock product or variation
  is refused, and the quantity is capped at what is left.
* **Checkout** (:func:`shortages`): every line is re-checked against live stock
  before an order is created, so a cart filled earlier can't slip through.
* **Placement** (:func:`deduct_for_order`): the units are taken off stock under
  a row lock, so two shoppers can't both buy the last unit. A COD order deducts
  when it is created, strictly (a shortfall rolls the order back). An online
  order deducts when the gateway confirms the payment; it was checked at
  checkout, and a paid order is never undone, so a rare race only floors the
  stock at 0.

:func:`release_for_order` puts the units back when an order is cancelled
before it leaves the warehouse, or when its parcel is returned to origin.
``Order.stock_deducted`` makes deduct/release idempotent.
"""

from __future__ import annotations

import logging

from django.db import transaction

from commerce.cart import _combo_for, _norm, _variant_label, combos_of
from commerce.models import Order, OrderItem, Product

logger = logging.getLogger("onewooz.audit")


class OutOfStockError(Exception):
    """Some line can't be fulfilled from stock. The message is shopper-safe."""


def _int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


# ── Reading stock ────────────────────────────────────────────────────────────

def _line_combo(product, variant_key: str = "", attrs=None):
    """The stored combination a cart line refers to, or ``None``."""
    if not (variant_key or attrs):
        return None
    return _combo_for(product, variant_key, attrs)


def available_for(product, variant_key: str = "", attrs=None) -> int:
    """Units on hand for one product, or one of its variations.

    A variation with a stored combination row has its own stock. A product
    without variations (or a pick that matches no stored row) uses the
    product's total.
    """
    if combos_of(product) and (variant_key or attrs):
        combo = _line_combo(product, variant_key, attrs)
        if combo is not None:
            from commerce.variant_profiles import combo_active

            # A variation the owner switched off is not for sale.
            if not combo_active(combo):
                return 0
            return _int(combo.get("inventory"))
    return _int(product.stock_on_hand)


def _stock_key(product, combo) -> tuple:
    return (str(product.pk), id(combo) if combo is not None else None)


def shortages(items) -> list[dict]:
    """Cart lines asking for more than is in stock.

    ``items`` are :meth:`commerce.cart.Cart.items` dicts. Lines of the same
    product + variation are summed, so two lines can't each take the last
    unit. Returns ``[{"title", "variant_label", "available", "requested"}]``.
    """
    wanted: dict = {}
    for it in items:
        product = it.get("product")
        if product is None:
            continue
        combo = _line_combo(product, it.get("variant_key") or "", it.get("variant_attributes") or None)
        key = _stock_key(product, combo)
        row = wanted.setdefault(
            key,
            {
                "title": it.get("title") or product.title,
                "variant_label": it.get("variant_label") or "",
                "available": available_for(
                    product, it.get("variant_key") or "", it.get("variant_attributes") or None
                ),
                "requested": 0,
            },
        )
        row["requested"] += int(it.get("quantity") or 0)
    return [row for row in wanted.values() if row["requested"] > row["available"]]


def shortage_message(rows) -> str:
    """One sentence naming what is out of stock / short, for the shopper."""
    parts = []
    for r in rows[:3]:
        name = r["title"] + (f" ({r['variant_label']})" if r.get("variant_label") else "")
        if r["available"] <= 0:
            parts.append(f"{name} is out of stock")
        else:
            parts.append(f"only {r['available']} of {name} left")
    more = f" and {len(rows) - 3} more" if len(rows) > 3 else ""
    text = "; ".join(parts) + more
    return f"Sorry, {text}. Please update your cart to continue."


# ── Deducting / releasing for an order ───────────────────────────────────────

def _combo_for_item(product, item: OrderItem):
    """The stored combination an order line was sold as: by its variation
    label, else by its own SKU (a variation's SKU differs from the product's)."""
    combos = combos_of(product)
    if not combos:
        return None
    label = (item.variant_label or "").strip()
    if label:
        want = _norm(label)
        for combo in combos:
            if _norm(_variant_label(combo)) == want:
                return combo
    sku = (item.sku or "").strip()
    if sku and sku != (product.sku or "").strip():
        for combo in combos:
            if str(combo.get("sku") or "").strip() == sku:
                return combo
    return None


def _apply(order: Order, *, sign: int, strict: bool) -> list[dict]:
    """Move stock for every line of ``order`` (``sign`` -1 = take, +1 = give
    back) under row locks. Returns shortages found (only when taking)."""
    site_id = order.site_id
    items = list(OrderItem.all_objects.filter(order_id=order.pk, site_id=site_id))
    pids = {it.product_id for it in items if it.product_id}
    if not pids:
        return []
    products = {
        p.pk: p
        for p in Product.all_objects.select_for_update().filter(pk__in=pids, site_id=site_id)
    }
    # (product, combo) → [qty, title, label]
    moves: dict = {}
    for it in items:
        product = products.get(it.product_id)
        if product is None:
            continue
        combo = _combo_for_item(product, it)
        # A variation line whose combination no longer exists can't be tracked
        # against a specific row; leave the stock alone rather than guess.
        if combo is None and combos_of(product):
            continue
        key = (product.pk, id(combo) if combo is not None else None)
        row = moves.setdefault(key, [product, combo, 0, it.title, it.variant_label])
        row[2] += int(it.quantity or 0)

    short = []
    if sign < 0:
        for product, combo, qty, title, label in moves.values():
            have = _int(combo.get("inventory")) if combo is not None else _int(product.inventory)
            if qty > have:
                short.append({"title": title, "variant_label": label, "available": have, "requested": qty})
        if short and strict:
            return short

    touched = {}
    for product, combo, qty, _title, _label in moves.values():
        if combo is not None:
            combo["inventory"] = max(0, _int(combo.get("inventory")) + sign * qty)
        else:
            product.inventory = max(0, _int(product.inventory) + sign * qty)
        touched[product.pk] = product
    for product in touched.values():
        product.save(update_fields=["inventory", "variant_combinations", "updated_at"])
    return short


def deduct_for_order(order: Order, *, strict: bool) -> None:
    """Take ``order``'s units off stock, once.

    ``strict`` (COD placement): raise :class:`OutOfStockError` if any line is
    short, changing nothing; call it inside the order's own transaction so the
    order rolls back too. Not strict (a confirmed online payment): deduct what
    there is, flooring at 0, because the shopper has already paid.
    """
    with transaction.atomic():
        locked = Order.all_objects.select_for_update().filter(pk=order.pk).values_list(
            "stock_deducted", flat=True
        ).first()
        if locked:
            return
        short = _apply(order, sign=-1, strict=strict)
        if short and strict:
            raise OutOfStockError(shortage_message(short))
        if short:
            logger.warning("paid order %s oversold stock: %s", order.pk, short)
        Order.all_objects.filter(pk=order.pk).update(stock_deducted=True)
        order.stock_deducted = True
    _alert_after_deduct(order)


def release_for_order(order: Order) -> bool:
    """Give ``order``'s units back to stock, once (cancelled before dispatch,
    or returned to origin). Returns True when stock was restored."""
    try:
        with transaction.atomic():
            locked = Order.all_objects.select_for_update().filter(pk=order.pk).values_list(
                "stock_deducted", flat=True
            ).first()
            if not locked:
                return False
            _apply(order, sign=+1, strict=False)
            Order.all_objects.filter(pk=order.pk).update(stock_deducted=False)
            order.stock_deducted = False
        return True
    except Exception:
        logger.exception("restoring stock failed order=%s", order.pk)
        return False


def _alert_after_deduct(order: Order) -> None:
    """Bell the owner when an order leaves a product low / out of stock."""
    try:
        from commerce.views.catalog import _alert_low_stock

        pids = set(
            OrderItem.all_objects.filter(order_id=order.pk).values_list("product_id", flat=True)
        )
        for product in Product.all_objects.filter(pk__in=pids, site_id=order.site_id):
            _alert_low_stock(order.site, product, product.stock_on_hand)
    except Exception:
        pass


# Shipment states where the parcel has left the warehouse: a cancellation then
# doesn't put the units back yet; they return with the parcel (RTO).
_LEFT_WAREHOUSE = ("picked_up", "in_transit", "out_for_delivery", "delivery_failed", "delivered")


def release_if_not_dispatched(order: Order) -> bool:
    """Restock a cancelled order whose parcel never left the warehouse."""
    from commerce.models import Shipment

    if Shipment.all_objects.filter(order_id=order.pk, status__in=_LEFT_WAREHOUSE).exists():
        return False
    return release_for_order(order)
