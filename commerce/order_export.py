"""Monthly orders export: one styled ``.xlsx`` per calendar month.

The owner downloads a month's orders from the Orders page, one month at a time
(never in bulk). A month becomes available on the **2nd of the following month**
(so the month is complete and late status changes have settled), and the last
**three years** of months can be downloaded.

The sheet opens with a KPI block for the orders placed in the month: delivered
orders and their revenue, cancelled orders and RTO orders (revenue negative),
replacements and the value re-sent, store credit given and used.

Then one row per order line: order id and date, product name, variation, SKU,
quantity, MRP, selling price, amount payable (quantity x selling price, a
live formula), then the order-level shipping, discount, store credit and amount
received
(on the order's first line only, so columns add up without double counting),
GST (rate + amount, split CGST/SGST within the seller's state, IGST otherwise),
billing city and state, payment method and the order status. A TOTAL row under
the table sums every amount column (following the state filter) (RTO for a returned parcel, "Replacement (...)" for
a replacement). A replacement order (see :mod:`commerce.order_requests`) is
shown as two blocks with the same order id: the original order's lines tagged
*Original*, directly followed by the replacement's lines tagged *Replacement*.

Rows have a fixed height (a long product title stays on one line) and the
Billing state header carries an Excel filter. The download can also be narrowed to one
billing state (``state``).

Uses the dependency-free writer in :mod:`commerce.xlsx`.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

from commerce import xlsx
from commerce.models import Order, OrderItem

#: A month's export opens on this day of the following month.
AVAILABLE_DAY = 2
#: How far back exports go, in months (three years).
HISTORY_MONTHS = 36

_CENT = Decimal("0.01")
_ZERO = Decimal("0.00")

_COLUMNS = [
    ("Order ID", 12),
    ("Order date", 18),
    ("Line type", 13),
    ("Product name", 34),
    ("Variation", 22),
    ("SKU ID", 18),
    ("Quantity", 10),
    ("MRP (INR)", 13),
    ("Selling price (INR)", 15),
    ("Amount Payable (INR)", 17),
    ("Shipping amount (INR)", 15),
    ("Discount (INR)", 13),
    ("Store credit used (INR)", 15),
    ("Amount Received (INR)", 18),
    ("GST (%)", 9),
    ("GST amount (INR)", 14),
    ("CGST (INR)", 12),
    ("SGST (INR)", 12),
    ("IGST (INR)", 12),
    ("Billing city", 18),
    ("Billing state", 18),
    ("Payment", 20),
    ("Order status", 22),
]
#: Last column the owner sees; the KPI helper columns follow it, hidden.
_LAST_COL = len(_COLUMNS) - 1
# Hidden helper columns the KPI formulas read: per KPI, a count and an amount,
# filled on the first line of each order placed in the month. SUBTOTAL(109, …)
# skips rows the Billing state filter hides, so the KPIs follow the filter.
_KPI_BUCKETS = ("delivered", "cancelled", "rto", "replacement", "credit")
_KPI_COL = {
    bucket: (len(_COLUMNS) + 2 * i, len(_COLUMNS) + 2 * i + 1)
    for i, bucket in enumerate(_KPI_BUCKETS)
}
_LABELS = [label for label, _ in _COLUMNS]
_STATE_COL = _LABELS.index("Billing state")
# Column positions, by name, so a new column never shifts a write by mistake.
C_ORDER, C_DATE, C_TYPE, C_PRODUCT, C_VARIATION, C_SKU = range(6)
C_QTY = _LABELS.index("Quantity")
C_MRP = _LABELS.index("MRP (INR)")
C_PRICE = _LABELS.index("Selling price (INR)")
C_LINE_TOTAL = _LABELS.index("Amount Payable (INR)")
C_SHIPPING = _LABELS.index("Shipping amount (INR)")
C_DISCOUNT = _LABELS.index("Discount (INR)")
C_CREDIT = _LABELS.index("Store credit used (INR)")
C_ORDER_TOTAL = _LABELS.index("Amount Received (INR)")
C_GST_RATE = _LABELS.index("GST (%)")
C_GST = _LABELS.index("GST amount (INR)")
C_CGST = _LABELS.index("CGST (INR)")
C_SGST = _LABELS.index("SGST (INR)")
C_IGST = _LABELS.index("IGST (INR)")
C_CITY = _LABELS.index("Billing city")
C_PAYMENT = _LABELS.index("Payment")
C_STATUS = _LABELS.index("Order status")
_MONEY_COLS = {
    C_MRP, C_PRICE, C_LINE_TOTAL, C_SHIPPING, C_DISCOUNT, C_CREDIT,
    C_ORDER_TOTAL, C_GST, C_CGST, C_SGST, C_IGST,
}
_INT_COLS = {C_QTY}
_NUM_COLS = _MONEY_COLS | _INT_COLS | {C_GST_RATE}
#: Columns the table's TOTAL row adds up (only the rows the filter shows).
_SUM_COLS = (
    C_QTY, C_LINE_TOTAL, C_SHIPPING, C_DISCOUNT, C_CREDIT, C_ORDER_TOTAL,
    C_GST, C_CGST, C_SGST, C_IGST,
)


def _q(value) -> Decimal:
    return Decimal(value or 0).quantize(_CENT, rounding=ROUND_HALF_UP)


def _shift(first: date, months: int) -> date:
    """``first`` (a 1st of month) moved by ``months``."""
    idx = first.year * 12 + (first.month - 1) + months
    return date(idx // 12, idx % 12 + 1, 1)


def month_key(first: date) -> str:
    return f"{first:%Y-%m}"


def parse_month(key: str) -> date | None:
    try:
        return datetime.strptime((key or "").strip(), "%Y-%m").date().replace(day=1)
    except ValueError:
        return None


def available_on(first: date) -> date:
    """The day a month's export opens: the 2nd of the following month."""
    return _shift(first, 1).replace(day=AVAILABLE_DAY)


