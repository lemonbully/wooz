"""Quantity discount pricing: the maths behind the Quantity Discounts app.

One place turns a store's :class:`~commerce.models.QuantityDiscountRule` rows
into money, so the product page, the cart, checkout and an owner's order edit
all agree to the paisa.

How a price is found
--------------------
* The **rule** for a product is the most specific active one: a rule naming
  the product, else one naming its category (or that category's parent), else
  a store-wide rule.
* The **quantity** that picks the tier is the units of that product in the
  cart, all variations together (or per variation, if the owner chose that).
* The **tier** is the highest one whose ``min_qty`` the quantity reaches. It
  lowers the line's *unit price* (percent off, amount off, or a fixed price,
  never above the regular price and never to zero), rounded to paise, and
  ``line_total = unit_price × quantity``.
* **Above the last tier** the owner chose what happens: the same discount
  continues, or the quantity stops at a limit (talk on WhatsApp / own message /
  no larger orders). The product page, cart, checkout and add-to-cart all
  enforce the limit. Quantities go up to 100.
* **On the product page** there is no extra block (Amazon-style): the design's
  own price per piece changes in place as the shopper changes the quantity.

Nothing here runs unless the store has the app live
(``QuantityDiscountSettings.app_active``), which only the marketplace module
writes. Rules are read once per request (memoised on the site object).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

_TWO = Decimal("0.01")
_ZERO = Decimal("0.00")
_HUNDRED = Decimal("100")

#: The most units a tier or a limit can name; the cart holds at most this many
#: units of one line (commerce.cart.MAX_QTY_PER_LINE).
MAX_TIER_QTY = 100
MAX_TIERS = 5


def _money(value) -> Decimal:
    return (Decimal(value or 0)).quantize(_TWO, rounding=ROUND_HALF_UP)


_SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "EURO": "€"}


def symbol_for(currency) -> str:
    """A display symbol for a currency code (``INR`` → ``₹``); symbols pass through."""
    c = str(currency or "INR").strip()
    return _SYMBOLS.get(c.upper(), c if len(c) <= 2 else "₹")


def _fmt_money(value, currency: str = "₹") -> str:
    currency = symbol_for(currency)
    v = _money(value)
    txt = f"{v:,.2f}"
    if txt.endswith(".00"):
        txt = txt[:-3]
    return f"{currency}{txt}"


def _fmt_num(value) -> str:
    v = Decimal(value)
    txt = f"{v.normalize():f}" if v == v.to_integral() else f"{v:.2f}".rstrip("0").rstrip(".")
    return txt


# --------------------------------------------------------------------------
# Plan: one rule, resolved and frozen
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Tier:
    min_qty: int
    value: Decimal


@dataclass
class Plan:
    rule_id: str
    name: str
    discount_type: str
    tiers: tuple
    together: bool = True
    beyond: str = "same"
    beyond_qty: int | None = None
    custom_message: str = ""
    custom_action: str = "whatsapp"

    # -- tiers -------------------------------------------------------------
    def tier_for(self, qty: int) -> Tier | None:
        found = None
        for t in self.tiers:
            if qty >= t.min_qty:
                found = t
        return found

    def next_tier(self, qty: int) -> Tier | None:
        for t in self.tiers:
            if t.min_qty > qty:
                if self.max_qty is not None and t.min_qty > self.max_qty:
                    return None
                return t
        return None

    def price_for_tier(self, base, tier: Tier | None) -> Decimal:
        """The unit price ``tier`` gives on ``base``; ``base`` when it gives nothing."""
        base = _money(base)
        if tier is None or base <= 0:
            return base
        if self.discount_type == "percent":
            pct = min(Decimal(tier.value), Decimal("99.99"))
            price = base * (Decimal("1") - pct / _HUNDRED)
        elif self.discount_type == "amount_off":
            price = base - Decimal(tier.value)
        else:  # fixed_price
            price = min(base, Decimal(tier.value))
        price = _money(price)
        if price <= 0 or price >= base:
            return base
        return price

    def unit_price(self, base, qty: int) -> tuple[Decimal, Tier | None]:
        tier = self.tier_for(qty)
        price = self.price_for_tier(base, tier)
        if price >= _money(base):
            return _money(base), None
        return price, tier

    # -- limit -------------------------------------------------------------
    @property
    def max_qty(self) -> int | None:
        if self.beyond == "same" or not self.beyond_qty:
            return None
        return int(self.beyond_qty)

    # -- words ---------------------------------------------------------------
    def tier_label(self, tier: Tier, currency: str = "₹") -> str:
        if self.discount_type == "percent":
            return f"{_fmt_num(tier.value)}% off"
        if self.discount_type == "amount_off":
            return f"{_fmt_money(tier.value, currency)} off each"
        return f"{_fmt_money(tier.value, currency)} each"

    def snapshot(self, tier: Tier | None, regular) -> dict:
        """What an order line keeps: enough to re-price after an owner edit."""
        return {
            "rule": self.rule_id,
            "name": self.name,
            "type": self.discount_type,
            "together": self.together,
            "tiers": [{"min_qty": t.min_qty, "value": str(t.value)} for t in self.tiers],
            "tier_min": tier.min_qty if tier else None,
            "regular": str(_money(regular)),
        }

    @classmethod
    def from_snapshot(cls, data: dict) -> "Plan | None":
        if not isinstance(data, dict) or not data.get("tiers"):
            return None
        tiers = []
        for t in data.get("tiers") or []:
            try:
                tiers.append(Tier(int(t["min_qty"]), Decimal(str(t["value"]))))
            except Exception:
                continue
        if not tiers:
            return None
        tiers.sort(key=lambda t: t.min_qty)
        return cls(
            rule_id=str(data.get("rule") or ""), name=str(data.get("name") or ""),
            discount_type=str(data.get("type") or "percent"), tiers=tuple(tiers),
            together=bool(data.get("together", True)),
        )


def _plan_of(rule) -> Plan | None:
    tiers = tuple(Tier(t["min_qty"], t["value"]) for t in rule.tier_list()[:MAX_TIERS])
    if not tiers:
        return None
    return Plan(
        rule_id=str(rule.pk), name=rule.name, discount_type=rule.discount_type, tiers=tiers,
        together=bool(rule.count_variations_together), beyond=rule.beyond,
        beyond_qty=rule.beyond_qty, custom_message=rule.custom_message or "",
        custom_action=rule.custom_action or "whatsapp",
    )


# --------------------------------------------------------------------------
# Store state (memoised on the site object: one read per request)
# --------------------------------------------------------------------------
def settings_for(site):
    """The store's settings row, or ``None`` (never raises)."""
    if site is None:
        return None
    if hasattr(site, "_qd_settings"):
        return site._qd_settings
    row = None
    try:
        from commerce.models import QuantityDiscountSettings

        row = QuantityDiscountSettings.objects.for_site(site).first()
    except Exception:
        row = None
    try:
        site._qd_settings = row
    except Exception:
        pass
    return row


