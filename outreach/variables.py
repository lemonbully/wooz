"""The pre-defined template variables, plus demo and live substitutions.

One source of truth for the ``{{placeholders}}`` a store fills in a marketing
email. The admin template builder shows this list, previews templates with the
*demo* values, and tells ChatGPT about them; at send time
:mod:`outreach.services.rendering` swaps in the *live* values from the store.

Every value is a plain string (``{{products}}`` and ``{{store_logo}}`` resolve
to ready-made HTML / an image URL), so substitution is a simple text replace and
a template can never fail on a missing key.
"""

from __future__ import annotations

import html as _html

# key → (label, description). The order is the order shown in the builder.
TEMPLATE_VARIABLES = [
    ("store_name", "Store name", "The store's brand name."),
    ("store_logo", "Store logo", "The store's logo  use inside an <img src>."),
    ("store_email", "Store email", "The store's contact / reply-to address."),
    ("positioning", "Positioning", "The store's one-line tagline / positioning."),
    ("products", "Products", "A ready-made product showcase grid (use once)."),
    ("recipient_name", "Recipient name", "The customer's first name (or “there”)."),
    ("cta_url", "Button link", "The tracked call-to-action URL."),
    ("unsubscribe_url", "Unsubscribe link", "The one-click unsubscribe URL."),
]

VARIABLE_KEYS = [k for k, _l, _d in TEMPLATE_VARIABLES]


def variables_prompt_reference() -> str:
    return "\n".join(f"  - {{{{{k}}}}}  {d}" for k, _l, d in TEMPLATE_VARIABLES)


def _product_cell(item: dict) -> str:
    title = _html.escape(str(item.get("title") or "Product"))
    price = _html.escape(str(item.get("price") or ""))
    image = item.get("image") or ""
    url = item.get("url") or "{{cta_url}}"
    price_row = (
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:13px;color:#4b2bf5;'
        f'font-weight:600;margin-top:2px;">{price}</div>'
        if price
        else ""
    )
    picture = (
        f'<img src="{image}" width="260" alt="{title}" '
        'style="display:block;width:100%;max-width:260px;height:auto;border:1px solid #e5e5e2;" />'
        if image
        else ""
    )
    return (
        '<td width="50%" valign="top" style="padding:8px;">'
        f'<a href="{url}" style="text-decoration:none;color:#0a0a0b;">'
        f"{picture}"
        f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#0a0a0b;'
        f'margin-top:8px;">{title}</div>{price_row}</a></td>'
    )


def products_block_html(items: list[dict]) -> str:
    """A two-column, email-safe product grid from ``[{title, price, image, url}]``."""
    if not items:
        return ""
    rows = []
    for i in range(0, len(items), 2):
        pair = items[i : i + 2]
        cells = "".join(_product_cell(p) for p in pair)
        if len(pair) == 1:
            cells += '<td width="50%"></td>'
        rows.append(f"<tr>{cells}</tr>")
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="border-collapse:collapse;margin:8px 0;">' + "".join(rows) + "</table>"
    )


# ── demo (preview) ───────────────────────────────────────────────────────────

_DEMO_PRODUCTS = [
    {"title": "Everyday Tote", "price": "₹1,299", "image": "https://picsum.photos/seed/owp1/520/650", "url": "#"},
    {"title": "Linen Shirt", "price": "₹1,899", "image": "https://picsum.photos/seed/owp2/520/650", "url": "#"},
    {"title": "Ceramic Mug", "price": "₹499", "image": "https://picsum.photos/seed/owp3/520/650", "url": "#"},
    {"title": "Wool Throw", "price": "₹2,499", "image": "https://picsum.photos/seed/owp4/520/650", "url": "#"},
]


