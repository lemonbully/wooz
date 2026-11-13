"""GST helpers for invoices: GSTIN → state, and the CGST/SGST vs IGST decision.

Dependency-free (no model imports) so both ``billing.models`` and the bill
builder can use it. State names are the canonical ones the onboarding dropdown
stores (``billing.onboarding.INDIAN_STATES``), so a state picked there and a
state detected from a GSTIN compare equal.
"""

from __future__ import annotations

import re

# A GSTIN: 2-digit state code, the holder's 10-char PAN, 1 entity digit, the
# letter Z, and a check character.
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")

# The official GST state codes (first two digits of a GSTIN).
STATE_BY_CODE: dict[str, str] = {
    "01": "Jammu and Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    # Daman & Diu (25) and Dadra & Nagar Haveli (26) merged into one UT in 2020;
    # both codes remain in use.
    "25": "Dadra and Nagar Haveli and Daman and Diu",
    "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra",
    "28": "Andhra Pradesh",  # pre-2014 Andhra Pradesh registrations
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman and Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
}

STATES: tuple[str, ...] = tuple(sorted(set(STATE_BY_CODE.values())))
_STATES_LOWER = {s.lower(): s for s in STATES}


def normalise_gstin(raw) -> str:
    return re.sub(r"\s+", "", str(raw or "")).upper()


def is_valid_gstin(raw) -> bool:
    return bool(GSTIN_RE.match(normalise_gstin(raw)))


def state_from_gstin(raw) -> str:
    """The state a GSTIN is registered in, or "" (invalid / unknown code)."""
    g = normalise_gstin(raw)
    if not GSTIN_RE.match(g):
        return ""
    return STATE_BY_CODE.get(g[:2], "")


def canonical_state(raw) -> str:
    """``raw`` as a canonical state name ("" if it isn't one)."""
    return _STATES_LOWER.get(" ".join(str(raw or "").split()).lower(), "")


def effective_state(state="", gstin="") -> str:
    """The place a party is taxed in: a valid GSTIN's state wins (it is the
    registered place of supply), else the named state, canonicalised."""
    return state_from_gstin(gstin) or canonical_state(state) or " ".join(str(state or "").split())


def is_intra_state(buyer_state="", buyer_gstin="", seller_state="", seller_gstin="") -> bool:
    """True → CGST + SGST (same state); False → IGST.

    Each side's state is its GSTIN's state when it has a valid GSTIN, otherwise
    the state it named. Unknown on either side → IGST.
    """
    buyer = effective_state(buyer_state, buyer_gstin).lower()
    seller = effective_state(seller_state, seller_gstin).lower()
    return bool(buyer and seller and buyer == seller)
