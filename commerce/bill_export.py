"""Build the per-invoice commerce fee workbook (``.xlsx``) an owner downloads.

One bill → one styled worksheet: a titled header, a meta block, a per-order
table (order number, who ordered, what, where/when it delivered, and exactly how
much commerce fee OneWooz took on it), a total row, and a shaded **aggregate
block** that spells out the whole calculation  gross value, the commerce fee rate
applied (per the store's subscription/billing config), the total deducted, the
effective rate, the net left with the store, and the amount payable to OneWooz.

Uses the dependency-free writer in :mod:`commerce.xlsx`, so the download works
without any extra package installed.
"""

from __future__ import annotations

from decimal import Decimal

from commerce import xlsx

_ZERO = Decimal("0.00")

# Per-order table columns, in order.
_COLUMNS = [
    ("Order #", 11),
    ("Order user", 22),
    ("Customer", 22),
    ("Product(s)", 34),
    ("Category", 20),
    ("Delivery details", 34),
    ("Delivery date", 16),
    ("Order value (INR)", 16),
    ("Selling value (INR)", 16),
    ("Commerce fee basis", 34),
    ("Commerce fee (INR)", 18),
    ("Net after commerce fee (INR)", 22),
]
_LAST_COL = len(_COLUMNS) - 1
_NUMERIC_COLS = {7, 8, 10, 11}


def _fee_basis(charge, config, currency) -> str:
    """How one order's commerce fee was worked out, in words."""
    meta = getattr(charge, "meta", None) or {}
    basis = meta.get("basis")
    if basis == "selling_price":
        return "\n".join(
            f"{ln.get('title', '')}"
            + (f" ({ln.get('variant')})" if ln.get("variant") else "")
            + f": {ln.get('quantity')} x {currency} {ln.get('fee_per_unit')}"
            + (f" [{ln.get('band')}]" if ln.get("band") else "")
            for ln in meta.get("lines") or []
        ) or "By product price range"
    if basis == "order_tier":
        return f"{currency} {charge.amount} per order"
    if basis == "no_price_ranges":
        return "No price range on the subscription"
    # A charge from an older bill, worked out on the retired store-level rate.
    return f"{currency} {charge.amount} (earlier store rate)"


def _order_products(order) -> str:
    items = list(order.items.all())
    if not items:
        return ""
    return "\n".join(
        f"{it.title}"
        + (f" ({it.variant_label})" if it.variant_label else "")
        + (f" ×{it.quantity}" if it.quantity and it.quantity != 1 else "")
        for it in items
    )


def _order_categories(order) -> str:
    names, seen = [], set()
    for it in order.items.all():
        product = getattr(it, "product", None)
        category = getattr(product, "category", None) if product else None
        name = getattr(category, "name", "") if category else ""
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return ", ".join(names) if names else ""


def _delivery_details(order) -> str:
    bits = [
        order.shipping_address,
        order.shipping_city,
        order.shipping_state,
        order.shipping_pincode,
    ]
    line = ", ".join(b.strip() for b in bits if b and b.strip())
    if not line:
        line = f"Status: {order.get_status_display()}"
    if order.tracking_id:
        line += f"\nTracking: {order.tracking_id}"
    return line


def _delivery_date(order) -> str:
    if order.delivered_at:
        return order.delivered_at.strftime("%d %b %Y, %H:%M")
    if order.shipped_at:
        return f"Shipped {order.shipped_at:%d %b %Y}"
    return f"Not delivered ({order.get_status_display()})"


