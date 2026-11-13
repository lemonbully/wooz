"""Per-variation product details ("variation profiles").

A product with variations stores each combination as a dict inside
``Product.variant_combinations`` (key, attributes, sku, mrp, price, inventory,
images, swatch, default_selected). A combination may also carry a ``profile``:
everything the owner changed for *that* variation in the product form's "Edit
this variation" mode, so one combination is edited and shown like a product of
its own. Every field of the product form can be set per variation except the
variation types themselves (and "Has variations?").

Only the fields that differ from the product are stored. Anything the profile
leaves out is inherited from the product, so a later edit to the product still
reaches every variation that hasn't changed that field. A key that *is*
present wins, even when blank or empty (e.g. a variation with no warning).

Where each field takes effect:

* product page (store + myugcstore): title, description, bullets, features,
  item details (material, colour, dimensions, weight, HSN, GST, return
  policy, dangerous goods …), origin / manufacturer / packer / importer,
  extras, FAQs, trust badges, breadcrumb category, video, price, stock,
  images, and "Active" (an inactive variation can't be bought);
* cart, checkout, invoices, order edits and exports: the line's GST % and
  "GST included" (:func:`combo_view`, :func:`line_view`);
* shipping: the line's dimensions and weight;
* stored for the variation (no storefront surface exists for them per
  variation): brand, keywords, currency, extra categories, best seller, low
  stock threshold (used for the "Only N left" line on myugcstore).

SKU, MRP, selling price, stock and images live on the combination row itself,
outside the profile. No migration: everything rides inside the existing JSON
column.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

#: Text fields of the Product model a variation may override, with the
#: longest value kept.
MODEL_TEXT = {
    "title": 255,
    "description": 20000,
    "brand": 120,
    "hsn_code": 32,
    "country_of_origin": 100,
    "guarantee_warranty": 1000,
    "manufacturer_contact": 2000,
    "packer_contact": 2000,
    "importer_contact": 2000,
}
#: Never blank on a product; a blank override is dropped (inherit instead).
REQUIRED_TEXT = frozenset({"title", "description", "brand", "hsn_code"})

#: Dimension / weight numbers (stored as two-decimal strings, "" = blank).
MODEL_DECIMAL = (
    "product_length", "product_breadth", "product_height",
    "package_length", "package_breadth", "package_height",
    "weight_value", "package_weight_value",
)
_LENGTH_UNITS = frozenset({"mm", "cm", "in", "ft"})
_WEIGHT_UNITS = frozenset({"g", "kg"})
MODEL_UNIT = {
    "product_unit": _LENGTH_UNITS,
    "package_unit": _LENGTH_UNITS,
    "weight_unit": _WEIGHT_UNITS,
    "package_weight_unit": _WEIGHT_UNITS,
}
#: Choice fields: allowed values ("" = none, where the product allows none).
MODEL_CHOICE = {
    "currency": frozenset({"INR", "USD", "EUR"}),
    "return_type": frozenset({"", "returnable", "non_returnable", "replacement"}),
    "dangerous_goods": frozenset({"", "ghs", "storage", "transportation", "waste", "other", "not_applicable", "unknown"}),
    "video_orientation": frozenset({"landscape", "portrait"}),
}
MODEL_BOOL = ("is_active", "is_best_seller", "gst_inclusive")
#: Whole numbers with their upper bound; "" / None = blank.
MODEL_INT = {"low_stock_threshold": 1000000, "return_days": 365, "replacement_days": 365}
MAX_RETURN_DAYS = 365

#: ``d2c_details`` keys a variation may override.
DETAIL_TEXT = {
    "recommended_use": 500,
    "material": 500,
    "colour": 255,
    "handling_time": 255,
    "warning": 2000,
    "support_phone": 40,
    "support_email": 254,
}
DETAIL_LISTS = ("bullets", "features", "accessories", "includes")

#: Pair lists: (stored name, first key, second key, second key required).
PAIR_LISTS = (
    ("faqs", "question", "answer", True),
    ("specifications", "name", "value", False),
    ("extras", "name", "value", False),
)

_LIST_MAX_ITEMS = 60
_LIST_ITEM_MAX = 1000
_PAIR_VALUE_MAX = 5000
_ID_RE = re.compile(r"^[0-9a-fA-F-]{1,64}$")


def _text(value, limit: int) -> str:
    return str(value if value is not None else "").strip()[:limit]


def _decimal_str(value, *, maximum=Decimal("100000000")) -> str:
    """A non-negative two-decimal string, or "" for blank / invalid input."""
    raw = str(value if value is not None else "").strip()
    if not raw:
        return ""
    try:
        d = Decimal(raw)
    except (InvalidOperation, ValueError):
        return ""
    if not d.is_finite() or d < 0 or d >= maximum:
        return ""
    return str(d.quantize(Decimal("0.01")))


def _int_or_none(value, maximum: int):
    raw = str(value if value is not None else "").strip()
    if raw == "":
        return None
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        return None
    return n if 0 <= n <= maximum else None


def _bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _str_list(value) -> list:
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for item in value:
        s = _text(item, _LIST_ITEM_MAX)
        if s:
            out.append(s)
        if len(out) >= _LIST_MAX_ITEMS:
            break
    return out


def _pair_list(value, a: str, b: str, b_required: bool) -> list:
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for item in value:
        if not isinstance(item, dict):
            continue
        first = _text(item.get(a), _LIST_ITEM_MAX)
        second = _text(item.get(b), _PAIR_VALUE_MAX)
        if not first or (b_required and not second):
            continue
        out.append({a: first, b: second})
        if len(out) >= _LIST_MAX_ITEMS:
            break
    return out


def _video_url(value) -> str:
    raw = _text(value, 500)
    if not raw:
        return ""
    if raw.lower().startswith(("http://", "https://", "//", "/")) and not raw.lower().startswith("/javascript"):
        return raw
    if re.match(r"^[a-z0-9.-]+\.[a-z]{2,}(/|$)", raw, re.I):  # "youtube.com/..." without a scheme
        return raw
    return ""


def clean_profile(raw) -> dict:
    """Whitelist and normalise a profile posted by the product form.

    Unknown keys are dropped; every kept value has the exact shape the
    storefront reads. Store-dependent checks (categories, badges against the
    product) happen in :func:`finalise_profiles`. Returns ``{}`` when nothing
    valid remains.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for name, limit in MODEL_TEXT.items():
        if name in raw:
            value = _text(raw.get(name), limit)
            if value or name not in REQUIRED_TEXT:
                out[name] = value
    for name in MODEL_DECIMAL:
        if name in raw:
            out[name] = _decimal_str(raw.get(name))
    for name, allowed in MODEL_UNIT.items():
        if name in raw:
            value = _text(raw.get(name), 4).lower()
            out[name] = value if value in allowed else ""
    for name, allowed in MODEL_CHOICE.items():
        if name in raw:
            value = _text(raw.get(name), 20)
            if name == "currency":
                value = value.upper()
            if value in allowed:
                out[name] = value
    for name in MODEL_BOOL:
        if name in raw:
            out[name] = _bool(raw.get(name))
    for name, maximum in MODEL_INT.items():
        if name in raw:
            out[name] = _int_or_none(raw.get(name), maximum)
    if "low_stock_threshold" in out and out["low_stock_threshold"] is None:
        out.pop("low_stock_threshold")  # never blank on a product
    if "gst_percentage" in raw:
        gst = _decimal_str(raw.get("gst_percentage"), maximum=Decimal("100.01"))
        if gst:
            out["gst_percentage"] = gst
    if "video_link" in raw:
        out["video_link"] = _video_url(raw.get("video_link"))
    for name, limit in DETAIL_TEXT.items():
        if name in raw:
            out[name] = _text(raw.get(name), limit)
    for name in DETAIL_LISTS + ("keywords",):
        if name in raw:
            out[name] = _str_list(raw.get(name))
    for name, a, b, b_required in PAIR_LISTS:
        if name in raw:
            out[name] = _pair_list(raw.get(name), a, b, b_required)
    if "trusted_icons" in raw:
        from commerce import trust_badges

        names = raw.get("trusted_icons")
        out["trusted_icons"] = trust_badges.clean_selection(names if isinstance(names, (list, tuple)) else [])
    if "category" in raw:
        cid = _text(raw.get("category"), 64)
        out["category"] = cid if (not cid or _ID_RE.match(cid)) else ""
    if "categories" in raw:
        ids = raw.get("categories") if isinstance(raw.get("categories"), (list, tuple)) else []
        out["categories"] = [str(c).strip() for c in ids if _ID_RE.match(str(c).strip())][:50]
    return out


