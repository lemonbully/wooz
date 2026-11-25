"""A tiny, dependency-free ``.xlsx`` writer (Office Open XML / SpreadsheetML).

The project ships no spreadsheet library (``openpyxl``/``xlsxwriter`` are not in
``requirements.txt``), and the working agreement forbids introducing manual
setup steps. So rather than add a dependency the operator would have to install,
this module builds a valid ``.xlsx`` package by hand from the standard library
(``zipfile`` + a little XML). It supports exactly what the commission export
needs and nothing more:

* inline-string, number, integer and percentage cells;
* a small fixed palette of **professional, coloured cell styles**  an accent
  header band, bordered body cells, a shaded aggregate block  selected per cell
  with the ``STYLE_*`` constants below;
* merged cells, per-column widths, fixed row heights, a frozen header row
  and an Excel filter (autoFilter) on a header row;
* real date cells (``datetime.date`` / ``datetime.datetime``), clickable
  external hyperlinks, pictures anchored to a cell (PNG / JPEG), a landscape
  fit-to-width print setup, a sheet tab colour and hidden gridlines.

An ``.xlsx`` file is a ZIP of XML parts. We emit the minimum set Excel, LibreOffice
and Google Sheets all open cleanly: ``[Content_Types].xml``, the two ``.rels``
parts, ``workbook.xml``, ``styles.xml`` and one worksheet. Strings are written
inline (``t="inlineStr"``) so there is no shared-strings table to keep in sync.
"""

from __future__ import annotations

import datetime as _dt
import io
import zipfile
from decimal import Decimal

# --- Cell styles ----------------------------------------------------------
# Each constant is an index into the <cellXfs> list baked into _STYLES_XML
# below. Keep the two in lock-step: the Nth <xf> is style N.
STYLE_DEFAULT = 0        # plain text, no border
STYLE_TITLE = 1          # large bold display title
STYLE_SUBTITLE = 2       # small muted caption
STYLE_META_LABEL = 3     # bold label in the meta block
STYLE_META_VALUE = 4     # plain value in the meta block
STYLE_HEADER = 5         # table header: bold white on the accent fill
STYLE_HEADER_NUM = 6     # right-aligned header (numeric columns)
STYLE_CELL = 7           # body text cell (wrap, top-aligned, bordered)
STYLE_CELL_CENTER = 8    # body cell, centred
STYLE_CELL_MONEY = 9     # body number, 2dp, right-aligned
STYLE_CELL_INT = 10      # body integer, thousands, right-aligned
STYLE_CELL_PCT = 11      # body percentage (value is a fraction), right-aligned
STYLE_TOTAL_LABEL = 12   # table total row: bold label
STYLE_TOTAL_MONEY = 13   # table total row: bold money
STYLE_AGG_LABEL = 14     # aggregate block: bold label on a tinted fill
STYLE_AGG_MONEY = 15     # aggregate block: bold money on a tinted fill
STYLE_AGG_PCT = 16       # aggregate block: bold percentage on a tinted fill
STYLE_AGG_TEXT = 17      # aggregate block: bold text on a tinted fill
STYLE_AGG_DUE_LABEL = 18  # the headline "amount payable" label (accent band)
STYLE_AGG_DUE_MONEY = 19  # the headline "amount payable" value (accent band)
STYLE_CELL_NOWRAP = 20   # body text on one line (no wrap), vertically centred
STYLE_CELL_CENTER_NOWRAP = 21  # body text centred on one line
STYLE_AGG_INT = 22       # aggregate block: bold integer on a tinted fill
# Vertically centred body styles (a row carrying a picture is tall, and centred
# text reads better beside it) plus a few report extras.
STYLE_LINK = 23          # hyperlink text: accent, underlined, left, vertically centred
STYLE_CELL_MID = 24      # body text, wrap, vertically centred
STYLE_CELL_MONEY_MID = 25  # body money, 2dp, right, vertically centred
STYLE_CELL_INT_MID = 26  # body integer, centred
STYLE_CELL_DATE_MID = 27  # body date "dd mmm yyyy", centred
STYLE_HEADER_GROUP = 28  # group band above the column headers (ink, white)
STYLE_TOTAL_INT = 29     # table total row: bold integer
STYLE_CELL_MUTED_MID = 30  # small muted text, centred (e.g. "Not uploaded")
STYLE_AGG_DATE = 31      # aggregate block: bold date on a tinted fill