def render_bill_xlsx(*, bill, site, charges, config, owner_name: str, tax=None) -> bytes:
    """Return the ``.xlsx`` bytes for one bill's commerce fee breakdown."""
    from commerce import billing_service

    charges = list(charges)
    tax = tax or billing_service.bill_tax(bill, site)
    commission = billing_service.current_commission(site)
    currency = bill.currency or "INR"

    wb = xlsx.Workbook()
    ws = wb.add_sheet(f"Invoice {bill.invoice_number or bill.pk}"[:31])
    for col, (_, width) in enumerate(_COLUMNS):
        ws.set_column_width(col, width)

    # -- Title + subtitle --------------------------------------------------
    ws.write(0, 0, "OneWooz  Commerce fee statement", xlsx.STYLE_TITLE)
    ws.merge(0, 0, 0, _LAST_COL)
    ws.write(
        1, 0,
        f"How much commerce fee was deducted on each delivered order, and what you owe OneWooz.",
        xlsx.STYLE_SUBTITLE,
    )
    ws.merge(1, 0, 1, _LAST_COL)

    # -- Meta block (label / value pairs) ---------------------------------
    # The bill's total commerce fee (before GST); each order's fee follows the
    # product price ranges of the owner's subscription.
    rate_label = f"{currency} {tax['subtotal']:,.2f} total (by product price range)"
    meta = [
        ("Invoice", bill.invoice_number or str(bill.pk), "Billing period",
         f"{bill.period_start:%d %b %Y}  {bill.period_end:%d %b %Y}"),
        ("Store", site.name, "Commerce fee", rate_label),
        ("Account holder", owner_name or "", "Charged on",
         config.get_order_charge_event_display()),
        ("Status", bill.get_status_display(), "Generated",
         f"{bill.period_end:%d %b %Y}"),
    ]
    row = 3
    for left_k, left_v, right_k, right_v in meta:
        ws.write(row, 0, left_k, xlsx.STYLE_META_LABEL)
        ws.write(row, 1, left_v, xlsx.STYLE_META_VALUE)
        ws.merge(row, 1, row, 4)
        ws.write(row, 5, right_k, xlsx.STYLE_META_LABEL)
        ws.write(row, 6, right_v, xlsx.STYLE_META_VALUE)
        ws.merge(row, 6, row, _LAST_COL)
        row += 1

    # -- Table header ------------------------------------------------------
    header_row = row + 1  # one blank row above the table
    for col, (label, _) in enumerate(_COLUMNS):
        style = xlsx.STYLE_HEADER_NUM if col in _NUMERIC_COLS else xlsx.STYLE_HEADER
        ws.write(header_row, col, label, style)
    ws.freeze_row = header_row + 1

    # -- Per-order rows ----------------------------------------------------
    gross_total = _ZERO
    selling_total = _ZERO
    commission_total = _ZERO
    r = header_row + 1
    for charge in charges:
        order = charge.order
        order_total = Decimal(order.total or 0)
        selling = billing_service.selling_value_of(charge) or _ZERO
        fee = Decimal(charge.amount or 0)
        gross_total += order_total
        selling_total += selling
        commission_total += fee

        ws.write(r, 0, f"#{order.number}", xlsx.STYLE_CELL_CENTER)
        ws.write(r, 1, owner_name or "", xlsx.STYLE_CELL)
        ws.write(r, 2, order.customer_name or "", xlsx.STYLE_CELL)
        ws.write(r, 3, _order_products(order), xlsx.STYLE_CELL)
        ws.write(r, 4, _order_categories(order), xlsx.STYLE_CELL)
        ws.write(r, 5, _delivery_details(order), xlsx.STYLE_CELL)
        ws.write(r, 6, _delivery_date(order), xlsx.STYLE_CELL)
        ws.write(r, 7, order_total, xlsx.STYLE_CELL_MONEY)
        ws.write(r, 8, selling, xlsx.STYLE_CELL_MONEY)
        ws.write(r, 9, _fee_basis(charge, config, currency), xlsx.STYLE_CELL)
        ws.write(r, 10, fee, xlsx.STYLE_CELL_MONEY)
        ws.write(r, 11, order_total - fee, xlsx.STYLE_CELL_MONEY)
        r += 1

    if not charges:
        ws.write(r, 0, "No orders were charged in this billing period.", xlsx.STYLE_CELL)
        ws.merge(r, 0, r, _LAST_COL)
        r += 1

    # -- Table total row ---------------------------------------------------
    ws.write(r, 0, "TOTAL", xlsx.STYLE_TOTAL_LABEL)
    ws.merge(r, 0, r, 6)
    ws.write(r, 7, gross_total, xlsx.STYLE_TOTAL_MONEY)
    ws.write(r, 8, selling_total, xlsx.STYLE_TOTAL_MONEY)
    ws.write(r, 9, "", xlsx.STYLE_TOTAL_LABEL)
    ws.write(r, 10, commission_total, xlsx.STYLE_TOTAL_MONEY)
    ws.write(r, 11, gross_total - commission_total, xlsx.STYLE_TOTAL_MONEY)

    # -- Aggregate block ---------------------------------------------------
    net_total = gross_total - commission_total
    effective = (
        (commission_total / gross_total) if gross_total > 0 else _ZERO
    )
    agg_row = r + 2
    ws.write(agg_row, 0, "Summary", xlsx.STYLE_TITLE)
    ws.merge(agg_row, 0, agg_row, _LAST_COL)
    agg_row += 1

    def _agg(label, value, value_style):
        nonlocal agg_row
        label_style = (
            xlsx.STYLE_AGG_DUE_LABEL
            if value_style == xlsx.STYLE_AGG_DUE_MONEY
            else xlsx.STYLE_AGG_LABEL
        )
        ws.write(agg_row, 0, label, label_style)
        ws.merge(agg_row, 0, agg_row, 9)
        ws.write(agg_row, 10, value, value_style)
        ws.merge(agg_row, 10, agg_row, _LAST_COL)
        agg_row += 1

    _agg("Orders billed", len(charges), xlsx.STYLE_AGG_TEXT)
    _agg(f"Gross order value ({currency})", gross_total, xlsx.STYLE_AGG_MONEY)
    _agg(f"Selling value, before coupons ({currency})", selling_total, xlsx.STYLE_AGG_MONEY)
    _agg("Commerce fee (per subscription)", rate_label, xlsx.STYLE_AGG_TEXT)
    _agg(f"Total commerce fee ({currency})", commission_total, xlsx.STYLE_AGG_MONEY)
    if tax.get("applied"):
        if tax.get("intra"):
            _agg(f"CGST @ {tax['half_rate']:g}% ({currency})", tax["cgst"], xlsx.STYLE_AGG_MONEY)
            _agg(f"SGST @ {tax['half_rate']:g}% ({currency})", tax["sgst"], xlsx.STYLE_AGG_MONEY)
        else:
            _agg(f"IGST @ {tax['rate']:g}% ({currency})", tax["igst"], xlsx.STYLE_AGG_MONEY)
    _agg("Effective commerce fee rate", effective, xlsx.STYLE_AGG_PCT)
    _agg(f"Net value retained by store ({currency})", net_total, xlsx.STYLE_AGG_MONEY)
    _agg(f"Amount payable incl. GST ({currency})", tax["total"], xlsx.STYLE_AGG_DUE_MONEY)

    return wb.to_bytes()
