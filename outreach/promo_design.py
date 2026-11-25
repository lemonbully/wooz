"""The built-in "Promo collection" email design.

A fixed, hand-built layout (hero with a frosted brand badge, headline + intro +
pill button, a "sale starts in" countdown, a product grid laid out by how many
products were chosen, a dark offer card
with a promo code, a beige waitlist band with a highlights card, and a quiet
footer). Unlike the admin-published :class:`~outreach.models.EmailTemplate`
designs, the owner never touches HTML here: the composer shows one input per
slot, the values are stored on ``Campaign.design_data`` and this module renders
the email from them.

Rendering happens at *send* time (see :func:`outreach.services.rendering.
render_delivery`) so the product grid shows the store's live titles, prices and
images, and the countdown reads the time actually remaining when the email goes
out. Email clients cannot run script, so the countdown is a snapshot, exactly as
it would be in any sent email.

Every value the owner types is HTML-escaped; every link is restricted to
``http(s)`` and routed through the ``link`` callable so the
sender can wrap it in a tracked click URL.
"""

from __future__ import annotations

import html as _html
from datetime import datetime
from decimal import Decimal, InvalidOperation

DESIGN_KEY = "promo"
DESIGN_NAME = "Promo collection"
DESIGN_DESCRIPTION = "Hero image with brand badge, countdown, product grid, promo code and highlights."

#: How many products the grid holds. The layout follows the count (see
#: :func:`grid_rows`): 1 full width, 2 or 3 on one row, 4 as 2x2, 5 as 2x2
#: with the fifth centred, 6 as two rows of three.
MAX_PRODUCTS = 6

#: Highlight icons (PNG, see ``static/email/icons``). key -> label.
ICONS = [
    ("box", "Parcel"),
    ("bag", "Shopping bag"),
    ("gift", "Gift"),
    ("truck", "Delivery truck"),
    ("returns", "Returns"),
    ("star", "Star"),
]
_ICON_KEYS = {k for k, _ in ICONS}

# Fallback hero photo when the owner has not chosen one yet (preview only feel;
# a real image is always better, and the composer says so).
_DEMO_HERO = "https://picsum.photos/seed/owpromo-hero/1200/1400"

_TEXT_FIELDS = (
    "hero_image", "badge_text",
    "headline", "intro_text", "hero_button_label", "hero_button_url",
    "countdown_label", "countdown_end",
    "offer_title", "offer_text", "offer_code", "offer_button_label", "offer_button_url",
    "waitlist_text", "waitlist_button_label", "waitlist_button_url",
    "hl1_icon", "hl1_label", "hl1_url",
    "hl2_icon", "hl2_label", "hl2_url",
    "hl3_icon", "hl3_label", "hl3_url",
    "footer_address",
)

_MAX_LEN = {
    "hero_image": 1000, "intro_text": 1200, "offer_text": 600, "waitlist_text": 1200,
}


def defaults(site) -> dict:
    """Starting values for a new campaign using this design."""
    brand = (getattr(site, "name", "") or "").strip() or "Our store"
    return {
        "hero_image": "",
        "badge_text": brand,
        "headline": "Designed for SUMMER in motion",
        "intro_text": (
            f"For a limited time, enjoy exclusive {brand} discounts on our signature "
            "collections. Pick your favourites before they are gone."
        ),
        "hero_button_label": "Explore the Collection",
        "hero_button_url": "",
        "countdown_label": "Sale starts in",
        "countdown_end": "",
        "offer_title": "15% off. Just for you.",
        "offer_text": "To redeem offer, enter promo code below during online checkout.",
        "offer_code": "WELCOME15",
        "offer_button_label": "Redeem Online",
        "offer_button_url": "",
        "waitlist_text": (
            f"Be the first to know when new {brand} drops land. Join the waitlist for "
            "early access to our signature collections."
        ),
        "waitlist_button_label": "Join the Waitlist",
        "waitlist_button_url": "",
        "hl1_icon": "box", "hl1_label": "Free shipping", "hl1_url": "",
        "hl2_icon": "bag", "hl2_label": "Store Finder", "hl2_url": "",
        "hl3_icon": "gift", "hl3_label": "Rewards", "hl3_url": "",
        "footer_address": "",
    }


