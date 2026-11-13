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
  and an Excel filter (autoFilter) on a header row.

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
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
        text = _esc(value)
        return (
            f'<c r="{ref}" s="{style}" t="inlineStr">'
            f'<is><t xml:space="preserve">{text}</t></is></c>'
        )

    def to_xml(self) -> str:
        parts = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        ]
        if self._max_row or self._max_col:
            dim = f"A1:{_col_letter(self._max_col)}{self._max_row + 1}"
            parts.append(f'<dimension ref="{dim}"/>')
        # Freeze everything above ``freeze_row`` so the header stays put.
        if self.freeze_row:
            parts.append(
                '<sheetViews><sheetView workbookViewId="0">'
                f'<pane ySplit="{self.freeze_row}" topLeftCell="A{self.freeze_row + 1}" '
                'activePane="bottomLeft" state="frozen"/>'
                '</sheetView></sheetViews>'
            )
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
        parts.append("</worksheet>")
        return "".join(parts)


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
            for i, ws in enumerate(self._sheets, start=1):
                zf.writestr(f"xl/worksheets/sheet{i}.xml", ws.to_xml())
        return buffer.getvalue()

    def _content_types_xml(self) -> str:
        overrides = "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, len(self._sheets) + 1)
        )
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
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
# numFmtId 4 = "#,##0.00", 3 = "#,##0", 10 = "0.00%" (all built-in).
_STYLES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    "<fonts count=\"6\">"
    '<font><sz val="11"/><name val="Calibri"/></font>'                                          # 0 default
    '<font><b/><sz val="16"/><color rgb="FF0A0A0B"/><name val="Calibri"/></font>'               # 1 title
    '<font><sz val="9"/><color rgb="FF66666B"/><name val="Calibri"/></font>'                    # 2 subtitle/muted
    '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'               # 3 header white
    '<font><b/><sz val="11"/><color rgb="FF0A0A0B"/><name val="Calibri"/></font>'               # 4 bold ink
    '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'               # 5 bold white (due)
    "</fonts>"
    "<fills count=\"5\">"
    '<fill><patternFill patternType="none"/></fill>'                                            # 0
    '<fill><patternFill patternType="gray125"/></fill>'                                         # 1 (reserved)
    '<fill><patternFill patternType="solid"><fgColor rgb="FF4B2BF5"/></patternFill></fill>'     # 2 accent
    '<fill><patternFill patternType="solid"><fgColor rgb="FFEDEAFD"/></patternFill></fill>'     # 3 accent tint
    '<fill><patternFill patternType="solid"><fgColor rgb="FFF2F2F1"/></patternFill></fill>'     # 4 neutral
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
    '<cellXfs count="23">'
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
    "</cellXfs>"
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    "</styleSheet>"
)