def months_for(site, today: date | None = None) -> dict:
    """The months the Orders page offers.

    ``{"available": [{key, label, first}], "next": {label, opens}}``  newest
    first, the last :data:`HISTORY_MONTHS` months that have opened and that the
    store existed in; ``next`` is the month that opens next (shown greyed out).
    """
    today = today or timezone.localdate()
    this_month = today.replace(day=1)
    oldest = _shift(this_month, -HISTORY_MONTHS)
    created = getattr(site, "created_at", None)
    if created is not None:
        started = timezone.localtime(created).date().replace(day=1)
        oldest = max(oldest, started)
    out = []
    first = _shift(this_month, -1)
    while first >= oldest:
        if available_on(first) <= today:
            out.append({"key": month_key(first), "label": f"{first:%B %Y}", "first": first})
        first = _shift(first, -1)
    pending = _shift(this_month, -1)
    if available_on(pending) > today:
        upcoming = pending
    else:
        upcoming = this_month
    return {
        "available": out,
        "next": {"label": f"{upcoming:%B %Y}", "opens": available_on(upcoming)},
    }


def is_available(site, key: str, today: date | None = None) -> bool:
    return any(m["key"] == key for m in months_for(site, today)["available"])


def _month_window(first: date):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(first, time.min), tz)
    end = timezone.make_aware(datetime.combine(_shift(first, 1), time.min), tz)
    return start, end


def line_mrp(item) -> Decimal | None:
    """The MRP of the product variation a line was sold as (else the product's)."""
    from commerce.cart import _norm, _variant_label, _variant_mrp, combos_of

    product = getattr(item, "product", None)
    if product is None:
        return None
    combo = None
    label = (getattr(item, "variant_label", "") or "").strip()
    if label:
        want = _norm(label)
        for c in combos_of(product):
            if _norm(_variant_label(c)) == want:
                combo = c
                break
    mrp = _variant_mrp(product, combo)
    try:
        return _q(mrp) if mrp not in (None, "") else None
    except Exception:
        return None