class Formula:
    """A formula cell: ``expr`` without the leading ``=``, plus the value to
    show until the spreadsheet recalculates (it does on open)."""

    def __init__(self, expr: str, cached=None):
        self.expr = expr
        self.cached = cached


def _col_letter(idx: int) -> str:
    """0-based column index → spreadsheet column letters (0→A, 26→AA)."""
    letters = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


_EXCEL_EPOCH = _dt.datetime(1899, 12, 30)
#: Pixels to EMUs (English Metric Units), the unit DrawingML positions use.
_EMU_PER_PX = 9525


def _excel_serial(value) -> float:
    """A date / datetime as an Excel serial day number (1900 date system).
    An aware datetime is written as its own wall-clock time."""
    if isinstance(value, _dt.datetime):
        value = value.replace(tzinfo=None)
        delta = value - _EXCEL_EPOCH
        return round(delta.days + delta.seconds / 86400, 6)
    return float((_dt.datetime(value.year, value.month, value.day) - _EXCEL_EPOCH).days)


def _esc(text) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


class Worksheet:
    def __init__(self, name: str):
        # Excel forbids these in a sheet name, and caps it at 31 chars.
        for ch in r'[]:*?/\\':
            name = name.replace(ch, " ")
        self.name = (name or "Sheet1")[:31]
        self._rows: dict[int, dict[int, tuple]] = {}
        self._merges: list[str] = []
        self._col_widths: dict[int, float] = {}
        self._max_row = 0
        self._max_col = 0
        self._row_heights: dict[int, float] = {}
        self._hidden_cols: set[int] = set()
        self.freeze_row: int | None = None
        # (first_row, first_col, last_row, last_col), 0-based: an Excel filter
        # drop-down on every column of ``first_row`` over the rows below it.
        self.auto_filter: tuple[int, int, int, int] | None = None
        self._hyperlinks: list[tuple[int, int, str]] = []
        self._images: list[dict] = []
        #: Print on landscape pages, scaled to the width of one page.
        self.landscape = False
        #: Sheet tab colour as "RRGGBB" (no #), or None.
        self.tab_color: str | None = None
        self.show_grid_lines = True

    def add_hyperlink(self, row: int, col: int, url: str) -> None:
        """Make the cell at (row, col) a clickable link to an external ``url``.
        Write the cell's display text separately (``STYLE_LINK`` suits it)."""
        if url:
            self._hyperlinks.append((row, col, str(url)))

    def add_image(
        self, row: int, col: int, data: bytes, ext: str, width_px: int, height_px: int,
        *, x_offset_px: int = 0, y_offset_px: int = 0, description: str = "",
    ) -> None:
        """Embed a picture whose top-left corner sits in cell (row, col).

        ``ext`` is "png" or "jpeg". The picture moves with its cell but keeps
        its own size, so size the row and column to fit it."""
        ext = "jpeg" if ext.lower() in ("jpg", "jpeg") else "png"
        self._images.append({
            "row": row, "col": col, "data": data, "ext": ext,
            "w": max(1, int(width_px)), "h": max(1, int(height_px)),
            "dx": max(0, int(x_offset_px)), "dy": max(0, int(y_offset_px)),
            "descr": str(description or "")[:200],
        })

    @property
    def has_drawing(self) -> bool:
        return bool(self._images)

    def set_column_width(self, col: int, width: float) -> None:
        self._col_widths[col] = width

    def hide_column(self, col: int) -> None:
        """Hide a column (helper data a formula reads but nobody needs to see)."""
        self._hidden_cols.add(col)

    def set_row_height(self, row: int, height: float) -> None:
        """Pin a row's height (points), so long text never makes it grow."""
        self._row_heights[row] = height

    def write(self, row: int, col: int, value, style: int = STYLE_DEFAULT) -> None:
        self._rows.setdefault(row, {})[col] = (value, style)
        self._max_row = max(self._max_row, row)
        self._max_col = max(self._max_col, col)

    def write_row(self, row: int, start_col: int, values, style: int = STYLE_DEFAULT) -> None:
        for offset, value in enumerate(values):
            self.write(row, start_col + offset, value, style)

    def merge(self, row1: int, col1: int, row2: int, col2: int) -> None:
        self._merges.append(f"{_col_letter(col1)}{row1 + 1}:{_col_letter(col2)}{row2 + 1}")

    # -- serialisation -----------------------------------------------------
    def _cell_xml(self, row: int, col: int, value, style: int) -> str:
        ref = f"{_col_letter(col)}{row + 1}"
        if isinstance(value, Formula):
            cached = value.cached
            v = f"<v>{cached}</v>" if isinstance(cached, (int, float, Decimal)) and not isinstance(cached, bool) else ""
            return f'<c r="{ref}" s="{style}"><f>{_esc(value.expr)}</f>{v}</c>'
        if value is None or value == "":
            return f'<c r="{ref}" s="{style}"/>'
        if isinstance(value, bool):
            value = "Yes" if value else "No"
        if isinstance(value, (_dt.date, _dt.datetime)):
            return f'<c r="{ref}" s="{style}"><v>{_excel_serial(value)}</v></c>'
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
        text = _esc(value)
        return (
            f'<c r="{ref}" s="{style}" t="inlineStr">'
            f'<is><t xml:space="preserve">{text}</t></is></c>'
        )

    def to_xml(self) -> str:
        """The worksheet part. Relationship ids (used by hyperlinks and the
        drawing) follow :meth:`rels_xml`: rId1..n are the hyperlinks in order,
        then the drawing."""
        parts = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">',
        ]
        if self.tab_color or self.landscape:
            tab = f'<tabColor rgb="FF{self.tab_color.upper()}"/>' if self.tab_color else ""
            fit = '<pageSetUpPr fitToPage="1"/>' if self.landscape else ""
            parts.append(f"<sheetPr>{tab}{fit}</sheetPr>")
        if self._max_row or self._max_col:
            dim = f"A1:{_col_letter(self._max_col)}{self._max_row + 1}"
            parts.append(f'<dimension ref="{dim}"/>')
        # Freeze everything above ``freeze_row`` so the header stays put.
        grid = "" if self.show_grid_lines else ' showGridLines="0"'
        if self.freeze_row:
            parts.append(
                f'<sheetViews><sheetView workbookViewId="0"{grid}>'
                f'<pane ySplit="{self.freeze_row}" topLeftCell="A{self.freeze_row + 1}" '
                'activePane="bottomLeft" state="frozen"/>'
                '</sheetView></sheetViews>'
            )
        elif grid:
            parts.append(f'<sheetViews><sheetView workbookViewId="0"{grid}/></sheetViews>')
        parts.append('<sheetFormatPr defaultRowHeight="15"/>')
        cols = sorted(set(self._col_widths) | self._hidden_cols)
        if cols:
            parts.append("<cols>")
            for col in cols:
                n = col + 1
                width = self._col_widths.get(col, 10)
                hidden = ' hidden="1"' if col in self._hidden_cols else ""
                parts.append(
                    f'<col min="{n}" max="{n}" width="{width:.2f}" customWidth="1"{hidden}/>'
                )
            parts.append("</cols>")
        parts.append("<sheetData>")
        for row in sorted(self._rows):
            cells = self._rows[row]
            height = self._row_heights.get(row)
            if height:
                parts.append(f'<row r="{row + 1}" ht="{height:.2f}" customHeight="1">')
            else:
                parts.append(f'<row r="{row + 1}">')
            for col in sorted(cells):
                value, style = cells[col]
                parts.append(self._cell_xml(row, col, value, style))
            parts.append("</row>")
        parts.append("</sheetData>")
        if self.auto_filter:
            r1, c1, r2, c2 = self.auto_filter
            parts.append(
                f'<autoFilter ref="{_col_letter(c1)}{r1 + 1}:{_col_letter(c2)}{max(r1, r2) + 1}"/>'
            )
        if self._merges:
            parts.append(f'<mergeCells count="{len(self._merges)}">')
            parts.extend(f'<mergeCell ref="{ref}"/>' for ref in self._merges)
            parts.append("</mergeCells>")
        if self._hyperlinks:
            parts.append("<hyperlinks>")
            for i, (row, col, _url) in enumerate(self._hyperlinks, start=1):
                parts.append(f'<hyperlink ref="{_col_letter(col)}{row + 1}" r:id="rId{i}"/>')
            parts.append("</hyperlinks>")
        if self.landscape:
            parts.append(
                '<pageMargins left="0.4" right="0.4" top="0.5" bottom="0.5" header="0.3" footer="0.3"/>'
                '<pageSetup paperSize="9" orientation="landscape" fitToWidth="1" fitToHeight="0"/>'
            )
        if self._images:
            parts.append(f'<drawing r:id="rId{len(self._hyperlinks) + 1}"/>')
        parts.append("</worksheet>")
        return "".join(parts)

    @property
    def needs_rels(self) -> bool:
        return bool(self._hyperlinks or self._images)

    def rels_xml(self, drawing_index: int) -> str:
        """``xl/worksheets/_rels/sheetN.xml.rels``: hyperlinks, then the drawing."""
        rels = [
            f'<Relationship Id="rId{i}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            f'Target="{_esc(url)}" TargetMode="External"/>'
            for i, (_r, _c, url) in enumerate(self._hyperlinks, start=1)
        ]
        if self._images:
            rels.append(
                f'<Relationship Id="rId{len(self._hyperlinks) + 1}" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" '
                f'Target="../drawings/drawing{drawing_index}.xml"/>'
            )
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{''.join(rels)}</Relationships>"
        )

    def drawing_xml(self) -> str:
        """``xl/drawings/drawingN.xml``: one one-cell anchored picture per
        image, embedding ``rId1..n`` of the drawing's own rels."""
        anchors = []
        for i, img in enumerate(self._images, start=1):
            cx, cy = img["w"] * _EMU_PER_PX, img["h"] * _EMU_PER_PX
            anchors.append(
                "<xdr:oneCellAnchor>"
                f"<xdr:from><xdr:col>{img['col']}</xdr:col><xdr:colOff>{img['dx'] * _EMU_PER_PX}</xdr:colOff>"
                f"<xdr:row>{img['row']}</xdr:row><xdr:rowOff>{img['dy'] * _EMU_PER_PX}</xdr:rowOff></xdr:from>"
                f'<xdr:ext cx="{cx}" cy="{cy}"/>'
                "<xdr:pic><xdr:nvPicPr>"
                f'<xdr:cNvPr id="{i + 1}" name="Picture {i}" descr="{_esc(img["descr"])}"/>'
                '<xdr:cNvPicPr><a:picLocks noChangeAspect="1"/></xdr:cNvPicPr></xdr:nvPicPr>'
                f'<xdr:blipFill><a:blip r:embed="rId{i}"/><a:stretch><a:fillRect/></a:stretch></xdr:blipFill>'
                f'<xdr:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
                '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
                '<a:ln w="9525"><a:solidFill><a:srgbClr val="D8D8DC"/></a:solidFill></a:ln></xdr:spPr>'
                "</xdr:pic><xdr:clientData/></xdr:oneCellAnchor>"
            )
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"{''.join(anchors)}</xdr:wsDr>"
        )

    def drawing_rels_xml(self, first_media: int) -> str:
        """The drawing's rels: its pictures are ``xl/media/image<first_media + i>``."""
        rels = "".join(
            f'<Relationship Id="rId{i}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            f'Target="../media/image{first_media + i - 1}.{img["ext"]}"/>'
            for i, img in enumerate(self._images, start=1)
        )
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{rels}</Relationships>"
        )


