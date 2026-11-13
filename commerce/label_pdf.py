"""Delhivery shipping label + tax invoice generator.

Ported from the 1matrix reference (``website/shipping/label_invoice_generator.py``)
and adapted to OneWooz's models (``tenancy.Site`` for the store, ``Order`` with a
``number`` and ``created_at``, ``Shipment.awb`` for the AWB, ``Warehouse`` fields,
``PaymentGatewayConfig`` for the seller/GST block). The overall layout, fonts,
colours and the GST-aware invoice table follow the reference; OneWooz changes:

- the label header carries the OneWooz wordmark (the dashboard's top-left icon);
- the label has a single, scannable Code128 barcode for the AWB (the reference's
  thin Order ID barcode is gone; the Order ID is still printed as text);
- beside the barcode, a boxed panel shows the package weight and L×B×H;
- SHIPMENT DETAILS lists SKUs (the variation combination's SKU for a variation
  line), never product names;
- payment mode (Prepaid / COD / Partial COD, with the amount to collect) on
  both pages, from :func:`payment_info`; the buyer's GSTIN when the order has
  one, on both pages;
- the invoice lists every order line as its own row with per-product GST
  (same rules as ``commerce.order_invoice``), continues onto further pages
  when the rows outgrow one, and ends with items total, discount, shipping,
  round-off, grand total and the paid / to-collect split;
- the invoice has no ship-to block (the label carries the address) and prints
  the signature uploaded on Store settings (``Site.authorized_signature``) in
  the Authorized Signatory box;
- every piece of text is wrapped to the measured printable width, so nothing
  (addresses, table cells) ever runs off the page or overlaps.

Two formats, same as the reference:
- ``a4``   label (left half) + invoice (right half), two orders per A4 page.
- ``4x6``  label on one 4×6 page, invoice on the next 4×6 page.

Only the reference's *primary* path is ported: one ReportLab canvas draws every
page directly, so no ``pypdf`` merge step (and no ``num2words``) is required.

The single entry point is :func:`generate_label_invoice_pdf`, which takes a list
of ``orders_data`` dicts (built by the Orders view) and returns the PDF bytes.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from typing import Any, Dict, List

logger = logging.getLogger("onewooz.audit")

# Colour scheme (kept from the reference).
BLUE_PRIMARY = "#1e88e5"
BLUE_HEADER = "#1565c0"
BLUE_TABLE = "#e3f2fd"
GRAY_TEXT = "#333333"
GRAY_LIGHT = "#666666"
BLACK = "#000000"

_BRAND_FOOTER = "Powered by OneWooz"

# The same file the dashboard sidebar shows top-left (templates/ud/_shell.html).
_ONEWOOZ_LOGO_STATIC = "img/final_onewooz.png"
_ONEWOOZ_LOGO_FALLBACK = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "onewooz", "static", "img", "Icon_main.png",
)
_logo_cache: Dict[str, Any] = {}


# --------------------------------------------------------------------------
# Image / logo helpers
# --------------------------------------------------------------------------
def _load_image_from_path(path: str):
    try:
        from reportlab.lib.utils import ImageReader

        if path and os.path.exists(path):
            return ImageReader(path)
    except Exception as e:  # pragma: no cover - best effort
        logger.warning("label: logo path load failed: %s", e)
    return None


def _onewooz_logo():
    """The OneWooz wordmark as an ``ImageReader`` (cached), or ``None``.

    The source PNG is several thousand pixels wide; it is downscaled once so
    every generated PDF does not embed the full-resolution file.
    """
    if "img" in _logo_cache:
        return _logo_cache["img"]
    path = None
    try:
        from django.contrib.staticfiles import finders

        path = finders.find(_ONEWOOZ_LOGO_STATIC)
    except Exception:
        path = None
    if not path or not os.path.exists(path):
        path = _ONEWOOZ_LOGO_FALLBACK
    img = None
    try:
        from PIL import Image
        from reportlab.lib.utils import ImageReader

        with Image.open(path) as src:
            src.load()
            pic = src.convert("RGBA")
        pic.thumbnail((1200, 400))
        # Flatten onto white: labels print on white stock and thermal printers
        # handle soft masks poorly.
        flat = Image.new("RGB", pic.size, (255, 255, 255))
        flat.paste(pic, mask=pic.split()[3])
        img = ImageReader(flat)
    except Exception:
        img = _load_image_from_path(path)
    _logo_cache["img"] = img
    return img


# --------------------------------------------------------------------------
# Text fitting helpers (measured, so nothing can overflow its box)
# --------------------------------------------------------------------------
def _wrap_width(text, font: str, size: float, max_width: float, max_lines: int | None = None) -> List[str]:
    """Word-wrap ``text`` so every line fits ``max_width`` points.

    Words longer than the width are broken mid-word. With ``max_lines`` the
    result is cut to that many lines and the last one ends in an ellipsis.
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth

    max_width = max(float(max_width), 1.0)
    words = str(text or "").split()
    lines: List[str] = []
    cur = ""
    for word in words:
        trial = ("%s %s" % (cur, word)) if cur else word
        if stringWidth(trial, font, size) <= max_width:
            cur = trial
            continue
        if cur:
            lines.append(cur)
            cur = ""
        while len(word) > 1 and stringWidth(word, font, size) > max_width:
            cut = len(word) - 1
            while cut > 1 and stringWidth(word[:cut], font, size) > max_width:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        cur = word
    if cur:
        lines.append(cur)
    if not lines:
        return [""]
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and stringWidth(last + "...", font, size) > max_width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "..."
    return lines


def _fit_size(text, font: str, size: float, max_width: float, min_size: float) -> float:
    """The largest font size (down to ``min_size``) at which ``text`` fits."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    while size > min_size and stringWidth(str(text or ""), font, size) > max_width:
        size -= 0.5
    return size


def _wh_address(warehouse) -> Dict[str, str]:
    """A ``get_address()``-shaped dict from OneWooz's flat Warehouse fields."""
    if not warehouse:
        return {}
    return {
        "line1": getattr(warehouse, "address_line", "") or "",
        "line2": "",
        "city": getattr(warehouse, "city", "") or "",
        "state": getattr(warehouse, "state", "") or "",
        "postal_code": getattr(warehouse, "pincode", "") or "",
    }