def _line_tax(item, intra: bool) -> dict:
    """GST on one line, the way the tax invoice works it out: extracted from an
    inclusive price, added on top of an exclusive one."""
    from commerce.variant_profiles import line_view

    product = line_view(item)  # the line's variation GST, where it has its own
    rate = _q(getattr(product, "gst_percentage", None) or 0) if product else _ZERO
    inclusive = bool(getattr(product, "gst_inclusive", True)) if product else True
    amount = _q(item.line_total)
    if rate > 0:
        if inclusive:
            taxable = _q(amount * Decimal(100) / (Decimal(100) + rate))
            tax = amount - taxable
        else:
            tax = _q(amount * rate / Decimal(100))
    else:
        tax = _ZERO
    if intra:
        cgst = _q(tax / 2)
        sgst, igst = tax - cgst, _ZERO
    else:
        cgst, sgst, igst = _ZERO, _ZERO, tax
    return {"rate": rate, "tax": tax, "cgst": cgst, "sgst": sgst, "igst": igst}


def states_for(site) -> list[str]:
    """The billing states the store's orders were shipped to, for the export's
    State filter: one entry per state whatever its spelling case, A-Z."""
    seen: dict[str, str] = {}
    raw = (
        Order.objects.for_site(site)
        .exclude(shipping_state="")
        .order_by()
        .values_list("shipping_state", flat=True)
        .distinct()[:500]
    )
    for value in raw:
        name = " ".join(str(value or "").split())
        if name and name.lower() not in seen:
            seen[name.lower()] = name.title() if name.islower() or name.isupper() else name
    return sorted(seen.values(), key=str.lower)


def clean_state(value: str) -> str:
    return " ".join(str(value or "").split())[:100]


def _ordered_orders(site, first: date, state: str = "") -> tuple[list[tuple[Order, str]], list[Order]]:
    """The month's orders in export order, each with its line type, and the
    orders placed in the month (what the KPIs count).

    Orders placed in the month (only those shipped to ``state`` when given),
    oldest first. An order with replacements is "Original" and its
    replacements follow it straight away; a replacement created this month for
    an order from an earlier month is preceded by that original, so the two
    always read together.
    """
    start, end = _month_window(first)
    base = Order.objects.for_site(site)
    month_qs = base.filter(created_at__gte=start, created_at__lt=end)
    if state:
        month_qs = month_qs.filter(shipping_state__iexact=state)
    in_month = list(month_qs.order_by("created_at", "number", "replacement_seq"))
    ids = {o.pk for o in in_month}
    replacements: dict = {}
    for r in base.filter(replacement_of__in=[o.pk for o in in_month] + [
        o.replacement_of_id for o in in_month if o.replacement_of_id
    ]).order_by("replacement_seq"):
        replacements.setdefault(r.replacement_of_id, []).append(r)
    outside_originals = {
        o.pk: o
        for o in base.filter(pk__in=[r.replacement_of_id for r in in_month if r.replacement_of_id and r.replacement_of_id not in ids])
    }

    out: list[tuple[Order, str]] = []
    done = set()
    for o in in_month:
        if o.pk in done:
            continue
        if o.replacement_seq:
            original = o.replacement_of if o.replacement_of_id in ids else outside_originals.get(o.replacement_of_id)
            if original is not None and original.pk not in done:
                out.append((original, "Original"))
                done.add(original.pk)
                for r in replacements.get(original.pk, []):
                    if r.pk in ids and r.pk not in done:
                        out.append((r, "Replacement"))
                        done.add(r.pk)
            if o.pk not in done:
                out.append((o, "Replacement"))
                done.add(o.pk)
            continue
        own = [r for r in replacements.get(o.pk, [])]
        out.append((o, "Original" if own else "Order"))
        done.add(o.pk)
        for r in own:
            if r.pk not in done:
                out.append((r, "Replacement"))
                done.add(r.pk)
    return out, in_month


def _rto_order_ids(site, order_ids) -> set:
    """Orders whose parcel the courier returned to the seller (RTO), the same
    rule as the Orders page's RTO tab."""
    from commerce.models import Shipment

    if not order_ids:
        return set()
    return set(
        Shipment.objects.for_site(site)
        .filter(order_id__in=list(order_ids), status=Shipment.Status.RTO)
        .order_by()
        .values_list("order_id", flat=True)
    )


def _status_label(order, rto_ids: set, replaced_ids: set) -> str:
    """The order's status as the owner knows it: RTO for a returned parcel,
    "Replacement (…)" for a replacement order, "(Replaced)" on an original
    that has been replaced; otherwise the dashboard's status name."""
    if order.pk in rto_ids:
        base = "RTO"
    elif order.status == Order.Status.CREATED:
        base = "Pending"
    else:
        base = order.get_status_display()
    if order.replacement_seq:
        return f"Replacement ({base})"
    if order.pk in replaced_ids:
        return f"{base} (Replaced)"
    return base