def _effective(product, profile: dict, name: str):
    return profile[name] if name in profile else getattr(product, name, None)


def finalise_profiles(product, site) -> None:
    """Store-aware checks on the product's combination profiles, in place,
    after the product's own fields are final (called by the product form view):

    * categories must belong to this store (a stale id is dropped);
    * a variation's trust badges lock its return policy and warranty the same
      way the product's badges lock the product's (``ProductForm.clean``);
    * the return window follows the return policy (0 for non-returnable, none
      without a policy, replacement days only for non-returnable).
    """
    from commerce import trust_badges
    from commerce.cart import combos_of
    from commerce.models import Category

    combos = combos_of(product)
    if not combos:
        return
    valid_ids = None
    for combo in combos:
        prof = profile_of(combo)
        if not prof:
            continue
        if "category" in prof or "categories" in prof:
            if valid_ids is None:
                valid_ids = {
                    str(i) for i in Category.objects.for_site(site).filter(is_deleted=False).values_list("id", flat=True)
                }
            if prof.get("category") and prof["category"] not in valid_ids:
                prof.pop("category")
            if "categories" in prof:
                main_cat = str(prof["category"] if "category" in prof else (product.category_id or ""))
                prof["categories"] = [c for c in prof["categories"] if c in valid_ids and c != main_cat]

        badges = prof["trusted_icons"] if "trusted_icons" in prof else (product.trusted_icons or [])
        warranty = next((b for b in badges if trust_badges.is_warranty(b)), None)
        if warranty:
            if "trusted_icons" in prof:
                prof["guarantee_warranty"] = trust_badges.display_name(warranty)
            else:
                prof.pop("guarantee_warranty", None)  # the product's badge is its warranty

        if any(trust_badges.is_non_returnable(b) for b in badges):
            if "trusted_icons" in prof or "return_type" in prof:
                prof["return_type"] = "non_returnable"
        elif trust_badges.return_policy_from(badges):
            # A "Returnable" / "Replacement Only" badge sets the policy.
            if "trusted_icons" in prof or "return_type" in prof:
                prof["return_type"] = trust_badges.return_policy_from(badges)
        if any(k in prof for k in ("return_type", "return_days", "replacement_days", "trusted_icons")):
            rt = _effective(product, prof, "return_type") or ""
            if any(trust_badges.is_non_returnable(b) for b in badges):
                rt = "non_returnable"
            if rt == "non_returnable":
                prof["return_days"] = 0
            elif rt in ("returnable", "replacement"):
                if "replacement_days" in prof:
                    prof["replacement_days"] = None
            else:
                if "return_days" in prof:
                    prof["return_days"] = None
                if "replacement_days" in prof:
                    prof["replacement_days"] = None
        if not prof:
            combo.pop("profile", None)