def _amount_to_words(amount) -> str:
    """Indian-format rupees in words. Uses num2words when present, else a
    self-contained converter (so the dependency is optional)."""
    try:
        from num2words import num2words as _n2w

        return _n2w(float(amount), lang="en_IN", to="currency", currency="INR").replace(",", "").title()
    except Exception:
        amt = int(amount)
        paise = int(round((float(amount) - amt) * 100))
        ones = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
                "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
        tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

        def _ltt(n):
            if n == 0:
                return ""
            if n < 20:
                return ones[n]
            if n < 100:
                return tens[n // 10] + ("" if n % 10 == 0 else " " + ones[n % 10])
            return ones[n // 100] + " Hundred" + ("" if n % 100 == 0 else " And " + _ltt(n % 100))

        def _conv(n):
            if n == 0:
                return "Zero"
            r = ""
            if n >= 10000000:
                r += _conv(n // 10000000) + " Crore "
                n %= 10000000
            if n >= 100000:
                r += _ltt(n // 100000) + " Lakh "
                n %= 100000
            if n >= 1000:
                r += _ltt(n // 1000) + " Thousand "
                n %= 1000
            r += _ltt(n)
            return r.strip()

        paise_text = " Zero Paise" if paise == 0 else " And %s Paise" % _conv(paise)
        return "%s Rupees%s" % (_conv(amt), paise_text)


_CENT = Decimal("0.01")


def _q(value) -> Decimal:
    try:
        return Decimal(str(value if value is not None else 0)).quantize(_CENT, rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0.00")


def _money(value) -> str:
    return "Rs.%s" % format(_q(value), ".2f")


def payment_info(order) -> Dict[str, Any]:
    """How an order is paid, for the label and the invoice.

    ``mode`` is ``cod``, ``partial`` (part paid online, the balance collected
    on delivery) or ``prepaid``; ``paid`` is what was paid online and
    ``collect`` what the courier collects. A partial order is a COD shipment
    for the courier: there is still money to collect.
    """
    total = _q(getattr(order, "total", 0) if order is not None else 0)
    method = str(getattr(order, "payment_method", "") or "").lower()
    if order is not None and (getattr(order, "is_partial_payment", False) or method == "partial"):
        collect = _q(getattr(order, "partial_cod_amount", 0))
        paid = _q(getattr(order, "partial_paid_amount", 0)) or max(Decimal("0.00"), total - collect)
        return {"mode": "partial", "label": "Partial COD", "badge": "PARTIAL COD",
                "paid": paid, "collect": collect, "is_cod": True}
    if "cod" in method:
        return {"mode": "cod", "label": "Cash on Delivery", "badge": "COD",
                "paid": Decimal("0.00"), "collect": total, "is_cod": True}
    return {"mode": "prepaid", "label": "Prepaid", "badge": "PREPAID",
            "paid": total, "collect": Decimal("0.00"), "is_cod": False}


def _payment_for(order_data, delhivery_data) -> Dict[str, Any]:
    """``payment_info`` for the order, or from the Delhivery ``pt`` flag when
    no order object is supplied."""
    order = order_data.get("order")
    if order is not None:
        return payment_info(order)
    if "pre" in str(delhivery_data.get("pt", "Pre-paid")).lower():
        return {"mode": "prepaid", "label": "Prepaid", "badge": "PREPAID",
                "paid": Decimal("0.00"), "collect": Decimal("0.00"), "is_cod": False}
    return {"mode": "cod", "label": "Cash on Delivery", "badge": "COD",
            "paid": Decimal("0.00"), "collect": Decimal("0.00"), "is_cod": True}


def _payment_line(pay) -> str:
    """``Payment: Cash on Delivery, collect Rs.999.00`` and friends."""
    if pay["mode"] == "prepaid":
        return "Payment: Prepaid (paid online)"
    if pay["collect"] > 0:
        return "Payment: %s, collect %s" % (pay["label"], _money(pay["collect"]))
    return "Payment: %s" % pay["label"]


def _fmt_num(value) -> str:
    """``0.15`` -> ``"0.15"``, ``20.0`` -> ``"20"`` (at most two decimals)."""
    try:
        return ("%.2f" % float(value)).rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return ""


def _sku_text(skus) -> str:
    """The SHIPMENT DETAILS SKU line from ``[{"sku", "qty"}]``.

    Each line's SKU is its variation combination's SKU, or the product SKU for
    a simple product (resolved by ``commerce.cart.line_sku``). With several
    lines each SKU carries its own quantity.
    """
    rows = [r for r in (skus or []) if isinstance(r, dict)]
    if not rows:
        return "SKU: -"
    if len(rows) == 1:
        return "SKU: %s" % (str(rows[0].get("sku") or "").strip() or "-")
    parts = []
    for r in rows:
        parts.append("%s (x%s)" % (str(r.get("sku") or "").strip() or "-", r.get("qty") or 1))
    return "SKUs: %s" % ", ".join(parts)


def _draw_package_panel(c, order_data, delhivery_data, left, top, right, bottom):
    """Weight and L×B×H of the package, boxed beside the AWB barcode."""
    from reportlab.lib.colors import HexColor, black
    from reportlab.lib.units import mm

    width = right - left
    if width <= 10 * mm or top - bottom <= 10 * mm:
        return
    package = order_data.get("package") or {}
    weight_kg = package.get("weight_kg")
    if weight_kg is None:
        weight_kg = delhivery_data.get("weight") or 0
    weight_text = ("%s kg" % _fmt_num(weight_kg)) if _fmt_num(weight_kg) not in ("", "0") else "-"
    dims = package.get("dims_cm")
    dims_text = "-"
    if dims and all(_fmt_num(d) not in ("", "0") for d in dims):
        dims_text = "%s × %s × %s cm" % tuple(_fmt_num(d) for d in dims)

    pad = 2.5 * mm
    inner = width - 2 * pad
    mid = (top + bottom) / 2

    c.setStrokeColor(black)
    c.setLineWidth(0.8)
    c.rect(left, bottom, width, top - bottom, stroke=True, fill=False)
    c.setStrokeColor(HexColor("#999999"))
    c.setLineWidth(0.4)
    c.line(left + pad, mid, right - pad, mid)

    for (label, value, size), row_top in (
        (("WEIGHT", weight_text, 11), top),
        (("DIMENSIONS (L × B × H)", dims_text, 9.5), mid),
    ):
        c.setFont("Helvetica-Bold", 5.5)
        c.setFillColor(HexColor("#555555"))
        c.drawString(left + pad, row_top - pad - 4, _wrap_width(label, "Helvetica-Bold", 5.5, inner, 1)[0])
        value_size = _fit_size(value, "Helvetica-Bold", size, inner, 6.5)
        c.setFont("Helvetica-Bold", value_size)
        c.setFillColor(black)
        c.drawString(left + pad, row_top - pad - 4 - value_size - 3, _wrap_width(value, "Helvetica-Bold", value_size, inner, 1)[0])


def _signature_image(website, payment_config, cache=None):
    """The authorised-signatory signature as an ``ImageReader``, or ``None``.

    Store settings' upload (``Site.authorized_signature``) first, then the
    legacy ``PaymentGatewayConfig.digital_signature``. Read through the storage
    API (``.path`` does not exist on S3), downscaled and flattened onto white.
    """
    fields = [getattr(website, "authorized_signature", None)]
    if payment_config is not None:
        fields.append(getattr(payment_config, "digital_signature", None))
    for field in fields:
        name = getattr(field, "name", "") if field else ""
        if not name:
            continue
        if cache is not None and name in cache:
            return cache[name]
        img = None
        try:
            from PIL import Image
            from reportlab.lib.utils import ImageReader

            field.open("rb")
            try:
                data = field.read()
            finally:
                field.close()
            with Image.open(BytesIO(data)) as src:
                src.load()
                pic = src.convert("RGBA")
            pic.thumbnail((900, 360))
            flat = Image.new("RGB", pic.size, (255, 255, 255))
            flat.paste(pic, mask=pic.split()[3])
            img = ImageReader(flat)
        except Exception as e:
            logger.warning("label: signature load failed (%s): %s", name, e)
        if cache is not None:
            cache[name] = img
        if img is not None:
            return img
    return None


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def generate_label_invoice_pdf(orders_data: List[Dict[str, Any]], label_format: str = "4x6") -> bytes:
    """Combined label + invoice PDF for one or more orders."""
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas

    buffer = BytesIO()
    # One download can hold many orders of the same store; load its signature
    # image once.
    sig_cache: Dict[str, Any] = {}

    if label_format == "a4":
        page_width, page_height = A4
        half_width = page_width / 2
        half_height = page_height / 2
        c = canvas.Canvas(buffer, pagesize=A4)
        # Each A4 page has two rows; an order takes a row (label left, invoice
        # right). An invoice that runs long continues in the right half of the
        # following row(s), so the next order simply starts one row later.
        rows = {"used": 0}

        def next_row():
            if rows["used"] == 2:
                c.showPage()
                rows["used"] = 0
            row = rows["used"]
            rows["used"] += 1
            y_off = half_height if row == 0 else 0
            c.setStrokeColor(HexColor("#cccccc"))
            c.setLineWidth(0.5)
            c.setDash(2, 2)
            c.line(half_width, y_off, half_width, y_off + half_height)
            if row == 1:
                c.line(0, half_height, page_width, half_height)
            c.setDash()
            return y_off

        def invoice_continuation():
            return (half_width, next_row(), half_width, half_height)

        for order_data in orders_data:
            y_offset = next_row()
            _draw_shipping_label(c, order_data, x_offset=0, y_offset=y_offset, width=half_width, height=half_height)
            _draw_tax_invoice(
                c, order_data, x_offset=half_width, y_offset=y_offset, width=half_width, height=half_height,
                sig_cache=sig_cache, next_region=invoice_continuation,
            )
        c.showPage()
    else:
        page_width = 4 * inch
        page_height = 6 * inch
        c = canvas.Canvas(buffer, pagesize=(page_width, page_height))

        def invoice_continuation():
            c.showPage()
            return (0, 0, page_width, page_height)

        for idx, order_data in enumerate(orders_data):
            if idx > 0:
                c.showPage()
            _draw_shipping_label(c, order_data, x_offset=0, y_offset=0, width=page_width, height=page_height)
            c.showPage()
            _draw_tax_invoice(
                c, order_data, x_offset=0, y_offset=0, width=page_width, height=page_height,
                sig_cache=sig_cache, next_region=invoice_continuation,
            )
        c.showPage()

    c.save()
    buffer.seek(0)
    return buffer.read()


def _draw_shipping_label(c, order_data, x_offset, y_offset, width, height):
    from reportlab.graphics.barcode import code128
    from reportlab.lib.colors import HexColor, black, white
    from reportlab.lib.units import mm

    order = order_data.get("order")
    shipment = order_data.get("shipment")
    delhivery_data = order_data.get("delhivery_data", {}) or {}
    website = order_data.get("website")
    warehouse = order_data.get("warehouse")

    awb = getattr(shipment, "awb", "") if shipment else ""
    waybill = str(delhivery_data.get("wbn", "") or awb or "").strip()
    sort_code = delhivery_data.get("sort_code", "")
    order_id = delhivery_data.get("oid", "") or ((getattr(order, "display_number", "") or str(order.number)) if order else "")

    customer_name = delhivery_data.get("name", "") or (order.customer_name if order else "")
    customer_address = delhivery_data.get("address", "") or (order.shipping_address if order else "")
    customer_pin = str(delhivery_data.get("pin", "")) or (order.shipping_pincode if order else "")
    customer_state = delhivery_data.get("st", "") or (order.shipping_state if order else "")
    customer_city = delhivery_data.get("destination_city", "") or (order.shipping_city if order else "")
    customer_country = "India"

    pay = _payment_for(order_data, delhivery_data)
    quantity = delhivery_data.get("qty", "1")
    customer_gstin = str(getattr(order, "gstin", "") or "").strip().upper() if order is not None else ""

    return_address = delhivery_data.get("radd", "")
    return_pin = delhivery_data.get("rpin", "")
    return_state = delhivery_data.get("rst", "")
    return_city = delhivery_data.get("rcty", "")
    return_name = delhivery_data.get("rname", "")

    if not return_address and warehouse:
        try:
            wh_addr = _wh_address(warehouse)
            return_name = warehouse.name or (website.name if website else "Company Name")
            return_address = ("%s, %s" % (wh_addr.get("line1", ""), wh_addr.get("line2", ""))).strip(", ")
            return_pin = wh_addr.get("postal_code", "")
            return_state = wh_addr.get("state", "")
            return_city = wh_addr.get("city", "")
        except Exception:
            pass
    if not return_name:
        return_name = website.name if website else "Company Name"

    margin = 6 * mm
    inner_width = width - (2 * margin)
    x = x_offset + margin
    is_small = width < 110 * mm

    header_size = 14 if is_small else 18
    sort_size = 10 if is_small else 12
    section_title_size = 8 if is_small else 9
    text_size = 7 if is_small else 8
    small_text_size = 6 if is_small else 7
    line_height = 3.5 * mm if is_small else 4 * mm
    section_gap = 4 * mm if is_small else 5 * mm

    c.setStrokeColor(HexColor("#000000"))
    c.setLineWidth(1)
    c.rect(x_offset + 2 * mm, y_offset + 2 * mm, width - 4 * mm, height - 4 * mm, stroke=True, fill=False)

    y = y_offset + height - margin

    # Header: courier name (left) + OneWooz wordmark (right). The courier is the
    # shipment's actual one (Delhivery / Shiprocket / Self-ship) so the label
    # isn't misbranded; falls back to a generic title.
    courier_label = (order_data.get("courier_name") or "").strip().upper() or "SHIPPING LABEL"
    header_y = y - 6 * mm

    logo_w = 0
    logo_img = _onewooz_logo()
    if logo_img is not None:
        try:
            iw, ih = logo_img.getSize()
            logo_w = 28 * mm if is_small else 34 * mm
            logo_h = logo_w * ih / float(iw or 1)
            c.drawImage(logo_img, x + inner_width - logo_w, header_y - 0.5 * mm, logo_w, logo_h, mask="auto")
        except Exception as e:  # pragma: no cover - best effort
            logger.warning("label: OneWooz logo draw failed: %s", e)
            logo_w = 0

    title_room = inner_width - (logo_w + 3 * mm if logo_w else 0)
    title_size = _fit_size(courier_label, "Helvetica-Bold", header_size, title_room, 8)
    c.setFont("Helvetica-Bold", title_size)
    c.setFillColor(black)
    c.drawString(x, header_y, _wrap_width(courier_label, "Helvetica-Bold", title_size, title_room, 1)[0])

    y = header_y - section_gap - 2 * mm

    # Sort code + prepaid/COD (black band)
    sort_section_height = 8 * mm if is_small else 10 * mm
    c.setFillColor(black)
    c.rect(x, y - sort_section_height + 2 * mm, inner_width, sort_section_height, fill=True, stroke=False)
    band_size = sort_size - 0.5
    c.setFont("Helvetica-Bold", band_size)
    c.setFillColor(white)
    payment_badge = pay["badge"]
    badge_w = c.stringWidth(payment_badge, "Helvetica-Bold", band_size)
    sort_text = ("SORT: %s" % sort_code) if sort_code else "SORT: -"
    sort_text = _wrap_width(sort_text, "Helvetica-Bold", band_size, inner_width - badge_w - 8 * mm, 1)[0]
    c.drawString(x + 2 * mm, y - (sort_section_height / 2) + 1 * mm, sort_text)
    c.drawRightString(x + inner_width - 2 * mm, y - (sort_section_height / 2) + 1 * mm, payment_badge)
    y -= sort_section_height + section_gap

    # AWB
    c.setFont("Helvetica-Bold", text_size + 1)
    c.setFillColor(black)
    c.drawString(x, y, _wrap_width("AWB: %s" % waybill, "Helvetica-Bold", text_size + 1, inner_width, 1)[0])
    y -= line_height + 1 * mm

    # Order ID (text only; the AWB barcode below is the one couriers scan)
    c.setFont("Helvetica-Bold", text_size)
    c.setFillColor(HexColor(BLUE_PRIMARY))
    c.drawString(x, y, _wrap_width("Order ID: #%s" % order_id, "Helvetica-Bold", text_size, inner_width, 1)[0])
    y -= section_gap

    # AWB barcode, left-aligned. Code128 in solid black with a module width of
    # ~0.35 mm (comfortably above a 203-dpi thermal printer's 2-dot minimum) and
    # a clear 10-module quiet zone either side so handheld scanners read it.
    # The package panel (weight + dimensions) sits to the right of the barcode;
    # the barcode is sized to leave it at least ``info_min`` of width.
    linear_barcode_height = 18 * mm if is_small else 22 * mm
    bar_top = y
    info_min = 30 * mm
    info_x = x + inner_width * 0.5
    if waybill:
        try:
            probe = code128.Code128(waybill, barWidth=1.0, barHeight=linear_barcode_height, quiet=0)
            modules = float(probe.width) or 1.0
            # Left quiet zone: the white margin up to the border counts towards it.
            white_left = margin - 2.5 * mm
            # Room for the bars plus the right-hand quiet zone, beside the panel.
            bar_room = inner_width - info_min - 2 * mm
            bar_width = min(1.0, bar_room / (modules + 10))
            bars_x = x + max(0.0, 10 * bar_width - white_left)
            bc_awb = code128.Code128(
                waybill,
                barWidth=bar_width,
                barHeight=linear_barcode_height,
                quiet=0,
                barFillColor=black,
                humanReadable=False,
            )
            c.setFillColor(black)
            bc_awb.drawOn(c, bars_x, bar_top - linear_barcode_height)
            c.setFont("Helvetica-Bold", small_text_size + 1)
            c.drawCentredString(bars_x + bc_awb.width / 2, bar_top - linear_barcode_height - 3.5 * mm, waybill)
            info_x = bars_x + bc_awb.width + 10 * bar_width + 2 * mm
        except Exception as e:
            logger.warning("label: AWB barcode failed: %s", e)
    else:
        c.setFont("Helvetica", small_text_size + 1)
        c.setFillColor(HexColor(GRAY_LIGHT))
        c.drawString(x, bar_top - linear_barcode_height / 2, "AWB not assigned yet")
    info_x = min(info_x, x + inner_width - info_min)
    _draw_package_panel(
        c, order_data, delhivery_data,
        left=info_x, top=bar_top, right=x + inner_width,
        bottom=bar_top - linear_barcode_height - 4.5 * mm,
    )
    y = bar_top - linear_barcode_height - 12 * mm

    # Everything below is measured before drawing: text wraps to the printable
    # width, and if the page would run out of room the longest blocks are cut
    # to fewer lines (ending in "...") instead of spilling past the footer.
    name_size = _fit_size(customer_name, "Helvetica-Bold", text_size + 2, inner_width, text_size)
    name_line = _wrap_width(customer_name, "Helvetica-Bold", name_size, inner_width, 1)[0]
    full_address = ", ".join(
        str(p).strip()
        for p in (customer_address, customer_city, customer_state, customer_pin, customer_country)
        if p and str(p).strip()
    )
    product_text = _sku_text(order_data.get("skus"))
    detail_text = "Qty: %s    %s" % (quantity, _payment_line(pay))
    gstin_line = ("GSTIN: %s" % customer_gstin) if customer_gstin else ""
    return_name_size = _fit_size(return_name, "Helvetica-Bold", text_size + 1, inner_width, text_size)
    return_name_line = _wrap_width(return_name, "Helvetica-Bold", return_name_size, inner_width, 1)[0]
    return_full = ", ".join([p.strip() for p in [return_address, return_city] if p and p.strip()])
    return_place = ""
    if return_city or return_state or return_pin:
        return_place = _wrap_width(
            "%s, %s - %s" % (return_city, return_state, return_pin), "Helvetica-Bold", text_size, inner_width, 1
        )[0]

    n_addr = min(len(_wrap_width(full_address, "Helvetica", text_size, inner_width)), 3)
    n_prod = min(len(_wrap_width(product_text, "Helvetica", text_size, inner_width)), 3)
    n_ret = min(len(_wrap_width(return_full, "Helvetica", text_size, inner_width)), 2) if return_full else 0
    detail_lines = _wrap_width(detail_text, "Helvetica-Bold", text_size, inner_width, 2)

    footer_y = y_offset + margin + 2 * mm
    floor = footer_y + 6 * mm

    def _needed(na, np_, nr):
        """Distance from the SHIP TO band to the last baseline drawn."""
        h = 8 * mm + (line_height + 1 * mm) + (line_height if gstin_line else 0) + na * line_height + section_gap
        h += (line_height + 1 * mm) + np_ * line_height + len(detail_lines) * line_height + section_gap + 6 * mm
        h += 8 * mm + (line_height + 1 * mm) + nr * line_height + (line_height if return_place else 0)
        return h - line_height

    while y - _needed(n_addr, n_prod, n_ret) < floor:
        if n_prod > 1:
            n_prod -= 1
        elif n_addr > 2:
            n_addr -= 1
        elif n_ret > 1:
            n_ret -= 1
        elif n_addr > 1:
            n_addr -= 1
        else:
            break

    # Ship to
    c.setFillColor(HexColor("#333333"))
    c.roundRect(x, y, 25 * mm, 6 * mm, 2 * mm, stroke=False, fill=True)
    c.setFont("Helvetica-Bold", section_title_size)
    c.setFillColor(white)
    c.drawString(x + 3 * mm, y + 1.5 * mm, "SHIP TO:")
    y -= 6 * mm + 2 * mm

    c.setFont("Helvetica-Bold", name_size)
    c.setFillColor(black)
    c.drawString(x, y, name_line)
    y -= line_height + 1 * mm

    # Business buyer: their GSTIN, printed only when the order carries one.
    if gstin_line:
        c.setFont("Helvetica-Bold", text_size)
        c.drawString(x, y, _wrap_width(gstin_line, "Helvetica-Bold", text_size, inner_width, 1)[0])
        y -= line_height

    c.setFont("Helvetica", text_size)
    for line in _wrap_width(full_address, "Helvetica", text_size, inner_width, n_addr):
        c.drawString(x, y, line)
        y -= line_height
    y -= section_gap

    # Shipment details
    c.setFont("Helvetica-Bold", section_title_size)
    c.setFillColor(black)
    c.drawString(x, y, "SHIPMENT DETAILS:")
    y -= line_height + 1 * mm
    c.setFont("Helvetica", text_size)
    for line in _wrap_width(product_text, "Helvetica", text_size, inner_width, n_prod):
        c.drawString(x, y, line)
        y -= line_height
    c.setFont("Helvetica-Bold", text_size)
    for line in detail_lines:
        c.drawString(x, y, line)
        y -= line_height
    y -= section_gap + 6 * mm

    # Ship from & return to
    c.setFillColor(HexColor("#333333"))
    c.roundRect(x, y, 52 * mm, 6 * mm, 2 * mm, stroke=False, fill=True)
    c.setFont("Helvetica-Bold", section_title_size)
    c.setFillColor(white)
    c.drawString(x + 3 * mm, y + 1.5 * mm, "SHIP FROM & RETURN TO")
    y -= 6 * mm + 2 * mm

    c.setFont("Helvetica-Bold", return_name_size)
    c.setFillColor(black)
    c.drawString(x, y, return_name_line)
    y -= line_height + 1 * mm
    c.setFont("Helvetica", text_size)
    if n_ret:
        for line in _wrap_width(return_full, "Helvetica", text_size, inner_width, n_ret):
            c.drawString(x, y, line)
            y -= line_height
    if return_place:
        c.setFont("Helvetica-Bold", text_size)
        c.setFillColor(HexColor(BLUE_PRIMARY))
        c.drawString(x, y, return_place)
        y -= line_height

    # Footer
    c.setStrokeColor(HexColor("#cccccc"))
    c.setLineWidth(0.5)
    c.line(x, footer_y + 4 * mm, x + inner_width, footer_y + 4 * mm)
    c.setFont("Helvetica", small_text_size - 1)
    c.setFillColor(HexColor("#888888"))
    c.drawCentredString(x_offset + width / 2, footer_y, _BRAND_FOOTER)


def _invoice_lines(order_data, delhivery_data):
    """One dict per order line with its GST worked out.

    Same rules as the shopper's invoice (``commerce.order_invoice``): the rate
    and inclusivity come from the line's product; an inclusive price has the
    tax extracted (``amount × r/(100+r)``), an exclusive one has it added
    (``amount × r/100``, what checkout charged as ``Order.tax_amount``). A
    product without a GST rate is untaxed, never assumed at 18%.
    """
    from commerce.cart import line_sku

    order = order_data.get("order")
    # Items are passed in pre-fetched (tenant-scoped) by the view; OneWooz's
    # OrderItem queryset refuses to evaluate via a bare ``order.items.all()``.
    items = order_data.get("items")
    if items is None:
        items = []
        if order is not None:
            try:
                items = list(order.items.all())
            except Exception:
                items = []

    from commerce.variant_profiles import line_view

    lines = []
    for item in items:
        # The line's variation GST / HSN, where it has its own.
        product = line_view(item) if getattr(item, "product", None) is not None else None
        rate = _q(getattr(product, "gst_percentage", None) or 0)
        inclusive = bool(getattr(product, "gst_inclusive", True))
        line_total = _q(item.line_total)
        if rate > 0:
            if inclusive:
                taxable = _q(line_total * Decimal(100) / (Decimal(100) + rate))
                tax = line_total - taxable
            else:
                taxable = line_total
                tax = _q(line_total * rate / Decimal(100))
        else:
            taxable, tax = line_total, Decimal("0.00")
        lines.append({
            "title": item.title or (getattr(product, "title", "") or "Item"),
            "variant": (getattr(item, "variant_label", "") or "").strip(),
            "sku": line_sku(item),
            "hsn": (getattr(product, "hsn_code", "") or "").strip(),
            "qty": int(item.quantity or 0),
            "unit": _q(item.unit_price),
            "rate": rate,
            "taxable": taxable,
            "tax": tax,
            "amount": taxable + tax,
        })

    if not lines:
        # No line rows (shouldn't happen for a real order): one line for the
        # whole order, untaxed, so the invoice still balances.
        total = _q(getattr(order, "total", 0) if order is not None else 0)
        raw_qty = str(delhivery_data.get("qty") or "1").strip()
        lines.append({
            "title": str(delhivery_data.get("prd") or "Item"),
            "variant": "", "sku": "",
            "hsn": str(delhivery_data.get("hsn_code") or ""),
            "qty": int(raw_qty) if raw_qty.isdigit() else 1,
            "unit": total, "rate": Decimal("0.00"),
            "taxable": total, "tax": Decimal("0.00"), "amount": total,
        })
    return lines


def _draw_tax_invoice(c, order_data, x_offset, y_offset, width, height, sig_cache=None, next_region=None):
    """The tax invoice for one order, on as many pages as its items need.

    Every order line is its own table row: title, variation and SKU, HSN, qty,
    rate, taxable value, GST rate, the CGST + SGST (same state) or IGST split,
    and the amount. When the rows outgrow the page the table continues, header
    row repeated, on the region ``next_region()`` returns (a fresh 4×6 page or
    the next A4 slot), and the totals, amount in words and signature follow
    the last row. ``Order.total`` is the grand total; discount, shipping and
    any round-off are shown between the items total and it.
    """
    from xml.sax.saxutils import escape

    from reportlab.lib.colors import HexColor, black
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.platypus import Paragraph, Table, TableStyle

    order = order_data.get("order")
    shipment = order_data.get("shipment")
    delhivery_data = order_data.get("delhivery_data", {}) or {}
    website = order_data.get("website")
    warehouse = order_data.get("warehouse")
    payment_config = order_data.get("payment_config")

    margin = 6 * mm
    inner_width = width - (2 * margin)
    is_small = width < 110 * mm

    title_size = 12 if is_small else 14
    label_size = 7 if is_small else 8
    text_size = 6 if is_small else 7
    small_text_size = 5.5 if is_small else 6.5
    table_font_size = 5 if is_small else 6
    line_height = 3.2 * mm if is_small else 3.8 * mm
    section_gap = 3 * mm if is_small else 4 * mm
    sig_box_height = 24 * mm

    if next_region is None:
        def next_region():
            c.showPage()
            return (x_offset, y_offset, width, height)

    # ── Data ──────────────────────────────────────────────────────────────
    pc = payment_config
    wh = _wh_address(warehouse) if warehouse else {}
    seller_name = (
        (getattr(pc, "company_name", "") if pc else "")
        or getattr(website, "legal_name", "")
        or (website.name if website else "")
        or getattr(warehouse, "name", "")
        or "Seller"
    )
    pc_address = ((getattr(pc, "address", "") or "") if pc else "").strip()
    if pc_address:
        seller_address, seller_city = pc_address, ""
    else:
        seller_address, seller_city = wh.get("line1", ""), wh.get("city", "")
    seller_state = ((getattr(pc, "state", "") or "") if pc else "") or wh.get("state", "")
    seller_pin = ((getattr(pc, "pincode", "") or "") if pc else "") or wh.get("postal_code", "")
    seller_gstin = str(
        ((getattr(pc, "gstin", "") or "") if pc else "")
        or getattr(website, "gstin", "")
        or delhivery_data.get("client_gst_tin", "")
        or delhivery_data.get("seller_gst_tin", "")
        or ""
    ).strip().upper()
    seller_pan = seller_gstin[2:12] if len(seller_gstin) >= 12 else str(getattr(website, "pan", "") or "").strip().upper()
    seller_phone = ((getattr(pc, "mobile_number", "") or "") if pc else "") or getattr(website, "support_phone", "")
    seller_email = ((getattr(pc, "email", "") or "") if pc else "") or getattr(website, "support_email", "")

    customer_name = delhivery_data.get("name", "") or (order.customer_name if order else "")
    customer_address = delhivery_data.get("address", "") or (order.shipping_address if order else "")
    customer_city = delhivery_data.get("destination_city", "") or (order.shipping_city if order else "")
    customer_state = delhivery_data.get("st", "") or (order.shipping_state if order else "")
    customer_pin = str(delhivery_data.get("pin", "")) or (order.shipping_pincode if order else "")
    customer_gstin = str(getattr(order, "gstin", "") or "").strip().upper() if order is not None else ""
    customer_company = str(getattr(order, "company_name", "") or "").strip() if order is not None else ""

    awb = getattr(shipment, "awb", "") if shipment else ""
    waybill = delhivery_data.get("wbn", "") or awb
    order_id = delhivery_data.get("oid", "") or ((getattr(order, "display_number", "") or str(order.number)) if order else "")
    order_dt = getattr(order, "created_at", None) if order else None
    order_date = order_dt.strftime("%d.%m.%Y") if order_dt else datetime.now().strftime("%d.%m.%Y")
    invoice_no = "INV-%s" % order_id
    pay = _payment_for(order_data, delhivery_data)

    # CGST + SGST within the seller's state, IGST across states. A valid
    # GSTIN's state wins over a typed state on either side.
    try:
        from billing import gst

        intra = gst.is_intra_state(customer_state, customer_gstin, seller_state, seller_gstin)
        place_of_supply = gst.effective_state(customer_state, customer_gstin)
    except Exception:
        intra = bool(seller_state) and seller_state.strip().lower() == (customer_state or "").strip().lower()
        place_of_supply = customer_state

    lines = _invoice_lines(order_data, delhivery_data)

    # ── Page frame / continuation ─────────────────────────────────────────
    region = {"x_off": x_offset, "y_off": y_offset, "page": 1}

    def _left():
        return region["x_off"] + margin

    def _floor():
        # Lowest baseline content may use: just above the footer rule.
        return region["y_off"] + margin + 8 * mm

    def _frame():
        xo, yo = region["x_off"], region["y_off"]
        c.setStrokeColor(black)
        c.setLineWidth(1)
        c.rect(xo + 2 * mm, yo + 2 * mm, width - 4 * mm, height - 4 * mm, stroke=True, fill=False)
        footer_y = yo + margin + 2 * mm
        c.setStrokeColor(HexColor("#cccccc"))
        c.setLineWidth(0.5)
        c.line(xo + margin, footer_y + 4 * mm, xo + margin + inner_width, footer_y + 4 * mm)
        c.setFont("Helvetica", small_text_size - 1)
        c.setFillColor(HexColor("#888888"))
        c.drawCentredString(xo + width / 2, footer_y, _BRAND_FOOTER)
        if region["page"] > 1:
            c.drawRightString(xo + margin + inner_width, footer_y, "Page %d" % region["page"])

    def _new_page():
        """Note the continuation, move to the next region, frame it and draw a
        short continuation header. Returns the y to continue from."""
        c.setFont("Helvetica-Oblique", small_text_size - 1)
        c.setFillColor(HexColor("#888888"))
        c.drawString(_left(), region["y_off"] + margin + 2 * mm, "Continued on next page")
        nx, ny = next_region()[:2]
        region.update(x_off=nx, y_off=ny, page=region["page"] + 1)
        _frame()
        top = ny + height - margin - 3 * mm
        c.setFont("Helvetica-Bold", title_size - 2)
        c.setFillColor(black)
        c.drawString(_left(), top, "TAX INVOICE (continued)")
        c.setFont("Helvetica", text_size)
        ref = "Invoice No: %s   Order ID: #%s" % (invoice_no, order_id)
        c.drawRightString(_left() + inner_width, top, _wrap_width(ref, "Helvetica", text_size, inner_width * 0.5, 1)[0])
        return top - section_gap - 2 * mm

    _frame()
    x = _left()
    y = y_offset + height - margin

    # ── Header ────────────────────────────────────────────────────────────
    c.setFont("Helvetica", small_text_size)
    c.setFillColor(HexColor("#666666"))
    c.drawRightString(x + inner_width, y + 1.5 * mm, "(Original for Recipient)")
    y -= section_gap

    heading = "TAX INVOICE / BILL OF SUPPLY"
    heading_size = _fit_size(heading, "Helvetica-Bold", title_size + 2, inner_width, 9)
    c.setFont("Helvetica-Bold", heading_size)
    c.setFillColor(black)
    c.drawCentredString(x_offset + width / 2, y, heading)
    y -= section_gap + 3 * mm

    left_x = x
    right_x = x + inner_width / 2 + 2 * mm
    left_col_w = inner_width / 2 - 2 * mm
    right_col_w = x + inner_width - right_x

    def _draw_field_left(canvas, x_pos, y_pos, label, value, col_width):
        canvas.setFont("Helvetica-Bold", label_size)
        canvas.setFillColor(black)
        canvas.drawString(x_pos, y_pos, label)
        label_width = max(15 * mm, canvas.stringWidth(label, "Helvetica-Bold", label_size) + 1.5 * mm)
        wrapped = _wrap_width(value, "Helvetica", text_size, col_width - label_width, 3)
        canvas.setFont("Helvetica", text_size)
        current_y = y_pos
        for line in wrapped:
            canvas.drawString(x_pos + label_width, current_y, line)
            current_y -= line_height
        return y_pos - (len(wrapped) * line_height)

    y_left = y
    y_left = _draw_field_left(c, left_x, y_left, "Sold By:", seller_name, left_col_w)
    if seller_pan:
        y_left = _draw_field_left(c, left_x, y_left, "PAN:", seller_pan, left_col_w)
    if seller_gstin:
        y_left = _draw_field_left(c, left_x, y_left, "GSTIN:", seller_gstin, left_col_w)
        y_left -= 1 * mm
    if seller_phone:
        y_left = _draw_field_left(c, left_x, y_left, "Contact No.", seller_phone, left_col_w)
    if seller_email:
        y_left = _draw_field_left(c, left_x, y_left, "Email:", seller_email, left_col_w)
        y_left -= 1 * mm

    c.setFont("Helvetica-Bold", label_size)
    c.drawString(left_x, y_left, "Address:")
    addr_label_width = c.stringWidth("Address:", "Helvetica-Bold", label_size)
    c.setStrokeColor(black)
    c.setLineWidth(0.5)
    c.line(left_x, y_left - 0.5 * mm, left_x + addr_label_width, y_left - 0.5 * mm)
    y_left -= line_height
    c.setFont("Helvetica", text_size)
    seller_addr_text = ("%s %s" % (seller_address, seller_city)).strip()
    if seller_addr_text:
        for line in _wrap_width(seller_addr_text, "Helvetica", text_size, left_col_w, 3):
            c.drawString(left_x, y_left, line)
            y_left -= line_height
    if seller_state and seller_pin:
        c.setFont("Helvetica-Bold", text_size)
        c.drawString(left_x, y_left, _wrap_width("%s - %s" % (seller_state, seller_pin), "Helvetica-Bold", text_size, left_col_w, 1)[0])
        y_left -= line_height

    # Buyer: company and GSTIN only when the order carries them (B2B).
    y_right = y
    y_right = _draw_field_left(c, right_x, y_right, "Billed To:", customer_name, right_col_w)
    if customer_company:
        y_right = _draw_field_left(c, right_x, y_right, "Company:", customer_company, right_col_w)
    if customer_gstin:
        y_right = _draw_field_left(c, right_x, y_right, "GSTIN:", customer_gstin, right_col_w)
    y_right -= 1 * mm
    c.setFont("Helvetica-Bold", label_size)
    c.drawString(right_x, y_right, "Address:")
    addr_label_width_r = c.stringWidth("Address:", "Helvetica-Bold", label_size)
    c.line(right_x, y_right - 0.5 * mm, right_x + addr_label_width_r, y_right - 0.5 * mm)
    y_right -= line_height
    c.setFont("Helvetica", text_size)
    customer_addr_text = ("%s %s" % (customer_address, customer_city)).strip()
    if customer_addr_text:
        for line in _wrap_width(customer_addr_text, "Helvetica", text_size, right_col_w, 3):
            c.drawString(right_x, y_right, line)
            y_right -= line_height
    if customer_state and customer_pin:
        c.setFont("Helvetica-Bold", text_size)
        c.drawString(right_x, y_right, _wrap_width("%s - %s" % (customer_state, customer_pin), "Helvetica-Bold", text_size, right_col_w, 1)[0])
        y_right -= line_height

    y = min(y_left, y_right) - section_gap

    c.setFont("Helvetica-Bold", text_size)
    c.setFillColor(black)
    c.drawString(left_x, y, _wrap_width("AWB: %s" % waybill, "Helvetica-Bold", text_size, left_col_w, 1)[0])
    c.drawRightString(x + inner_width, y, _wrap_width("Invoice No: %s" % invoice_no, "Helvetica-Bold", text_size, right_col_w, 1)[0])
    y -= line_height
    c.setFont("Helvetica", text_size)
    c.drawString(left_x, y, _wrap_width("Order ID: #%s" % order_id, "Helvetica", text_size, left_col_w, 1)[0])
    c.drawRightString(x + inner_width, y, "Invoice Date: %s" % order_date)
    y -= line_height
    c.setFont("Helvetica-Bold", text_size)
    c.drawString(left_x, y, _wrap_width(_payment_line(pay), "Helvetica-Bold", text_size, left_col_w, 1)[0])
    if place_of_supply:
        c.setFont("Helvetica", text_size)
        c.drawRightString(x + inner_width, y, _wrap_width("Place of Supply: %s" % place_of_supply, "Helvetica", text_size, right_col_w, 1)[0])
    y -= section_gap + 2 * mm

    # ── Items table ───────────────────────────────────────────────────────
    def _num(value) -> str:
        return format(_q(value), ".2f")

    tax_heads = ["CGST", "SGST"] if intra else ["IGST"]
    header_row = ["#", "Description", "HSN", "Qty", "Rate", "Taxable Value", "GST %"] + tax_heads + ["Amount"]
    n_cols = len(header_row)

    body_rows: List[List[str]] = []
    descs: List[str] = []
    qty_total = 0
    taxable_total = tax_total = cgst_total = sgst_total = amount_total = Decimal("0.00")
    for idx, ln in enumerate(lines):
        half = _q(ln["tax"] / 2)
        cgst, sgst = half, ln["tax"] - half
        tax_cells = [_num(cgst), _num(sgst)] if intra else [_num(ln["tax"])]
        body_rows.append(
            [str(idx + 1), "", ln["hsn"] or "-", str(ln["qty"]), _num(ln["unit"]), _num(ln["taxable"]),
             "%s%%" % (_fmt_num(ln["rate"]) or "0")] + tax_cells + [_num(ln["amount"])]
        )
        title = ln["title"] if len(ln["title"]) <= 120 else ln["title"][:120].rstrip() + "..."
        markup = escape(title)
        if ln["variant"]:
            markup += '<br/><font color="#555555">%s</font>' % escape(ln["variant"])
        if ln["sku"]:
            markup += '<br/><font color="#555555">SKU: %s</font>' % escape(ln["sku"])
        descs.append(markup)
        qty_total += ln["qty"]
        taxable_total += ln["taxable"]
        tax_total += ln["tax"]
        cgst_total += cgst
        sgst_total += sgst
        amount_total += ln["amount"]
    tax_totals = [_num(cgst_total), _num(sgst_total)] if intra else [_num(tax_total)]
    total_row = ["", "TOTAL", "", str(qty_total), "", _num(taxable_total), ""] + tax_totals + [_num(amount_total)]

    # Column widths come from the actual text (headers by their longest word,
    # since header cells wrap) so numbers stay on one line; Description takes
    # what is left. Every cell is a Paragraph, so anything that still doesn't
    # fit wraps inside its own cell instead of spilling into a neighbour.
    cell_pad = 1.5
    col_widths = [0.0] * n_cols
    for col in range(n_cols):
        if col == 1:
            continue
        head_w = max(stringWidth(w, "Helvetica-Bold", table_font_size) for w in header_row[col].split())
        cell_w = max(stringWidth(str(r[col]), "Helvetica-Bold", table_font_size) for r in body_rows + [total_row])
        col_widths[col] = max(head_w, cell_w) + 2 * cell_pad + 1
    fixed_width = sum(col_widths)
    min_desc_width = inner_width * 0.24
    if inner_width - fixed_width >= min_desc_width:
        col_widths[1] = inner_width - fixed_width
    else:
        scale = (inner_width - min_desc_width) / fixed_width
        col_widths = [w * scale for w in col_widths]
        col_widths[1] = min_desc_width

    leading = table_font_size + 1.5
    head_style = ParagraphStyle("inv_head", fontName="Helvetica-Bold", fontSize=table_font_size, leading=leading, alignment=TA_CENTER, textColor=HexColor(BLUE_HEADER))
    cell_styles = {
        TA_LEFT: ParagraphStyle("inv_cell_l", fontName="Helvetica", fontSize=table_font_size, leading=leading, alignment=TA_LEFT),
        TA_CENTER: ParagraphStyle("inv_cell_c", fontName="Helvetica", fontSize=table_font_size, leading=leading, alignment=TA_CENTER),
        TA_RIGHT: ParagraphStyle("inv_cell_r", fontName="Helvetica", fontSize=table_font_size, leading=leading, alignment=TA_RIGHT),
    }
    total_styles = {
        key: ParagraphStyle(style.name + "_b", parent=style, fontName="Helvetica-Bold")
        for key, style in cell_styles.items()
    }

    def _align(col):
        if col in (0, 2, 6):
            return TA_CENTER
        if col == 1:
            return TA_LEFT
        return TA_RIGHT

    data = [[Paragraph(escape(h), head_style) for h in header_row]]
    for row, desc in zip(body_rows, descs):
        cells = []
        for i, v in enumerate(row):
            cells.append(Paragraph(desc if i == 1 else escape(v), cell_styles[_align(i)]))
        data.append(cells)
    data.append([Paragraph(escape(v), total_styles[_align(i)]) for i, v in enumerate(total_row)])
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HexColor(BLUE_TABLE)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ("LEFTPADDING", (0, 0), (-1, -1), cell_pad),
        ("RIGHTPADDING", (0, 0), (-1, -1), cell_pad),
        ("BACKGROUND", (0, -1), (-1, -1), HexColor("#f5f5f5")),
        ("GRID", (0, 0), (-1, -1), 0.5, HexColor("#cccccc")),
        ("BOX", (0, 0), (-1, -1), 1, HexColor("#000000")),
    ]))

    # ── Totals block (measured first so the table knows what must follow) ─
    discount = Decimal("0.00")
    shipping = Decimal("0.00")
    grand_total = amount_total
    if order is not None:
        discount = _q(getattr(order, "discount_amount", 0)) + _q(getattr(order, "credit_discount_amount", 0))
        shipping = _q(getattr(order, "shipping_amount", 0))
        grand_total = _q(order.total)
    summary = [("Items total", _money(amount_total), False)]
    if discount > 0:
        summary.append(("Discount", "- %s" % _money(discount), False))
    if shipping > 0:
        summary.append(("Shipping", _money(shipping), False))
    # Per-line rounding (or an owner-edited total) can leave a difference
    # against the order's own total; show it rather than hide it.
    difference = grand_total - (amount_total - discount + shipping)
    if difference != 0:
        sign = "- " if difference < 0 else ""
        summary.append(("Round off" if abs(difference) < 1 else "Adjustment", "%s%s" % (sign, _money(abs(difference))), False))
    summary.append(("Grand Total", _money(grand_total), True))
    if pay["mode"] == "partial":
        summary.append(("Paid online", _money(pay["paid"]), False))
        summary.append(("To collect on delivery", _money(pay["collect"]), True))
    elif pay["mode"] == "cod":
        summary.append(("To collect on delivery", _money(pay["collect"]), True))
    else:
        summary.append(("Paid online", _money(grand_total), False))

    words_w = inner_width * 0.5 - 3 * mm
    words = _wrap_width(_amount_to_words(grand_total), "Helvetica-Oblique", small_text_size, words_w, 4)
    block_h = max(len(summary), 1 + len(words)) * line_height
    bottom_h = section_gap + block_h + 1 * mm + sig_box_height

    # ── Draw the table, continuing on new pages as needed ─────────────────
    remaining = table
    fresh = False  # nothing but the continuation header on this page yet
    while remaining is not None:
        x = _left()
        avail = y - _floor()
        _, th = remaining.wrapOn(c, inner_width, max(avail, 1))
        if th <= avail - bottom_h:
            remaining.drawOn(c, x, y - th)
            y -= th
            break
        if th <= avail:
            # The rows fit, the totals don't: they open the next page.
            remaining.drawOn(c, x, y - th)
            y = _new_page()
            break
        parts = remaining.split(inner_width, avail) if avail > 0 else []
        if len(parts) >= 2:
            first = parts[0]
            _, fh = first.wrapOn(c, inner_width, avail)
            first.drawOn(c, x, y - fh)
            remaining = parts[1]
            y = _new_page()
            fresh = True
            continue
        if not fresh:
            y = _new_page()
            fresh = True
            continue
        # A single row taller than a whole page: draw it and carry on.
        remaining.drawOn(c, x, y - th)
        y -= th
        break

    # ── Totals, amount in words, signature ────────────────────────────────
    x = _left()
    y -= section_gap
    top = y

    sum_x = x + inner_width * 0.5
    sum_w = inner_width * 0.5
    sy = top
    for label, value, strong in summary:
        font = "Helvetica-Bold" if strong else "Helvetica"
        if label == "Grand Total":
            c.setStrokeColor(black)
            c.setLineWidth(0.5)
            c.line(sum_x, sy + line_height * 0.72, x + inner_width, sy + line_height * 0.72)
        c.setFont(font, text_size)
        c.setFillColor(black)
        value_w = c.stringWidth(value, font, text_size)
        c.drawString(sum_x, sy, _wrap_width(label, font, text_size, sum_w - value_w - 2 * mm, 1)[0])
        c.drawRightString(x + inner_width, sy, value)
        sy -= line_height

    wy = top
    c.setFont("Helvetica-Bold", small_text_size)
    c.setFillColor(black)
    c.drawString(x, wy, "Amount in Words:")
    c.setFont("Helvetica-Oblique", small_text_size)
    c.setFillColor(HexColor("#2e7d32"))
    for line in words:
        wy -= line_height
        c.drawString(x, wy, line)

    y = top - block_h - 1 * mm
    sig_box_width = inner_width * 0.4
    sig_box_x = x + inner_width - sig_box_width
    sig_box_y = y - sig_box_height
    c.setStrokeColor(black)
    c.setLineWidth(1)
    c.rect(sig_box_x, sig_box_y, sig_box_width, sig_box_height)
    c.setFont("Helvetica-Bold", small_text_size)
    c.setFillColor(black)
    c.drawString(
        sig_box_x + 2 * mm,
        y - 3 * mm,
        _wrap_width("For %s" % (seller_name or ""), "Helvetica-Bold", small_text_size, sig_box_width - 4 * mm, 1)[0],
    )
    # The signature uploaded on Store settings, fitted (aspect kept, centred)
    # between the "For <seller>" line and the "Authorized Signatory" caption.
    signature = _signature_image(website, payment_config, sig_cache)
    if signature is not None:
        try:
            c.drawImage(
                signature,
                sig_box_x + 2 * mm, sig_box_y + 4.5 * mm,
                sig_box_width - 4 * mm, sig_box_height - 9.5 * mm,
                preserveAspectRatio=True, anchor="c",
            )
        except Exception as e:
            logger.warning("label: signature draw failed: %s", e)
    c.setFont("Helvetica", small_text_size - 1)
    c.setFillColor(HexColor("#cc9966"))
    c.drawString(sig_box_x + 2 * mm, sig_box_y + 2 * mm, "Authorized Signatory")

    c.setFont("Helvetica", small_text_size)
    c.setFillColor(black)
    c.drawString(x, sig_box_y + 2 * mm, "Tax payable under reverse charge: No")