def _credit_used(order) -> Decimal:
    """What store credit took off ``order`` and was not given back."""
    usage = [e for e in (order.credit_usage or []) if isinstance(e, dict)]
    if usage:
        total = _ZERO
        for entry in usage:
            if entry.get("booked") and not entry.get("restored"):
                total += _q(entry.get("used") if entry.get("used") not in (None, "") else entry.get("amount"))
        return total
    # Orders from before per-credit usage was recorded.
    if order.status != Order.Status.CANCELLED:
        return _q(order.credit_discount_amount)
    return _ZERO


def _kpi_parts(site, first: date, in_month: list, rto_ids: set):
    """What each order placed in the month adds to the KPIs, and the store
    credit granted in the month.

    Returns ``(per_order, given)``: ``per_order[order_id]`` maps a KPI bucket
    to ``(count, amount)``; ``given`` is ``(count, amount, percent_count)``.
    Buckets: delivered / cancelled / RTO (ordinary orders, revenue = order
    total, cancelled and RTO negative), replacement (value = the original
    order's total, since a replacement is sent at INR 0) and credit (store
    credit the order used and kept).
    """
    from commerce.models import CustomerCredit

    replacements = [o for o in in_month if o.replacement_seq]
    original_ids = {o.replacement_of_id for o in replacements if o.replacement_of_id}
    originals = {
        o.pk: o for o in Order.objects.for_site(site).filter(pk__in=list(original_ids)).order_by()
    } if original_ids else {}

    per_order: dict = {}
    for o in in_month:
        parts = {}
        total = _q(o.total)
        if o.replacement_seq:
            original = originals.get(o.replacement_of_id)
            parts["replacement"] = (1, _q(original.total) if original is not None else total)
        elif o.pk in rto_ids:
            parts["rto"] = (1, -total if total else _ZERO)
        elif o.status == Order.Status.DELIVERED:
            parts["delivered"] = (1, total)
        elif o.status == Order.Status.CANCELLED:
            parts["cancelled"] = (1, -total if total else _ZERO)
        used = _credit_used(o)
        if used > 0:
            parts["credit"] = (1, used)
        per_order[o.pk] = parts

    start, end = _month_window(first)
    granted = list(
        CustomerCredit.objects.for_site(site)
        .filter(created_at__gte=start, created_at__lt=end)
        .order_by()
        .only("credit_type", "credit_value")
    )
    given_amount = sum(
        (_q(c.credit_value) for c in granted if c.credit_type == CustomerCredit.CreditType.AMOUNT),
        _ZERO,
    )
    given_percent = sum(1 for c in granted if c.credit_type == CustomerCredit.CreditType.PERCENT)
    return per_order, (len(granted), given_amount, given_percent)


#: KPI rows: (label, bucket). ``None`` = store credit granted (not per order).
_KPI_ROWS = (
    ("Delivered orders · revenue", "delivered"),
    ("Cancelled orders · revenue lost (negative)", "cancelled"),
    ("RTO orders (returned to you) · revenue lost (negative)", "rto"),
    ("Replacements · value of the original orders re-sent at INR 0", "replacement"),
    ("Store credits given (granted this month, all states)", None),
    ("Store credits utilized (orders · credit used)", "credit"),
)


# Fixed row heights (points), so a long product title never makes a row grow.
_ROW_H = 18
_HEADER_H = 32
_TITLE_H = 26