def is_live(site) -> bool:
    row = settings_for(site)
    return bool(row and row.app_active)


def _in_window(rule, now) -> bool:
    if rule.starts_at and rule.starts_at > now:
        return False
    if rule.ends_at and rule.ends_at <= now:
        return False
    return True


def _rules(site) -> list:
    if hasattr(site, "_qd_rules"):
        return site._qd_rules
    rows = []
    if is_live(site):
        try:
            from commerce.models import QuantityDiscountRule

            now = timezone.now()
            rows = [
                r for r in QuantityDiscountRule.objects.for_site(site).filter(is_active=True, is_deleted=False)
                if _in_window(r, now)
            ]
            # Newest edit first, so an (unexpected) overlap resolves predictably.
            rows.sort(key=lambda r: r.updated_at, reverse=True)
        except Exception:
            rows = []
    try:
        site._qd_rules = rows
    except Exception:
        pass
    return rows


def _category_parents(site) -> dict:
    if hasattr(site, "_qd_cat_parents"):
        return site._qd_cat_parents
    out = {}
    try:
        from commerce.models import Category

        out = {str(i): (str(p) if p else "") for i, p in Category.objects.for_site(site).values_list("id", "parent_id")}
    except Exception:
        out = {}
    try:
        site._qd_cat_parents = out
    except Exception:
        pass
    return out


def plan_for(site, product) -> Plan | None:
    """The quantity plan for ``product`` on ``site`` (most specific rule wins)."""
    if product is None:
        return None
    rules = _rules(site)
    if not rules:
        return None
    pid = str(product.pk)
    for r in rules:
        if r.scope == r.Scope.PRODUCTS and pid in {str(x) for x in (r.product_ids or [])}:
            plan = _plan_of(r)
            if plan:
                return plan
    cat = str(getattr(product, "category_id", "") or "")
    if cat:
        cat_rules = [r for r in rules if r.scope == r.Scope.CATEGORIES]
        if cat_rules:
            chain = [cat]
            parent = _category_parents(site).get(cat, "")
            if parent:
                chain.append(parent)
            for c in chain:  # the product's own category beats its parent
                for r in cat_rules:
                    if c in {str(x) for x in (r.category_ids or [])}:
                        plan = _plan_of(r)
                        if plan:
                            return plan
    for r in rules:
        if r.scope == r.Scope.STORE:
            plan = _plan_of(r)
            if plan:
                return plan
    return None


