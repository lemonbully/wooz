"""Editing an order's lines from the dashboard, keeping the whole order true.

The order edit page lets the owner change a line's quantity or unit price,
remove a line, and add a product (a specific variation for products that have
them). Any such change re-derives everything that depends on the lines:

* **Totals**: subtotal, GST added on top for tax-exclusive products, the
  discount / store credit (capped at the new subtotal), shipping, and the total;
  the partial-COD balance and a UGC order's unsettled commission follow it.
* **Stock**: when the order's units were taken off stock
  (``Order.stock_deducted``), the old lines go back and the new ones are taken
  again, atomically; if the new quantities aren't in stock nothing changes.
* **Courier**: the caller pushes the result to Shiprocket
  (:func:`commerce.shipping.sync_order_edit`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.db import transaction

from commerce import stock
from commerce.cart import (
    _variant_label,
    _variant_price,
    _variant_sku,
    combos_of,
    variant_key_of,
)
from commerce.models import OrderItem, Product

MAX_QTY = 999
_ZERO = Decimal("0.00")
_CENT = Decimal("0.01")


class OrderEditError(Exception):
    """The edit can't be applied. The message is safe to show the owner."""


def _money(value) -> Decimal:
    try:
        return Decimal(str(value)).quantize(_CENT)
    except (InvalidOperation, TypeError, ValueError):
        return _ZERO


# ── The add-a-product picker ─────────────────────────────────────────────────

def product_choices(site) -> list[dict]:
    """Every sellable product, one option per stored variation, for the
    "Add item" picker: ``[{value: "<product id>|<variant key>", label}]``."""
    out = []
    qs = (
        Product.objects.for_site(site)
        .filter(is_active=True, is_deleted=False)
        .order_by("title")
    )
    for p in qs[:1000]:
        combos = combos_of(p)
        if not combos:
            out.append({"value": f"{p.pk}|", "label": f"{p.title} · {p.stock_on_hand} in stock"})
            continue
        for c in combos:
            try:
                left = max(0, int(c.get("inventory") or 0))
            except (TypeError, ValueError):
                left = 0
            label = _variant_label(c) or variant_key_of(c)
            out.append({"value": f"{p.pk}|{variant_key_of(c)}", "label": f"{p.title} ({label}) · {left} in stock"})
    return out


# ── Parsing the form into a plan ─────────────────────────────────────────────

@dataclass
class _LineEdit:
    item: OrderItem
    quantity: int
    unit_price: Decimal
    remove: bool = False


@dataclass
class _NewLine:
    product: Product
    combo: dict | None
    quantity: int


@dataclass
class EditPlan:
    edits: list = field(default_factory=list)
    additions: list = field(default_factory=list)

    @property
    def changed(self) -> bool:
        if self.additions:
            return True
        for e in self.edits:
            if e.remove or e.quantity != int(e.item.quantity or 0) or e.unit_price != _money(e.item.unit_price):
                return True
        return False


def _int(raw, default: int) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def build_plan(site, order, post) -> EditPlan:
    """Read the edit form. Raises :class:`OrderEditError` for an invalid edit."""
    plan = EditPlan()
    items = list(order.items.for_site(site).select_related("product"))
    for item in items:
        remove = (post.get(f"item_{item.id}_remove") or "") in ("1", "on", "true")
        q_raw = (post.get(f"item_{item.id}_quantity") or "").strip()
        p_raw = (post.get(f"item_{item.id}_unit_price") or "").strip()
        qty = _int(q_raw, int(item.quantity or 1)) if q_raw else int(item.quantity or 1)
        if qty < 1:
            remove = True
            qty = int(item.quantity or 1)
        price = _money(p_raw) if p_raw else _money(item.unit_price)
        if price < 0:
            price = _ZERO
        plan.edits.append(_LineEdit(item=item, quantity=min(qty, MAX_QTY), unit_price=price, remove=remove))

    # New lines: parallel lists add_product[] / add_qty[] ("<pid>|<variant key>").
    picks = post.getlist("add_product")
    qtys = post.getlist("add_qty")
    for i, value in enumerate(picks):
        value = (value or "").strip()
        if not value:
            continue
        pid, _, vkey = value.partition("|")
        product = (
            Product.objects.for_site(site)
            .filter(pk=pid.strip() or None, is_active=True, is_deleted=False)
            .first()
        )
        if product is None:
            raise OrderEditError("A product you tried to add no longer exists.")
        combo = None
        if combos_of(product):
            combo = next((c for c in combos_of(product) if variant_key_of(c) == vkey.strip()), None)
            if combo is None:
                raise OrderEditError(f"Choose which variation of “{product.title}” to add.")
        qty = max(1, min(MAX_QTY, _int(qtys[i] if i < len(qtys) else 1, 1)))
        plan.additions.append(_NewLine(product=product, combo=combo, quantity=qty))

    kept = sum(1 for e in plan.edits if not e.remove) + len(plan.additions)
    if kept == 0:
        raise OrderEditError("An order needs at least one item. To drop every item, cancel the order instead.")
    return plan


