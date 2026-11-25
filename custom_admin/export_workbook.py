"""Lay out a :class:`custom_admin.exports.Report` as a styled ``.xlsx``.

Two sheets:

* **Report**  a title block, a two-row header (column groups over column
  names), one row per record with Excel filters on every column, the header
  frozen while scrolling, and a totals row using ``SUBTOTAL`` so the totals
  follow whatever filter is applied. Commerce fee reports link the payment
  screenshot and the bill's invoice PDF (clickable URLs, nothing embedded).
* **Summary**  the headline totals, then the same money broken down by
  product / service and by state (place of supply), and a short note on how
  each figure is worked out.

Written with the dependency-free :mod:`commerce.xlsx`.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal

from commerce import xlsx
from commerce.xlsx import _col_letter

from .exports import RENEWALS, Report, ReportRow, retention_days

_ZERO = Decimal("0.00")


@dataclass(frozen=True)
class Col:
    key: str
    header: str
    width: float
    kind: str  # sr | date | text | wrap | money | int | link
    group: str


_ID = "Record"
_CUSTOMER = "Customer"
_PURCHASE = "Purchase"
_TAX = "Amount and GST (₹)"
_TERM = "Location and term"
_TOTAL = "Total"
_RENEWAL = "Renewal"
_PROOF = "Payment proof"


def _columns(report: Report) -> list[Col]:
    spec = report.spec
    date_header = "Paid on" if spec.uses_date_range else "Purchased on"
    cols = [
        Col("sr", "Sr. no.", 7, "sr", _ID),
        Col("date", date_header, 13, "date", _ID),
        Col("number", "Order / invoice no.", 18, "text", _ID),
        Col("name", "Name of the user", 22, "wrap", _CUSTOMER),
        Col("email", "Email of the user", 30, "wrap", _CUSTOMER),
        Col("mobile", "Mobile of the user", 15, "text", _CUSTOMER),
        Col("gstin", "GST", 18, "text", _CUSTOMER),
        Col("company", "Company", 26, "wrap", _CUSTOMER),
        Col("websites", "Websites", 36, "wrap", _CUSTOMER),
        Col("product_type", "Product / service type", 22, "wrap", _PURCHASE),
        Col("item", "Discount code / Product / Service name", 34, "wrap", _PURCHASE),
        Col("taxable", "Paid amount", 14, "money", _TAX),
        Col("cgst", "CGST", 12, "money", _TAX),
        Col("sgst", "SGST", 12, "money", _TAX),
        Col("igst", "IGST", 12, "money", _TAX),
        Col("others", "Others", 11, "money", _TAX),
        Col("city", "City", 16, "wrap", _TERM),
        Col("state", "State (place of supply)", 18, "wrap", _TERM),
        Col("duration", "Purchase duration", 32, "wrap", _TERM),
        Col("total", "Total amount paid", 16, "money", _TOTAL),
    ]
    if spec.group == RENEWALS:
        cols += [
            Col("period_end", "Plan ends on", 13, "date", _RENEWAL),
            Col("days", "Days left (minus = overdue)", 13, "int", _RENEWAL),
            Col("deletion_on", "Data deletion on", 14, "date", _RENEWAL),
            Col("standing", "Status", 30, "wrap", _RENEWAL),
        ]
    if spec.is_commerce:
        cols += [
            Col("payment_status", "Payment status", 15, "wrap", _PROOF),
            Col("reference", "Reference number", 22, "wrap", _PROOF),
            Col("attachment_url", "Attachment", 40, "link", _PROOF),
            Col("photo_url", "Photos", 40, "link", _PROOF),
        ]
    return cols



def _title(report: Report) -> str:
    return f"OneWooz | {report.spec.label}"


def _generated(report: Report) -> str:
    when = report.generated_at.strftime("%d %b %Y, %H:%M")
    zone = report.generated_at.strftime("%Z")
    by = f" by {report.generated_by}" if report.generated_by else ""
    return f"Generated {when}{(' ' + zone) if zone else ''}{by}"


# --------------------------------------------------------------------------
# Report sheet
# --------------------------------------------------------------------------


def _report_sheet(wb: xlsx.Workbook, report: Report) -> None:
    ws = wb.add_sheet("Report")
    ws.tab_color = "4B2BF5"
    ws.landscape = True
    cols = _columns(report)
    last_col = len(cols) - 1
    for i, col in enumerate(cols):
        ws.set_column_width(i, col.width)

    # -- title block (rows 0-2) -------------------------------------------
    ws.write(0, 0, _title(report), xlsx.STYLE_TITLE)
    ws.merge(0, 0, 0, min(last_col, 10))
    ws.set_row_height(0, 26)
    count = len(report.rows)
    ws.write(
        1, 0,
        f"{report.period_label}  |  {count} record{'' if count == 1 else 's'}  |  {_generated(report)}",
        xlsx.STYLE_SUBTITLE,
    )
    ws.merge(1, 0, 1, min(last_col, 14))
    detail = "  |  ".join([report.spec.description, *report.filters, *report.notes])
    ws.write(2, 0, detail, xlsx.STYLE_SUBTITLE)
    ws.merge(2, 0, 2, min(last_col, 14))

    # -- two-row header (rows 3-4) ----------------------------------------
    group_row, head_row = 3, 4
    start = 0
    while start <= last_col:
        group = cols[start].group
        end = start
        while end + 1 <= last_col and cols[end + 1].group == group:
            end += 1
        ws.write(group_row, start, group, xlsx.STYLE_HEADER_GROUP)
        for c in range(start + 1, end + 1):
            ws.write(group_row, c, "", xlsx.STYLE_HEADER_GROUP)
        if end > start:
            ws.merge(group_row, start, group_row, end)
        start = end + 1
    for i, col in enumerate(cols):
        style = xlsx.STYLE_HEADER_NUM if col.kind == "money" else xlsx.STYLE_HEADER
        ws.write(head_row, i, col.header, style)
    ws.set_row_height(group_row, 20)
    ws.set_row_height(head_row, 34)
    ws.freeze_row = head_row + 1

    first_data = head_row + 1
    if not report.rows:
        ws.write(first_data, 0, "No records match this report.", xlsx.STYLE_CELL_MUTED_MID)
        for c in range(1, last_col + 1):
            ws.write(first_data, c, "", xlsx.STYLE_CELL_MUTED_MID)
        ws.merge(first_data, 0, first_data, last_col)
        ws.set_row_height(first_data, 30)
        ws.auto_filter = (head_row, 0, head_row, last_col)
        return

    # -- data rows -----------------------------------------------------------
    for n, row in enumerate(report.rows):
        r = first_data + n
        for i, col in enumerate(cols):
            _write_cell(ws, r, i, col, row, n + 1)
    last_data = first_data + len(report.rows) - 1

    # -- totals row (SUBTOTAL follows any filter applied) ---------------------
    t = last_data + 1
    label_end = next(i for i, c in enumerate(cols) if c.kind == "money") - 1
    ws.write(t, 0, f"Total ({len(report.rows)} record{'' if len(report.rows) == 1 else 's'})",
             xlsx.STYLE_TOTAL_LABEL)
    for c in range(1, last_col + 1):
        ws.write(t, c, "", xlsx.STYLE_TOTAL_LABEL)
    ws.merge(t, 0, t, label_end)
    for i, col in enumerate(cols):
        if col.kind == "money":
            letter = _col_letter(i)
            cached = sum((getattr(r_, col.key) for r_ in report.rows), _ZERO)
            ws.write(
                t, i,
                xlsx.Formula(f"SUBTOTAL(109,{letter}{first_data + 1}:{letter}{last_data + 1})", cached),
                xlsx.STYLE_TOTAL_MONEY,
            )
    ws.set_row_height(t, 22)
    ws.auto_filter = (head_row, 0, last_data, last_col)


def _write_cell(ws, r: int, i: int, col: Col, row: ReportRow, sr: int) -> None:
    if col.kind == "sr":
        ws.write(r, i, sr, xlsx.STYLE_CELL_INT_MID)
        return
    if col.kind == "link":
        url = getattr(row, col.key)
        if url:
            # The URL itself is the cell text: visible, copyable and clickable.
            ws.write(r, i, url, xlsx.STYLE_LINK)
            ws.add_hyperlink(r, i, url)
        else:
            missing = "Not uploaded" if col.key == "photo_url" else "Not generated yet"
            ws.write(r, i, missing, xlsx.STYLE_CELL_MUTED_MID)
        return
    value = getattr(row, col.key)
    if col.kind == "money":
        ws.write(r, i, value if value is not None else _ZERO, xlsx.STYLE_CELL_MONEY_MID)
    elif col.kind == "date":
        ws.write(r, i, value, xlsx.STYLE_CELL_DATE_MID)
    elif col.kind == "int":
        ws.write(r, i, value if value is not None else "", xlsx.STYLE_CELL_INT_MID)
    elif col.kind == "text":
        ws.write(r, i, value or "", xlsx.STYLE_CELL_CENTER_NOWRAP)
    else:
        ws.write(r, i, value or "", xlsx.STYLE_CELL_MID)


# --------------------------------------------------------------------------
# Summary sheet
# --------------------------------------------------------------------------


def _summary_sheet(wb: xlsx.Workbook, report: Report) -> None:
    ws = wb.add_sheet("Summary")
    ws.tab_color = "1E1A3A"
    ws.show_grid_lines = False
    widths = (34, 12, 16, 14, 14, 14, 12, 18)
    for i, w in enumerate(widths):
        ws.set_column_width(i, w)

    rows = report.rows
    ws.write(0, 0, f"{_title(report)}: summary", xlsx.STYLE_TITLE)
    ws.merge(0, 0, 0, 7)
    ws.set_row_height(0, 26)
    ws.write(1, 0, f"{report.period_label}  |  {_generated(report)}", xlsx.STYLE_SUBTITLE)
    ws.merge(1, 0, 1, 7)

    # -- headline totals ----------------------------------------------------
    r = 3
    ws.write(r, 0, "Headline", xlsx.STYLE_HEADER)
    ws.write(r, 1, "", xlsx.STYLE_HEADER)
    ws.merge(r, 0, r, 1)
    ws.write(r, 2, "Amount (₹)", xlsx.STYLE_HEADER_NUM)
    r += 1

    def total(key):
        return sum((getattr(x, key) for x in rows), _ZERO)

    lines = [
        ("Records", len(rows), xlsx.STYLE_AGG_INT),
        ("Paid amount (taxable value)", total("taxable"), xlsx.STYLE_AGG_MONEY),
        ("CGST", total("cgst"), xlsx.STYLE_AGG_MONEY),
        ("SGST", total("sgst"), xlsx.STYLE_AGG_MONEY),
        ("IGST", total("igst"), xlsx.STYLE_AGG_MONEY),
        ("Total GST", total("cgst") + total("sgst") + total("igst"), xlsx.STYLE_AGG_MONEY),
        ("Others", total("others"), xlsx.STYLE_AGG_MONEY),
    ]
    for label, value, style in lines:
        ws.write(r, 0, label, xlsx.STYLE_AGG_LABEL)
        ws.write(r, 1, "", xlsx.STYLE_AGG_LABEL)
        ws.merge(r, 0, r, 1)
        ws.write(r, 2, value, style)
        r += 1
    ws.write(r, 0, "Total amount paid", xlsx.STYLE_AGG_DUE_LABEL)
    ws.write(r, 1, "", xlsx.STYLE_AGG_DUE_LABEL)
    ws.merge(r, 0, r, 1)
    ws.write(r, 2, total("total"), xlsx.STYLE_AGG_DUE_MONEY)
    ws.set_row_height(r, 22)
    r += 2

    # -- by product / service ------------------------------------------------
    r = _breakdown(ws, r, "By product / service", rows, key=lambda x: x.item or x.product_type or "Unnamed")
    r += 1
    # -- by state --------------------------------------------------------------
    r = _breakdown(ws, r, "By state (place of supply)", rows, key=lambda x: x.state or "State not given")
    r += 1

    # -- how figures are worked out ----------------------------------------------
    ws.write(r, 0, "How these figures are worked out", xlsx.STYLE_META_LABEL)
    r += 1
    notes = [
        "Paid amount is the taxable value (before GST). Total amount paid = paid amount + CGST + SGST + IGST + others.",
        "CGST + SGST apply when the buyer's state (from their GSTIN, else the state they gave) matches the "
        "seller's state; otherwise IGST. The buyer's state is shown in the Report sheet.",
        "Others holds any amount in the total beyond the taxable value and GST (paise rounding on the invoice); "
        "it is normally 0.00.",
    ]
    if report.spec.key == "subscription_sales" or report.spec.group == RENEWALS:
        notes.append("Subscription figures use the same arithmetic as the owner's subscription bill: "
                     "taxable value = amount paid / (1 + GST rate).")
    if report.spec.is_commerce:
        notes.append("Commerce fee figures are the GST breakup printed on each bill. Bills issued before GST "
                     "was added carry no GST.")
        notes.append("Photos links to the payment screenshot the owner uploaded; Attachment links to the "
                     "bill's invoice PDF. Click a link to open the file.")
    if report.spec.group == RENEWALS:
        notes.append(f"Data deletion date = plan end date + {retention_days()} days "
                     "(settings.SUBSCRIPTION_DATA_RETENTION_DAYS).")
    notes.extend(report.notes)
    for note in notes:
        ws.write(r, 0, f"•  {note}", xlsx.STYLE_SUBTITLE)
        ws.merge(r, 0, r, 7)
        r += 1


def _breakdown(ws, r: int, title: str, rows, *, key) -> int:
    groups: OrderedDict[str, list] = OrderedDict()
    for row in sorted(rows, key=lambda x: (key(x) or "").lower()):
        groups.setdefault(key(row), []).append(row)

    headers = (title, "Records", "Paid amount", "CGST", "SGST", "IGST", "Others", "Total amount paid")
    for i, h in enumerate(headers):
        ws.write(r, i, h, xlsx.STYLE_HEADER if i == 0 else xlsx.STYLE_HEADER_NUM)
    ws.set_row_height(r, 22)
    r += 1
    if not groups:
        ws.write(r, 0, "No records", xlsx.STYLE_CELL_MUTED_MID)
        for c in range(1, 8):
            ws.write(r, c, "", xlsx.STYLE_CELL_MUTED_MID)
        ws.merge(r, 0, r, 7)
        return r + 1

    def s(items, attr):
        return sum((getattr(x, attr) for x in items), _ZERO)

    for name, items in groups.items():
        ws.write(r, 0, name, xlsx.STYLE_CELL_MID)
        ws.write(r, 1, len(items), xlsx.STYLE_CELL_INT_MID)
        for c, attr in enumerate(("taxable", "cgst", "sgst", "igst", "others", "total"), start=2):
            ws.write(r, c, s(items, attr), xlsx.STYLE_CELL_MONEY_MID)
        r += 1
    ws.write(r, 0, "Total", xlsx.STYLE_TOTAL_LABEL)
    ws.write(r, 1, len(rows), xlsx.STYLE_TOTAL_INT)
    for c, attr in enumerate(("taxable", "cgst", "sgst", "igst", "others", "total"), start=2):
        ws.write(r, c, s(rows, attr), xlsx.STYLE_TOTAL_MONEY)
    return r + 1


# --------------------------------------------------------------------------


def to_xlsx(report: Report) -> bytes:
    wb = xlsx.Workbook()
    _report_sheet(wb, report)
    _summary_sheet(wb, report)
    return wb.to_bytes()


def filename(report: Report, date_from=None, date_to=None) -> str:
    slug = report.spec.key.replace("_", "-")
    if report.spec.uses_date_range and (date_from or date_to):
        span = f"{date_from.isoformat() if date_from else 'start'}-to-{date_to.isoformat() if date_to else 'today'}"
    else:
        span = report.generated_at.date().isoformat()
    return f"onewooz-{slug}-{span}.xlsx"
