"""Instant discounts  short-lived, self-applying store promotions.

An *instant discount* is a fast way for an owner to run a flash deal without
walking the full discount editor. It is deliberately narrow:

* it is either **off the cart value** (amount off the whole order) or **off a
  single product** (with a minimum quantity), and
* it lasts **at most one day**  the owner picks 1 hour, 4 hours or 1 day, and
  the discount expires on its own.

Rather than invent a parallel storage model, an instant discount *is* an
ordinary :class:`~commerce.models.Coupon`: a **coded** discount whose code is
minted automatically (the owner never types one) with a start of *now* and an
end of *now + duration*. The shopper enters that code at checkout and it applies
exactly like any other coded discount  only it self-expires. It therefore rides
the exact same evaluation engine (:mod:`commerce.discounts`) as every other
discount, so the storefront needs no special case. The only thing that marks it
as "instant" is a flag in the coupon's free-form ``conditions`` JSON
(``{"instant": True, "duration": …}``), which is what the dedicated
instant-discount page filters on and what keeps these rows out of the ordinary
discounts list.

This module owns parsing, validating and persisting that form, keeping
:mod:`commerce.views.growth` thin  mirroring how :mod:`commerce.coupon_form`
serves the full discount editor.
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from commerce.models import Coupon, Product

C = Coupon  # local alias for the choice enums

# Unambiguous alphabet for auto-generated codes (no 0/O/1/I), matching the
# "Generate" button in the full discount editor.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 8

# The (order-preserving) duration menu the form offers. Each value maps to a
# human label and the timedelta it adds to "now" for the coupon's end. One day
# is the hard ceiling  an instant discount can never outlast it.
DURATIONS: dict[str, tuple[str, timedelta]] = {
    "1h": ("1 hour", timedelta(hours=1)),
    "4h": ("4 hours", timedelta(hours=4)),
    "1d": ("1 day", timedelta(days=1)),
}
DEFAULT_DURATION = "1h"

# The two scopes an instant discount can take, expressed as the underlying
# coupon discount class.
SCOPE_ORDER = C.DiscountClass.ORDER
SCOPE_PRODUCT = C.DiscountClass.PRODUCT
VALID_SCOPES = {SCOPE_ORDER, SCOPE_PRODUCT}

# Marker key stored in ``Coupon.conditions`` for every instant discount.
CONDITION_KEY = "instant"


# --------------------------------------------------------------------------
# Small coercion helpers (kept local so this module stands alone)
# --------------------------------------------------------------------------
def _dec(raw):
    try:
        return Decimal(str(raw).strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None


def _int(raw):
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Queries
# --------------------------------------------------------------------------
def for_site(site):
    """Every instant discount for this store, newest first.

    Instant discounts are the coupons carrying the ``instant`` marker in their
    ``conditions`` JSON; ``has_key`` cleanly separates them from ordinary
    discounts (which default to an empty ``conditions``) on both SQLite and
    MySQL.
    """
    return (
        Coupon.objects.for_site(site)
        .filter(is_deleted=False, conditions__has_key=CONDITION_KEY)
        .order_by("-created_at")
    )


def is_instant(coupon: Coupon) -> bool:
    return bool(isinstance(coupon.conditions, dict) and coupon.conditions.get(CONDITION_KEY))


def generate_code(site) -> str:
    """A fresh code unique across this store's coupons (including soft-deleted).

    Checked against ``all_objects`` because the ``(site, code)`` uniqueness
    constraint ignores the soft-delete flag, so a retired code would still
    collide on insert.
    """
    for _ in range(12):
        candidate = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
        if not Coupon.all_objects.filter(site=site, code=candidate).exists():
            return candidate
    # Astronomically unlikely to reach here; widen the space and try once more.
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH + 4))


# --------------------------------------------------------------------------
# Presentation
# --------------------------------------------------------------------------
def duration_label(coupon: Coupon) -> str:
    """The human duration label stored on an instant discount (best-effort)."""
    key = (coupon.conditions or {}).get("duration") if isinstance(coupon.conditions, dict) else None
    entry = DURATIONS.get(key)
    return entry[0] if entry else ""


def _value_label(discount_type: str, value, currency: str) -> str:
    value = value or Decimal("0")
    if discount_type == C.DiscountType.PERCENT:
        # Trim a trailing ".00" so "10.00%" reads as "10%".
        return f"{value.normalize():f}% off" if isinstance(value, Decimal) else f"{value}% off"
    return f"{currency}{value.normalize():f} off" if isinstance(value, Decimal) else f"{currency}{value} off"


def summarise(coupon: Coupon, currency: str = "₹") -> str:
    """A one-line description of what an instant discount does."""
    vlabel = _value_label(coupon.discount_type, coupon.value, currency)
    if coupon.discount_class == SCOPE_PRODUCT:
        product = _first_product_title(coupon)
        qty = coupon.min_quantity or 1
        return f"{vlabel} on {product} (min {qty} qty)"
    if coupon.min_order_value:
        return f"{vlabel} on carts over {currency}{coupon.min_order_value.normalize():f}"
    return f"{vlabel} on cart value"


def _first_product_title(coupon: Coupon) -> str:
    ids = coupon.applies_product_ids or []
    if not ids:
        return "a product"
    product = (
        Product.objects.for_site(coupon.site)
        .filter(pk=str(ids[0]))
        .only("id", "title")
        .first()
    )
    return product.title if product else "a product"


def _auto_title(scope: str, discount_type: str, value: Decimal, product_title: str | None) -> str:
    vlabel = _value_label(discount_type, value, "₹")
    if scope == SCOPE_PRODUCT and product_title:
        return f"Instant {vlabel} · {product_title}"[:120]
    return f"Instant {vlabel} · cart value"[:120]


# --------------------------------------------------------------------------
# Form state (echoed back so a validation error never loses input)
# --------------------------------------------------------------------------
def blank_state() -> dict:
    return {
        "scope": SCOPE_ORDER,
        "discount_type": C.DiscountType.PERCENT,
        "value": "",
        "duration": DEFAULT_DURATION,
        "product": "",
        "min_quantity": "1",
        "min_amount": "",
    }


def state_from_post(post) -> dict:
    scope = post.get("scope")
    return {
        "scope": scope if scope in VALID_SCOPES else SCOPE_ORDER,
        "discount_type": post.get("discount_type") or C.DiscountType.PERCENT,
        "value": (post.get("value") or "").strip(),
        "duration": post.get("duration") if post.get("duration") in DURATIONS else DEFAULT_DURATION,
        "product": (post.get("product") or "").strip(),
        "min_quantity": (post.get("min_quantity") or "").strip(),
        "min_amount": (post.get("min_amount") or "").strip(),
    }


# --------------------------------------------------------------------------
# Validation + persistence
# --------------------------------------------------------------------------
def parse_and_validate(post, site) -> tuple[dict, list[str]]:
    """Return ``(cleaned, errors)``. ``cleaned`` maps onto model fields."""
    errors: list[str] = []
    cleaned: dict = {}

    scope = post.get("scope")
    if scope not in VALID_SCOPES:
        scope = SCOPE_ORDER
    cleaned["discount_class"] = scope

    # -- value & type (shared by both scopes) ------------------------------
    dtype = post.get("discount_type")
    if dtype not in C.DiscountType.values:
        dtype = C.DiscountType.PERCENT
    cleaned["discount_type"] = dtype

    value = _dec(post.get("value"))
    if value is None or value <= 0:
        errors.append("Enter a discount value greater than zero.")
    elif dtype == C.DiscountType.PERCENT and value > 100:
        errors.append("A percentage discount can’t be more than 100%.")
    cleaned["value"] = value or Decimal("0.00")

    # -- duration ----------------------------------------------------------
    duration = post.get("duration")
    if duration not in DURATIONS:
        errors.append("Choose how long this instant discount runs for.")
        duration = DEFAULT_DURATION
    cleaned["_duration"] = duration  # consumed by save_instant, not a model field

    # -- product scope specifics ------------------------------------------
    product_title = None
    if scope == SCOPE_PRODUCT:
        product_id = (post.get("product") or "").strip()
        product = None
        if product_id:
            product = (
                Product.objects.for_site(site)
                .filter(pk=product_id, is_deleted=False)
                .only("id", "title")
                .first()
            )
        if product is None:
            errors.append("Choose the product this instant discount applies to.")
            cleaned["applies_product_ids"] = []
        else:
            cleaned["applies_product_ids"] = [str(product.id)]
            product_title = product.title
        cleaned["applies_to"] = C.AppliesTo.PRODUCTS
        cleaned["applies_category_ids"] = []

        mqty = _int(post.get("min_quantity"))
        if mqty is None or mqty < 1:
            errors.append("Enter a minimum quantity of at least one item.")
            mqty = 1
        cleaned["min_requirement"] = C.MinRequirement.QUANTITY
        cleaned["min_quantity"] = mqty
        cleaned["min_order_value"] = None
    else:
        # Cart-value scope: amount off the whole order once the cart reaches a
        # minimum spend.
        cleaned["applies_to"] = C.AppliesTo.ALL
        cleaned["applies_product_ids"] = []
        cleaned["applies_category_ids"] = []
        mamt = _dec(post.get("min_amount"))
        if mamt is None or mamt <= 0:
            errors.append("Enter the minimum cart amount this discount needs.")
            mamt = None
        cleaned["min_requirement"] = C.MinRequirement.AMOUNT
        cleaned["min_order_value"] = mamt
        cleaned["min_quantity"] = None

    cleaned["_product_title"] = product_title  # consumed by save_instant
    return cleaned, errors


def save_instant(site, cleaned: dict) -> Coupon:
    """Persist a validated instant discount as an automatic, time-boxed coupon."""
    now = timezone.now()
    duration = cleaned.pop("_duration", DEFAULT_DURATION)
    product_title = cleaned.pop("_product_title", None)
    _, delta = DURATIONS.get(duration, DURATIONS[DEFAULT_DURATION])

    coupon = Coupon(site=site)
    # A code is minted automatically  the shopper enters it at checkout and it
    # applies exactly like any other coded discount, only it self-expires.
    coupon.method = C.Method.CODE
    coupon.code = generate_code(site)
    coupon.title = _auto_title(cleaned["discount_class"], cleaned["discount_type"], cleaned["value"], product_title)
    coupon.active = True
    coupon.starts_at = now
    coupon.ends_at = now + delta
    coupon.conditions = {CONDITION_KEY: True, "duration": duration}

    for field_name, value in cleaned.items():
        setattr(coupon, field_name, value)

    coupon.save()
    return coupon
