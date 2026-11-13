"""Small template helpers for the store surface."""

from __future__ import annotations

from django import template

register = template.Library()


@register.filter
def dictkey(mapping, key):
    """Look up ``mapping[key]`` from a template. Returns 0 when absent so count
    badges render cleanly."""
    if hasattr(mapping, "get"):
        return mapping.get(key, 0)
    return 0


@register.filter
def split(value, separator=","):
    """Split a string into a list  used to build step labels inline."""
    return (value or "").split(separator)


@register.filter
def pad_rows(rows, minimum=""):
    """``rows`` padded with blank strings up to ``minimum`` entries, so a
    repeatable list (e.g. a product's bullet points) opens with a few empty
    inputs ready to type into. Existing values are never dropped; a blank or
    unparseable ``minimum`` returns the rows unchanged. Blank rows post as empty
    strings, which the views already discard."""
    out = list(rows or [])
    try:
        need = int(minimum or 0)
    except (TypeError, ValueError):
        need = 0
    while len(out) < need:
        out.append("")
    return out


@register.simple_tag(takes_context=True)
def field_tip(context, key):
    """The info icon + hover tooltip for one product-form field, when OneWooz
    staff wrote a description for it in the Django admin (Product field
    tooltips). Renders nothing for a field without one."""
    from django.utils.html import format_html

    tips = context.get("field_tips") or {}
    text = tips.get(str(key or "")) if hasattr(tips, "get") else None
    if not text:
        return ""
    return format_html(
        '<span class="app-tip" tabindex="0" role="button" aria-label="About this field">'
        '<span class="app-tip__ico" aria-hidden="true">!</span>'
        '<span class="app-tip__bubble" role="tooltip">{}</span>'
        "</span>",
        text,
    )


@register.filter
def widget_type(bound_field):
    """Return a bound field's widget class name, e.g. 'Textarea', so templates
    can lay a field out by its input kind."""
    try:
        return bound_field.field.widget.__class__.__name__
    except AttributeError:
        return ""


@register.filter
def money(value, currency="INR"):
    """Format a Decimal as ``INR 1,234`` with thousands separators."""
    try:
        return f"{currency} {value:,.2f}"
    except (TypeError, ValueError):
        return f"{currency} 0.00"


# Video file extensions we can preview inline with a <video> element. Anything
# else set as a product's video link (YouTube, Vimeo, a page URL) is an external
# embed, previewed as a link instead.
_VIDEO_EXTS = (".mp4", ".webm", ".ogg", ".ogv", ".mov", ".m4v")


@register.filter
def is_video_file(url):
    """True when ``url`` points at an uploaded video file we can play in a
    ``<video>`` tag (as opposed to an external link like YouTube). Used by the
    product form to preview the currently-attached product video."""
    raw = (url or "").strip().lower().split("?")[0].split("#")[0]
    return raw.endswith(_VIDEO_EXTS)


# How an order was paid, as the short tag the Orders page / order page show:
# Order.payment_method -> (label, kind). ``kind`` picks the tag colour.
_PAYMENT_TAGS = {
    "cod": ("COD", "cod"),
    "partial": ("Partial COD", "partial"),
    "razorpay": ("Prepaid · Razorpay", "prepaid"),
    "cashfree": ("Prepaid · Cashfree", "prepaid"),
    "prepaid": ("Prepaid", "prepaid"),
    "online": ("Prepaid", "prepaid"),
    "store_credit": ("Store credit", "credit"),
    "replacement": ("Replacement · No charge", "free"),
}


@register.filter
def payment_tag(order):
    """``{"label", "kind"}`` for an order's payment method, e.g. COD, Partial
    COD, Prepaid · Razorpay, Store credit. A partial-payment order is always
    "Partial COD" (the balance is collected on delivery)."""
    method = str(getattr(order, "payment_method", "") or "").strip().lower()
    if getattr(order, "is_partial_payment", False):
        method = "partial"
    if method in _PAYMENT_TAGS:
        label, kind = _PAYMENT_TAGS[method]
    elif "cod" in method:
        label, kind = "COD", "cod"
    elif method:
        label, kind = f"Prepaid · {method.replace('_', ' ').title()}", "prepaid"
    else:
        label, kind = "Not recorded", "none"
    return {"label": label, "kind": kind}
