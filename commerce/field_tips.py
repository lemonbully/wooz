"""Hover tooltips for the store owner's create / edit product form.

OneWooz staff write a short explanation for any product-form input in the
Django admin (**Product field tooltips**, :class:`~commerce.models.ProductFieldTooltip`).
Every field with a description shows a small "!" circle just right of its
label; hovering (or focusing) it shows the description.

:data:`PRODUCT_FIELDS` is the vocabulary: one stable key per input on the form
(D2C and B2B steps alike). The key is the name the input posts, or for grouped
inputs the name of the first input in the group. The admin lists one row per
key automatically (:func:`ensure_rows`), so staff never have to add a field by
hand: they open a row and type its description.
"""

from __future__ import annotations

#: (key, label as the owner sees it, form step) for every product-form input.
PRODUCT_FIELDS: tuple[tuple[str, str, str], ...] = (
    # Details / Basics
    ("category", "Category", "Details"),
    ("has_variations", "Has variations?", "Details"),
    ("currency", "Currency", "Details"),
    ("categories_multi", "Additional categories", "Details"),
    ("title", "Product title", "Details"),
    ("brand", "Brand", "Details"),
    ("bullets", "Bullet points", "Details"),
    ("description", "Description", "Details"),
    ("keywords", "Keywords / Tags", "Details"),
    ("recommended_use", "Recommended use", "Details"),
    ("material", "Material / Ingredients", "Details"),
    ("colour", "Colour / texture", "Details"),
    ("features", "Special features", "Details"),
    ("faq_q", "FAQs", "Details"),
    ("is_active", "Active", "Details"),
    ("is_best_seller", "Mark as Best Seller", "Details"),
    # Images & video
    ("images", "Product images", "Images & video"),
    ("video_orientation", "Product video format", "Images & video"),
    ("video_link", "Video link (URL)", "Images & video"),
    ("media_video", "Upload a product video", "Images & video"),
    # Pricing & tax
    ("sku", "SKU", "Pricing"),
    ("handling_time", "Handling time", "Pricing"),
    ("mrp", "MRP", "Pricing"),
    ("price", "Selling price", "Pricing"),
    ("gst_percentage", "GST %", "Pricing"),
    ("hsn_code", "HSN code", "Pricing"),
    ("gst_inclusive", "GST included in the price", "Pricing"),
    ("inventory", "Stock available", "Pricing"),
    ("low_stock_threshold", "Low stock threshold", "Pricing"),
    ("trusted_icons", "Trusted badges", "Pricing"),
    ("accessories", "Accessories", "Pricing"),
    ("includes", "What's in the box", "Pricing"),
    # Dimensions & logistics
    ("product_length", "Product length", "Dimensions"),
    ("product_breadth", "Product breadth", "Dimensions"),
    ("product_height", "Product height", "Dimensions"),
    ("product_unit", "Product size unit", "Dimensions"),
    ("weight_value", "Weight", "Dimensions"),
    ("weight_unit", "Weight unit", "Dimensions"),
    ("package_length", "Package length", "Dimensions"),
    ("package_breadth", "Package breadth", "Dimensions"),
    ("package_height", "Package height", "Dimensions"),
    ("package_unit", "Package size unit", "Dimensions"),
    ("package_weight_value", "Package weight", "Dimensions"),
    ("package_weight_unit", "Package weight unit", "Dimensions"),
    # Variations
    ("variant_types", "Variant types", "Variations"),
    ("variant_combinations", "Variant combinations", "Variations"),
    # Specifications
    ("spec_k", "Specifications", "Specifications"),
    # Compliance
    ("country_of_origin", "Country of origin", "Compliance"),
    ("dangerous_goods", "Dangerous goods", "Compliance"),
    ("return_type", "Return policy", "Compliance"),
    ("return_days", "Return days", "Compliance"),
    ("replacement_days", "Replacement days", "Compliance"),
    ("guarantee_warranty", "Guarantee / warranty", "Compliance"),
    ("manufacturer_contact", "Manufacturer contact", "Compliance"),
    ("packer_contact", "Packer contact", "Compliance"),
    ("importer_contact", "Importer contact", "Compliance"),
    ("warning", "Warning", "Compliance"),
    ("support_phone", "Support phone", "Compliance"),
    ("support_email", "Support email", "Compliance"),
    # Extras
    ("extra_name", "Extra sections", "Extras"),
)

FIELD_KEYS = frozenset(k for k, _l, _s in PRODUCT_FIELDS)
_LABELS = {k: label for k, label, _s in PRODUCT_FIELDS}
_ORDER = {k: i for i, (k, _l, _s) in enumerate(PRODUCT_FIELDS)}


def choices() -> list[tuple[str, str]]:
    """``(key, "Step · Label")`` for the admin's field dropdown."""
    return [(k, f"{step} · {label}") for k, label, step in PRODUCT_FIELDS]


def label_for(key: str) -> str:
    return _LABELS.get(key, key)


def ensure_rows() -> None:
    """Create one (blank) tooltip row per known field that has none yet, so the
    admin always lists every product-form input ready to be described. Safe to
    call repeatedly; never touches an existing row's description."""
    from django.db import DatabaseError

    from .models import ProductFieldTooltip

    try:
        have = set(ProductFieldTooltip.objects.values_list("field_key", flat=True))
        missing = [
            ProductFieldTooltip(field_key=k, label=label, order=_ORDER[k])
            for k, label, _s in PRODUCT_FIELDS
            if k not in have
        ]
        if missing:
            ProductFieldTooltip.objects.bulk_create(missing, ignore_conflicts=True)
    except DatabaseError:
        # Table not migrated yet: nothing to seed.
        pass


def tooltips_map() -> dict[str, str]:
    """``{field key: description}`` for every active tooltip with text. Fails
    open to ``{}`` (e.g. before the migration runs), so the product form never
    breaks over a tooltip."""
    from django.db import DatabaseError

    from .models import ProductFieldTooltip

    try:
        rows = ProductFieldTooltip.objects.filter(is_active=True).exclude(description="").values_list(
            "field_key", "description"
        )
        return {k: " ".join(str(d).split()) for k, d in rows if k and str(d).strip()}
    except DatabaseError:
        return {}
