"""The one place a subscription's money breakdown is computed.

The pricing quote (AJAX), the checkout submit, and anything that needs to show
"what will I pay" all call :func:`quote`, so the figure the buyer sees, the
figure stored on the order, and the figure Cashfree is asked to charge are
computed by the same code from the same inputs. The browser never supplies an
amount  only a plan, a cadence, and (optionally) a discount code.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings

from . import cycles
from .models import Discount, SubscriptionPlan


def gst_rate() -> Decimal:
    """The GST percentage applied to subscriptions (18 by default)."""
    return Decimal(str(getattr(settings, "SUBSCRIPTION_GST_RATE", 18)))


def plan_cards() -> list[SubscriptionPlan]:
    """Active plans, ordered, each annotated for the pricing page template.

    Each plan carries one ``duration`` (its own object per duration), so the card
    shows that plan's per-month price and its up-front total. Also attaches the
    prefetched ``feature_list``, ``activity_list`` (penalties) and
    ``commission_list`` (the commerce fee bands, with ``commerce_fee_from`` as the
    lowest per-order fee) so the template can read them without calling a method
    with arguments (which Django templates can't).
    """
    plans = list(
        SubscriptionPlan.objects.filter(is_active=True)
        .prefetch_related("features", "activity_penalties", "commission_tiers", "commerce_fee_bands")
        .order_by("order", "price_monthly", "name")
    )
    for plan in plans:
        # The single price figures for this plan's own duration.
        plan.card_per_month = plan.per_month_price()
        plan.card_total = plan.cycle_total()
        # One-time plans show a single up-front figure (charged once, one year).
        plan.one_time_total = cycles.money(plan.one_time_price)
        plan.feature_list = list(plan.features.all())
        # Activity/penalty windows (may be several), for the card note.
        plan.activity_list = list(plan.activity_penalties.all())
        # Commerce fee price ranges (minimum value to maximum value → fee), for
        # the card's compact fee/penalty disclosure. ``commerce_fee_from`` is the
        # lowest range's fee, used as the collapsed headline. Order-count tiers
        # not yet migrated are shown as price ranges, exactly as billing reads
        # them (billing.fee_bands.tiers_as_bands).
        bands = sorted(plan.commerce_fee_bands.all(), key=lambda b: (b.order, b.min_price))
        if not bands:
            from types import SimpleNamespace

            from .fee_bands import tiers_as_bands

            tiers = sorted(plan.commission_tiers.all(), key=lambda t: (t.order, t.min_orders))
            bands = [
                SimpleNamespace(**b)
                for b in tiers_as_bands([
                    {"min_orders": t.min_orders, "max_orders": t.max_orders, "per_order_fee": t.per_order_fee}
                    for t in tiers
                ])
            ]
        plan.fee_band_list = bands
        plan.commission_list = []
        fees = [b.fee for b in plan.fee_band_list if b.fee is not None]
        plan.commerce_fee_from = min(fees) if fees else None
    return plans


def available_durations(plans) -> list[dict]:
    """The durations that have at least one active recurring plan, in canonical
    order  used to render only the toggle buttons that lead somewhere.

    ``plans`` is the annotated list from :func:`plan_cards`.
    """
    present = {
        p.duration
        for p in plans
        if getattr(p, "is_paid", False) and p.duration in cycles.MONTHS
    }
    return [
        {"key": key, "label": cycles.DURATION_LABELS[key]}
        for key in cycles.ORDER
        if key in present
    ]


@dataclass(frozen=True)
class Quote:
    """A computed breakdown. Amounts are 2-dp ``Decimal`` rupee figures."""

    plan: SubscriptionPlan
    cycle: str
    months: int
    per_month: Decimal
    base: Decimal
    discount: Discount | None
    discount_amount: Decimal
    discount_error: str
    taxable: Decimal
    gst_rate: Decimal
    gst_amount: Decimal
    total: Decimal

    def as_json(self) -> dict:
        """Serialise for the checkout page's live breakdown (strings, 2-dp)."""
        def s(v: Decimal) -> str:
            return f"{v:.2f}"

        return {
            "cycle": self.cycle,
            "months": self.months,
            "per_month": s(self.per_month),
            "base": s(self.base),
            "discount_code": (self.discount.code if self.discount else ""),
            "discount_amount": s(self.discount_amount),
            "discount_error": self.discount_error,
            "taxable": s(self.taxable),
            "gst_rate": f"{self.gst_rate:.0f}",
            "gst_amount": s(self.gst_amount),
            "total": s(self.total),
        }


def resolve_discount(code: str, plan: SubscriptionPlan, base: Decimal) -> tuple[Discount | None, str]:
    """Look a code up and check it applies to this plan/amount.

    Returns ``(discount, error)``. A blank code is not an error  it just means
    no discount. An unknown or inapplicable code returns ``(None, reason)`` so
    the caller can show the reason without ever leaking whether the code exists
    beyond what the buyer typed.
    """
    code = (code or "").strip().upper()
    if not code:
        return None, ""
    discount = Discount.objects.filter(code=code).first()
    if discount is None:
        return None, "That code isn’t valid."
    reason = discount.validate_for(plan, base)
    if reason:
        return None, reason
    return discount, ""


def quote(
    plan: SubscriptionPlan,
    cycle: str | None = None,
    *,
    discount_code: str = "",
    base_override: Decimal | None = None,
    per_month_override: Decimal | None = None,
) -> Quote:
    """Compute the full breakdown for buying ``plan``.

    The cadence is the plan's **own** ``duration`` (each duration is its own plan
    object), so any ``cycle`` the browser sends is ignored  the amount is
    computed from the plan alone and can never be steered from the client. An
    invalid discount code does not raise: the quote is returned without the
    discount and carries ``discount_error`` explaining why.

    Two authoritative overrides support admin-issued custom-priced invites (both
    set server-side, never by the browser):

    * ``per_month_override``  a custom **per-month** price; the cycle base is
      that figure times the plan's months (quarterly ₹100/mo → ₹300), exactly
      like the platform's own plans are priced.
    * ``base_override``  a fixed whole-cycle base, used directly.

    ``per_month_override`` wins if both are given. A zero override yields a zero
    total  a free grant that skips the gateway.
    """
    cycle = plan.billing_cycle
    months = plan.months
    if per_month_override is not None:
        per_month = cycles.money(per_month_override)
        base = cycles.money(per_month * months) if months else per_month
    elif base_override is not None:
        base = cycles.money(base_override)
        per_month = cycles.money(base / months) if months else base
    else:
        per_month = plan.per_month_price()
        base = plan.cycle_total()

    discount, discount_error = resolve_discount(discount_code, plan, base)
    discount_amount = discount.amount_off(base) if discount else Decimal("0.00")

    taxable = cycles.money(base - discount_amount)
    rate = gst_rate()
    gst_amount = cycles.money(taxable * rate / Decimal(100))
    total = cycles.money(taxable + gst_amount)

    return Quote(
        plan=plan,
        cycle=cycle,
        months=months,
        per_month=per_month,
        base=base,
        discount=discount,
        discount_amount=discount_amount,
        discount_error=discount_error,
        taxable=taxable,
        gst_rate=rate,
        gst_amount=gst_amount,
        total=total,
    )
