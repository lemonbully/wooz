"""Storefront shopping cart and discount logic.

The cart lives in the visitor's session (no DB row until an order is placed):
a simple ``{product_id: quantity}`` map. :class:`Cart` resolves those ids to
live products, snapshots prices, computes the subtotal, and applies a validated
:class:`~commerce.models.Coupon`. Everything is tenant-scoped through
``.for_site`` so one store's cart can never resolve another's products.

The money math is done in :class:`~decimal.Decimal` and rounded to paise at the
edges, so totals match what the payment gateway is later asked to charge.
"""

from __future__ import annotations

import hashlib
from decimal import ROUND_HALF_UP, Decimal

from commerce.models import Product

CART_SESSION_KEY = "ow_cart"
COUPON_SESSION_KEY = "ow_coupon"
MAX_QTY_PER_LINE = 99

_TWO = Decimal("0.01")


def _money(value) -> Decimal:
    return (Decimal(value or 0)).quantize(_TWO, rounding=ROUND_HALF_UP)


def variant_key_of(combo) -> str:
    """The canonical key for a variation combination.

    Prefers the stored ``key`` (what the dashboard writes), but falls back to
    deriving it from the attributes exactly as the dashboard and
    ``_parse_variant_combinations`` do (``Name-Value__Name2-Value2``). This keeps
    the storefront, the cart and the add-to-cart guard in agreement even for a
    combination saved without an explicit key.
    """
    if not isinstance(combo, dict):
        return ""
    k = str(combo.get("key") or "").strip()
    if k:
        return k
    return "__".join(f"{a['name']}-{a['value']}" for a in attrs_of(combo))


def _norm(value) -> str:
    """Case- and whitespace-insensitive form used to compare variation names,
    values and keys (``"Navy  Blue "`` == ``"navy blue"``)."""
    return " ".join(str(value if value is not None else "").split()).casefold()


def combos_of(product) -> list:
    """The product's variation combinations as a list of dicts, whatever shape
    they were stored in (list, or a dict keyed by combination key)."""
    raw = getattr(product, "variant_combinations", None) or []
    if isinstance(raw, dict):
        raw = list(raw.values())
    if not isinstance(raw, list):
        return []
    return [c for c in raw if isinstance(c, dict)]


def attrs_of(combo) -> list:
    """A combination's attributes as ``[{"name", "value"}]``. Tolerates the
    object form ``{"Size": "M"}`` as well as the usual list form."""
    raw = combo.get("attributes") if isinstance(combo, dict) else None
    if isinstance(raw, dict):
        raw = [{"name": k, "value": v} for k, v in raw.items()]
    out = []
    for a in raw or []:
        if isinstance(a, dict):
            out.append({"name": str(a.get("name") or "").strip(), "value": str(a.get("value") or "").strip()})
    return out


def clean_attrs(raw) -> list:
    """Sanitise a shopper-supplied selection into ``[{"name", "value"}]``."""
    if isinstance(raw, dict):
        raw = [{"name": k, "value": v} for k, v in raw.items()]
    out, seen = [], set()
    for a in raw if isinstance(raw, list) else []:
        if not isinstance(a, dict):
            continue
        name = str(a.get("name") or "").strip()[:100]
        value = str(a.get("value") or "").strip()[:200]
        if not name or not value or _norm(name) in seen:
            continue
        seen.add(_norm(name))
        out.append({"name": name, "value": value})
    return out[:10]


def _attr_map(attrs) -> dict:
    return {_norm(a["name"]): _norm(a["value"]) for a in attrs if a.get("name")}


def _combo_for(product, variant_key, attrs=None):
    """The variation combination on ``product`` matching ``variant_key`` (exact,
    then case/whitespace-insensitive), or else matching the selected ``attrs``.
    None when nothing matches."""
    combos = combos_of(product)
    vk = (variant_key or "").strip()
    if vk:
        for c in combos:
            if variant_key_of(c) == vk:
                return c
        nvk = _norm(vk)
        for c in combos:
            if _norm(variant_key_of(c)) == nvk:
                return c
    want = _attr_map(clean_attrs(attrs)) if attrs else {}
    if want:
        for c in combos:
            if _attr_map(attrs_of(c)) == want:
                return c
    return None