def clean(data, site) -> dict:
    """Normalise stored or posted values: known keys only, trimmed, capped."""
    base = defaults(site)
    out = {}
    src = data if isinstance(data, dict) else {}
    for key in _TEXT_FIELDS:
        raw = src.get(key, base[key])
        value = str(raw if raw is not None else "").strip()
        out[key] = value[: _MAX_LEN.get(key, 255)]
    for n in (1, 2, 3):
        if out[f"hl{n}_icon"] not in _ICON_KEYS:
            out[f"hl{n}_icon"] = base[f"hl{n}_icon"]
    return out


def from_post(post, site) -> dict:
    """Read the composer's ``pd_<field>`` inputs."""
    return clean({key: post.get(f"pd_{key}", "") for key in _TEXT_FIELDS}, site)


# ── helpers ──────────────────────────────────────────────────────────────────

def _e(value) -> str:
    return _html.escape(str(value or ""), quote=True)


def _multiline(value) -> str:
    return "<br>".join(_e(line) for line in str(value or "").splitlines())


def store_base_url(site) -> str:
    domain = (getattr(site, "custom_domain", "") or "").strip()
    if domain and getattr(site, "custom_domain_verified", False):
        return f"https://{domain}/"
    try:
        return site.storefront_url()
    except Exception:
        return "/"


def _safe_url(url: str, fallback: str) -> str:
    url = (url or "").strip()
    low = url.lower()
    if low.startswith(("https://", "http://")):
        return url
    return fallback


def _absolute(url: str) -> str:
    from outreach.email_images import absolute

    return absolute(url)


def _email_image(url: str) -> str:
    """Absolute, mail-client-safe (JPEG rather than WebP) image URL."""
    from outreach.email_images import email_safe_url

    return email_safe_url(url)


def _money(value) -> str:
    if value in (None, ""):
        return ""
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return ""
    if d == d.to_integral_value():
        return f"₹{int(d):,}"
    return f"₹{d:,.2f}"


def _icon_url(key: str) -> str:
    from django.templatetags.static import static

    return static(f"email/icons/{key}.png")