def profile_of(combo) -> dict:
    """The stored profile of a combination (always a dict)."""
    if not isinstance(combo, dict):
        return {}
    prof = combo.get("profile")
    return prof if isinstance(prof, dict) else {}


def combo_active(combo) -> bool:
    """False when the owner switched this variation off ("Active" unticked
    while editing it): it then can't be bought, like a sold-out one."""
    return profile_of(combo).get("is_active", True) is not False


def combo_units(combo) -> int:
    """Units of a combination a shopper can buy (0 when it is inactive)."""
    if not combo_active(combo):
        return 0
    try:
        return max(0, int((combo or {}).get("inventory") or 0))
    except (TypeError, ValueError):
        return 0


def sellable_stock(product) -> int:
    """The product's buyable units: its own stock, or the sum over its active
    variations."""
    from commerce.cart import combos_of

    combos = combos_of(product)
    if not combos:
        return int(getattr(product, "inventory", 0) or 0)
    return sum(combo_units(c) for c in combos)


def low_stock_level(product, combo) -> int:
    prof = profile_of(combo)
    value = prof.get("low_stock_threshold", getattr(product, "low_stock_threshold", 0))
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def merged_details(product, profile: dict) -> dict:
    """The product's ``d2c_details`` with the variation's overrides applied."""
    base = product.d2c_details if isinstance(getattr(product, "d2c_details", None), dict) else {}
    merged = dict(base)
    for name in tuple(DETAIL_TEXT) + DETAIL_LISTS + ("extras",):
        if name in profile:
            merged[name] = profile[name]
    return merged


