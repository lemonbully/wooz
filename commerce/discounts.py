"""Discount evaluation engine  Shopify-parity discount logic.

The store dashboard lets an owner build discounts across four classes (amount
off products, amount off the order, free shipping, buy X get Y), by code or
automatic, with minimum requirements, customer eligibility, usage limits,
combinations and an active window (see :class:`commerce.models.Coupon`). *This*
module is where all of that is turned into money.

Everything is computed from a cart snapshot  a list of line dicts (as produced
by :class:`commerce.cart.Cart.items`) plus the subtotal  so the same engine
serves the live cart, the checkout re-validation and any preview. Product,
category and customer selections stored on the coupon as id lists are matched
against the cart's own products; ids that no longer resolve are simply ignored,
so nothing can leak across tenants and a deleted product just stops qualifying.

Money is Decimal throughout, rounded to paise at the boundaries so a computed
discount always matches what the order is created for and the gateway charged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from commerce.models import Coupon

_TWO = Decimal("0.01")
_ZERO = Decimal("0.00")


def _money(value) -> Decimal:
    return (Decimal(value or 0)).quantize(_TWO, rounding=ROUND_HALF_UP)


def _ids(raw) -> set[str]:
    """A coupon id-list column normalised to a set of strings."""
    if not raw:
        return set()
    return {str(x) for x in raw}


# --------------------------------------------------------------------------
# Result type
# --------------------------------------------------------------------------
@dataclass
class DiscountResult:
    """The outcome of evaluating one coupon against one cart.

    ``error`` is a user-facing string when the coupon cannot be used, otherwise
    ``None``. ``amount`` is the monetary discount on the subtotal;
    ``free_shipping`` is set independently for shipping-class discounts (the
    shipping figure is applied later, at checkout).
    """

    coupon: Coupon | None = None
    amount: Decimal = _ZERO
    free_shipping: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.coupon is not None and (
            self.amount > 0 or self.free_shipping
        )


# --------------------------------------------------------------------------
# Cart helpers
# --------------------------------------------------------------------------
def _line_matches(line, product_ids: set[str], category_ids: set[str]) -> bool:
    product = line["product"]
    if str(product.id) in product_ids:
        return True
    cat_id = getattr(product, "category_id", None)
    return cat_id is not None and str(cat_id) in category_ids


def _scoped_lines(lines, applies_to, product_ids, category_ids):
    if applies_to == Coupon.AppliesTo.ALL:
        return list(lines)
    return [ln for ln in lines if _line_matches(ln, product_ids, category_ids)]


def _cart_quantity(lines) -> int:
    return sum(int(ln["quantity"]) for ln in lines)


def _units(lines):
    """Expand lines into per-unit (unit_price) entries, cheapest handling later.

    Returns a list of ``[unit_price, count]`` we can draw from; kept as counts so
    a big-quantity line doesn't blow up into thousands of entries.
    """
    return [[_money(ln["unit_price"]), int(ln["quantity"])] for ln in lines if int(ln["quantity"]) > 0]


# --------------------------------------------------------------------------
# Per-class discount maths
# --------------------------------------------------------------------------
def _percent_or_fixed_on(base: Decimal, coupon: Coupon) -> Decimal:
    if coupon.discount_type == Coupon.DiscountType.PERCENT:
        amount = base * (Decimal(coupon.value) / Decimal("100"))
    else:
        amount = Decimal(coupon.value)
    return amount


def _cap(amount: Decimal, coupon: Coupon, ceiling: Decimal) -> Decimal:
    if coupon.max_discount_amount:
        amount = min(amount, Decimal(coupon.max_discount_amount))
    return _money(max(_ZERO, min(amount, ceiling)))


def _order_amount(coupon: Coupon, subtotal: Decimal) -> Decimal:
    return _cap(_percent_or_fixed_on(subtotal, coupon), coupon, subtotal)


def _product_amount(coupon: Coupon, lines) -> tuple[Decimal, str | None]:
    eligible = _scoped_lines(
        lines, coupon.applies_to, _ids(coupon.applies_product_ids), _ids(coupon.applies_category_ids)
    )
    eligible_subtotal = _money(sum((ln["line_total"] for ln in eligible), _ZERO))
    if eligible_subtotal <= 0:
        return _ZERO, "This code doesn’t apply to anything in your cart."
    if coupon.discount_type == Coupon.DiscountType.PERCENT:
        amount = eligible_subtotal * (Decimal(coupon.value) / Decimal("100"))
    else:
        # Fixed "amount off products" is per eligible unit (Shopify semantics),
        # never more than the unit's own price.
        amount = _ZERO
        per_unit = Decimal(coupon.value)
        for price, count in _units(eligible):
            amount += min(per_unit, price) * count
    return _cap(amount, coupon, eligible_subtotal), None


def _bxgy_amount(coupon: Coupon, lines) -> tuple[Decimal, str | None]:
    """Buy X get Y.

    Count how many "buy" requirements the cart satisfies (by quantity or spend),
    optionally capped per order, then discount that many of the cheapest eligible
    "get" units. When the buy and get scopes overlap, the units needed to satisfy
    the purchase are reserved first so a single unit is never both bought and
    rewarded.
    """
    buy_pids, buy_cids = _ids(coupon.buy_product_ids), _ids(coupon.buy_category_ids)
    get_pids, get_cids = _ids(coupon.get_product_ids), _ids(coupon.get_category_ids)

    # An empty buy/get scope means "any product".
    buy_lines = lines if not (buy_pids or buy_cids) else _scoped_lines(lines, Coupon.AppliesTo.PRODUCTS, buy_pids, buy_cids)
    get_lines = lines if not (get_pids or get_cids) else _scoped_lines(lines, Coupon.AppliesTo.PRODUCTS, get_pids, get_cids)

    # How many reward "sets" the cart has earned.
    if coupon.buy_type == Coupon.BuyType.AMOUNT:
        threshold = Decimal(coupon.buy_min_amount or 0)
        buy_amount = _money(sum((ln["line_total"] for ln in buy_lines), _ZERO))
        sets = int(buy_amount // threshold) if threshold > 0 else 0
    else:
        need = int(coupon.buy_quantity or 0)
        buy_qty = _cart_quantity(buy_lines)
        sets = buy_qty // need if need > 0 else 0

    if sets <= 0:
        return _ZERO, "Add the qualifying items to unlock this offer."
    if coupon.bxgy_max_per_order:
        sets = min(sets, int(coupon.bxgy_max_per_order))

    reward_units = sets * int(coupon.get_quantity or 0)
    if reward_units <= 0:
        return _ZERO, "Add the qualifying items to unlock this offer."

    # Pool of get-eligible units (unit price, count), cheapest first.
    pool = sorted(_units(get_lines), key=lambda u: u[0])

    # Reserve the units consumed by the "buy" side where scopes overlap, so we
    # never reward a unit that was needed to qualify.
    overlap = (buy_pids or buy_cids) == (get_pids or get_cids) or not (get_pids or get_cids)
    if coupon.buy_type == Coupon.BuyType.QUANTITY and overlap:
        reserve = sets * int(coupon.buy_quantity or 0)
        # Reserve the most expensive units for the "buy" requirement, leaving the
        # cheapest to be rewarded.
        for entry in sorted(pool, key=lambda u: u[0], reverse=True):
            if reserve <= 0:
                break
            take = min(reserve, entry[1])
            entry[1] -= take
            reserve -= take

    amount = _ZERO
    remaining = reward_units
    for price, count in pool:
        if remaining <= 0:
            break
        take = min(remaining, count)
        if coupon.reward_type == Coupon.RewardType.FREE:
            per = price
        elif coupon.reward_type == Coupon.RewardType.PERCENT:
            per = price * (Decimal(coupon.reward_value) / Decimal("100"))
        else:  # AMOUNT off each
            per = min(Decimal(coupon.reward_value), price)
        amount += per * take
        remaining -= take

    amount = _money(max(_ZERO, amount))
    if amount <= 0:
        return _ZERO, "Add the qualifying items to unlock this offer."
    return amount, None


# --------------------------------------------------------------------------
# Condition gates
# --------------------------------------------------------------------------
def _min_requirement_error(coupon: Coupon, lines, subtotal: Decimal, currency: str) -> str | None:
    if coupon.min_requirement == Coupon.MinRequirement.AMOUNT and coupon.min_order_value:
        if subtotal < Decimal(coupon.min_order_value):
            return f"Spend at least {currency}{Decimal(coupon.min_order_value):.0f} to use this offer."
    if coupon.min_requirement == Coupon.MinRequirement.QUANTITY and coupon.min_quantity:
        if _cart_quantity(lines) < int(coupon.min_quantity):
            n = int(coupon.min_quantity)
            return f"Add at least {n} item{'s' if n != 1 else ''} to use this offer."
    return None


def _eligibility_error(coupon: Coupon, customer, strict: bool) -> str | None:
    if coupon.customer_eligibility != Coupon.CustomerEligibility.SPECIFIC:
        return None
    eligible = _ids(coupon.eligible_customer_ids)
    if customer is not None:
        return None if str(customer.id) in eligible else "This offer isn’t available for your account."
    # No identified shopper. Defer to checkout (strict) where the email is known.
    return "Sign in to use this offer." if strict else None


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def evaluate(
    coupon: Coupon,
    *,
    lines,
    subtotal: Decimal,
    customer=None,
    currency: str = "₹",
    strict: bool = False,
) -> DiscountResult:
    """Evaluate one coupon against a cart snapshot.

    ``lines`` are cart item dicts; ``subtotal`` their money total. ``customer``
    is the resolved shopper (or ``None`` for a guest). ``strict`` tightens the
    checks that can only be settled once the shopper is known (used at checkout).
    """
    if coupon is None:
        return DiscountResult(error="That code isn’t valid.")
    if not coupon.is_valid_now():
        if coupon.is_expired():
            return DiscountResult(coupon=coupon, error="This offer has expired.")
        if coupon.is_scheduled():
            return DiscountResult(coupon=coupon, error="This offer hasn’t started yet.")
        return DiscountResult(coupon=coupon, error="That code isn’t valid.")

    err = _eligibility_error(coupon, customer, strict)
    if err:
        return DiscountResult(coupon=coupon, error=err)

    # BXGY uses its own buy-side gate rather than the minimum-purchase card.
    if coupon.discount_class != Coupon.DiscountClass.BXGY:
        err = _min_requirement_error(coupon, lines, subtotal, currency)
        if err:
            return DiscountResult(coupon=coupon, error=err)

    if coupon.discount_class == Coupon.DiscountClass.ORDER:
        amount = _order_amount(coupon, subtotal)
        if amount <= 0:
            return DiscountResult(coupon=coupon, error="This code doesn’t apply to your cart.")
        return DiscountResult(coupon=coupon, amount=amount)

    if coupon.discount_class == Coupon.DiscountClass.PRODUCT:
        amount, err = _product_amount(coupon, lines)
        if err:
            return DiscountResult(coupon=coupon, error=err)
        return DiscountResult(coupon=coupon, amount=amount)

    if coupon.discount_class == Coupon.DiscountClass.SHIPPING:
        # Free shipping carries no subtotal discount; the shipping figure is
        # zeroed (or capped by shipping_max_amount) at checkout.
        return DiscountResult(coupon=coupon, amount=_ZERO, free_shipping=True)

    if coupon.discount_class == Coupon.DiscountClass.BXGY:
        amount, err = _bxgy_amount(coupon, lines)
        if err:
            return DiscountResult(coupon=coupon, error=err)
        return DiscountResult(coupon=coupon, amount=amount)

    return DiscountResult(coupon=coupon, error="That code isn’t valid.")


def resolve_code(site, code: str) -> Coupon | None:
    """The single coded discount matching ``code`` for this store, or ``None``."""
    code = (code or "").strip().upper()
    if not code:
        return None
    return (
        Coupon.objects.for_site(site)
        .filter(code=code, method=Coupon.Method.CODE, is_deleted=False)
        .first()
    )


def resolve_customer(site, email: str):
    """Resolve a shopper to a ``Customer`` row by email within the store."""
    email = (email or "").strip().lower()
    if not email:
        return None
    from commerce.models import Customer

    return (
        Customer.objects.for_site(site).filter(email=email, is_deleted=False).first()
    )


def _active_automatics(site):
    return (
        Coupon.objects.for_site(site)
        .filter(method=Coupon.Method.AUTOMATIC, active=True, is_deleted=False)
    )


def _combinable(a: Coupon, b: Coupon) -> bool:
    """Whether two applied discounts may stack, per both sides' combination flags."""
    flag = {
        Coupon.DiscountClass.PRODUCT: "combines_with_product",
        Coupon.DiscountClass.ORDER: "combines_with_order",
        Coupon.DiscountClass.SHIPPING: "combines_with_shipping",
        Coupon.DiscountClass.BXGY: "combines_with_product",
    }
    return getattr(a, flag[b.discount_class], False) and getattr(b, flag[a.discount_class], False)