# ── Applying it ──────────────────────────────────────────────────────────────

def apply_plan(site, order, plan: EditPlan) -> None:
    """Apply the line edits, re-taking stock when the order holds stock.
    Raises :class:`OrderEditError` (and changes nothing) when the new lines
    aren't in stock."""
    if not plan.changed:
        return
    with transaction.atomic():
        held = bool(order.stock_deducted)
        if held:
            stock.release_for_order(order)

        # A price the owner typed always wins over a quantity-discount re-price.
        typed = {e.item.pk for e in plan.edits if e.unit_price != _money(e.item.unit_price)}

        for e in plan.edits:
            if e.remove:
                e.item.delete()
                continue
            e.item.quantity = e.quantity
            e.item.unit_price = e.unit_price
            e.item.line_total = (e.unit_price * e.quantity).quantize(_CENT)
            fields = ["quantity", "unit_price", "line_total"]
            if e.item.pk in typed and (e.item.quantity_discount or e.item.regular_unit_price is not None):
                # A hand-set price ends the quantity deal on this line for good.
                e.item.quantity_discount = {}
                e.item.regular_unit_price = None
                fields += ["quantity_discount", "regular_unit_price"]
            e.item.save(update_fields=fields)

        live = {
            (str(e.item.product_id), (e.item.variant_label or "").strip().lower()): e.item
            for e in plan.edits
            if not e.remove
        }
        for add in plan.additions:
            label = _variant_label(add.combo)
            price = _money(_variant_price(add.product, add.combo))
            key = (str(add.product.pk), label.strip().lower())
            existing = live.get(key)
            if existing is not None:
                # Same product + variation already on the order: grow that line.
                existing.quantity = min(MAX_QTY, int(existing.quantity or 0) + add.quantity)
                existing.line_total = (_money(existing.unit_price) * existing.quantity).quantize(_CENT)
                existing.save(update_fields=["quantity", "line_total"])
                continue
            live[key] = OrderItem.objects.create(
                site=site,
                order=order,
                product=add.product,
                title=add.product.title,
                variant_label=label,
                sku=(_variant_sku(add.product, add.combo) or "")[:100],
                quantity=add.quantity,
                unit_price=price,
                line_total=(price * add.quantity).quantize(_CENT),
            )

        # Lines sold at a quantity price follow the deal frozen on them: drop
        # below a tier and the discount goes, reach a higher one and it grows.
        from commerce import quantity_pricing as qp

        current = list(order.items.for_site(site).all())
        for it in qp.reprice_order_items(current, keep_ids=typed):
            it.save(update_fields=["unit_price", "line_total", "quantity_discount"])

        if held:
            try:
                stock.deduct_for_order(order, strict=True)
            except stock.OutOfStockError as exc:
                # Roll every line (and the released stock) back.
                raise OrderEditError(str(exc).replace("Please update your cart to continue.", "Lower the quantity or restock first."))


def recompute_totals(site, order, *, override_total: str = "") -> None:
    """Re-derive the order's money from its current lines (sets the fields;
    the caller saves). ``override_total`` (a typed amount) wins when given."""
    items = list(order.items.for_site(site).select_related("product"))
    subtotal = sum((_money(i.line_total) for i in items), _ZERO)

    from commerce.variant_profiles import line_view

    tax = _ZERO
    for i in items:
        p = line_view(i)  # the line's variation GST, where it has its own
        if p is None or getattr(p, "gst_inclusive", True) or not getattr(p, "gst_percentage", None):
            continue
        tax += _money(i.line_total) * (_money(p.gst_percentage) / Decimal("100"))
    tax = tax.quantize(_CENT)

    discount = min(_money(order.discount_amount), subtotal)
    credit = min(_money(order.credit_discount_amount), subtotal - discount)
    total = max(_ZERO, subtotal - discount - credit) + tax + _money(order.shipping_amount)

    override = (override_total or "").strip()
    if override:
        typed = _money(override)
        if typed >= 0:
            total = typed

    order.subtotal = subtotal
    order.discount_amount = discount
    order.credit_discount_amount = credit
    order.tax_amount = tax
    order.total = total.quantize(_CENT)

    # The COD balance of a part-paid order is whatever the online part didn't cover.
    if order.is_partial_payment:
        order.partial_cod_amount = max(_ZERO, order.total - _money(order.partial_paid_amount))
    # A UGC order's commission follows the new total until it has been credited.
    if order.collaboration_id and not order.collaboration_credited and order.collaboration is not None:
        order.commission_amount = order._commission_for(order.collaboration)