def return_policy_label(product, profile: dict) -> str:
    """``Product.return_policy_label`` for a variation (its own badges and
    return policy where it has them)."""
    from commerce import trust_badges

    badges = profile["trusted_icons"] if "trusted_icons" in profile else (getattr(product, "trusted_icons", None) or [])
    if any(trust_badges.is_non_returnable(b) for b in badges):
        rt = "non_returnable"
    else:
        rt = _effective(product, profile, "return_type") or ""
    if not rt:
        return ""
    if rt == "non_returnable":
        rdays = _effective(product, profile, "replacement_days")
        if rdays:
            rdays = int(rdays)
            return f"Non-Returnable · Replacement within {rdays} {'day' if rdays == 1 else 'days'}"
        return "Non-Returnable"
    days = _effective(product, profile, "return_days")
    if days:
        days = int(days)
        verb = "Replacement" if rt == "replacement" else "Returnable"
        return f"{verb} within {days} {'day' if days == 1 else 'days'}"
    return {"returnable": "Returnable", "replacement": "Replacement only"}.get(rt, "")


class VariantProductView:
    """A product (usually a storefront ``ProductProxy``) seen through one
    variation's profile: overridden fields come from the profile, everything
    else from the wrapped object, so the shared product-page partials render a
    variation exactly as they render the product."""

    _MODEL_FIELDS = (
        frozenset(MODEL_TEXT) | frozenset(MODEL_DECIMAL) | frozenset(MODEL_UNIT)
        | frozenset(MODEL_CHOICE) | frozenset(MODEL_BOOL) | frozenset(MODEL_INT)
        | frozenset({"video_link"})
    )

    def __init__(self, base, profile: dict, product=None, categories=None):
        self._base = base
        self._profile = profile or {}
        self._product = product if product is not None else getattr(base, "_p", base)
        self._categories = categories or {}

    def _wrap(self, value):
        from storefront.safe import wrap

        return wrap(value)

    @property
    def specifications(self):
        if "specifications" in self._profile:
            return self._wrap(self._profile["specifications"])
        return self._base.specifications

    @property
    def faqs(self):
        if "faqs" in self._profile:
            return self._wrap(self._profile["faqs"])
        return self._base.faqs

    @property
    def trusted_icons(self):
        if "trusted_icons" in self._profile:
            from commerce.trust_badges import is_removed

            return self._wrap([n for n in self._profile["trusted_icons"] if not is_removed(n)])
        return self._base.trusted_icons

    @property
    def tags(self):
        return self._profile["keywords"] if "keywords" in self._profile else getattr(self._product, "tags", None)

    @property
    def gst_percentage(self):
        if "gst_percentage" in self._profile:
            try:
                return Decimal(str(self._profile["gst_percentage"]))
            except (InvalidOperation, ValueError):
                return None
        return self._base.gst_percentage

    @property
    def category(self):
        if "category" in self._profile:
            return self._categories.get(self._profile["category"]) if self._profile["category"] else None
        return self._base.category

    @property
    def dangerous_good(self):
        if "dangerous_goods" in self._profile:
            from commerce.models import Product

            value = self._profile["dangerous_goods"]
            return dict(Product.DangerousGoods.choices).get(value, "") if value else ""
        return self._base.dangerous_good

    @property
    def return_policy_label(self):
        return return_policy_label(self._product, self._profile)

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        if name in self._MODEL_FIELDS and name in self._profile:
            return self._profile[name]
        return getattr(self._base, name)