def countdown_text(end_raw: str, now=None) -> str:
    """``DD:HH:MM:SS`` left until ``end_raw`` (a datetime-local value), or ""
    when unset or already past (the block is then left out)."""
    from django.utils import timezone
    from django.utils.dateparse import parse_datetime

    if not end_raw:
        return ""
    end = parse_datetime(end_raw)
    if end is None:
        return ""
    if timezone.is_naive(end):
        end = timezone.make_aware(end, timezone.get_current_timezone())
    now = now or timezone.now()
    left = int((end - now).total_seconds())
    if left <= 0:
        return ""
    days, rem = divmod(left, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{days:02d}:{hours:02d}:{minutes:02d}:{seconds:02d}"


def load_products(
    site, ids=None, limit: int = MAX_PRODUCTS, *, demo: bool = False, show_price: bool = True
) -> list[dict]:
    """The grid's products: the chosen ids in order, else the newest.

    ``demo`` (the composer preview only) shows a stock photo for a product
    without images; a real send never does, it shows a neutral tile instead.
    ``show_price=False`` (the campaign's "Show product prices" switched off)
    leaves price and MRP blank, so the cards carry title + picture only.
    """
    from commerce.models import Product

    base = Product.objects.for_site(site).filter(is_deleted=False, is_active=True)
    if ids:
        wanted = [str(i) for i in ids]
        by_id = {str(p.id): p for p in base.filter(id__in=wanted)}
        rows = [by_id[i] for i in wanted if i in by_id][:limit]
    else:
        from core.queries import sorted_rows

        rows = sorted_rows(base.order_by("-created_at"), limit=limit)

    shop = store_base_url(site)
    items = []
    for i, p in enumerate(rows):
        img = ""
        try:
            first = p.images.all().first()
            if first and getattr(first, "image", None):
                img = first.image.url
        except Exception:
            img = ""
        if not img and demo:
            img = f"https://picsum.photos/seed/owpromo-p{i + 1}/400/500"
        price = getattr(p, "price", None)
        mrp = getattr(p, "mrp", None)
        try:
            show_mrp = mrp is not None and price is not None and Decimal(mrp) > Decimal(price)
        except (InvalidOperation, TypeError, ValueError):
            show_mrp = False
        slug = getattr(p, "slug", "") or ""
        items.append(
            {
                "title": p.title,
                "price": _money(price) if show_price else "",
                "mrp": _money(mrp) if (show_mrp and show_price) else "",
                "image": img,
                "url": f"{shop}products/{slug}/" if slug else shop,
            }
        )
    return items


# ── render ───────────────────────────────────────────────────────────────────

_FONT = "'Inter','Helvetica Neue',Helvetica,Arial,sans-serif"


def _pill(href: str, label: str, *, dark: bool = True, size: int = 13) -> str:
    bg, fg, border = ("#111111", "#ffffff", "#111111") if dark else ("#ffffff", "#111111", "#ffffff")
    return (
        f'<a href="{_e(href)}" class="ow-pill" style="display:inline-block;background:{bg};color:{fg};'
        f"border:1px solid {border};border-radius:999px;padding:10px 22px;font-family:{_FONT};"
        f'font-size:{size}px;font-weight:500;line-height:1.2;text-decoration:none;mso-padding-alt:0;">'
        f"{_e(label)}</a>"
    )


#: Inner width of the product grid (600px email, 21px padding each side).
_GRID_W = 558
#: Per column width -> (image width, title px, price px).
_CELL_SPEC = {
    558: (518, 15, 14),  # one product: the full width, with a little air around it
    279: (239, 12, 12),  # two per row
    186: (132, 11, 11),  # three per row
}


def grid_rows(count: int) -> list[int]:
    """How many products sit on each row of the grid.

    * 1 -> one product across the whole width
    * 2 -> both on one row
    * 3 -> all three on one row
    * 4 -> two rows of two
    * 5 -> two rows of two, the fifth centred on a row of its own
    * 6 -> two rows of three

    Anything past :data:`MAX_PRODUCTS` is never passed in (the composer and
    the save path cap it), but larger counts still lay out sensibly: rows of
    two, an odd last one centred.
    """
    n = max(0, int(count or 0))
    if n == 0:
        return []
    if n <= 3:
        return [n]
    if n == 6:
        return [3, 3]
    rows = [2] * (n // 2)
    if n % 2:
        rows.append(1)
    return rows


def _col_width(row_size: int, total: int) -> int:
    """Column width for a row of ``row_size``. A lone product on the last row
    of a bigger grid (the fifth of five) keeps the two-per-row size and is
    centred, so it matches the cards above it."""
    if row_size == 1:
        return _GRID_W if total == 1 else _GRID_W // 2
    return _GRID_W // row_size


def _product_cell(item: dict, link, width: int = 186) -> str:
    """One product card.

    Nothing in a card may have a fixed pixel width: an email table grows to
    fit its widest unshrinkable child, so a single fixed-width picture (the
    518px one of a lone product) used to stretch the WHOLE email past a phone
    screen, re-cropping the hero, pushing the offer card off the edge and
    cutting off the third highlight. Pictures are therefore ``width:100%``
    capped by ``max-width`` (the ``width`` attribute is for Outlook only), and
    the photo-less tile is a fluid table whose height the phone styles retune.
    """
    img_w, title_px, price_px = _CELL_SPEC.get(width, _CELL_SPEC[186])
    img_h = round(img_w * 1.25)
    full = width == _GRID_W
    price = f'<span style="color:#111111;">{_e(item["price"])}</span>' if item.get("price") else ""
    if item.get("mrp"):
        price += f' <span style="color:#9a9a9a;text-decoration:line-through;">{_e(item["mrp"])}</span>'
    href = _e(link(item["url"]))
    radius = "border-radius:6px;" if full else ""
    if item.get("image"):
        picture = (
            f'<img src="{_e(item["image"])}" width="{img_w}" alt="{_e(item["title"])}" class="ow-pimg" '
            f'style="display:block;margin:0 auto;width:100%;max-width:{img_w}px;height:auto;border:0;'
            f'outline:none;{radius}">'
        )
    else:
        picture = (
            '<table role="presentation" align="center" width="100%" cellpadding="0" cellspacing="0" '
            f'border="0" style="margin:0 auto;width:100%;max-width:{img_w}px;"><tr>'
            f'<td height="{img_h}" bgcolor="#f3efeb" class="ow-ph ow-ph--{width}" '
            f'style="height:{img_h}px;background:#f3efeb;font-size:0;line-height:0;{radius}">&nbsp;</td>'
            "</tr></table>"
        )
    pad = "20px 20px 26px" if full else "14px 10px 22px"
    return (
        f'<div class="ow-col ow-col--{width}" style="display:inline-block;width:100%;max-width:{width}px;'
        'vertical-align:top;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'<tr><td align="center" class="ow-pcell" style="padding:{pad};">'
        f'<a href="{href}" style="text-decoration:none;">{picture}</a>'
        f'<p class="ow-ptitle" style="margin:14px 0 0;font-family:{_FONT};font-size:{title_px}px;line-height:1.4;letter-spacing:0.04em;'
        "word-wrap:break-word;overflow-wrap:break-word;"
        f'text-transform:uppercase;color:#111111;"><a href="{href}" style="color:#111111;text-decoration:none;">'
        f'{_e(item["title"])}</a></p>'
        f'<p class="ow-pprice" style="margin:4px 0 0;font-family:{_FONT};font-size:{price_px}px;line-height:1.4;">{price}</p>'
        "</td></tr></table></div>"
    )


def _products_block(items: list[dict], link) -> str:
    """The product grid laid out per :func:`grid_rows`.

    Every card sits in ONE centred line of inline-blocks, so the browser wraps
    them: on desktop the card widths give exactly the rows :func:`grid_rows`
    asks for (a short last row, the fifth of five, lands in the middle); on a
    phone the stylesheet makes each card half the width, so products show two
    per row (and one per row only on the narrowest screens). Outlook ignores
    inline-block, so it gets one ghost table per row instead.
    """
    if not items:
        return ""
    total = len(items)
    parts = []
    pos = 0
    for size in grid_rows(total):
        row = items[pos:pos + size]
        pos += size
        width = _col_width(size, total)
        parts.append(
            f'<!--[if mso]><table role="presentation" align="center" width="{width * len(row)}" '
            f'cellpadding="0" cellspacing="0" border="0"><tr><td width="{width}" valign="top"><![endif]-->'
        )
        for i, item in enumerate(row):
            if i:
                parts.append(f'<!--[if mso]></td><td width="{width}" valign="top"><![endif]-->')
            parts.append(_product_cell(item, link, width))
        parts.append("<!--[if mso]></td></tr></table><![endif]-->")
    pad = "10px 21px 18px" if total == 1 else "4px 21px 18px"
    return (
        f'<tr><td align="center" class="ow-grid" style="padding:{pad};font-size:0;line-height:0;">'
        '<div class="ow-row" style="text-align:center;font-size:0;line-height:0;">'
        + "".join(parts)
        + "</div></td></tr>"
    )


def _highlights(data: dict, link, shop: str, absolute: bool) -> str:
    cells = []
    for n in (1, 2, 3):
        label = data.get(f"hl{n}_label") or ""
        if not label:
            continue
        icon = _icon_url(data.get(f"hl{n}_icon") or "box")
        if absolute:
            icon = _absolute(icon)
        href = _e(link(_safe_url(data.get(f"hl{n}_url"), shop)))
        cells.append(
            '<td align="center" valign="top" class="ow-hl" style="padding:22px 6px 20px;">'
            f'<a href="{href}" style="text-decoration:none;color:#111111;">'
            f'<img src="{_e(icon)}" width="24" height="24" alt="" '
            'style="display:block;margin:0 auto;width:24px;height:24px;border:0;">'
            f'<span style="display:block;margin-top:10px;font-family:{_FONT};font-size:12px;'
            f'line-height:1.4;color:#111111;">{_e(label)}</span></a></td>'
        )
    if not cells:
        return ""
    width = f"{100 // len(cells)}%"
    cells = [c.replace('<td align="center"', f'<td width="{width}" align="center"', 1) for c in cells]
    return (
        '<tr><td style="padding:34px 20px 22px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="background:#ffffff;border-radius:10px;border-collapse:separate;">'
        f'<tr>{"".join(cells)}</tr></table></td></tr>'
    )


def render(site, data: dict, *, products: list[dict], preheader: str = "", link=None,
           absolute: bool = True, now=None, demo: bool = False) -> str:
    """Return the full email document.

    ``link`` wraps every outbound href (the sender passes a tracked-click
    builder; the preview passes nothing). ``{{unsubscribe_url}}`` is left as a
    placeholder for :mod:`outreach.services.rendering` to fill.
    """
    link = link or (lambda url: url)
    d = clean(data, site)
    brand = (getattr(site, "name", "") or "").strip() or "Our store"
    shop = store_base_url(site)

    hero = d["hero_image"] or (_DEMO_HERO if demo else "")
    if absolute:
        # Sent email: absolute URLs, and JPEG renditions of WebP uploads so
        # classic Outlook shows them too.
        hero = _email_image(hero) if hero else ""
        products = [dict(p, image=_email_image(p["image"]) if p.get("image") else "") for p in products]
    hero_h = 700 if hero else 360

    hero_href = link(_safe_url(d["hero_button_url"], shop))
    parts = []

    # Hero: background photo with a frosted brand badge (VML for Outlook).
    badge = ""
    if d["badge_text"]:
        badge = (
            '<table role="presentation" align="center" cellpadding="0" cellspacing="0" border="0" '
            'style="margin:0 auto;"><tr><td class="ow-badge" style="background:rgba(255,255,255,0.28);'
            "border:1px solid rgba(255,255,255,0.6);border-radius:999px;padding:14px 34px;"
            f'font-family:{_FONT};font-size:28px;font-weight:600;letter-spacing:-0.02em;line-height:1;'
            f'color:#ffffff;">{_e(d["badge_text"])}</td></tr></table>'
        )
    if hero:
        bg_attr = f' background="{_e(hero)}"'
        bg_css = (
            f"background-image:url('{_e(hero)}');background-size:cover;"
            "background-position:center top;background-repeat:no-repeat;"
        )
        vml_open = (
            '<!--[if gte mso 9]><v:rect xmlns:v="urn:schemas-microsoft-com:vml" fill="true" stroke="false" '
            f'style="width:600px;height:{hero_h}px;"><v:fill type="frame" src="{_e(hero)}" color="#d8cdc3" />'
            '<v:textbox inset="0,0,0,0" style="v-text-anchor:middle;"><![endif]-->'
        )
        vml_close = "<!--[if gte mso 9]></v:textbox></v:rect><![endif]-->"
    else:
        # No photo chosen: a calm, shorter colour band behind the badge.
        bg_attr, bg_css, vml_open, vml_close = "", "", "", ""
    parts.append(
        f'<tr><td class="ow-hero"{bg_attr} bgcolor="#d8cdc3" valign="middle" '
        f'style="height:{hero_h}px;background-color:#d8cdc3;{bg_css}">'
        + vml_open
        + f'<a href="{_e(hero_href)}" style="display:block;text-decoration:none;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'<tr><td class="ow-hero-in" height="{hero_h}" align="center" valign="middle" '
        f'style="height:{hero_h}px;padding-top:{60 if hero else 0}px;">{badge}</td></tr>'
        "</table></a>"
        + vml_close
        + "</td></tr>"
    )

    # Headline, intro, button.
    intro = []
    if d["headline"]:
        intro.append(
            f'<h1 class="ow-h1" style="margin:0 auto;max-width:400px;font-family:{_FONT};font-size:30px;line-height:1.25;'
            f'font-weight:400;letter-spacing:-0.01em;color:#111111;">{_e(d["headline"])}</h1>'
        )
    if d["intro_text"]:
        intro.append(
            f'<p style="margin:18px 0 0;font-family:{_FONT};font-size:14px;line-height:1.6;color:#333333;">'
            f'{_multiline(d["intro_text"])}</p>'
        )
    if d["hero_button_label"]:
        intro.append(f'<div style="margin-top:24px;">{_pill(hero_href, d["hero_button_label"])}</div>')
    if intro:
        parts.append(
            '<tr><td align="center" class="ow-pad" style="padding:44px 64px 0;">'
            + "".join(intro)
            + "</td></tr>"
        )

    # Countdown (time left at send), preceded by a hairline.
    count = countdown_text(d["countdown_end"], now=now)
    if count:
        parts.append(
            '<tr><td style="padding:40px 40px 0;"><table role="presentation" width="100%" cellpadding="0" '
            'cellspacing="0" border="0"><tr><td style="border-top:1px solid #e7e7e7;font-size:0;line-height:0;'
            'height:1px;">&nbsp;</td></tr></table></td></tr>'
        )
        label = (
            f'<p style="margin:0;font-family:{_FONT};font-size:13px;line-height:1.4;color:#333333;">'
            f'{_e(d["countdown_label"])}</p>'
            if d["countdown_label"]
            else ""
        )
        parts.append(
            '<tr><td align="center" style="padding:30px 24px 6px;">'
            + label
            + f'<p class="ow-count" style="margin:6px 0 0;font-family:{_FONT};font-size:44px;line-height:1.15;'
            f'font-weight:400;letter-spacing:0.02em;color:#111111;">{count}</p>'
            "</td></tr>"
        )
    elif intro:
        parts.append(
            '<tr><td style="padding:40px 40px 6px;"><table role="presentation" width="100%" cellpadding="0" '
            'cellspacing="0" border="0"><tr><td style="border-top:1px solid #e7e7e7;font-size:0;line-height:0;'
            'height:1px;">&nbsp;</td></tr></table></td></tr>'
        )

    # Product grid.
    grid = _products_block(products, link)
    if grid:
        parts.append(grid)
    else:
        parts.append('<tr><td style="height:24px;font-size:0;line-height:0;">&nbsp;</td></tr>')

    # Offer card.
    if d["offer_title"] or d["offer_code"]:
        offer = []
        if d["offer_title"]:
            offer.append(
                f'<p class="ow-offer-h" style="margin:0;font-family:{_FONT};font-size:26px;line-height:1.2;'
                f'font-weight:500;color:#ffffff;">{_e(d["offer_title"])}</p>'
            )
        if d["offer_text"]:
            offer.append(
                f'<p style="margin:10px 0 0;font-family:{_FONT};font-size:12px;line-height:1.5;color:#d6d6d6;">'
                f'{_multiline(d["offer_text"])}</p>'
            )
        buttons = []
        if d["offer_code"]:
            buttons.append(
                '<td style="padding:0 8px 8px 0;"><span style="display:inline-block;border:1px solid #ffffff;'
                f'border-radius:999px;padding:9px 18px;font-family:{_FONT};font-size:12px;font-weight:500;'
                f'letter-spacing:0.04em;line-height:1.2;color:#ffffff;">{_e(d["offer_code"])}</span></td>'
            )
        if d["offer_button_label"]:
            href = link(_safe_url(d["offer_button_url"], shop))
            buttons.append(
                f'<td style="padding:0 0 8px;">{_pill(href, d["offer_button_label"], dark=False, size=12)}</td>'
            )
        if buttons:
            offer.append(
                '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin-top:22px;">'
                f'<tr>{"".join(buttons)}</tr></table>'
            )
        parts.append(
            '<tr><td style="padding:8px 20px 40px;">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="background:#161616;border-radius:8px;border-collapse:separate;">'
            '<tr><td class="ow-offer" style="padding:30px 28px 26px;">'
            + "".join(offer)
            + "</td></tr></table></td></tr>"
        )

    # Beige band: waitlist text + button, then the highlights card.
    band = []
    if d["waitlist_text"]:
        band.append(
            '<tr><td align="center" class="ow-pad" style="padding:44px 64px 0;">'
            f'<p style="margin:0;font-family:{_FONT};font-size:13px;line-height:1.6;color:#333333;">'
            f'{_multiline(d["waitlist_text"])}</p></td></tr>'
        )
    if d["waitlist_button_label"]:
        href = link(_safe_url(d["waitlist_button_url"], shop))
        band.append(
            f'<tr><td align="center" style="padding:22px 24px 0;">{_pill(href, d["waitlist_button_label"], size=12)}</td></tr>'
        )
    hl = _highlights(d, link, shop, absolute)
    if band and not hl:
        band.append('<tr><td style="height:44px;font-size:0;line-height:0;">&nbsp;</td></tr>')
    if hl:
        band.append(hl)
    if band:
        parts.append(
            '<tr><td style="background:#ece6e0;">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
            + "".join(band)
            + "</table></td></tr>"
        )

    # Footer.
    address = f"©{brand}" + (f", {d['footer_address']}" if d["footer_address"] else "")
    parts.append(
        '<tr><td align="center" style="padding:30px 40px 38px;">'
        f'<p style="margin:0;font-family:{_FONT};font-size:11px;line-height:1.6;color:#666666;">'
        "No longer want to receive these emails? "
        '<a href="{{unsubscribe_url}}" style="color:#666666;text-decoration:underline;">Unsubscribe</a>.</p>'
        f'<p style="margin:4px 0 0;font-family:{_FONT};font-size:11px;line-height:1.6;color:#666666;">'
        f"{_e(address)}</p></td></tr>"
    )

    pre = (
        '<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;'
        f'color:#ffffff;opacity:0;">{_e(preheader)}&#8203;&#847;&#8203;&#847;&#8203;&#847;</div>'
        if preheader
        else ""
    )

    return (
        "<!DOCTYPE html>"
        '<html lang="en" xmlns="http://www.w3.org/1999/xhtml" xmlns:v="urn:schemas-microsoft-com:vml" '
        'xmlns:o="urn:schemas-microsoft-com:office:office"><head>'
        '<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="x-apple-disable-message-reformatting">'
        '<meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light">'
        f"<title>{_e(brand)}</title>"
        '<!--[if !mso]><!--><link rel="stylesheet" '
        'href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&amp;display=swap"><!--<![endif]-->'
        "<!--[if mso]><noscript><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch>"
        "</o:OfficeDocumentSettings></xml></noscript><![endif]-->"
        "<style>"
        "body{margin:0;padding:0;-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;}"
        "table,td{mso-table-lspace:0pt;mso-table-rspace:0pt;}img{-ms-interpolation-mode:bicubic;}"
        "a.ow-pill:hover{opacity:.88;}"
        "@media only screen and (max-width:620px){"
        ".ow-wrap{width:100%!important;}"
        ".ow-hero,.ow-hero-in{height:440px!important;}"
        ".ow-hero-in{padding-top:40px!important;}"
        ".ow-pad{padding-left:28px!important;padding-right:28px!important;}"
        ".ow-h1{font-size:24px!important;}"
        ".ow-count{font-size:36px!important;}"
        ".ow-badge{font-size:22px!important;padding:11px 26px!important;}"
        ".ow-offer{padding:24px 20px 20px!important;}"
        ".ow-offer-h{font-size:22px!important;}"
        ".ow-col{max-width:100%!important;}"
        # Phones: products two per row (a single product keeps the full width).
        ".ow-grid{padding-left:8px!important;padding-right:8px!important;}"
        ".ow-col--279,.ow-col--186{width:50%!important;max-width:50%!important;}"
        ".ow-col--279 .ow-pcell,.ow-col--186 .ow-pcell{padding:10px 6px 18px!important;}"
        ".ow-col--279 .ow-pimg,.ow-col--186 .ow-pimg{width:100%!important;max-width:100%!important;}"
        ".ow-col--279 .ow-ptitle,.ow-col--186 .ow-ptitle{font-size:11px!important;}"
        ".ow-col--279 .ow-pprice,.ow-col--186 .ow-pprice{font-size:11px!important;}"
        ".ow-col--558 .ow-pcell{padding:14px 12px 22px!important;}"
        # Photo-less tiles keep roughly the 4:5 shape at phone widths.
        ".ow-ph--558{height:420px!important;}"
        ".ow-ph--279,.ow-ph--186{height:210px!important;}"
        "}"
        # Only the narrowest phones fall back to one product per row.
        "@media only screen and (max-width:300px){"
        ".ow-col--279,.ow-col--186{width:100%!important;max-width:100%!important;}"
        ".ow-col--279 .ow-pimg,.ow-col--186 .ow-pimg{width:75%!important;}"
        "}"
        "</style></head>"
        '<body style="margin:0;padding:0;background:#f3efeb;">'
        + pre
        + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="background:#f3efeb;"><tr><td align="center" style="padding:0;">'
        '<!--[if mso]><table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->'
        # table-layout:fixed: the email's width is the table's own (600px, or
        # the phone's width), never its content's, so no block inside it can
        # ever stretch the hero, offer card and highlights past the screen.
        '<table role="presentation" class="ow-wrap" width="600" cellpadding="0" cellspacing="0" border="0" '
        'style="width:600px;max-width:600px;table-layout:fixed;background:#ffffff;">'
        + "".join(parts)
        + "</table>"
        "<!--[if mso]></td></tr></table><![endif]-->"
        "</td></tr></table>"
        "{{tracking_pixel}}"
        "</body></html>"
    )
