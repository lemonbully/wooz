"""Product selling-price history (the storefront's "Price history" drawer).

Recording: :func:`record` runs after every Product save (``post_save``, wired in
``CommerceConfig.ready``). For the product's base price and for each variation
combination it compares the *effective* selling price with the last recorded
one and appends a :class:`~commerce.models.ProductPriceChange` only when it
differs, so the table holds exactly one row per real change.

Reading: :func:`series` turns the rows into the last ``days`` (30) as a stepped
trend: the price in force when the window opened, then one point per change,
then "now". Each price holds flat until the next change, which is how the
drawer's chart draws it.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

WINDOW_DAYS = 30


def _dec(value):
    if value in (None, ""):
        return None
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return d.quantize(Decimal("0.01"))


def _combos(product):
    raw = product.variant_combinations or []
    if isinstance(raw, dict):
        raw = list(raw.values())
    return [c for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []


def combo_key(combo) -> str:
    """A combination's key: its stored ``key``, else derived from attributes
    exactly as the dashboard / storefront derive it."""
    key = str(combo.get("key") or "").strip()
    if key:
        return key
    attrs = combo.get("attributes") or []
    if isinstance(attrs, dict):
        attrs = [{"name": k, "value": v} for k, v in attrs.items()]
    return "__".join(
        f"{a.get('name', '')}-{a.get('value', '')}" for a in attrs if isinstance(a, dict)
    )


def effective_prices(product) -> dict:
    """``{variant_key: (price, mrp)}``  ``""`` is the base price; each
    combination falls back to the base price/MRP when it leaves them blank
    (the same rule the cart and the product page apply)."""
    base_price = _dec(product.price) or Decimal("0.00")
    base_mrp = _dec(product.mrp)
    out = {"": (base_price, base_mrp)}
    for c in _combos(product):
        key = combo_key(c)
        if not key or len(key) > 255:
            continue
        out[key] = (_dec(c.get("price")) or base_price, _dec(c.get("mrp")) or base_mrp)
    return out


def record(product, *, at=None, previous=None) -> int:
    """Append a history row for every price that changed since the last one.

    ``previous`` is ``{variant_key: (price, mrp)}`` as it was *before* this save
    (see :func:`on_product_pre_save`). It seeds a price track that has no rows
    yet  e.g. a product that existed before price history did: the old price
    is recorded as in force since the product was created, then the new one
    from now if it differs. Returns how many rows were written."""
    from .models import ProductPriceChange

    site = product.site
    now = at or timezone.now()
    current = effective_prices(product)
    previous = previous or {}
    since = getattr(product, "created_at", None) or now

    latest = {}
    rows = (
        ProductPriceChange.objects.for_site(site)
        .filter(product=product, variant_key__in=list(current.keys()))
        .order_by("variant_key", "-changed_at", "-created_at")
    )
    for row in rows:
        latest.setdefault(row.variant_key, row)

    new = []
    for key, (price, mrp) in current.items():
        last = latest.get(key)
        if last is None and key in previous and since < now:
            # First time this track is recorded: the pre-save price has held
            # since the product was created.
            old_price, old_mrp = previous[key]
            new.append(ProductPriceChange(
                site=site, product=product, variant_key=key,
                price=old_price, mrp=old_mrp, changed_at=since,
            ))
            if old_price == price:
                continue
        if last is not None and last.price == price:
            if last.mrp != mrp:
                # MRP-only edit: keep the step but show the new strike-through.
                last.mrp = mrp
                last.save(update_fields=["mrp", "updated_at"])
            continue
        new.append(ProductPriceChange(
            site=site, product=product, variant_key=key, price=price, mrp=mrp, changed_at=now,
        ))
    if new:
        ProductPriceChange.objects.bulk_create(new)
    return len(new)


def on_product_pre_save(sender, instance, **kwargs):
    """``pre_save`` hook: remember the prices as stored before this save, so
    :func:`record` can seed a track that has no history yet."""
    instance._price_history_previous = None
    if kwargs.get("raw") or not instance.pk:
        return
    try:
        old = (
            type(instance).all_objects.filter(pk=instance.pk)
            .only("price", "mrp", "variant_combinations")
            .first()
        )
        if old is not None:
            instance._price_history_previous = effective_prices(old)
    except Exception:
        instance._price_history_previous = None


def on_product_saved(sender, instance, **kwargs):
    """``post_save`` hook: record after the surrounding transaction commits, so a
    rolled-back save never leaves a phantom price change."""
    if kwargs.get("raw"):
        return  # fixture loading
    if getattr(instance, "is_deleted", False):
        return
    previous = getattr(instance, "_price_history_previous", None)
    # A brand-new product has no "before"; its first price starts now.
    if kwargs.get("created"):
        previous = None

    def _run():
        try:
            record(instance, previous=previous)
        except Exception:
            # History is a nicety: never let it break saving a product.
            pass

    transaction.on_commit(_run)


def _num(d):
    return float(d) if d is not None else None


def series(product, variant_key: str = "", *, days: int = WINDOW_DAYS) -> dict:
    """The last ``days`` of selling price for ``variant_key`` as a stepped trend.

    ``points`` are ``{at, price, mrp, kind}`` in time order: ``kind`` is
    ``"start"`` (price in force when the window opens), ``"change"`` (a change
    inside the window) or ``"now"`` (today, at the current price).
    """
    from .models import ProductPriceChange

    now = timezone.now()
    window_start = now - timedelta(days=days)
    current = effective_prices(product)
    key = variant_key if variant_key in current else ""
    cur_price, cur_mrp = current[key]

    rows = list(
        ProductPriceChange.objects.for_site(product.site)
        .filter(product=product, variant_key=key)
        .order_by("changed_at", "created_at")
    )

    before = [r for r in rows if r.changed_at <= window_start]
    inside = [r for r in rows if r.changed_at > window_start]

    points = []
    if before:
        opening = before[-1]
        points.append({"at": window_start, "price": opening.price, "mrp": opening.mrp, "kind": "start"})
    elif inside:
        # History begins inside the window (new product, or the feature is new):
        # the trend starts at the first recorded price.
        first = inside.pop(0)
        points.append({"at": first.changed_at, "price": first.price, "mrp": first.mrp, "kind": "start"})
    else:
        # Nothing recorded yet: the current price, since the product existed
        # (or the window opened, whichever is later).
        created = getattr(product, "created_at", None) or window_start
        points.append({"at": max(created, window_start), "price": cur_price, "mrp": cur_mrp, "kind": "start"})

    for r in inside:
        if points and points[-1]["price"] == r.price:
            continue  # no visible step
        points.append({"at": r.changed_at, "price": r.price, "mrp": r.mrp, "kind": "change"})

    # Today, at the live price (also corrects any change the hook missed).
    if points[-1]["price"] != cur_price:
        points.append({"at": now, "price": cur_price, "mrp": cur_mrp, "kind": "change"})
    points.append({"at": now, "price": cur_price, "mrp": cur_mrp, "kind": "now"})

    prices = [p["price"] for p in points]
    lowest = min(prices)
    highest = max(prices)
    lowest_at = next(p["at"] for p in points if p["price"] == lowest)

    return {
        "variant_key": key,
        "days": days,
        "window_start": window_start.isoformat(),
        "now": now.isoformat(),
        "currency": getattr(product, "currency", "INR") or "INR",
        "current": _num(cur_price),
        "current_mrp": _num(cur_mrp),
        "lowest": _num(lowest),
        "lowest_at": lowest_at.isoformat(),
        "highest": _num(highest),
        "changes": sum(1 for p in points if p["kind"] == "change"),
        "points": [
            {"at": p["at"].isoformat(), "price": _num(p["price"]), "mrp": _num(p["mrp"]), "kind": p["kind"]}
            for p in points
        ],
    }
