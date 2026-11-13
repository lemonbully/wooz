"""A customer's tax invoice for one storefront order.

Built for the shopper's account page (Invoice → a printable page) from the
order, its lines, the store's business & tax details (``Site.legal_name /
gstin / pan / msme_number``, set in Store settings) and the default warehouse
(the seller's address).

Tax: an order line does not store its GST, so each line's rate and inclusivity
are read from its product. A GST-inclusive price has the tax extracted
(``price × r/(100+r)``); an exclusive one has it added (``price × r/100``),
which is what checkout charged as ``Order.tax_amount``. The tax is split into
CGST + SGST when seller and buyer are in the same state (by GSTIN, else the
named state), otherwise IGST. The order's own totals stay authoritative; the
tax table is the breakup by rate.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

#: Orders a shopper can get an invoice for: delivered ones only. An order still
#: on its way (or cancelled) has no invoice yet, on the account page or by URL.
INVOICEABLE = frozenset({"delivered"})

_CENT = Decimal("0.01")


def _q(value) -> Decimal:
    return Decimal(value or 0).quantize(_CENT, rounding=ROUND_HALF_UP)


def can_invoice(order) -> bool:
    return str(getattr(order, "status", "")) in INVOICEABLE


def invoice_number(order) -> str:
    when = order.confirmed_at or order.created_at
    # A replacement gets its own invoice number: the original's, with RE.
    suffix = order.display_number[len(str(order.number)):] if getattr(order, "replacement_seq", 0) else ""
    return f"INV-{when:%Y%m}-{order.number:05d}{suffix}" if when else f"INV-{order.number:05d}{suffix}"


def _seller(site):
    from billing import gst

    from .models import Warehouse

    wh = (
        Warehouse.objects.for_site(site).filter(is_default=True).first()
        or Warehouse.objects.for_site(site).first()
    )
    address_parts = []
    if wh is not None:
        address_parts = [p for p in (wh.address_line, wh.city, wh.state, wh.pincode) if p]
    gstin = gst.normalise_gstin(getattr(site, "gstin", ""))
    state = gst.effective_state(wh.state if wh else "", gstin)
    return {
        "name": getattr(site, "legal_name", "") or site.name,
        "brand": site.name,
        "address": ", ".join(address_parts),
        "state": state,
        "gstin": gstin,
        "pan": getattr(site, "pan", ""),
        "msme": getattr(site, "msme_number", ""),
        "email": site.support_email or (wh.email if wh else ""),
        "phone": site.support_phone or (wh.phone if wh else ""),
    }


def build(order, site) -> dict:
    """Everything the invoice page renders."""
    from billing import gst

    seller = _seller(site)
    buyer_gstin = gst.normalise_gstin(order.gstin)
    buyer_state = gst.effective_state(order.shipping_state, buyer_gstin)
    intra = gst.is_intra_state(order.shipping_state, buyer_gstin, seller["state"], seller["gstin"])

    lines = []
    by_rate: dict[Decimal, dict] = {}
    items = list(order.items.all().select_related("product"))
    from commerce.variant_profiles import line_view

    for item in items:
        product = line_view(item)  # the line's variation GST / HSN, where it has its own
        rate = _q(getattr(product, "gst_percentage", None) or 0)
        inclusive = bool(getattr(product, "gst_inclusive", True))
        amount = _q(item.line_total)
        if rate > 0:
            if inclusive:
                taxable = _q(amount * Decimal(100) / (Decimal(100) + rate))
                tax = amount - taxable
            else:
                taxable = amount
                tax = _q(amount * rate / Decimal(100))
        else:
            taxable, tax = amount, Decimal("0.00")
        lines.append({
            "title": item.title,
            "variant": item.variant_label,
            "sku": item.sku,
            "hsn": getattr(product, "hsn_code", "") or "",
            "quantity": item.quantity,
            "unit_price": _q(item.unit_price),
            "rate": rate,
            "inclusive": inclusive,
            "taxable": taxable,
            "tax": tax,
            "amount": amount,
        })
        if rate > 0:
            row = by_rate.setdefault(rate, {"rate": rate, "taxable": Decimal("0.00"), "tax": Decimal("0.00")})
            row["taxable"] += taxable
            row["tax"] += tax

    tax_rows = []
    for rate in sorted(by_rate):
        row = by_rate[rate]
        if intra:
            half = _q(row["tax"] / 2)
            row.update({"cgst": half, "sgst": row["tax"] - half, "igst": Decimal("0.00"), "half_rate": _q(rate / 2)})
        else:
            row.update({"cgst": Decimal("0.00"), "sgst": Decimal("0.00"), "igst": row["tax"], "half_rate": _q(rate / 2)})
        tax_rows.append(row)
    tax_total = sum((r["tax"] for r in tax_rows), Decimal("0.00"))

    address = order.billing_address or order.shipping_address
    ship_parts = [p for p in (order.shipping_address, order.shipping_city, order.shipping_state, order.shipping_pincode) if p]
    return {
        "number": invoice_number(order),
        "date": order.delivered_at or order.confirmed_at or order.created_at,
        "order": order,
        "seller": seller,
        "buyer": {
            "name": order.customer_name,
            "company": order.company_name,
            "gstin": buyer_gstin,
            "email": order.customer_email,
            "phone": order.customer_phone,
            "billing_address": address,
            "shipping_address": ", ".join(ship_parts),
            "state": buyer_state,
        },
        "place_of_supply": buyer_state or order.shipping_state,
        "intra_state": intra,
        "lines": lines,
        "tax_rows": tax_rows,
        "tax_total": _q(tax_total),
        "tax_added": _q(order.tax_amount),
        "subtotal": _q(order.subtotal),
        "discount": _q(order.discount_amount) + _q(getattr(order, "credit_discount_amount", 0)),
        "shipping": _q(order.shipping_amount),
        "total": _q(order.total),
        "payment_method": order.payment_method,
        "currency": getattr(site, "currency", "INR") or "INR",
    }
