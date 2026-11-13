"""Parse an uploaded recipient file (CSV / XLSX) into clean rows.

Header-name aware with a positional fallback, so a file with an obvious email
column imports even when the header is missing or oddly named. Returns plain
dicts; the caller decides how to persist them (dedupe, caps, suppression).
"""

from __future__ import annotations

import csv
import re
from io import BytesIO, StringIO

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_ALIASES = {
    "name": {"name", "fullname", "full name", "customer", "customername", "customer name"},
    "email": {"email", "emailaddress", "email address", "e-mail", "mail"},
    "phone": {"phone", "mobile", "mobilenumber", "mobile number", "phone number", "contact", "contactnumber"},
}


def _norm_header(v) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(v or "").strip().lower())


def _is_email(v) -> bool:
    return bool(_EMAIL_RE.match(str(v or "").strip()))


def _is_phoneish(v) -> bool:
    digits = re.sub(r"\D", "", str(v or ""))
    return len(digits) >= 7


def _detect(header_row, sample_rows):
    header = [_norm_header(h) for h in (header_row or [])]
    alias = {k: {_norm_header(a) for a in v} for k, v in _ALIASES.items()}

    def find(keys):
        for i, h in enumerate(header):
            if h in keys:
                return i
        return None

    name_i, email_i, phone_i = find(alias["name"]), find(alias["email"]), find(alias["phone"])

    if email_i is None:
        width = max((len(r) for r in sample_rows[:25]), default=0)
        if width:
            escore = [0] * width
            pscore = [0] * width
            for r in sample_rows[:25]:
                for i in range(width):
                    cell = r[i] if i < len(r) else ""
                    if _is_email(cell):
                        escore[i] += 1
                    if _is_phoneish(cell):
                        pscore[i] += 1
            best_e = max(range(width), key=lambda i: escore[i])
            if escore[best_e] > 0:
                email_i = best_e
            phone_cands = [i for i in range(width) if i != email_i]
            if phone_cands:
                best_p = max(phone_cands, key=lambda i: pscore[i])
                if pscore[best_p] > 0:
                    phone_i = best_p
            name_cands = [i for i in range(width) if i not in {email_i, phone_i}]
            if name_cands:
                name_i = name_cands[0]
    return name_i, email_i, phone_i


def parse_recipients(uploaded_file) -> tuple[list[dict], str | None]:
    """Return ``(rows, error)``. Each row is ``{name, email, phone}`` (emails
    lower-cased and de-duplicated within the file)."""
    filename = (getattr(uploaded_file, "name", "") or "").lower()
    rows: list[list] = []

    try:
        uploaded_file.seek(0)
    except Exception:
        pass

    if filename.endswith(".csv"):
        raw = uploaded_file.read()
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            content = raw.decode("latin-1", errors="ignore")
        try:
            dialect = csv.Sniffer().sniff(content[:4096], delimiters=[",", ";", "\t", "|"])
        except Exception:
            dialect = csv.excel
        for r in csv.reader(StringIO(content), dialect):
            if r and any(str(c).strip() for c in r):
                rows.append(r)
    elif filename.endswith(".xlsx"):
        try:
            import openpyxl
        except Exception:
            return [], "Reading .xlsx needs the openpyxl package."
        try:
            wb = openpyxl.load_workbook(BytesIO(uploaded_file.read()), data_only=True)
            ws = wb.active
            for r in ws.iter_rows(values_only=True):
                if r and any(c is not None and str(c).strip() != "" for c in r):
                    rows.append([c for c in r])
        except Exception as exc:
            return [], f"Could not read the Excel file: {exc}"
    elif filename.endswith(".xls"):
        return [], "The old .xls format isn't supported  save as .xlsx or .csv."
    else:
        return [], "Upload a .csv or .xlsx file."

    if not rows:
        return [], "No rows found in the file."

    header = rows[0]
    data = rows[1:] if len(rows) > 1 else rows
    name_i, email_i, phone_i = _detect(header, data)
    if email_i is None:
        return [], 'Could not find an email column. Add a header called "Email".'

    out: list[dict] = []
    seen: set[str] = set()

    def _cell(row, idx):
        if idx is None or idx >= len(row) or row[idx] is None:
            return ""
        return str(row[idx]).strip()

    for row in data:
        email = _cell(row, email_i).lower()
        if not _is_email(email) or email in seen:
            continue
        seen.add(email)
        out.append({"name": _cell(row, name_i), "email": email, "phone": _cell(row, phone_i)})
    return out, None
