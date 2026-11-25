"""Owner pages for the Quantity Discounts app.

Reached only through the marketplace app page (``store_apps:page``), so it
always runs inside the marketplace ``app_boundary`` (owner only, installed and
licensed, fail-soft). Everything happens on one URL:

* ``GET``                 the list of rules + store-wide switches
* ``GET ?rule=new``       a new rule
* ``GET ?rule=<uuid>``    edit a rule
* ``POST action=...``     save_rule / delete_rule / toggle_rule / save_settings

The pricing itself is :mod:`commerce.quantity_pricing`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone

from commerce import activity
from commerce import quantity_pricing as qp
from commerce.context import store_context
from commerce.models import Category, Product, QuantityDiscountRule, QuantityDiscountSettings
from core import ratelimit
from core.queries import first_row
from core.queries import sorted_rows

Rule = QuantityDiscountRule


def _nav_key(app, placement) -> str:
    if app is not None and placement is not None:
        return f"app:{app.slug}:{placement.pk}"
    return "apps"


def _render(request, site, template, ctx, *, app=None, placement=None):
    context = store_context(request, site, _nav_key(app, placement))
    context.update(ctx)
    context.setdefault("currency", qp.symbol_for(site.currency))
    context["app"] = app
    context["crumb"] = "Quantity discounts"
    return render(request, template, context)


def _settings(site) -> QuantityDiscountSettings:
    row = QuantityDiscountSettings.objects.for_site(site).first()
    if row is None:
        row = QuantityDiscountSettings.objects.create(site=site)
    return row


def _rule(site, raw):
    try:
        pk = uuid.UUID(str(raw))
    except (TypeError, ValueError):
        return None
    return Rule.objects.for_site(site).filter(pk=pk, is_deleted=False).first()


def _moq(product) -> int:
    try:
        return max(1, int((product.d2c_details or {}).get("moq") or 1))
    except Exception:
        return 1


def _catalogue(site):
    # Sort-safe (core.queries): only keys go through MySQL's sort, never wide rows.
    products = sorted_rows(
        Product.objects.for_site(site)
        .filter(is_deleted=False)
        .only("id", "title", "price", "sku", "is_active", "d2c_details")
        .order_by("title"),
        limit=2000,
    )
    categories = sorted_rows(
        Category.objects.for_site(site).only("id", "name", "parent_id").order_by("name"), limit=500,
    )
    return products, categories


# --------------------------------------------------------------------------
# Describing a rule (list + summary)
# --------------------------------------------------------------------------
def describe(rule, currency: str, names: dict | None = None) -> dict:
    plan = qp._plan_of(rule)
    tiers = []
    if plan is not None:
        tiers = [f"{t.min_qty}+ : {plan.tier_label(t, currency)}" for t in plan.tiers]
    if rule.scope == Rule.Scope.STORE:
        scope = "Every product"
    elif rule.scope == Rule.Scope.CATEGORIES:
        n = len(rule.category_ids or [])
        scope = f"{n} categor{'y' if n == 1 else 'ies'}"
    else:
        n = len(rule.product_ids or [])
        scope = f"{n} product{'' if n == 1 else 's'}"
        if names and n == 1:
            scope = names.get(str((rule.product_ids or [""])[0]), scope)
    if rule.beyond == Rule.Beyond.SAME:
        beyond = "Same discount for larger orders"
    elif rule.beyond == Rule.Beyond.LIMIT:
        beyond = f"Up to {rule.beyond_qty} per order"
    elif rule.beyond == Rule.Beyond.WHATSAPP:
        beyond = f"Above {rule.beyond_qty}: WhatsApp"
    else:
        beyond = f"Above {rule.beyond_qty}: your message"
    now = timezone.now()
    if not rule.is_active:
        status = ("Paused", "")
    elif rule.ends_at and rule.ends_at <= now:
        status = ("Ended", "")
    elif rule.starts_at and rule.starts_at > now:
        status = ("Scheduled", "accent")
    else:
        status = ("Live", "live")
    return {"rule": rule, "tiers": tiers, "scope": scope, "beyond": beyond,
            "status": status[0], "status_kind": status[1]}


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def _dt(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        return "bad"
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    return value


def _dec(raw):
    try:
        return Decimal(str(raw).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None


def clean_rule(site, post, *, instance=None) -> tuple[dict, list[str]]:
    """Read and validate the rule form. Returns ``(values, errors)``."""
    errors: list[str] = []
    v: dict = {}
    v["name"] = (post.get("name") or "").strip()[:120]
    v["is_active"] = post.get("is_active") in ("1", "on", "true")

    scope = post.get("scope") or ""
    if scope not in Rule.Scope.values:
        errors.append("Choose which products this discount is for.")
        scope = Rule.Scope.PRODUCTS
    v["scope"] = scope

    products = []
    categories = []
    if scope == Rule.Scope.PRODUCTS:
        ids = [x for x in post.getlist("product_ids") if x]
        products = list(Product.objects.for_site(site).filter(pk__in=_uuids(ids), is_deleted=False))
        if not products:
            errors.append("Pick at least one product.")
    elif scope == Rule.Scope.CATEGORIES:
        ids = [x for x in post.getlist("category_ids") if x]
        categories = list(Category.objects.for_site(site).filter(pk__in=_uuids(ids)))
        if not categories:
            errors.append("Pick at least one category.")
    v["product_ids"] = [str(p.pk) for p in products]
    v["category_ids"] = [str(c.pk) for c in categories]

    dtype = post.get("discount_type") or ""
    if dtype not in Rule.DiscountType.values:
        errors.append("Choose how the price drops.")
        dtype = Rule.DiscountType.PERCENT
    if dtype == Rule.DiscountType.FIXED_PRICE and scope != Rule.Scope.PRODUCTS:
        errors.append("A fixed price per unit only works for specific products. Use a percentage or amount off "
                      "for categories or every product.")
    v["discount_type"] = dtype

    # Tiers ---------------------------------------------------------------
    qtys = post.getlist("tier_qty")
    vals = post.getlist("tier_value")
    tiers = []
    for i, raw_q in enumerate(qtys):
        raw_v = vals[i] if i < len(vals) else ""
        if not str(raw_q).strip() and not str(raw_v).strip():
            continue
        try:
            q = int(str(raw_q).strip())
        except (TypeError, ValueError):
            errors.append(f"Tier {i + 1}: the quantity must be a whole number.")
            continue
        val = _dec(raw_v)
        if val is None or val <= 0:
            errors.append(f"Tier {i + 1}: enter the discount for {q} units.")
            continue
        tiers.append((q, val.quantize(Decimal("0.01"))))
    if not tiers:
        errors.append("Add at least one tier, for example 2 units at 5% off.")
    if len(tiers) > qp.MAX_TIERS:
        errors.append(f"Use at most {qp.MAX_TIERS} tiers.")
    for i, (q, val) in enumerate(tiers):
        if q < 2:
            errors.append("A tier starts at 2 units or more (1 unit is the regular price).")
        if q > qp.MAX_TIER_QTY:
            errors.append(f"A tier can start at most at {qp.MAX_TIER_QTY} units.")
        if dtype == Rule.DiscountType.PERCENT and val >= Decimal("95"):
            errors.append("A percentage tier must be below 95%.")
        if i:
            pq, pv = tiers[i - 1]
            if q <= pq:
                errors.append("Each tier needs a larger quantity than the one before it.")
            if dtype == Rule.DiscountType.FIXED_PRICE and val >= pv:
                errors.append("With a fixed price, each bigger tier needs a lower price per unit.")
            if dtype != Rule.DiscountType.FIXED_PRICE and val <= pv:
                errors.append("Each bigger tier needs a bigger discount than the one before it.")
    v["tiers"] = [{"min_qty": q, "value": str(val)} for q, val in tiers]
    v["count_variations_together"] = post.get("count_variations_together", "1") in ("1", "on", "true")

    # Prices the tiers must stay below (product rules only).
    if products and tiers:
        cheapest = min((p.price or Decimal("0")) for p in products)
        if dtype == Rule.DiscountType.AMOUNT_OFF and tiers[-1][1] >= cheapest:
            errors.append(f"The amount off must stay below the product price ({qp.symbol_for(site.currency)}{cheapest}).")
        if dtype == Rule.DiscountType.FIXED_PRICE and tiers[0][1] >= cheapest:
            errors.append(f"A fixed price must be below the regular price ({qp.symbol_for(site.currency)}{cheapest}).")
        # B2B: never a tier below a product's minimum order quantity.
        low = [p for p in products if _moq(p) > tiers[0][0]]
        if low:
            p = low[0]
            errors.append(f"“{p.title}” has a minimum order of {_moq(p)}. Start the first tier at "
                          f"{_moq(p)} units or more.")

    # Above the last tier (the owner must choose) -------------------------
    beyond = post.get("beyond") or ""
    chosen = beyond in Rule.Beyond.values
    if not chosen:
        # Left unanswered on purpose: the form shows no option picked.
        errors.append("Choose what happens when someone wants more than your last tier.")
        beyond = ""
    v["beyond"] = beyond
    v["beyond_qty"] = None
    v["custom_message"] = ""
    v["custom_action"] = Rule.ContactAction.WHATSAPP
    if chosen and beyond != Rule.Beyond.SAME:
        try:
            n = int(str(post.get("beyond_qty") or "").strip())
        except (TypeError, ValueError):
            n = 0
        last = tiers[-1][0] if tiers else 1
        if n < 1:
            errors.append("Enter the most units one order can have.")
        elif n < last:
            errors.append(f"The limit must be at least your last tier ({last} units).")
        elif n > qp.MAX_TIER_QTY:
            errors.append(f"The limit can be at most {qp.MAX_TIER_QTY} units.")
        v["beyond_qty"] = n or None
    if beyond == Rule.Beyond.WHATSAPP and not qp.whatsapp_number(site):
        errors.append("Add your WhatsApp number in Edit content → Contact first, or pick another option.")
    if beyond == Rule.Beyond.CUSTOM:
        # A message only: with no block on the product page there is no
        # button to show (custom_action stays at its default, unused).
        msg = (post.get("custom_message") or "").strip()[:240]
        if not msg:
            errors.append("Write the message shoppers see above your limit.")
        v["custom_message"] = msg

    # Schedule --------------------------------------------------------------
    starts = _dt(post.get("starts_at"))
    ends = _dt(post.get("ends_at"))
    if starts == "bad" or ends == "bad":
        errors.append("Enter the start and end as a date and time.")
        starts = ends = None
    if starts and ends and ends <= starts:
        errors.append("The end must be after the start.")
    v["starts_at"], v["ends_at"] = starts, ends

    # Overlaps: one rule per product / category / the store -----------------
    if v["is_active"] and not errors:
        others = Rule.objects.for_site(site).filter(is_deleted=False, is_active=True, scope=scope)
        if instance is not None:
            others = others.exclude(pk=instance.pk)
        if scope == Rule.Scope.STORE and others.exists():
            errors.append(f"“{first_row(others).name}” already covers every product. Pause it first, or edit it.")
        elif scope == Rule.Scope.PRODUCTS:
            mine = set(v["product_ids"])
            for o in others:
                clash = mine & {str(x) for x in (o.product_ids or [])}
                if clash:
                    title = next((p.title for p in products if str(p.pk) in clash), "A product")
                    errors.append(f"“{title}” is already in “{o.name}”. A product can be in one quantity discount.")
                    break
        elif scope == Rule.Scope.CATEGORIES:
            mine = set(v["category_ids"])
            for o in others:
                clash = mine & {str(x) for x in (o.category_ids or [])}
                if clash:
                    name = next((c.name for c in categories if str(c.pk) in clash), "A category")
                    errors.append(f"“{name}” is already in “{o.name}”. A category can be in one quantity discount.")
                    break

    if not v["name"]:
        if products:
            v["name"] = f"{products[0].title}" + (f" + {len(products) - 1} more" if len(products) > 1 else "")
        elif categories:
            v["name"] = f"{categories[0].name}" + (f" + {len(categories) - 1} more" if len(categories) > 1 else "")
        else:
            v["name"] = "Every product"
        v["name"] = v["name"][:120]
    return v, list(dict.fromkeys(errors))


def _uuids(raw) -> list:
    out = []
    for x in raw:
        try:
            out.append(uuid.UUID(str(x)))
        except (TypeError, ValueError):
            continue
    return out


def _form_state(rule=None, values=None) -> dict:
    """What the form shows: posted values on an error, else the rule's."""
    if values is not None:
        src = dict(values)
        src["tiers"] = values.get("tiers") or []
    elif rule is not None:
        src = {
            "name": rule.name, "is_active": rule.is_active, "scope": rule.scope,
            "product_ids": [str(x) for x in (rule.product_ids or [])],
            "category_ids": [str(x) for x in (rule.category_ids or [])],
            "discount_type": rule.discount_type, "tiers": rule.tiers or [],
            "count_variations_together": rule.count_variations_together,
            "beyond": rule.beyond, "beyond_qty": rule.beyond_qty,
            "custom_message": rule.custom_message, "custom_action": rule.custom_action,
            "starts_at": rule.starts_at, "ends_at": rule.ends_at,
        }
    else:
        src = {
            "name": "", "is_active": True, "scope": Rule.Scope.PRODUCTS, "product_ids": [], "category_ids": [],
            "discount_type": Rule.DiscountType.PERCENT,
            "tiers": [{"min_qty": 2, "value": "5"}, {"min_qty": 5, "value": "10"}],
            "count_variations_together": True,
            # No pre-picked answer: the owner must decide what happens above the last tier.
            "beyond": "", "beyond_qty": None, "custom_message": "", "custom_action": Rule.ContactAction.WHATSAPP,
            "starts_at": None, "ends_at": None,
        }
    tiers = list(src.get("tiers") or [])
    rows = [{"min_qty": t.get("min_qty", ""), "value": _plain(t.get("value", ""))} for t in tiers]
    while len(rows) < qp.MAX_TIERS:
        rows.append({"min_qty": "", "value": ""})
    src["tier_rows"] = rows[: qp.MAX_TIERS]
    src["tier_count"] = max(1, len(tiers))
    for k in ("starts_at", "ends_at"):
        val = src.get(k)
        src[k + "_input"] = timezone.localtime(val).strftime("%Y-%m-%dT%H:%M") if val else ""
    return src


