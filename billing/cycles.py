"""Billing cadences and the money helpers the whole app shares.

Each plan is authored for a single billing period (monthly / quarterly /
half-yearly / yearly) as its own object, with a **per-month** price. The amount
actually charged is that per-month figure multiplied by the number of months in
the period (``MONTHS``).

Kept free of any Django-model import so both the models and the pricing math can
use it without a circular import.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

__all__ = [
    "MONTHLY", "QUARTERLY", "HALF_YEARLY", "YEARLY",
    "MONTHS", "CYCLE_LABELS", "DURATION_LABELS", "ORDER",
    "money", "add_months", "normalise_cycle",
]

MONTHLY = "monthly"
QUARTERLY = "quarterly"
HALF_YEARLY = "half_yearly"
YEARLY = "yearly"

#: Canonical display order of the billing periods.
ORDER: tuple[str, ...] = (MONTHLY, QUARTERLY, HALF_YEARLY, YEARLY)

#: Cadence -> number of months billed up front.
MONTHS: dict[str, int] = {MONTHLY: 1, QUARTERLY: 3, HALF_YEARLY: 6, YEARLY: 12}

#: Cadence -> how it reads to a buyer ("billed every …").
CYCLE_LABELS: dict[str, str] = {
    MONTHLY: "month",
    QUARTERLY: "3 months",
    HALF_YEARLY: "6 months",
    YEARLY: "year",
}

#: Cadence -> the label shown on the pricing-page toggle.
DURATION_LABELS: dict[str, str] = {
    MONTHLY: "Monthly",
    QUARTERLY: "Quarterly",
    HALF_YEARLY: "Half-yearly",
    YEARLY: "Yearly",
}

_TWO_PLACES = Decimal("0.01")


def money(value) -> Decimal:
    """Coerce to a 2-decimal-place ``Decimal`` rounded half-up (rupee amounts).

    Every figure that reaches a customer or the gateway passes through here, so
    the breakdown a buyer sees, the total we store, and the amount Cashfree is
    asked to charge are the same rounded number rather than three that differ in
    the last paisa.
    """
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def normalise_cycle(value: str | None) -> str:
    """Return a valid cadence, defaulting an unknown/blank value to monthly."""
    value = (value or "").strip().lower()
    return value if value in MONTHS else MONTHLY


def add_months(start: date, months: int) -> date:
    """The same calendar day ``months`` ahead, clamped to the month's length.

    Used to compute a subscription's period end. Pure calendar arithmetic (no
    dateutil dependency) so 31 Jan + 1 month lands on 28/29 Feb rather than
    overflowing.
    """
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    # Clamp the day to the last valid day of the target month.
    if month == 12:
        next_month_first = date(year + 1, 1, 1)
    else:
        next_month_first = date(year, month + 1, 1)
    last_day = (next_month_first - date(year, month, 1)).days
    return date(year, month, min(start.day, last_day))