def render_month_xlsx(site, first: date, state: str = "") -> bytes:
    """The ``.xlsx`` bytes for ``site``'s orders placed in the month of
    ``first`` (only those shipped to ``state`` when given)."""
    from billing import gst

    from commerce.cart import line_sku
    from commerce.order_invoice import _seller
    from commerce.templatetags.store_extras import payment_tag

    state = clean_state(state)
    seller = _seller(site)
    orders, in_month = _ordered_orders(site, first, state)
    order_ids = [o.pk for o, _ in orders]
    items_by_order: dict = {}
    for it in (
        OrderItem.objects.for_site(site)
        .filter(order__in=order_ids)
        .select_related("product")
        .order_by("created_at")
    ):
        items_by_order.setdefault(it.order_id, []).append(it)
    rto_ids = _rto_order_ids(site, order_ids)
    replaced_ids = set(
        Order.objects.for_site(site)
        .filter(replacement_of_id__in=order_ids)
        .order_by()
        .values_list("replacement_of_id", flat=True)
    )

    wb = xlsx.Workbook()
    ws = wb.add_sheet(f"Orders {first:%b %Y}"[:31])
    for col, (_, width) in enumerate(_COLUMNS):
        ws.set_column_width(col, width)

    scope = state or "All states"
    ws.write(0, 0, f"{site.name}: orders for {first:%B %Y}" + (f" · {state}" if state else ""), xlsx.STYLE_TITLE)
    ws.merge(0, 0, 0, _LAST_COL)
    ws.set_row_height(0, _TITLE_H)
    ws.write(
        1, 0,
        "One row per product line. Amount Payable = Quantity x Selling price. Order-level amounts (shipping, discount, "
        "store credit, amount received) appear once, on the order's first line, so every column adds up without double counting. "
        "Amount Received = amounts payable - discount - store credit + GST added on GST-exclusive prices + shipping. "
        "A replacement shows the original order's lines, then the replacement's, under the same order ID. "
        "Use the filter on the Billing state header to show one state's orders; the key figures and the TOTAL row follow it.",
        xlsx.STYLE_SUBTITLE,
    )
    ws.merge(1, 0, 1, _LAST_COL)
    ws.set_row_height(1, _ROW_H)
    meta = [
        ("Store", site.name, "Month", f"{first:%B %Y}"),
        ("Orders", str(len({o.pk for o, _ in orders})), "State", scope),
        ("Generated", f"{timezone.localtime():%d %b %Y, %H:%M}", "", ""),
    ]
    row = 3
    for left_k, left_v, right_k, right_v in meta:
        ws.write(row, 0, left_k, xlsx.STYLE_META_LABEL)
        ws.write(row, 1, left_v, xlsx.STYLE_META_VALUE)
        ws.merge(row, 1, row, 5)
        if right_k:
            ws.write(row, 6, right_k, xlsx.STYLE_META_LABEL)
            ws.write(row, 7, right_v, xlsx.STYLE_META_VALUE)
            ws.merge(row, 7, row, _LAST_COL)
        ws.set_row_height(row, _ROW_H)
        row += 1

    # ── KPIs (orders placed this month): formulas over the hidden helper
    # columns, filled in once the table below is written ──
    per_order, given = _kpi_parts(site, first, in_month, rto_ids)
    row += 1
    ws.write(row, 0, "Key figures", xlsx.STYLE_HEADER)
    ws.merge(row, 0, row, 3)
    ws.write(row, 4, "Count", xlsx.STYLE_HEADER_NUM)
    ws.write(row, 5, "Amount (INR)", xlsx.STYLE_HEADER_NUM)
    ws.set_row_height(row, _ROW_H + 2)
    row += 1
    kpi_first_row = row
    row += len(_KPI_ROWS)
    ws.write(
        row, 0,
        f"Counts orders placed in {first:%B %Y}" + (f" shipped to {state}" if state else "")
        + ". The figures follow the Billing state filter below. Revenue is the amount received; "
        "cancelled and RTO revenue is shown negative. Store credits given are for the whole store.",
        xlsx.STYLE_SUBTITLE,
    )
    ws.merge(row, 0, row, _LAST_COL)
    ws.set_row_height(row, _ROW_H)
    row += 1

    header = row + 1
    for col, (label, _) in enumerate(_COLUMNS):
        ws.write(header, col, label, xlsx.STYLE_HEADER_NUM if col in _NUM_COLS else xlsx.STYLE_HEADER)
    for bucket, (c_count, c_amount) in _KPI_COL.items():
        ws.write(header, c_count, f"KPI {bucket} count", xlsx.STYLE_HEADER_NUM)
        ws.write(header, c_amount, f"KPI {bucket} amount", xlsx.STYLE_HEADER_NUM)
        ws.hide_column(c_count)
        ws.hide_column(c_amount)
    ws.set_row_height(header, _HEADER_H)
    # No frozen pane: the KPI block sits above the table, and freezing at the
    # header row would pin all of it, leaving only a sliver of the sheet to
    # scroll. The whole sheet scrolls; the header row carries the filters.

    T = xlsx.STYLE_CELL_NOWRAP
    r = header + 1
    sums = {col: _ZERO for col in _SUM_COLS}
    for order, line_type in orders:
        buyer_gstin = gst.normalise_gstin(order.gstin)
        intra = gst.is_intra_state(order.shipping_state, buyer_gstin, seller["state"], seller["gstin"])
        status = _status_label(order, rto_ids, replaced_ids)
        payment = payment_tag(order)["label"]
        placed = timezone.localtime(order.created_at)
        lines = items_by_order.get(order.pk, [])
        if not lines:
            lines = [None]
        for idx, item in enumerate(lines):
            excel_row = r + 1  # 1-based row number, for formulas
            ws.set_row_height(r, _ROW_H)
            ws.write(r, C_ORDER, f"#{order.display_number}", xlsx.STYLE_CELL_CENTER_NOWRAP)
            ws.write(r, C_DATE, f"{placed:%d %b %Y, %H:%M}", T)
            ws.write(r, C_TYPE, line_type, T)
            if item is not None:
                tax = _line_tax(item, intra)
                mrp = line_mrp(item)
                qty = int(item.quantity or 0)
                price = _q(item.unit_price)
                ws.write(r, C_PRODUCT, item.title, T)
                ws.write(r, C_VARIATION, item.variant_label or "", T)
                ws.write(r, C_SKU, line_sku(item), T)
                ws.write(r, C_QTY, qty, xlsx.STYLE_CELL_INT)
                ws.write(r, C_MRP, mrp if mrp is not None else "", xlsx.STYLE_CELL_MONEY)
                ws.write(r, C_PRICE, price, xlsx.STYLE_CELL_MONEY)
                # A live formula, so an auditor sees the line total is exactly
                # quantity x selling price.
                ql, pl = xlsx._col_letter(C_QTY), xlsx._col_letter(C_PRICE)
                ws.write(
                    r, C_LINE_TOTAL,
                    xlsx.Formula(f"ROUND({ql}{excel_row}*{pl}{excel_row},2)", _q(price * qty)),
                    xlsx.STYLE_CELL_MONEY,
                )
                ws.write(r, C_GST_RATE, tax["rate"], xlsx.STYLE_CELL_MONEY)
                ws.write(r, C_GST, tax["tax"], xlsx.STYLE_CELL_MONEY)
                ws.write(r, C_CGST, tax["cgst"], xlsx.STYLE_CELL_MONEY)
                ws.write(r, C_SGST, tax["sgst"], xlsx.STYLE_CELL_MONEY)
                ws.write(r, C_IGST, tax["igst"], xlsx.STYLE_CELL_MONEY)
                line_sums = {
                    C_QTY: Decimal(qty), C_LINE_TOTAL: _q(price * qty), C_GST: tax["tax"],
                    C_CGST: tax["cgst"], C_SGST: tax["sgst"], C_IGST: tax["igst"],
                }
            else:
                for col in (C_PRODUCT, C_VARIATION, C_SKU):
                    ws.write(r, col, "", T)
                for col in (C_QTY, C_MRP, C_PRICE, C_LINE_TOTAL, C_GST_RATE, C_GST, C_CGST, C_SGST, C_IGST):
                    ws.write(r, col, "", xlsx.STYLE_CELL_MONEY)
                line_sums = {}
            # Order-level amounts are shown once, on the order's first line,
            # so adding up a column never counts an order twice.
            first_line = idx == 0
            if first_line:
                order_level = {
                    C_SHIPPING: _q(order.shipping_amount),
                    C_DISCOUNT: _q(order.discount_amount),
                    C_CREDIT: _q(order.credit_discount_amount),
                    C_ORDER_TOTAL: _q(order.total),
                }
                for col, value in order_level.items():
                    ws.write(r, col, value, xlsx.STYLE_CELL_MONEY)
                line_sums.update(order_level)
            else:
                for col in (C_SHIPPING, C_DISCOUNT, C_CREDIT, C_ORDER_TOTAL):
                    ws.write(r, col, "", xlsx.STYLE_CELL_MONEY)
            for col, value in line_sums.items():
                sums[col] += value
            ws.write(r, C_CITY, order.shipping_city or "", T)
            ws.write(r, _STATE_COL, order.shipping_state or "", T)
            ws.write(r, C_PAYMENT, payment, T)
            ws.write(r, C_STATUS, status, T)
            # What this order adds to the KPIs, once (on its first line).
            parts = per_order.get(order.pk, {}) if first_line else {}
            for bucket, (c_count, c_amount) in _KPI_COL.items():
                count, amount = parts.get(bucket, (0, _ZERO))
                ws.write(r, c_count, count, xlsx.STYLE_CELL_INT)
                ws.write(r, c_amount, amount, xlsx.STYLE_CELL_MONEY)
            r += 1

    # TOTAL row under the table. SUBTOTAL(109, ...) adds only the rows the
    # Billing state filter shows, so the totals follow the filter too.
    first_data, last_data = header + 1, r - 1
    if orders:
        ws.set_row_height(r, _ROW_H + 2)
        for col in range(len(_COLUMNS)):
            if col in _SUM_COLS:
                letter = xlsx._col_letter(col)
                if col == C_QTY:
                    cached, style = int(sums[col]), xlsx.STYLE_AGG_INT
                else:
                    cached, style = _q(sums[col]), xlsx.STYLE_TOTAL_MONEY
                ws.write(
                    r, col,
                    xlsx.Formula(f"SUBTOTAL(109,{letter}{first_data + 1}:{letter}{last_data + 1})", cached),
                    style,
                )
            else:
                ws.write(r, col, "TOTAL" if col == C_ORDER else "", xlsx.STYLE_TOTAL_LABEL)

    # KPI rows. SUBTOTAL(109, …) adds only the rows still visible, so picking
    # a state in the Billing state filter re-totals every figure.
    for i, (label, bucket) in enumerate(_KPI_ROWS):
        krow = kpi_first_row + i
        ws.write(krow, 0, label, xlsx.STYLE_AGG_LABEL)
        ws.merge(krow, 0, krow, 3)
        if bucket is None:
            given_count, given_amount, given_percent = given
            if given_percent:
                ws.write(
                    krow, 0,
                    f"{label}: {given_percent} percentage credit{'s' if given_percent != 1 else ''} counted, no fixed value",
                    xlsx.STYLE_AGG_LABEL,
                )
            ws.write(krow, 4, int(given_count), xlsx.STYLE_AGG_INT)
            ws.write(krow, 5, given_amount, xlsx.STYLE_AGG_MONEY)
        else:
            c_count, c_amount = _KPI_COL[bucket]
            cached_count = sum(p.get(bucket, (0, _ZERO))[0] for p in per_order.values())
            cached_amount = sum((p.get(bucket, (0, _ZERO))[1] for p in per_order.values()), _ZERO)
            if orders:
                cl, al = xlsx._col_letter(c_count), xlsx._col_letter(c_amount)
                ws.write(krow, 4, xlsx.Formula(f"SUBTOTAL(109,{cl}{first_data + 1}:{cl}{last_data + 1})", cached_count), xlsx.STYLE_AGG_INT)
                ws.write(krow, 5, xlsx.Formula(f"SUBTOTAL(109,{al}{first_data + 1}:{al}{last_data + 1})", cached_amount), xlsx.STYLE_AGG_MONEY)
            else:
                ws.write(krow, 4, 0, xlsx.STYLE_AGG_INT)
                ws.write(krow, 5, _ZERO, xlsx.STYLE_AGG_MONEY)
        ws.set_row_height(krow, _ROW_H + 2)

    if orders:
        # One Excel filter drop-down, on Billing state only: picking a state
        # hides every other order's rows (and the KPIs above re-total).
        ws.auto_filter = (header, _STATE_COL, last_data, _STATE_COL)
    else:
        where = f" shipped to {state}" if state else ""
        ws.write(r, 0, f"No orders were placed in {first:%B %Y}{where}.", T)
        ws.merge(r, 0, r, _LAST_COL)
        ws.set_row_height(r, _ROW_H)

    return wb.to_bytes()
