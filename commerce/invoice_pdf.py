"""Render a commerce fee :class:`~commerce.models.Bill` to a PDF tax invoice.

A structured, print-ready invoice built with ReportLab's platypus layout:
the billing company (from the admin's "Commerce fee bill details"), the store
billed, invoice metadata, a line-item table of every per-order commerce fee
charge, the GST on the fee (CGST + SGST within a state, IGST across states),
the amount due and how to pay it. Currency is shown as its ISO code (e.g.
``INR 1,234.00``) rather than a symbol so it renders correctly in the core PDF
fonts on every platform.

The single entry point is :func:`render_bill_pdf`, which returns the PDF bytes;
the caller stores them on ``bill.invoice_pdf`` or streams them to the browser.
"""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Palette mirrors the dashboard: one accent, warm neutrals, hairline rules.
_ACCENT = colors.HexColor("#4b2bf5")
_INK = colors.HexColor("#0a0a0b")
_INK2 = colors.HexColor("#26262a")
_MUTED = colors.HexColor("#66666b")
_FAINT = colors.HexColor("#9a9aa0")
_LINE = colors.HexColor("#d9d9d6")
_LINE_SOFT = colors.HexColor("#efefec")
_ZEBRA = colors.HexColor("#faf9f7")


def _money(currency: str, amount) -> str:
    return f"{currency} {Decimal(amount or 0):,.2f}"


def _txt(value) -> str:
    """Escape for a Paragraph and swap the rupee sign (not in the core fonts)."""
    return escape(str(value or "")).replace("₹", "INR ")


def _styles() -> dict:
    return {
        "brand": ParagraphStyle("brand", fontName="Helvetica-Bold", fontSize=17, textColor=_INK, leading=20),
        "brandsub": ParagraphStyle("brandsub", fontName="Helvetica", fontSize=8.5, textColor=_MUTED, leading=12, spaceBefore=2),
        "doctype": ParagraphStyle("doctype", fontName="Helvetica-Bold", fontSize=13, textColor=_INK, leading=15, alignment=TA_RIGHT),
        "docmeta": ParagraphStyle("docmeta", fontName="Helvetica", fontSize=9, textColor=_MUTED, leading=13, alignment=TA_RIGHT),
        "klabel": ParagraphStyle("klabel", fontName="Helvetica-Bold", fontSize=7.5, textColor=_FAINT, leading=11, spaceAfter=2),
        "pname": ParagraphStyle("pname", fontName="Helvetica-Bold", fontSize=10.5, textColor=_INK, leading=14),
        "pline": ParagraphStyle("pline", fontName="Helvetica", fontSize=9, textColor=_MUTED, leading=13),
        "th": ParagraphStyle("th", fontName="Helvetica-Bold", fontSize=8, textColor=_MUTED, leading=11),
        "thr": ParagraphStyle("thr", fontName="Helvetica-Bold", fontSize=8, textColor=_MUTED, leading=11, alignment=TA_RIGHT),
        "td": ParagraphStyle("td", fontName="Helvetica", fontSize=9, textColor=_INK2, leading=12.5),
        "tdsub": ParagraphStyle("tdsub", fontName="Helvetica", fontSize=7.5, textColor=_MUTED, leading=10),
        "tdr": ParagraphStyle("tdr", fontName="Helvetica", fontSize=9, textColor=_INK2, leading=12.5, alignment=TA_RIGHT),
        "tot": ParagraphStyle("tot", fontName="Helvetica", fontSize=9.5, textColor=_MUTED, leading=14),
        "totr": ParagraphStyle("totr", fontName="Helvetica", fontSize=9.5, textColor=_INK2, leading=14, alignment=TA_RIGHT),
        "grand": ParagraphStyle("grand", fontName="Helvetica-Bold", fontSize=12, textColor=_INK, leading=16),
        "grandr": ParagraphStyle("grandr", fontName="Helvetica-Bold", fontSize=12, textColor=_INK, leading=16, alignment=TA_RIGHT),
        "note": ParagraphStyle("note", fontName="Helvetica", fontSize=8, textColor=_FAINT, leading=12),
    }


def _charge_detail(charge) -> str:
    """One short line under the order number: what the fee was worked out on."""
    meta = getattr(charge, "meta", None) or {}
    lines = meta.get("lines") or []
    if meta.get("basis") == "selling_price" and lines:
        bits = []
        for ln in lines[:4]:
            qty = ln.get("quantity") or 0
            bits.append(
                f"{ln.get('title', '')}"
                + (f" ({ln.get('variant')})" if ln.get("variant") else "")
                + f": {qty} x INR {Decimal(str(ln.get('fee_per_unit') or 0)):,.2f}"
                + (f" [{str(ln.get('band')).replace(chr(8377), 'INR ')}]" if ln.get("band") else "")
            )
        if len(lines) > 4:
            bits.append(f"+{len(lines) - 4} more")
        return "; ".join(bits)
    return ""