def storefront_profiles(product, base_proxy, *, default_faqs=None) -> list:
    """The variations of ``product`` that have their own details, ready for the
    product page: one dict per combination with a profile::

        {"key", "title", "own_title", "product", "details", "faqs",
         "video", "category"}

    ``product`` is the wrapped proxy for the shared partials, ``details`` the
    merged ``d2c_details`` (wrapped null-safe), ``faqs`` the FAQ list the page
    shows for that variation (its own, else ``default_faqs``; ``None`` =
    unchanged), ``video`` its product video (``None`` = unchanged, ``False``
    = none) and ``category`` its breadcrumb category (``None`` = unchanged).
    """
    from commerce.cart import combos_of, variant_key_of
    from storefront.safe import wrap

    combos = [c for c in combos_of(product) if profile_of(c)]
    if not combos:
        return []
    cats = {}
    cat_ids = {profile_of(c).get("category") for c in combos} - {None, ""}
    if cat_ids:
        from commerce.models import Category

        site = getattr(product, "site", None)
        for cat in Category.objects.for_site(site).filter(pk__in=cat_ids, is_deleted=False):
            cats[str(cat.pk)] = cat

    out = []
    for combo in combos:
        prof = profile_of(combo)
        key = variant_key_of(combo)
        if not key:
            continue
        view = VariantProductView(base_proxy, prof, product=product, categories=cats)
        if "faqs" in prof and prof["faqs"]:
            faqs = [dict(f) for f in prof["faqs"]]
        elif "faqs" in prof:
            # Cleared on the variation: the page's usual fallback set.
            faqs = [dict(f) for f in (default_faqs or [])]
        else:
            faqs = None  # inherit the product's FAQ section unchanged
        video = None
        if "video_link" in prof or "video_orientation" in prof:
            from storefront.views import _build_product_video

            video = _build_product_video(view) or False
        category = None
        if "category" in prof:
            category = view.category or False
        out.append(
            {
                "key": key,
                "title": prof.get("title") or product.title,
                "own_title": bool(prof.get("title")),
                "product": view,
                "details": wrap(merged_details(product, prof)),
                "faqs": faqs,
                "video": video,
                "category": category,
            }
        )
    return out


# ---------------------------------------------------------------------------
# One cart / order line seen through its variation (tax, HSN, measurements)
# ---------------------------------------------------------------------------

_LINE_FIELDS = frozenset(MODEL_DECIMAL) | frozenset(MODEL_UNIT) | frozenset({"gst_percentage", "gst_inclusive", "hsn_code"})


class _LineView:
    """A product with one variation's tax, HSN, dimension and weight overrides
    applied; every other attribute comes from the product."""

    def __init__(self, product, profile: dict):
        self._p = product
        self._profile = profile

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        if name in _LINE_FIELDS and name in self._profile:
            raw = self._profile[name]
            if name in MODEL_DECIMAL or name == "gst_percentage":
                if raw in ("", None):
                    return None
                try:
                    return Decimal(str(raw))
                except (InvalidOperation, ValueError):
                    return None
            return raw
        return getattr(self._p, name)


def combo_view(product, combo):
    """``product`` as sold in the variation ``combo`` (for tax / shipping).
    The plain product when the variation changes none of those fields."""
    if product is None:
        return None
    prof = profile_of(combo)
    if not any(k in prof for k in _LINE_FIELDS):
        return product
    return _LineView(product, prof)


def _combo_for_line(item):
    product = getattr(item, "product", None)
    if product is None:
        return None
    from commerce.cart import _norm, _variant_label, combos_of

    combos = combos_of(product)
    if not combos:
        return None
    label = (getattr(item, "variant_label", "") or "").strip()
    if label:
        want = _norm(label)
        for combo in combos:
            if _norm(_variant_label(combo)) == want:
                return combo
    snapshot = str(getattr(item, "sku", "") or "").strip().lower()
    if snapshot and snapshot != str(getattr(product, "sku", "") or "").strip().lower():
        for combo in combos:
            if str(combo.get("sku") or "").strip().lower() == snapshot:
                return combo
    return None


def line_view(item):
    """The product of an order line with that line's variation tax, HSN,
    dimensions and weight applied. Falls back to the plain product (unchanged
    behaviour) whenever no profile matches."""
    product = getattr(item, "product", None)
    if product is None:
        return None
    return combo_view(product, _combo_for_line(item))


# Older name, kept for the shipping callers.
line_measures = line_view
