"""Commerce fee price ranges: one place for how a range reads and is matched.

A plan's commerce fee is a list of price ranges, each with a flat rupee fee:
e.g. ₹0 – ₹299 → ₹9, ₹500 – ₹2,000 → ₹20. A product sold at a price inside a
range is charged that range's fee (₹150, ₹200 or ₹299 → ₹9; ₹1,200 → ₹20).

Ranges are stored as :class:`billing.models.CommerceFeeBand` rows (minimum
value, maximum value, fee). The older order-count Commission tiers are read as
price ranges too (:func:`tiers_as_bands`), so nothing is ever charged by order
count any more.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_TWO = Decimal("0.01")


def format_inr(value, *, symbol: bool = True) -> str:
    """``₹1,200`` / ``₹1,00,000`` / ``₹299.50``: Indian digit grouping, paise
    only when there are any. Blank for a value that is not a number."""
    try:
        amount = Decimal(str(value)).quantize(_TWO, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return ""
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    whole, _, paise = f"{amount:.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    text = whole if paise == "00" else f"{whole}.{paise}"
    return f"{sign}{'₹' if symbol else ''}{text}"


def range_label(min_value, max_value) -> str:
    """``₹0 – ₹299``, or ``₹2,000 and above`` for an open-ended range."""
    if max_value is None or max_value == "":
        return f"{format_inr(min_value or 0)} and above"
    return f"{format_inr(min_value or 0)} – {format_inr(max_value)}"


def tiers_as_bands(tiers) -> list[dict]:
    """Older order-count Commission tiers, read as price ranges.

    ``[{"min_orders", "max_orders", "per_order_fee"}]`` →
    ``[{"min_price", "max_price", "fee"}]``: the numbers the admin typed as the
    range's two ends become the minimum and maximum value of the range.
    """
    out = []
    for t in tiers or []:
        try:
            out.append({
                "min_price": Decimal(str(t.get("min_orders") or 0)),
                "max_price": (
                    Decimal(str(t["max_orders"])) if t.get("max_orders") not in (None, "") else None
                ),
                "fee": Decimal(str(t.get("per_order_fee") or 0)),
            })
        except (InvalidOperation, TypeError, ValueError, KeyError):
            continue
    return out