class Workbook:
    def __init__(self):
        self._sheets: list[Worksheet] = []

    def add_sheet(self, name: str) -> Worksheet:
        ws = Worksheet(name)
        self._sheets.append(ws)
        return ws

    def to_bytes(self) -> bytes:
        if not self._sheets:
            self.add_sheet("Sheet1")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", self._content_types_xml())
            zf.writestr("_rels/.rels", _ROOT_RELS_XML)
            zf.writestr("xl/workbook.xml", self._workbook_xml())
            zf.writestr("xl/_rels/workbook.xml.rels", self._workbook_rels_xml())
            zf.writestr("xl/styles.xml", _STYLES_XML)
            drawing_no = 0
            media_no = 1
            for i, ws in enumerate(self._sheets, start=1):
                zf.writestr(f"xl/worksheets/sheet{i}.xml", ws.to_xml())
                if not ws.needs_rels:
                    continue
                if ws.has_drawing:
                    drawing_no += 1
                    zf.writestr(f"xl/drawings/drawing{drawing_no}.xml", ws.drawing_xml())
                    zf.writestr(
                        f"xl/drawings/_rels/drawing{drawing_no}.xml.rels",
                        ws.drawing_rels_xml(media_no),
                    )
                    for img in ws._images:
                        zf.writestr(f"xl/media/image{media_no}.{img['ext']}", img["data"])
                        media_no += 1
                zf.writestr(
                    f"xl/worksheets/_rels/sheet{i}.xml.rels",
                    ws.rels_xml(drawing_no if ws.has_drawing else 0),
                )
        return buffer.getvalue()

    def _content_types_xml(self) -> str:
        overrides = "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, len(self._sheets) + 1)
        )
        drawings = sum(1 for ws in self._sheets if ws.has_drawing)
        overrides += "".join(
            f'<Override PartName="/xl/drawings/drawing{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>'
            for i in range(1, drawings + 1)
        )
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="png" ContentType="image/png"/>'
            '<Default Extension="jpeg" ContentType="image/jpeg"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            f"{overrides}</Types>"
        )

    def _workbook_xml(self) -> str:
        sheets = "".join(
            f'<sheet name="{_esc(ws.name)}" sheetId="{i}" r:id="rId{i}"/>'
            for i, ws in enumerate(self._sheets, start=1)
        )
        # Excel needs the filter range named (hidden _xlnm._FilterDatabase,
        # scoped to its sheet) for a sheet's <autoFilter> to actually filter.
        names = []
        for idx, ws in enumerate(self._sheets):
            if not ws.auto_filter:
                continue
            r1, c1, r2, c2 = ws.auto_filter
            quoted = ws.name.replace("'", "''")
            ref = (
                f"'{quoted}'!${_col_letter(c1)}${r1 + 1}:"
                f"${_col_letter(c2)}${max(r1, r2) + 1}"
            )
            names.append(
                f'<definedName name="_xlnm._FilterDatabase" localSheetId="{idx}" hidden="1">'
                f"{_esc(ref)}</definedName>"
            )
        defined = f"<definedNames>{''.join(names)}</definedNames>" if names else ""
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets>{sheets}</sheets>{defined}<calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>'
        )

    def _workbook_rels_xml(self) -> str:
        n = len(self._sheets)
        rels = "".join(
            f'<Relationship Id="rId{i}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, n + 1)
        )
        rels += (
            f'<Relationship Id="rId{n + 1}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/>'
        )
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{rels}</Relationships>"
        )


