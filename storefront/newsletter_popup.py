"""The storefront newsletter popup.

Configured on the Edit Content → **Newsletter** tab (``commerce.views.editor``),
stored in ``Site.content`` under ``newsletter_popup_*`` keys:

* ``newsletter_popup_enabled``  show it to visitors at all.
* ``newsletter_popup_delay``    seconds after the page loads (1–5).
* ``newsletter_popup_image``    optional desktop banner (portrait, beside the form).
* ``newsletter_popup_mobile_image``  optional phone banner (a wide strip above
  the form). Desktop shows only the desktop banner; phones reuse it when they
  have none of their own (unless the owner removed the phone banner).
* ``newsletter_popup_coupon``   optional id of one of the store's own discount
  codes, offered to people who sign up. Choosing one gives the popup an
  automatic heading ("Sign up and get 10% off your first order") unless the
  owner wrote their own.
* ``newsletter_popup_heading`` / ``_text`` / ``_button`` / ``_success``  copy.

A signup from the popup is saved as a :class:`~commerce.models.NewsletterSubscriber`
with ``source = "newsletter_popup"``, so it lists on the owner's Subscribers page
tagged "Subscribed from newsletter". The offer's code is only revealed after a
successful signup, and only while the discount is still valid.
"""

from __future__ import annotations

from decimal import Decimal

#: ``NewsletterSubscriber.source`` for signups made through the popup.
SOURCE = "newsletter_popup"

DELAY_CHOICES = (1, 2, 3, 5)
DEFAULT_DELAY = 2

DEFAULT_HEADING = "Join our newsletter"
DEFAULT_BUTTON = "Subscribe"
DEFAULT_SUCCESS = "You’re subscribed. Thank you!"

# Pages where a popup would get in the way of finishing a purchase or reading a
# document: no popup there.
_QUIET_URL_NAMES = frozenset({
    "public_checkout", "public_order_confirmation", "public_order_invoice",
    "public_cart", "public_user_account",
})


def _amount(value) -> str:
    """``10`` for 10.00, ``12.5`` for 12.50."""
    try:
        d = Decimal(value or 0).normalize()
    except Exception:
        return str(value or "")
    text = format(d, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def offer_phrase(coupon, symbol: str = "₹") -> str:
    """What the discount gives, in a few words ("10% off", "₹200 off",
    "free shipping", "Buy 2 get 1 free")."""
    from commerce.models import Coupon

    if coupon.discount_class == Coupon.DiscountClass.SHIPPING:
        return "free shipping"
    if coupon.discount_class == Coupon.DiscountClass.BXGY:
        buy = coupon.buy_quantity or 1
        get = coupon.get_quantity or 1
        if coupon.reward_type == Coupon.RewardType.PERCENT and coupon.reward_value:
            reward = f"{_amount(coupon.reward_value)}% off"
        elif coupon.reward_type == Coupon.RewardType.AMOUNT and coupon.reward_value:
            reward = f"{symbol}{_amount(coupon.reward_value)} off"
        else:
            reward = "free"
        return f"Buy {buy} get {get} {reward}"
    if coupon.discount_type == Coupon.DiscountType.PERCENT:
        return f"{_amount(coupon.value)}% off"
    return f"{symbol}{_amount(coupon.value)} off"


def offer_headline(coupon, symbol: str = "₹") -> str:
    """The automatic popup heading for a chosen offer."""
    from commerce.models import Coupon

    phrase = offer_phrase(coupon, symbol)
    if coupon.discount_class == Coupon.DiscountClass.BXGY:
        return f"Sign up to unlock {phrase}"
    if coupon.discount_class == Coupon.DiscountClass.SHIPPING:
        return "Sign up and get free shipping on your first order"
    return f"Sign up and get {phrase} your first order"


def _symbol(site) -> str:
    try:
        from .templatetags.website_filters import currency_symbol

        return currency_symbol(getattr(site, "currency", "INR")) or "₹"
    except Exception:
        return "₹"


def offer_coupons(site):
    """The store's discount codes that can be offered in the popup: coded (a
    shopper types it), not deleted, and valid right now."""
    from commerce.models import Coupon

    qs = (
        Coupon.objects.for_site(site)
        .filter(is_deleted=False, method=Coupon.Method.CODE, active=True)
        .exclude(code="")
        .order_by("-created_at")
    )
    return [c for c in qs if c.is_valid_now()]


def coupon_options(site) -> list[dict]:
    """Options for the editor's "Offer" dropdown, each with its auto heading."""
    symbol = _symbol(site)
    out = []
    for c in offer_coupons(site):
        label = f"{c.code} · {offer_phrase(c, symbol)}"
        if c.ends_at:
            label += f" · ends {c.ends_at:%d %b %Y}"
        out.append({"value": str(c.pk), "label": label, "headline": offer_headline(c, symbol)})
    return out


def selected_coupon(site):
    """The popup's offer, if one is chosen and still valid."""
    raw = str((site.content or {}).get("newsletter_popup_coupon") or "").strip()
    if not raw:
        return None
    from commerce.models import Coupon

    try:
        coupon = (
            Coupon.objects.for_site(site)
            .filter(pk=raw, is_deleted=False, method=Coupon.Method.CODE)
            .exclude(code="")
            .first()
        )
    except Exception:
        return None
    return coupon if coupon is not None and coupon.is_valid_now() else None


def config(site, request=None) -> dict | None:
    """Everything the storefront popup renders, or ``None`` when it is off (or
    this page shouldn't show it)."""
    content = site.content or {} if site is not None else {}
    if not content.get("newsletter_popup_enabled"):
        return None
    match = getattr(request, "resolver_match", None) if request is not None else None
    if match is not None and match.url_name in _QUIET_URL_NAMES:
        return None

    def text(key, default="", limit=300):
        return " ".join(str(content.get(key) or "").split())[:limit] or default

    coupon = selected_coupon(site)
    symbol = _symbol(site)
    heading = text("newsletter_popup_heading", limit=120)
    if not heading:
        heading = offer_headline(coupon, symbol) if coupon else DEFAULT_HEADING
    brand = text("site_name") or getattr(site, "name", "") or "us"
    try:
        delay = int(content.get("newsletter_popup_delay") or DEFAULT_DELAY)
    except (TypeError, ValueError):
        delay = DEFAULT_DELAY
    if delay not in DELAY_CHOICES:
        delay = DEFAULT_DELAY
    desktop_image = str(content.get("newsletter_popup_image") or "").strip()
    mobile_image = str(content.get("newsletter_popup_mobile_image") or "").strip()
    return {
        "heading": heading,
        "text": text(
            "newsletter_popup_text",
            f"Be the first to hear about new arrivals, offers and stories from {brand}.",
            limit=400,
        ),
        "button": text("newsletter_popup_button", DEFAULT_BUTTON, limit=40),
        "success": text("newsletter_popup_success", DEFAULT_SUCCESS, limit=200),
        # Desktop shows only its own banner. Phones show theirs, or reuse the
        # desktop banner unless the owner removed the phone banner.
        "image": desktop_image,
        "mobile_image": mobile_image or ("" if content.get("newsletter_popup_mobile_image_removed") else desktop_image),
        "delay_ms": delay * 1000,
        "has_offer": coupon is not None,
        "offer": offer_phrase(coupon, symbol) if coupon else "",
        # Bumps when the owner changes the popup's offer or heading, so a
        # visitor who dismissed the old one sees the new one.
        "version": f"{coupon.pk if coupon else ''}:{heading}"[:120],
    }