def resolve_variant(product, variant_key="", attrs=None):
    """Resolve a shopper's pick to ``(key, attrs)`` for the cart line.

    ``key`` is the canonical key of the stored combination when one matches.
    When the product's variation types offer the picked values but no stored
    combination row exists for them (the owner edited the variations and the
    combinations drifted, or the product has types but no combination rows),
    the pick is still valid: ``key`` is derived from the product's own names and
    values and ``attrs`` carries the selection so the line can label itself.
    Returns ``("", [])`` when the pick is not a valid, complete variation.
    """
    combo = _combo_for(product, variant_key, attrs)
    if combo is not None:
        return variant_key_of(combo), []

    picked = _attr_map(clean_attrs(attrs)) if attrs else {}
    if not picked:
        return "", []
    types = []
    for vt in getattr(product, "variants", None) or []:
        if not isinstance(vt, dict):
            continue
        name = str(vt.get("name") or "").strip()
        values = []
        for v in vt.get("values") or []:
            s = str((v.get("value") or v.get("name") or "") if isinstance(v, dict) else v).strip()
            if s:
                values.append(s)
        if name and values:
            types.append((name, values))
    if not types or len(picked) != len(types):
        return "", []
    resolved = []
    for name, values in types:
        want = picked.get(_norm(name))
        match = next((v for v in values if _norm(v) == want), None) if want else None
        if match is None:
            return "", []
        resolved.append({"name": name, "value": match})
    return "__".join(f"{a['name']}-{a['value']}" for a in resolved), resolved


def _variant_price(product, combo):
    """The selling price for a line: the combination's own price when it has one,
    otherwise the product's base price. A blank combo price means "same as base"."""
    if combo is not None:
        p = combo.get("price")
        if p not in (None, ""):
            return p
    return product.price


def _variant_mrp(product, combo):
    if combo is not None:
        m = combo.get("mrp")
        if m not in (None, ""):
            return m
    return product.mrp


def _variant_label(combo) -> str:
    """A human label for the chosen combination, e.g. ``Size: M, Colour: Red``."""
    if not combo:
        return ""
    parts = []
    for a in attrs_of(combo):
        if a["value"]:
            parts.append(f"{a['name']}: {a['value']}" if a["name"] else a["value"])
    return ", ".join(parts)


def _variant_sku(product, combo) -> str:
    """The SKU for a line: the combination's own SKU when it has one, otherwise
    the product's."""
    if combo is not None:
        s = str(combo.get("sku") or "").strip()
        if s:
            return s
    return str(getattr(product, "sku", "") or "").strip()


def line_sku(item) -> str:
    """The SKU an order line was sold under.

    A variation line resolves to that combination's own SKU; a simple product
    to the product SKU. Orders placed before checkout snapshotted the variation
    SKU stored the product's SKU on every line, so when the snapshot equals the
    product SKU the combination is looked up again from the line's variant
    label.
    """
    product = getattr(item, "product", None)
    snapshot = str(getattr(item, "sku", "") or "").strip()
    product_sku = str(getattr(product, "sku", "") or "").strip() if product is not None else ""
    label = (getattr(item, "variant_label", "") or "").strip()
    if product is not None and label:
        if snapshot and snapshot != product_sku:
            return snapshot
        want = _norm(label)
        for combo in combos_of(product):
            if _norm(_variant_label(combo)) == want:
                combo_sku = str(combo.get("sku") or "").strip()
                if combo_sku:
                    return combo_sku
                break
    return snapshot or product_sku


