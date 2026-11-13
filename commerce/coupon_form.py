"""Server-side parsing, validation and persistence for the discount editor.

The discount editor (``templates/store/coupon_form.html``) is a bespoke,
Shopify-shaped form with many conditional sections, so rather than force it
through a generic ``ModelForm`` it posts named fields that are parsed here. This
module owns three jobs, keeping :mod:`commerce.views.growth` thin:

* :func:`build_state`  the form's initial values (blank defaults, or an
  existing :class:`~commerce.models.Coupon` for editing).
* :func:`state_from_post`  echo a submission back verbatim so a validation
  error never loses what the owner typed.
* :func:`parse_and_validate` / :func:`save_coupon`  coerce and check every
  field, then write it.

Product/category/customer selections are validated against ``.for_site`` so a
crafted POST can only ever reference this store's own records.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from commerce.models import Category, Coupon, Customer, Product

C = Coupon  # local alias for the choice enums


# --------------------------------------------------------------------------
# Small coercion helpers
# --------------------------------------------------------------------------
def _bool(post, name) -> bool:
    return post.get(name) in ("on", "1", "true", "True", "yes")


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


def _choice(raw, choices, default):
    return raw if raw in choices.values else default


def _clean_ids(site, model, raw_ids) -> list[str]:
    """Keep only the ids that belong to this store, as strings."""
    ids = [str(x) for x in (raw_ids or []) if str(x).strip()]
    if not ids:
        return []
    valid = (
        model.objects.for_site(site)
        .filter(pk__in=ids, is_deleted=False)
        .values_list("pk", flat=True)
    )
    valid = {str(v) for v in valid}
    # Preserve the submitted order, drop anything that didn't resolve.
    return [i for i in ids if i in valid]


def _parse_dt(date_str, time_str):
    """Combine a ``YYYY-MM-DD`` date and ``HH:MM`` time into an aware datetime."""
    date_str = (date_str or "").strip()
    if not date_str:
        return None
    time_str = (time_str or "").strip() or "00:00"
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            naive = datetime.strptime(f"{date_str} {time_str}", fmt)
            break
        except ValueError:
            naive = None
    if naive is None:
        try:
            naive = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return None
    if timezone.is_naive(naive):
        return timezone.make_aware(naive, timezone.get_current_timezone())
    return naive


def _split_dt(dt):
    if not dt:
        return "", ""
    local = timezone.localtime(dt)
    return local.strftime("%Y-%m-%d"), local.strftime("%H:%M")


# --------------------------------------------------------------------------
# Template state
# --------------------------------------------------------------------------
def _blank_state() -> dict:
    today = timezone.localtime(timezone.now())
    return {
        "discount_class": C.DiscountClass.ORDER,
        "method": C.Method.CODE,
        "code": "",
        "title": "",
        "discount_type": C.DiscountType.PERCENT,
        "value": "",
        "applies_to": C.AppliesTo.ALL,
        "applies_products": [],
        "applies_categories": [],
        "min_requirement": C.MinRequirement.NONE,
        "min_order_value": "",
        "min_quantity": "",
        "max_discount_enabled": False,
        "max_discount_amount": "",
        "customer_eligibility": C.CustomerEligibility.ALL,
        "eligible_customers": [],
        "usage_limit_enabled": False,
        "usage_limit": "",
        "once_per_customer": False,
        "combines_product": False,
        "combines_order": False,
        "combines_shipping": False,
        "shipping_max_enabled": False,
        "shipping_max_amount": "",
        "buy_type": C.BuyType.QUANTITY,
        "buy_quantity": "",
        "buy_min_amount": "",
        "buy_products": [],
        "buy_categories": [],
        "get_quantity": "",
        "get_products": [],
        "get_categories": [],
        "reward_type": C.RewardType.FREE,
        "reward_value": "",
        "bxgy_max_enabled": False,
        "bxgy_max_per_order": "",
        "status": "active",
        "start_date": today.strftime("%Y-%m-%d"),
        "start_time": today.strftime("%H:%M"),
        "set_end_date": False,
        "end_date": "",
        "end_time": "",
    }


def build_state(coupon: Coupon | None) -> dict:
    state = _blank_state()
    if coupon is None:
        return state
    sd, st = _split_dt(coupon.starts_at)
    ed, et = _split_dt(coupon.ends_at)
    state.update(
        {
            "discount_class": coupon.discount_class,
            "method": coupon.method,
            "code": coupon.code,
            "title": coupon.title,
            "discount_type": coupon.discount_type,
            "value": _str_num(coupon.value),
            "applies_to": coupon.applies_to,
            "applies_products": [str(x) for x in (coupon.applies_product_ids or [])],
            "applies_categories": [str(x) for x in (coupon.applies_category_ids or [])],
            "min_requirement": coupon.min_requirement,
            "min_order_value": _str_num(coupon.min_order_value),
            "min_quantity": _str_num(coupon.min_quantity),
            "max_discount_enabled": coupon.max_discount_amount is not None,
            "max_discount_amount": _str_num(coupon.max_discount_amount),
            "customer_eligibility": coupon.customer_eligibility,
            "eligible_customers": [str(x) for x in (coupon.eligible_customer_ids or [])],
            "usage_limit_enabled": coupon.usage_limit is not None,
            "usage_limit": _str_num(coupon.usage_limit),
            "once_per_customer": coupon.once_per_customer,
            "combines_product": coupon.combines_with_product,
            "combines_order": coupon.combines_with_order,
            "combines_shipping": coupon.combines_with_shipping,
            "shipping_max_enabled": coupon.shipping_max_amount is not None,
            "shipping_max_amount": _str_num(coupon.shipping_max_amount),
            "buy_type": coupon.buy_type,
            "buy_quantity": _str_num(coupon.buy_quantity),
            "buy_min_amount": _str_num(coupon.buy_min_amount),
            "buy_products": [str(x) for x in (coupon.buy_product_ids or [])],
            "buy_categories": [str(x) for x in (coupon.buy_category_ids or [])],
            "get_quantity": _str_num(coupon.get_quantity),
            "get_products": [str(x) for x in (coupon.get_product_ids or [])],
            "get_categories": [str(x) for x in (coupon.get_category_ids or [])],
            "reward_type": coupon.reward_type,
            "reward_value": _str_num(coupon.reward_value),
            "bxgy_max_enabled": coupon.bxgy_max_per_order is not None,
            "bxgy_max_per_order": _str_num(coupon.bxgy_max_per_order),
            "status": "active" if coupon.active else "draft",
            "start_date": sd or state["start_date"],
            "start_time": st or state["start_time"],
            "set_end_date": bool(coupon.ends_at),
            "end_date": ed,
            "end_time": et or "23:59",
        }
    )
    return state


def _str_num(v) -> str:
    """Render a stored number for an input without trailing ``.00`` noise."""
    if v is None:
        return ""
    if isinstance(v, Decimal):
        v = v.normalize()
        # normalize() can yield exponent form (e.g. 1E+2); expand it.
        return format(v, "f")
    return str(v)


def state_from_post(post) -> dict:
    """Echo a submission back into template state (nothing typed is lost)."""
    return {
        "discount_class": post.get("discount_class") or C.DiscountClass.ORDER,
        "method": post.get("method") or C.Method.CODE,
        "code": (post.get("code") or "").strip().upper(),
        "title": (post.get("title") or "").strip(),
        "discount_type": post.get("discount_type") or C.DiscountType.PERCENT,
        "value": (post.get("value") or "").strip(),
        "applies_to": post.get("applies_to") or C.AppliesTo.ALL,
        "applies_products": post.getlist("applies_products"),
        "applies_categories": post.getlist("applies_categories"),
        "min_requirement": post.get("min_requirement") or C.MinRequirement.NONE,
        "min_order_value": (post.get("min_order_value") or "").strip(),
        "min_quantity": (post.get("min_quantity") or "").strip(),
        "max_discount_enabled": _bool(post, "max_discount_enabled"),
        "max_discount_amount": (post.get("max_discount_amount") or "").strip(),
        "customer_eligibility": post.get("customer_eligibility") or C.CustomerEligibility.ALL,
        "eligible_customers": post.getlist("eligible_customers"),
        "usage_limit_enabled": _bool(post, "usage_limit_enabled"),
        "usage_limit": (post.get("usage_limit") or "").strip(),
        "once_per_customer": _bool(post, "once_per_customer"),
        "combines_product": _bool(post, "combines_product"),
        "combines_order": _bool(post, "combines_order"),
        "combines_shipping": _bool(post, "combines_shipping"),
        "shipping_max_enabled": _bool(post, "shipping_max_enabled"),
        "shipping_max_amount": (post.get("shipping_max_amount") or "").strip(),
        "buy_type": post.get("buy_type") or C.BuyType.QUANTITY,
        "buy_quantity": (post.get("buy_quantity") or "").strip(),
        "buy_min_amount": (post.get("buy_min_amount") or "").strip(),
        "buy_products": post.getlist("buy_products"),
        "buy_categories": post.getlist("buy_categories"),
        "get_quantity": (post.get("get_quantity") or "").strip(),
        "get_products": post.getlist("get_products"),
        "get_categories": post.getlist("get_categories"),
        "reward_type": post.get("reward_type") or C.RewardType.FREE,
        "reward_value": (post.get("reward_value") or "").strip(),
        "bxgy_max_enabled": _bool(post, "bxgy_max_enabled"),
        "bxgy_max_per_order": (post.get("bxgy_max_per_order") or "").strip(),
        "status": post.get("status") or "active",
        "start_date": (post.get("start_date") or "").strip(),
        "start_time": (post.get("start_time") or "").strip(),
        "set_end_date": _bool(post, "set_end_date"),
        "end_date": (post.get("end_date") or "").strip(),
        "end_time": (post.get("end_time") or "").strip(),
    }


# --------------------------------------------------------------------------
# Validation + persistence
# --------------------------------------------------------------------------
def parse_and_validate(post, site, instance: Coupon | None) -> tuple[dict, list[str]]:
    """Return ``(cleaned, errors)``. ``cleaned`` maps directly onto model fields."""
    errors: list[str] = []
    cleaned: dict = {}

    dclass = _choice(post.get("discount_class"), C.DiscountClass, C.DiscountClass.ORDER)
    method = _choice(post.get("method"), C.Method, C.Method.CODE)
    cleaned["discount_class"] = dclass
    cleaned["method"] = method

    # -- identity ----------------------------------------------------------
    code = (post.get("code") or "").strip().upper()
    title = (post.get("title") or "").strip()
    if method == C.Method.CODE:
        if not code:
            errors.append("Enter a discount code shoppers will type at checkout.")
        else:
            clash = (
                Coupon.objects.for_site(site)
                .filter(code=code, is_deleted=False)
                .exclude(pk=instance.pk if instance else None)
                .exists()
            )
            if clash:
                errors.append(f"The code “{code}” is already in use. Choose another.")
        cleaned["code"] = code
        cleaned["title"] = title or code
    else:
        if not title:
            errors.append("Give this automatic discount a title.")
        cleaned["code"] = ""
        cleaned["title"] = title

    # -- value / reward ----------------------------------------------------
    if dclass in (C.DiscountClass.ORDER, C.DiscountClass.PRODUCT):
        dtype = _choice(post.get("discount_type"), C.DiscountType, C.DiscountType.PERCENT)
        cleaned["discount_type"] = dtype
        value = _dec(post.get("value"))
        if value is None or value <= 0:
            errors.append("Enter a discount value greater than zero.")
        elif dtype == C.DiscountType.PERCENT and value > 100:
            errors.append("A percentage discount can’t be more than 100%.")
        cleaned["value"] = value or Decimal("0.00")
    else:
        cleaned["discount_type"] = C.DiscountType.PERCENT
        cleaned["value"] = Decimal("0.00")

    # -- applies to (product class) ---------------------------------------
    if dclass == C.DiscountClass.PRODUCT:
        applies_to = _choice(post.get("applies_to"), C.AppliesTo, C.AppliesTo.ALL)
        cleaned["applies_to"] = applies_to
        prods = _clean_ids(site, Product, post.getlist("applies_products"))
        cats = _clean_ids(site, Category, post.getlist("applies_categories"))
        cleaned["applies_product_ids"] = prods
        cleaned["applies_category_ids"] = cats
        if applies_to == C.AppliesTo.PRODUCTS and not prods:
            errors.append("Choose at least one product this discount applies to.")
        if applies_to == C.AppliesTo.CATEGORIES and not cats:
            errors.append("Choose at least one category this discount applies to.")
    else:
        cleaned["applies_to"] = C.AppliesTo.ALL
        cleaned["applies_product_ids"] = []
        cleaned["applies_category_ids"] = []

    # -- minimum requirement (all but BXGY) --------------------------------
    if dclass == C.DiscountClass.BXGY:
        cleaned["min_requirement"] = C.MinRequirement.NONE
        cleaned["min_order_value"] = None
        cleaned["min_quantity"] = None
    else:
        minreq = _choice(post.get("min_requirement"), C.MinRequirement, C.MinRequirement.NONE)
        cleaned["min_requirement"] = minreq
        mov = _dec(post.get("min_order_value"))
        mqty = _int(post.get("min_quantity"))
        if minreq == C.MinRequirement.AMOUNT:
            if mov is None or mov <= 0:
                errors.append("Enter a minimum purchase amount greater than zero.")
            cleaned["min_order_value"] = mov
            cleaned["min_quantity"] = None
        elif minreq == C.MinRequirement.QUANTITY:
            if mqty is None or mqty < 1:
                errors.append("Enter a minimum quantity of at least one item.")
            cleaned["min_quantity"] = mqty
            cleaned["min_order_value"] = None
        else:
            cleaned["min_order_value"] = None
            cleaned["min_quantity"] = None

    # -- max discount cap (percentage) ------------------------------------
    if _bool(post, "max_discount_enabled") and cleaned.get("discount_type") == C.DiscountType.PERCENT \
            and dclass in (C.DiscountClass.ORDER, C.DiscountClass.PRODUCT):
        cap = _dec(post.get("max_discount_amount"))
        if cap is None or cap <= 0:
            errors.append("Enter a maximum discount amount greater than zero.")
        cleaned["max_discount_amount"] = cap
    else:
        cleaned["max_discount_amount"] = None

    # -- customer eligibility ---------------------------------------------
    eligibility = _choice(post.get("customer_eligibility"), C.CustomerEligibility, C.CustomerEligibility.ALL)
    cleaned["customer_eligibility"] = eligibility
    if eligibility == C.CustomerEligibility.SPECIFIC:
        custs = _clean_ids(site, Customer, post.getlist("eligible_customers"))
        if not custs:
            errors.append("Choose at least one customer, or open the discount to all customers.")
        cleaned["eligible_customer_ids"] = custs
    else:
        cleaned["eligible_customer_ids"] = []

    # -- usage limits ------------------------------------------------------
    if _bool(post, "usage_limit_enabled"):
        limit = _int(post.get("usage_limit"))
        if limit is None or limit < 1:
            errors.append("Enter a total usage limit of at least one.")
        cleaned["usage_limit"] = limit
    else:
        cleaned["usage_limit"] = None
    cleaned["once_per_customer"] = _bool(post, "once_per_customer")

    # -- combinations ------------------------------------------------------
    cleaned["combines_with_product"] = _bool(post, "combines_product")
    cleaned["combines_with_order"] = _bool(post, "combines_order")
    cleaned["combines_with_shipping"] = _bool(post, "combines_shipping")

    # -- free-shipping cap -------------------------------------------------
    if dclass == C.DiscountClass.SHIPPING and _bool(post, "shipping_max_enabled"):
        cap = _dec(post.get("shipping_max_amount"))
        if cap is None or cap <= 0:
            errors.append("Enter a shipping-rate cap greater than zero.")
        cleaned["shipping_max_amount"] = cap
    else:
        cleaned["shipping_max_amount"] = None

    # -- buy X get Y -------------------------------------------------------
    if dclass == C.DiscountClass.BXGY:
        buy_type = _choice(post.get("buy_type"), C.BuyType, C.BuyType.QUANTITY)
        cleaned["buy_type"] = buy_type
        cleaned["buy_product_ids"] = _clean_ids(site, Product, post.getlist("buy_products"))
        cleaned["buy_category_ids"] = _clean_ids(site, Category, post.getlist("buy_categories"))
        cleaned["get_product_ids"] = _clean_ids(site, Product, post.getlist("get_products"))
        cleaned["get_category_ids"] = _clean_ids(site, Category, post.getlist("get_categories"))
        if not (cleaned["buy_product_ids"] or cleaned["buy_category_ids"]):
            errors.append("Choose the products a customer must buy to unlock this offer.")
        if not (cleaned["get_product_ids"] or cleaned["get_category_ids"]):
            errors.append("Choose the products the customer gets.")
        if buy_type == C.BuyType.QUANTITY:
            bq = _int(post.get("buy_quantity"))
            if bq is None or bq < 1:
                errors.append("Enter the quantity a customer must buy (at least one).")
            cleaned["buy_quantity"] = bq
            cleaned["buy_min_amount"] = None
        else:
            ba = _dec(post.get("buy_min_amount"))
            if ba is None or ba <= 0:
                errors.append("Enter the purchase amount that unlocks this offer.")
            cleaned["buy_min_amount"] = ba
            cleaned["buy_quantity"] = None
        gq = _int(post.get("get_quantity"))
        if gq is None or gq < 1:
            errors.append("Enter how many items the customer gets (at least one).")
        cleaned["get_quantity"] = gq
        reward = _choice(post.get("reward_type"), C.RewardType, C.RewardType.FREE)
        cleaned["reward_type"] = reward
        if reward == C.RewardType.PERCENT:
            rv = _dec(post.get("reward_value"))
            if rv is None or rv <= 0 or rv > 100:
                errors.append("Enter a reward percentage between 0 and 100.")
            cleaned["reward_value"] = rv or Decimal("0.00")
        elif reward == C.RewardType.AMOUNT:
            rv = _dec(post.get("reward_value"))
            if rv is None or rv <= 0:
                errors.append("Enter the amount off each item.")
            cleaned["reward_value"] = rv or Decimal("0.00")
        else:
            cleaned["reward_value"] = Decimal("0.00")
        if _bool(post, "bxgy_max_enabled"):
            mx = _int(post.get("bxgy_max_per_order"))
            if mx is None or mx < 1:
                errors.append("Enter a per-order reward cap of at least one.")
            cleaned["bxgy_max_per_order"] = mx
        else:
            cleaned["bxgy_max_per_order"] = None
    else:
        # Reset BXGY fields to their defaults for non-BXGY classes.
        cleaned.update(
            buy_type=C.BuyType.QUANTITY, buy_quantity=None, buy_min_amount=None,
            buy_product_ids=[], buy_category_ids=[], get_quantity=None,
            get_product_ids=[], get_category_ids=[], reward_type=C.RewardType.FREE,
            reward_value=Decimal("0.00"), bxgy_max_per_order=None,
        )

    # -- active window -----------------------------------------------------
    cleaned["active"] = (post.get("status") or "active") != "draft"
    starts_at = _parse_dt(post.get("start_date"), post.get("start_time"))
    cleaned["starts_at"] = starts_at
    if _bool(post, "set_end_date"):
        ends_at = _parse_dt(post.get("end_date"), post.get("end_time") or "23:59")
        if ends_at is None:
            errors.append("Enter a valid end date, or turn off the end date.")
        elif starts_at and ends_at <= starts_at:
            errors.append("The end date must be after the start date.")
        cleaned["ends_at"] = ends_at
    else:
        cleaned["ends_at"] = None

    return cleaned, errors


def save_coupon(site, cleaned: dict, instance: Coupon | None) -> Coupon:
    coupon = instance or Coupon(site=site)
    coupon.site = site
    for field_name, value in cleaned.items():
        setattr(coupon, field_name, value)
    coupon.save()
    return coupon