def render_bill_pdf(*, bill, site, charges, config, tax=None, seller=None, buyer=None, payment=None) -> bytes:
    """Return the invoice PDF for ``bill`` as bytes.

    ``charges`` is the iterable of :class:`~commerce.models.OrderCharge` on the
    bill (each with its ``order`` available); ``config`` the store's
    :class:`~commerce.models.BillingConfig`. ``tax`` / ``seller`` / ``buyer`` /
    ``payment`` come from :mod:`commerce.billing_service` (``bill_tax``,
    ``bill_seller``, ``bill_buyer``, ``bill_payment_details``).
    """
    from commerce import billing_service

    charges = list(charges)
    tax = tax or billing_service.bill_tax(bill, site)
    seller = seller or billing_service.bill_seller()
    buyer = buyer or billing_service.bill_buyer(site)
    payment = payment or billing_service.bill_payment_details()

    s = _styles()
    currency = (bill.currency or "INR")
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"Invoice {bill.invoice_number or bill.pk}", author=seller.get("company_name") or "OneWooz",
    )
    content_w = doc.width
    story = []

    # -- header: billing company (left) / invoice meta (right) -------------
    status = bill.get_status_display() if hasattr(bill, "get_status_display") else str(bill.status)
    meta_html = (
        f"<b>{_txt(bill.invoice_number or bill.pk)}</b><br/>"
        f"Issued {bill.period_end:%d %b %Y}<br/>"
        f"Status: {_txt(status)}"
    )
    header = Table(
        [[
            [Paragraph(_txt(seller.get("company_name")), s["brand"]),
             Paragraph("Commerce fee for the OneWooz platform", s["brandsub"])],
            [Paragraph("TAX INVOICE" if tax.get("applied") else "COMMERCE FEE INVOICE", s["doctype"]),
             Paragraph(meta_html, s["docmeta"])],
        ]],
        colWidths=[content_w * 0.55, content_w * 0.45],
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [header, Spacer(1, 10), HRFlowable(width="100%", thickness=1, color=_LINE), Spacer(1, 12)]

    # -- parties: from / billed to ----------------------------------------
    from_bits = [seller.get("address")]
    if seller.get("gstin"):
        from_bits.append(f"GSTIN: {seller['gstin']}")
    if seller.get("pan"):
        from_bits.append(f"PAN: {seller['pan']}")
    if seller.get("state"):
        from_bits.append(f"State: {seller['state']}")
    if seller.get("email"):
        from_bits.append(seller["email"])
    if seller.get("phone"):
        from_bits.append(seller["phone"])
    from_html = "<br/>".join(_txt(b) for b in from_bits if b) or "&nbsp;"

    to_bits = []
    if buyer.get("name") and buyer.get("name") != buyer.get("brand"):
        to_bits.append(f"Store: {buyer.get('brand')}")
    to_bits.append(buyer.get("address"))
    if buyer.get("gstin"):
        to_bits.append(f"GSTIN: {buyer['gstin']}")
    if buyer.get("state"):
        to_bits.append(f"State: {buyer['state']}")
    to_bits += [buyer.get("email"), buyer.get("phone")]
    to_html = "<br/>".join(_txt(b) for b in to_bits if b) or "&nbsp;"

    parties = Table(
        [[
            [Paragraph("FROM", s["klabel"]), Paragraph(_txt(seller.get("company_name")), s["pname"]), Paragraph(from_html, s["pline"])],
            [Paragraph("BILLED TO", s["klabel"]), Paragraph(_txt(buyer.get("name") or site.name), s["pname"]), Paragraph(to_html, s["pline"])],
        ]],
        colWidths=[content_w * 0.5, content_w * 0.5],
    )
    parties.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [parties, Spacer(1, 12)]

    # -- invoice meta strip ------------------------------------------------
    # The bill's total commerce fee (before GST), by product price range.
    basis = f"{currency} {Decimal(str(tax.get('subtotal') or 0)):,.2f} total, by product price range"
    event = config.get_order_charge_event_display() if hasattr(config, "get_order_charge_event_display") else config.order_charge_event
    meta = Table(
        [[
            [Paragraph("BILLING PERIOD", s["klabel"]), Paragraph(f"{bill.period_start:%d %b %Y} &ndash; {bill.period_end:%d %b %Y}", s["td"])],
            [Paragraph("COMMERCE FEE", s["klabel"]), Paragraph(_txt(basis), s["td"])],
            [Paragraph("CHARGED ON", s["klabel"]), Paragraph(_txt(event), s["td"])],
            [Paragraph("SAC", s["klabel"]), Paragraph(_txt(seller.get("sac")), s["td"])],
        ]],
        colWidths=[content_w * 0.34, content_w * 0.28, content_w * 0.22, content_w * 0.16],
    )
    meta.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEABOVE", (0, 0), (-1, 0), 0.5, _LINE_SOFT),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, _LINE_SOFT),
    ]))
    story += [meta, Spacer(1, 14)]

    # -- line items --------------------------------------------------------
    rows = [[
        Paragraph("ORDER", s["th"]), Paragraph("DELIVERED", s["th"]),
        Paragraph("SELLING VALUE", s["thr"]), Paragraph("COMMERCE FEE", s["thr"]),
    ]]
    for c in charges:
        order = getattr(c, "order", None)
        order_cell = [Paragraph(f"#{order.display_number}" if order else "", s["td"])]
        detail = _charge_detail(c)
        if detail:
            order_cell.append(Paragraph(_txt(detail), s["tdsub"]))
        value = billing_service.selling_value_of(c)
        rows.append([
            order_cell,
            Paragraph(f"{c.charged_at:%d %b %Y}", s["td"]),
            Paragraph(_money(currency, value) if value is not None else "", s["tdr"]),
            Paragraph(_money(currency, c.amount), s["tdr"]),
        ])
    if not charges:
        rows.append([Paragraph("No individual charges recorded for this bill.", s["td"]), "", "", ""])

    col_w = [content_w * 0.46, content_w * 0.18, content_w * 0.18, content_w * 0.18]
    table = Table(rows, colWidths=col_w, repeatRows=1)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, _LINE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, _LINE_SOFT),
    ]
    for i in range(1, len(rows)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), _ZEBRA))
    if not charges:
        style.append(("SPAN", (0, 1), (-1, 1)))
    table.setStyle(TableStyle(style))
    story += [table, Spacer(1, 14)]

    # -- totals (right aligned) -------------------------------------------
    total_rows = [
        [Paragraph("Orders billed", s["tot"]), Paragraph(str(bill.total_orders), s["totr"])],
        [Paragraph("Commerce fee (taxable value)", s["tot"]), Paragraph(_money(currency, tax["subtotal"]), s["totr"])],
    ]
    if tax.get("applied"):
        half = f"{Decimal(tax['half_rate']):g}"
        full = f"{Decimal(tax['rate']):g}"
        if tax.get("intra"):
            total_rows += [
                [Paragraph(f"CGST @ {half}%", s["tot"]), Paragraph(_money(currency, tax["cgst"]), s["totr"])],
                [Paragraph(f"SGST @ {half}%", s["tot"]), Paragraph(_money(currency, tax["sgst"]), s["totr"])],
            ]
        else:
            total_rows.append([Paragraph(f"IGST @ {full}%", s["tot"]), Paragraph(_money(currency, tax["igst"]), s["totr"])])
    total_rows.append([Paragraph("Amount due", s["grand"]), Paragraph(_money(currency, tax["total"]), s["grandr"])])
    grand = len(total_rows) - 1
    totals = Table(total_rows, colWidths=[content_w * 0.34, content_w * 0.23], hAlign="RIGHT")
    totals.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEABOVE", (0, grand), (-1, grand), 0.75, _LINE),
        ("TOPPADDING", (0, grand), (-1, grand), 9),
    ]))
    story += [totals, Spacer(1, 18)]

    # -- payment details ---------------------------------------------------
    pay_cells = []
    for b in payment.get("banks") or []:
        bits = [
            f"Bank: {b.get('bank_name')}" if b.get("bank_name") else "",
            f"Account name: {b.get('account_holder')}" if b.get("account_holder") else "",
            f"Account no.: {b.get('account_number')}" if b.get("account_number") else "",
            f"IFSC: {b.get('ifsc_code')}" if b.get("ifsc_code") else "",
            f"Branch: {b.get('branch')}" if b.get("branch") else "",
        ]
        pay_cells.append("<br/>".join(_txt(x) for x in bits if x))
    for upi in payment.get("upis") or []:
        pay_cells.append(_txt(f"UPI ID: {upi}"))
    if pay_cells:
        story.append(Paragraph("PAYMENT DETAILS", s["klabel"]))
        cells = [Paragraph(c, s["pline"]) for c in pay_cells]
        width = content_w / max(1, min(len(cells), 3))
        grid = [cells[i:i + 3] for i in range(0, len(cells), 3)]
        for row in grid:
            while len(row) < min(len(cells), 3):
                row.append("")
        pay_table = Table(grid, colWidths=[width] * min(len(cells), 3), hAlign="LEFT")
        pay_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(pay_table)
        if payment.get("note"):
            story += [Spacer(1, 4), Paragraph(_txt(payment["note"]), s["note"])]
        story.append(Spacer(1, 14))

    # -- footer note -------------------------------------------------------
    story += [HRFlowable(width="100%", thickness=0.5, color=_LINE_SOFT), Spacer(1, 8)]
    if bill.status == "paid":
        paid = f" on {bill.paid_at:%d %b %Y}" if getattr(bill, "paid_at", None) else ""
        note = f"Paid{paid}. Thank you. This invoice covers the commerce fee on orders your store delivered during the billing period."
    else:
        note = ("This invoice covers the commerce fee on orders your store delivered during the billing period. "
                "Pay from your dashboard to keep your store fully active.")
    story.append(Paragraph(note, s["note"]))

    doc.build(story)
    return buf.getvalue()