class Cart:
    """A session-backed cart for one store."""

    def __init__(self, request, site):
        self.request = request
        self.site = site
        self.session = request.session
        self.data = self._normalize(self.session.get(CART_SESSION_KEY))

    # -- line identity -----------------------------------------------------
    @staticmethod
    def _line_key(product_id, variant_key: str = "") -> str:
        """A stable, URL-safe id for one cart line.

        Without a variation it is just the product id, so legacy carts and
        non-variant products are unchanged. With a variation it is the product id
        plus a short hash of the ``variant_key`` (never the raw key, which can
        carry spaces or slashes and would break the cart-mutation URLs). Two adds
        of the same product+variation therefore collapse onto the same line.
        """
        pid = str(product_id)
        vk = (variant_key or "").strip()
        if not vk:
            return pid
        digest = hashlib.sha1(vk.encode("utf-8")).hexdigest()[:12]
        return f"{pid}-v{digest}"

    def _normalize(self, data) -> dict:
        """Coerce any stored shape into ``{line_key: {"pid","vk","qty"}}``.

        Legacy carts stored ``{product_id: qty}`` (an int, no variations); those
        migrate to a variation-less line keyed by the product id, so a cart saved
        before this change keeps working.
        """
        out: dict = {}
        if not isinstance(data, dict):
            return out
        for key, val in data.items():
            attrs = []
            if isinstance(val, dict):
                pid = str(val.get("pid") or key)
                vk = str(val.get("vk") or val.get("variant_key") or "").strip()
                raw_qty = val.get("qty", val.get("quantity", 0))
                attrs = clean_attrs(val.get("attrs"))
            else:
                pid, vk, raw_qty = str(key), "", val
            try:
                qty = int(raw_qty)
            except (TypeError, ValueError):
                qty = 0
            if qty <= 0:
                continue
            lk = self._line_key(pid, vk)
            if lk in out:
                out[lk]["qty"] = min(MAX_QTY_PER_LINE, out[lk]["qty"] + qty)
            else:
                out[lk] = {"pid": pid, "vk": vk, "qty": min(MAX_QTY_PER_LINE, qty)}
                if vk and attrs:
                    out[lk]["attrs"] = attrs
        return out

    # -- mutation ----------------------------------------------------------
    def _save(self):
        self.session[CART_SESSION_KEY] = self.data
        self.session.modified = True

    def add(self, product_id, qty: int = 1, variant_key: str = "", attributes=None):
        """Add ``qty`` of a product (optionally a specific variation).

        ``attributes`` is only needed for a variation that has no stored
        combination row (see :func:`resolve_variant`); it lets the line label
        itself without one.
        """
        pid = str(product_id)
        vk = (variant_key or "").strip()
        lk = self._line_key(pid, vk)
        line = self.data.get(lk)
        current = int(line["qty"]) if isinstance(line, dict) else 0
        self.data[lk] = {
            "pid": pid,
            "vk": vk,
            "qty": max(1, min(MAX_QTY_PER_LINE, current + max(1, qty))),
        }
        attrs = clean_attrs(attributes) if vk else []
        if attrs:
            self.data[lk]["attrs"] = attrs
        self._save()

    def qty_of(self, product_id, variant_key: str = "") -> int:
        """Units of this product (+ variation) already in the cart."""
        line = self.data.get(self._line_key(product_id, variant_key))
        return int(line.get("qty") or 0) if isinstance(line, dict) else 0

    def set_qty(self, line_key, qty: int):
        lk = str(line_key)
        line = self.data.get(lk)
        if not isinstance(line, dict):
            return
        qty = int(qty)
        if qty <= 0:
            self.data.pop(lk, None)
        else:
            qty = self._stock_cap(line, qty)
            line["qty"] = min(MAX_QTY_PER_LINE, qty)
            self.data[lk] = line
        self._save()

    def _stock_cap(self, line: dict, qty: int) -> int:
        """Cap a requested quantity at the stock left. A line whose item sold
        out may still be lowered (never raised), so it stays visible as out of
        stock until the shopper removes it."""
        from commerce import stock

        product = (
            Product.objects.for_site(self.site)
            .filter(pk=line.get("pid"), is_active=True, is_deleted=False)
            .first()
        )
        if product is None:
            return qty
        left = stock.available_for(product, str(line.get("vk") or ""), clean_attrs(line.get("attrs")) or None)
        if left > 0:
            return max(1, min(qty, left))
        return min(qty, int(line.get("qty") or qty))

    def remove(self, line_key):
        self.data.pop(str(line_key), None)
        self._save()

    def clear(self):
        self.data = {}
        self.session.pop(COUPON_SESSION_KEY, None)
        self._save()

    # -- reading -----------------------------------------------------------
    def _products(self):
        ids = {
            str(line.get("pid"))
            for line in self.data.values()
            if isinstance(line, dict) and line.get("pid")
        }
        if not ids:
            return {}
        qs = Product.objects.for_site(self.site).filter(
            pk__in=list(ids), is_active=True, is_deleted=False
        )
        return {str(p.id): p for p in qs}

    def items(self) -> list[dict]:
        """Resolved line items. Silently drops lines that no longer resolve (a
        product deactivated or removed since it was added). Each line prices and
        labels itself from its chosen variation combination when it has one."""
        products = self._products()
        pruned = False
        out = []
        for lk, line in list(self.data.items()):
            product = products.get(str(line.get("pid"))) if isinstance(line, dict) else None
            if product is None:
                self.data.pop(lk, None)
                pruned = True
                continue
            qty = int(line.get("qty") or 0)
            vk = str(line.get("vk") or "")
            attrs = clean_attrs(line.get("attrs"))
            combo = _combo_for(product, vk, attrs) if vk else None
            # Pricing only ever comes from a stored combination; a variation
            # without its own row is sold at the product's base price, labelled
            # from the selection it was added with.
            label_combo = combo if combo is not None else ({"attributes": attrs} if attrs else None)
            unit = _money(_variant_price(product, combo))
            mrp_val = _variant_mrp(product, combo)
            from commerce import stock

            available = stock.available_for(product, vk, attrs or None)
            images = list(product.images.all()[:1])
            from commerce.variant_profiles import combo_view
            out.append(
                {
                    "id": lk,
                    "product": product,
                    "title": product.title,
                    "slug": product.slug,
                    "image": images[0].image if images else None,
                    "quantity": qty,
                    "unit_price": unit,
                    "mrp": _money(mrp_val) if mrp_val else None,
                    "line_total": _money(unit * qty),
                    # Stock of this exact product/variation: the cart and
                    # checkout flag a sold-out or short line and checkout
                    # refuses to place it.
                    "available": available,
                    "in_stock": available > 0,
                    "short": qty > available,
                    # Chosen variation  the key (for re-adds), the human label
                    # (for the cart/checkout UI and the WhatsApp message) and the
                    # raw attributes.
                    "variant_key": vk,
                    "variant_label": _variant_label(label_combo),
                    "variant_attributes": attrs_of(label_combo) if label_combo else [],
                    "sku": _variant_sku(product, combo),
                    # The product as sold in this variation: its own GST % /
                    # "GST included" / HSN when the owner set them for it.
                    "tax_product": combo_view(product, combo),
                }
            )
        if pruned:
            self._save()
        return out

    def count(self) -> int:
        return sum(
            int(line.get("qty") or 0)
            for line in self.data.values()
            if isinstance(line, dict)
        )

    def is_empty(self) -> bool:
        return not self.data

    def subtotal(self) -> Decimal:
        return _money(sum((i["line_total"] for i in self.items()), Decimal("0")))

    def tax(self, items=None) -> Decimal:
        """GST added on top of the price, summed over the lines whose product is
        marked *tax-exclusive* (``gst_inclusive`` is False). A tax-inclusive
        product contributes nothing here  its GST is already in the price. Each
        contribution is ``line_total × gst_percentage / 100``.
        """
        total = Decimal("0")
        for i in (self.items() if items is None else items):
            product = i.get("tax_product") or i.get("product")
            if product is None or getattr(product, "gst_inclusive", True):
                continue
            rate = getattr(product, "gst_percentage", None)
            if not rate:
                continue
            total += _money(i["line_total"]) * (_money(rate) / Decimal("100"))
        return _money(total)

    # -- coupon ------------------------------------------------------------
    @property
    def coupon_code(self) -> str:
        return self.session.get(COUPON_SESSION_KEY) or ""

    def _currency(self) -> str:
        return getattr(self.site, "currency", "₹") or "₹"

    def _discounts(self):
        """The full discount picture for the current cart (code + automatics).

        Recomputed from live products each read, so a code that expires, stops
        meeting its minimum, or ceases to apply falls away on its own.
        """
        from commerce import discounts as engine

        return engine.compute(
            self.site,
            lines=self.items(),
            subtotal=self.subtotal(),
            code=self.coupon_code,
            customer=None,  # guest cart; eligibility is re-checked at checkout
            currency=self._currency(),
            strict=False,
        )

    def apply_coupon(self, code: str):
        """Validate and remember a coupon code. Returns ``(ok, message)``."""
        from commerce import discounts as engine

        code = (code or "").strip().upper()
        if not code:
            self.clear_coupon()
            return True, "Coupon removed."
        result = engine.compute(
            self.site, lines=self.items(), subtotal=self.subtotal(),
            code=code, customer=None, currency=self._currency(), strict=False,
        )
        if result.code_error or result.code_coupon is None:
            return False, result.code_error or "That code isn’t valid."
        self.session[COUPON_SESSION_KEY] = result.code_coupon.code
        self.session.modified = True
        return True, f"“{result.code_coupon.code}” applied."

    def clear_coupon(self):
        self.session.pop(COUPON_SESSION_KEY, None)
        self.session.modified = True

    def coupon(self):
        """The primary coupon recorded against an order (or None).

        This is the shopper's coded discount when one is applied; otherwise the
        best automatic discount in effect. Automatic discounts still contribute
        to :meth:`discount` even when this returns the coded one.
        """
        result = self._discounts()
        if result.code_coupon is not None:
            # A code was entered but no longer applies  drop it so it doesn't
            # linger in the session.
            if not any(r.coupon and r.coupon.pk == result.code_coupon.pk for r in result.applied):
                self.clear_coupon()
            else:
                return result.code_coupon
        applied = result.applied
        return applied[0].coupon if applied else None

    def discount(self) -> Decimal:
        return _money(self._discounts().amount)

    def free_shipping(self) -> bool:
        return self._discounts().free_shipping

    def total(self) -> Decimal:
        items = self.items()
        sub = _money(sum((i["line_total"] for i in items), Decimal("0")))
        return _money(max(Decimal("0.00"), sub - self.discount()) + self.tax(items))

    def summary(self) -> dict:
        items = self.items()
        sub = _money(sum((i["line_total"] for i in items), Decimal("0")))
        result = self._discounts()
        disc = _money(result.amount)
        tax = self.tax(items)
        return {
            "count": self.count(),
            "subtotal": sub,
            "discount": disc,
            "tax": tax,
            "coupon_code": self.coupon_code,
            "free_shipping": result.free_shipping,
            "discount_labels": [r.coupon.display_name for r in result.applied],
            "total": _money(max(Decimal("0.00"), sub - disc) + tax),
        }