def _plain(value) -> str:
    d = _dec(value)
    if d is None:
        return str(value or "")
    txt = f"{d:f}"
    if "." in txt:
        txt = txt.rstrip("0").rstrip(".")
    return txt


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------
def manage(request, site, *, app=None, placement=None):
    settings_row = _settings(site)
    if request.method == "POST":
        if not ratelimit.consume("marketplace.settings", str(request.user.pk)).allowed:
            messages.error(request, "Too many saves in a short time. Please wait a minute.")
            return redirect(request.path)
        action = request.POST.get("action") or ""
        if action == "save_settings":
            settings_row.combine_with_coupons = request.POST.get("combine_with_coupons") in ("1", "on")
            settings_row.save(update_fields=["combine_with_coupons", "updated_at"])
            activity.record("store.quantity_discount.settings", request=request, site=site, target=site,
                            target_kind="Site", label="Quantity discount settings")
            messages.success(request, "Quantity discount settings saved.")
            return redirect(request.path)
        if action in ("delete_rule", "toggle_rule"):
            rule = _rule(site, request.POST.get("rule"))
            if rule is None:
                messages.error(request, "That quantity discount no longer exists.")
                return redirect(request.path)
            if action == "delete_rule":
                rule.soft_delete()
                activity.record("store.quantity_discount.deleted", request=request, site=site, target=rule,
                                target_kind="QuantityDiscountRule", label=rule.name)
                messages.success(request, f"“{rule.name}” deleted.")
            else:
                if not rule.is_active:
                    _values, errs = clean_rule(site, _post_of(rule, active=True), instance=rule)
                    if errs:
                        messages.error(request, errs[0])
                        return redirect(request.path)
                rule.is_active = not rule.is_active
                rule.save(update_fields=["is_active", "updated_at"])
                messages.success(request, f"“{rule.name}” {'is live' if rule.is_active else 'paused'}.")
            return redirect(request.path)
        if action == "save_rule":
            raw_id = request.POST.get("rule") or ""
            rule = _rule(site, raw_id) if raw_id and raw_id != "new" else None
            if raw_id and raw_id != "new" and rule is None:
                messages.error(request, "That quantity discount no longer exists.")
                return redirect(request.path)
            values, errors = clean_rule(site, request.POST, instance=rule)
            if errors:
                return _form(request, site, rule=rule, values=values, errors=errors, app=app,
                             placement=placement, settings_row=settings_row)
            created = rule is None
            if rule is None:
                rule = Rule(site=site)
            for k, val in values.items():
                setattr(rule, k, val)
            rule.save()
            activity.record(
                "store.quantity_discount." + ("created" if created else "updated"), request=request, site=site,
                target=rule, target_kind="QuantityDiscountRule", label=rule.name,
            )
            messages.success(request, f"“{rule.name}” saved." + (
                "" if settings_row.app_active else " It goes live once the app is active on your store."))
            return redirect(request.path)
        return redirect(request.path)

    which = request.GET.get("rule")
    if which:
        rule = None if which == "new" else _rule(site, which)
        if which != "new" and rule is None:
            messages.error(request, "That quantity discount no longer exists.")
            return redirect(request.path)
        return _form(request, site, rule=rule, app=app, placement=placement, settings_row=settings_row)

    rules = list(Rule.objects.for_site(site).filter(is_deleted=False))
    rules.sort(key=lambda r: (not r.is_active, r.name.lower()))
    single_ids = [str(r.product_ids[0]) for r in rules if r.scope == Rule.Scope.PRODUCTS and len(r.product_ids or []) == 1]
    names = {}
    if single_ids:
        names = {str(p.pk): p.title for p in Product.objects.for_site(site).filter(pk__in=_uuids(single_ids)).only("id", "title")}
    return _render(request, site, "store/quantity_discounts.html", {
        "rows": [describe(r, qp.symbol_for(site.currency), names) for r in rules],
        "settings_row": settings_row,
        "page_url": request.path,
    }, app=app, placement=placement)