# --------------------------------------------------------------------------
# Cart lines
# --------------------------------------------------------------------------
def _group_key(line, plan: Plan) -> str:
    pid = str(getattr(line.get("product"), "pk", "") or "")
    return pid if plan.together else f"{pid}:{line.get('variant_key') or line.get('id')}"


def apply_to_lines(site, lines: list[dict], currency: str = "₹") -> list[dict]:
    """Price cart lines (as built by :meth:`commerce.cart.Cart.items`) in place.

    Every line gains ``regular_unit_price``; a discounted one also gets a lower
    ``unit_price`` / ``line_total`` and ``qd`` (labels, savings, nudge, limit and
    the snapshot the order line keeps).
    """
    for ln in lines:
        ln.setdefault("regular_unit_price", ln.get("unit_price"))
    if not lines or not _rules(site):
        return lines
    plans: dict[str, Plan | None] = {}
    groups: dict[str, int] = {}
    for ln in lines:
        product = ln.get("product")
        if product is None:
            continue
        key = str(product.pk)
        if key not in plans:
            plans[key] = plan_for(site, product)
        plan = plans[key]
        if plan is None:
            continue
        g = _group_key(ln, plan)
        groups[g] = groups.get(g, 0) + int(ln.get("quantity") or 0)

    for ln in lines:
        product = ln.get("product")
        plan = plans.get(str(getattr(product, "pk", "")))
        if plan is None:
            continue
        group_qty = groups.get(_group_key(ln, plan), 0)
        regular = _money(ln.get("regular_unit_price") or ln.get("unit_price"))
        priced_qty = group_qty
        if plan.max_qty is not None:
            priced_qty = min(group_qty, plan.max_qty)
        unit, tier = plan.unit_price(regular, priced_qty)
        qty = int(ln.get("quantity") or 0)
        nxt = plan.next_tier(group_qty)
        info = {
            "rule": plan.rule_id,
            "applied": tier is not None,
            "tier_min": tier.min_qty if tier else None,
            "label": plan.tier_label(tier, currency) if tier else "",
            "note": (f"Quantity price: {plan.tier_label(tier, currency)} for {tier.min_qty}+ units"
                     if tier else ""),
            "saving": _money((regular - unit) * qty) if tier else _ZERO,
            "next": None,
            "limit": plan.max_qty,
            "over_limit": plan.max_qty is not None and group_qty > plan.max_qty,
            "group_qty": group_qty,
            "snapshot": plan.snapshot(tier, regular) if tier else {},
        }
        if nxt is not None:
            need = nxt.min_qty - group_qty
            info["next"] = {
                "need": need,
                "label": plan.tier_label(nxt, currency),
                "text": f"Add {need} more to get {plan.tier_label(nxt, currency)}",
            }
        ln["qd"] = info
        if tier is not None:
            ln["unit_price"] = unit
            ln["line_total"] = _money(unit * qty)
    return lines


def lines_over_limit(lines) -> list[dict]:
    return [ln for ln in lines if (ln.get("qd") or {}).get("over_limit")]


def limit_message(site, lines_over) -> str:
    """Why checkout refuses, naming each product and its limit."""
    parts = []
    seen = set()
    for ln in lines_over:
        qd = ln.get("qd") or {}
        key = str(getattr(ln.get("product"), "pk", ""))
        if key in seen:
            continue
        seen.add(key)
        parts.append(f"{ln.get('title', 'This item')}: up to {qd.get('limit')} per order")
    msg = "Some items are above the most you can order online (" + "; ".join(parts) + ")."
    if any(_contact_kind_for(site, ln) for ln in lines_over):
        msg += " Contact the store for a bulk price."
    return msg + " Please lower the quantity to continue."


def _contact_kind_for(site, ln) -> str:
    plan = plan_for(site, ln.get("product"))
    if plan is None or plan.beyond in ("same", "limit"):
        return ""
    return "whatsapp" if plan.beyond == "whatsapp" else plan.custom_action


def cap_for(site, product) -> int | None:
    """Most units of ``product`` (its group) one order may hold, or ``None``."""
    plan = plan_for(site, product)
    return plan.max_qty if plan else None