@dataclass
class CartDiscounts:
    """The set of discounts an evaluated cart ends up with."""

    applied: list[DiscountResult] = field(default_factory=list)
    amount: Decimal = _ZERO
    free_shipping: bool = False
    code_error: str | None = None
    code_coupon: Coupon | None = None


def compute(site, *, lines, subtotal: Decimal, code: str = "", customer=None,
            currency: str = "₹", strict: bool = False) -> CartDiscounts:
    """Resolve the full discount picture for a cart.

    Applies the shopper's entered ``code`` (if any and valid), then layers on any
    automatic discounts that qualify and are allowed to combine with what's
    already applied. Returns the applied results and their combined money +
    free-shipping outcome. ``code_error`` explains a code that could not apply so
    the storefront can surface it without dropping the rest of the cart.
    """
    result = CartDiscounts()
    applied: list[DiscountResult] = []

    coded = resolve_code(site, code) if code else None
    if code and coded is None:
        result.code_error = "That code isn’t valid."
    elif coded is not None:
        result.code_coupon = coded
        r = evaluate(coded, lines=lines, subtotal=subtotal, customer=customer, currency=currency, strict=strict)
        if r.ok:
            applied.append(r)
        else:
            result.code_error = r.error

    # Layer automatic discounts, best-first, respecting combinations and one per
    # class.
    autos = []
    for c in _active_automatics(site):
        r = evaluate(c, lines=lines, subtotal=subtotal, customer=customer, currency=currency, strict=strict)
        if r.ok:
            autos.append(r)
    autos.sort(key=lambda r: (r.amount, 1 if r.free_shipping else 0), reverse=True)

    used_classes = {r.coupon.discount_class for r in applied}
    for r in autos:
        cls = r.coupon.discount_class
        if cls in used_classes:
            continue
        if all(_combinable(existing.coupon, r.coupon) for existing in applied):
            applied.append(r)
            used_classes.add(cls)

    result.applied = applied
    result.amount = _money(sum((r.amount for r in applied), _ZERO))
    result.free_shipping = any(r.free_shipping for r in applied)
    return result