def _post_of(rule, *, active: bool):
    """A rule as form data, to re-validate it (e.g. before un-pausing)."""
    from django.http import QueryDict

    q = QueryDict(mutable=True)
    q["name"] = rule.name
    q["is_active"] = "1" if active else ""
    q["scope"] = rule.scope
    q.setlist("product_ids", [str(x) for x in (rule.product_ids or [])])
    q.setlist("category_ids", [str(x) for x in (rule.category_ids or [])])
    q["discount_type"] = rule.discount_type
    q.setlist("tier_qty", [str(t.get("min_qty", "")) for t in (rule.tiers or [])])
    q.setlist("tier_value", [str(t.get("value", "")) for t in (rule.tiers or [])])
    q["count_variations_together"] = "1" if rule.count_variations_together else "0"
    q["beyond"] = rule.beyond
    q["beyond_qty"] = str(rule.beyond_qty or "")
    q["custom_message"] = rule.custom_message
    q["custom_action"] = rule.custom_action
    if rule.starts_at:
        q["starts_at"] = timezone.localtime(rule.starts_at).strftime("%Y-%m-%dT%H:%M")
    if rule.ends_at:
        q["ends_at"] = timezone.localtime(rule.ends_at).strftime("%Y-%m-%dT%H:%M")
    return q