def allowed_more(site, product, cart_data: dict, variant_key: str = "") -> tuple[int | None, Plan | None]:
    """How many more units of ``product`` may go into the cart (``None`` = no limit).

    ``cart_data`` is the session cart map (``Cart.data``)."""
    plan = plan_for(site, product)
    if plan is None or plan.max_qty is None:
        return None, plan
    pid = str(product.pk)
    held = 0
    for line in (cart_data or {}).values():
        if not isinstance(line, dict) or str(line.get("pid")) != pid:
            continue
        if not plan.together and str(line.get("vk") or "") != (variant_key or ""):
            continue
        held += int(line.get("qty") or 0)
    return max(0, plan.max_qty - held), plan


# --------------------------------------------------------------------------
# Coupons
# --------------------------------------------------------------------------
def coupon_lines(site, lines: list[dict]) -> tuple[list[dict], Decimal, bool]:
    """Lines (and their subtotal) that coupons may discount.

    When the store says quantity prices don't combine with coupons, lines
    already at a quantity price are left out. The flag is True when lines were
    left out, so the cart can explain a code that no longer applies."""
    row = settings_for(site)
    if row is None or not row.app_active or row.combine_with_coupons:
        return lines, _money(sum((ln["line_total"] for ln in lines), _ZERO)), False
    kept = [ln for ln in lines if not (ln.get("qd") or {}).get("applied")]
    return kept, _money(sum((ln["line_total"] for ln in kept), _ZERO)), len(kept) != len(lines)


# --------------------------------------------------------------------------
# Order edits
# --------------------------------------------------------------------------
def reprice_order_items(items, *, keep_ids=frozenset()) -> list:
    """Re-price order lines that were sold at a quantity price, from the deal
    frozen on them, after an owner changed quantities. Lines in ``keep_ids``
    (the owner typed a price) are left alone. Returns the changed items
    (unsaved)."""
    groups: dict[str, int] = {}
    plans = {}
    for it in items:
        plan = Plan.from_snapshot(getattr(it, "quantity_discount", None) or {})
        if plan is None or it.regular_unit_price is None:
            continue
        plans[it.pk] = plan
        key = str(it.product_id) if plan.together else f"{it.product_id}:{it.variant_label}"
        groups[key] = groups.get(key, 0) + int(it.quantity or 0)
    changed = []
    for it in items:
        plan = plans.get(it.pk)
        if plan is None or it.pk in keep_ids:
            continue
        key = str(it.product_id) if plan.together else f"{it.product_id}:{it.variant_label}"
        unit, tier = plan.unit_price(it.regular_unit_price, groups.get(key, 0))
        if unit != _money(it.unit_price):
            it.unit_price = unit
            it.line_total = _money(unit * int(it.quantity or 0))
            snap = dict(it.quantity_discount or {})
            snap["tier_min"] = tier.min_qty if tier else None
            it.quantity_discount = snap
            changed.append(it)
    return changed


# --------------------------------------------------------------------------
# Product page
# --------------------------------------------------------------------------
def _digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def whatsapp_number(site) -> str:
    """The store's WhatsApp number (digits), from Contact, else the support phone."""
    content = getattr(site, "content", None) or {}
    info = content.get("contact_info") if isinstance(content, dict) else None
    num = ""
    if isinstance(info, dict):
        num = _digits(info.get("whatsapp_number"))
    return num or _digits(getattr(site, "support_phone", ""))


def phone_number(site) -> str:
    content = getattr(site, "content", None) or {}
    info = content.get("contact_info") if isinstance(content, dict) else None
    num = ""
    if isinstance(info, dict):
        num = str(info.get("mobile_number") or info.get("phone") or "").strip()
    return num or str(getattr(site, "support_phone", "") or "").strip()


def pdp_config(site, product, *, base_price=None) -> dict | None:
    """What the product page script needs to swap the price per piece in place.

    No markup: the design's own price element shows the tier price for the
    quantity the shopper picks (see static/js/marketplace/ow-qty-pricing.js).
    ``None`` when this product has no quantity discount."""
    plan = plan_for(site, product)
    if plan is None:
        return None
    regular = _money(base_price if base_price is not None else getattr(product, "price", 0))
    if regular <= 0 or not any(plan.price_for_tier(regular, t) < regular for t in plan.tiers):
        return None
    limit = plan.max_qty
    notice = ""
    if limit:
        notice = f"You can order up to {limit} of this item."
        if plan.beyond == "whatsapp":
            if whatsapp_number(site):
                notice += " For more, message us on WhatsApp."
        elif plan.beyond == "custom" and plan.custom_message:
            notice = plan.custom_message
    return {
        "type": plan.discount_type,
        "tiers": [{"min": t.min_qty, "v": str(t.value)} for t in plan.tiers],
        "regular": str(regular),
        "limit": limit,
        "notice": notice,
    }