def demo_context() -> dict:
    """Demo values so a template previews as a finished email."""
    return {
        "store_name": "Aurora &amp; Co.",
        "store_logo": "https://picsum.photos/seed/owlogo/180/54",
        "store_email": "hello@auroraco.example",
        "positioning": "Thoughtfully made essentials for everyday life.",
        "products": products_block_html(_DEMO_PRODUCTS),
        "recipient_name": "Alex",
        "cta_url": "#preview-cta",
        "unsubscribe_url": "#preview-unsubscribe",
        "tracking_pixel": "",
    }


# ── live (send-time) ─────────────────────────────────────────────────────────

def _store_logo_url(site) -> str:
    """The store's logo as an absolute, email-safe URL ("" when none).

    The editor stores it as ``content["desktop_logo"]`` (Brand tab); the older
    nested keys are still honoured.
    """
    from outreach.email_images import email_safe_url

    try:
        content = getattr(site, "content", None) or {}
        candidates = [content.get("desktop_logo") if isinstance(content, dict) else None]
        for path in (("brand", "logo"), ("brand", "logo_url"), ("logo",)):
            node = content
            for key in path:
                node = node.get(key) if isinstance(node, dict) else None
            candidates.append(node)
        for value in candidates:
            if isinstance(value, str) and value.strip():
                return email_safe_url(value.strip())
    except Exception:
        pass
    return ""


def _money(value) -> str:
    from decimal import Decimal, InvalidOperation

    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return ""
    if d == d.to_integral_value():
        return f"₹{int(d):,}"
    return f"₹{d:,.2f}"


def _live_products(site, limit: int = 4, ids: list | None = None) -> list[dict]:
    """The campaign's products for the {{products}} grid of a sent email.

    Only live (active, not deleted) products; prices formatted like the
    storefront; images as email-safe JPEG URLs. A product without a photo gets
    no image rather than a stock picture.
    """
    try:
        from commerce.models import Product
        from core.queries import sorted_rows
        from outreach.email_images import email_safe_url

        items = []
        base = Product.objects.for_site(site).filter(is_deleted=False, is_active=True)
        if ids:
            # The campaign chose specific products: honour them, in the chosen
            # order, capped to the grid limit. Ignore ids that no longer resolve
            # to a live product of this store.
            by_id = {str(p.id): p for p in base.filter(id__in=[str(i) for i in ids])}
            qs = [by_id[str(i)] for i in ids if str(i) in by_id][:limit]
        else:
            qs = sorted_rows(base.order_by("-created_at"), limit=limit)
        for p in qs:
            img = ""
            first = p.images.all().first() if hasattr(p, "images") else None
            if first and getattr(first, "image", None):
                try:
                    img = email_safe_url(first.image.url)
                except Exception:
                    img = ""
            items.append(
                {
                    "title": p.title,
                    "price": _money(p.price),
                    "image": img,
                    "url": "{{cta_url}}",
                }
            )
        return items
    except Exception:
        return []


def _first_name(name: str) -> str:
    parts = (name or "").strip().split()
    return parts[0] if parts else ""


def live_context(site, *, recipient_name: str, cta_url: str, unsubscribe_url: str, tracking_pixel: str, product_ids: list | None = None, product_limit: int = 4) -> dict:
    """Real values for one store's send.

    ``product_ids`` optionally pins the {{products}} grid to a campaign's chosen
    products (in order); ``product_limit`` caps how many render (1 for a
    single-product campaign, 4 for multi). Blank ids fall back to latest.
    """
    from tenancy.email import store_from_address

    products = _live_products(site, limit=product_limit, ids=product_ids)
    return {
        "store_name": getattr(site, "name", "") or "",
        "store_logo": _store_logo_url(site),
        "store_email": (getattr(site, "support_email", "") or "").strip() or store_from_address(site),
        "positioning": (getattr(site, "tagline", "") or "").strip(),
        "products": products_block_html(products),
        "recipient_name": _first_name(recipient_name) or "there",
        "cta_url": cta_url,
        "unsubscribe_url": unsubscribe_url,
        "tracking_pixel": tracking_pixel,
    }