def _form(request, site, *, rule=None, values=None, errors=None, app=None, placement=None, settings_row=None):
    products, categories = _catalogue(site)
    state = _form_state(rule, values)
    picked_p = set(state.get("product_ids") or [])
    picked_c = set(state.get("category_ids") or [])
    cat_names = {str(c.pk): c.name for c in categories}
    return _render(request, site, "store/quantity_discount_form.html", {
        "rule": rule,
        "form": state,
        "errors": errors or [],
        "products": [
            {"id": str(p.pk), "title": p.title, "price": p.price, "sku": p.sku or "", "moq": _moq(p),
             "picked": str(p.pk) in picked_p, "inactive": not p.is_active}
            for p in products
        ],
        "categories": [
            {"id": str(c.pk), "name": c.name, "parent": cat_names.get(str(c.parent_id or ""), ""),
             "picked": str(c.pk) in picked_c}
            for c in categories
        ],
        "scopes": Rule.Scope.choices,
        "types": Rule.DiscountType.choices,
        "beyond_choices": Rule.Beyond.choices,
        "contact_choices": Rule.ContactAction.choices,
        "has_whatsapp": bool(qp.whatsapp_number(site)),
        "has_phone": bool(qp.phone_number(site)),
        "settings_row": settings_row,
        "max_tiers": qp.MAX_TIERS,
        "max_qty": qp.MAX_TIER_QTY,
        "page_url": request.path,
        "is_b2b": bool(getattr(site, "is_b2b", False)),
    }, app=app, placement=placement)