_ROOT_RELS_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="xl/workbook.xml"/></Relationships>'
)


# The style table. Colours mirror the OneWooz palette: accent #4B2BF5, an accent
# tint #EDEAFD for the aggregate block, ink #0A0A0B, hairline borders #D8D8DC.
# numFmtId 4 = "#,##0.00", 3 = "#,##0", 10 = "0.00%" (built-in); 164 = "dd mmm yyyy".
_STYLES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<numFmts count="1"><numFmt numFmtId="164" formatCode="dd mmm yyyy"/></numFmts>'
    "<fonts count=\"8\">"
    '<font><sz val="11"/><name val="Calibri"/></font>'                                          # 0 default
    '<font><b/><sz val="16"/><color rgb="FF0A0A0B"/><name val="Calibri"/></font>'               # 1 title
    '<font><sz val="9"/><color rgb="FF66666B"/><name val="Calibri"/></font>'                    # 2 subtitle/muted
    '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'               # 3 header white
    '<font><b/><sz val="11"/><color rgb="FF0A0A0B"/><name val="Calibri"/></font>'               # 4 bold ink
    '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'               # 5 bold white (due)
    '<font><u/><sz val="11"/><color rgb="FF4B2BF5"/><name val="Calibri"/></font>'               # 6 hyperlink
    '<font><i/><sz val="9"/><color rgb="FF9A9AA0"/><name val="Calibri"/></font>'                # 7 faint italic
    "</fonts>"
    "<fills count=\"6\">"
    '<fill><patternFill patternType="none"/></fill>'                                            # 0
    '<fill><patternFill patternType="gray125"/></fill>'                                         # 1 (reserved)
    '<fill><patternFill patternType="solid"><fgColor rgb="FF4B2BF5"/></patternFill></fill>'     # 2 accent
    '<fill><patternFill patternType="solid"><fgColor rgb="FFEDEAFD"/></patternFill></fill>'     # 3 accent tint
    '<fill><patternFill patternType="solid"><fgColor rgb="FFF2F2F1"/></patternFill></fill>'     # 4 neutral
    '<fill><patternFill patternType="solid"><fgColor rgb="FF1E1A3A"/></patternFill></fill>'     # 5 ink indigo
    "</fills>"
    "<borders count=\"2\">"
    "<border><left/><right/><top/><bottom/><diagonal/></border>"                                # 0 none
    '<border>'
    '<left style="thin"><color rgb="FFD8D8DC"/></left>'
    '<right style="thin"><color rgb="FFD8D8DC"/></right>'
    '<top style="thin"><color rgb="FFD8D8DC"/></top>'
    '<bottom style="thin"><color rgb="FFD8D8DC"/></bottom>'
    "<diagonal/></border>"                                                                       # 1 thin box
    "</borders>"
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="32">'
    # 0 default
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    # 1 title
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    # 2 subtitle
    '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    # 3 meta label
    '<xf numFmtId="0" fontId="4" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    # 4 meta value
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    # 5 header (accent, white, centred, wrap)
    '<xf numFmtId="0" fontId="3" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>'
    # 6 header numeric (right)
    '<xf numFmtId="0" fontId="3" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="right" vertical="center" wrapText="1"/></xf>'
    # 7 body text (wrap, top)
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="top" wrapText="1"/></xf>'
    # 8 body centred
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="top" wrapText="1"/></xf>'
    # 9 body money
    '<xf numFmtId="4" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="right" vertical="top"/></xf>'
    # 10 body integer
    '<xf numFmtId="3" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="right" vertical="top"/></xf>'
    # 11 body percent
    '<xf numFmtId="10" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="right" vertical="top"/></xf>'
    # 12 total label
    '<xf numFmtId="0" fontId="4" fillId="4" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>'
    # 13 total money
    '<xf numFmtId="4" fontId="4" fillId="4" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>'
    # 14 aggregate label
    '<xf numFmtId="0" fontId="4" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="center" wrapText="1"/></xf>'
    # 15 aggregate money
    '<xf numFmtId="4" fontId="4" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>'
    # 16 aggregate percent
    '<xf numFmtId="10" fontId="4" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>'
    # 17 aggregate text
    '<xf numFmtId="0" fontId="4" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>'
    # 18 amount-due label (accent band, white)
    '<xf numFmtId="0" fontId="5" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>'
    # 19 amount-due money (accent band, white)
    '<xf numFmtId="4" fontId="5" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>'
    # 20 body text, one line (no wrap), vertically centred
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="center"/></xf>'
    # 21 body text centred, one line
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
    # 22 aggregate integer
    '<xf numFmtId="3" fontId="4" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>'
    # 23 hyperlink text
    '<xf numFmtId="0" fontId="6" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="center" wrapText="1"/></xf>'
    # 24 body text, wrap, vertically centred
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="center" wrapText="1"/></xf>'
    # 25 body money, vertically centred
    '<xf numFmtId="4" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>'
    # 26 body integer, centred
    '<xf numFmtId="3" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
    # 27 body date, centred
    '<xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
    # 28 group header band (ink indigo, white bold)
    '<xf numFmtId="0" fontId="3" fillId="5" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>'
    # 29 total integer
    '<xf numFmtId="3" fontId="4" fillId="4" borderId="1" xfId="0" applyNumberFormat="1" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'
    # 30 small muted text, centred
    '<xf numFmtId="0" fontId="7" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>'
    # 31 aggregate date
    '<xf numFmtId="164" fontId="4" fillId="3" borderId="1" xfId="0" applyNumberFormat="1" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="right" vertical="center"/></xf>'
    "</cellXfs>"
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    "</styleSheet>"
)
