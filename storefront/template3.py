"""Store data for Template 3 (the owner-facing "Template 2") and Template 4
(the owner-facing "Template 3", Eurus look), which share this feed.

Template 3 is a JavaScript-rendered design: every section reads one global
object, ``window.T3``. In the static preview that object came from the demo
file ``assets/js/data.js``; on a live store it is built here from the store's
own catalogue and content blob and printed into the page as JSON, so the design
renders unchanged while every segment shows the owner's data.

The shape mirrors ``data.js`` (``brand``, ``categories``, ``products``,
``heroSlides``, ``reels``, ``reviews``, ``blogs``, ``faqs``, ``why`` …) plus the
pieces a live store needs that the preview faked: real URLs, the cart/wishlist
endpoints, the CSRF token, contact details, policies and per-page data.

Images an owner has not uploaded yet fall back to the design's own reference
photography (the ``IMG`` ids in ``assets/js/boot.js``), never to an empty box.
"""

from __future__ import annotations

import re
from decimal import Decimal

from django.middleware.csrf import get_token
from django.urls import NoReverseMatch, reverse
from django.utils.html import strip_tags
from django.utils.text import Truncator

from commerce.models import Category, Product, display_ordered_categories

# Bound the product feed every page carries (menus, search, sliders, reels).
FEED_LIMIT = 60

# A colour variation's value → a CSS colour for its swatch. Unknown names fall
# back to the value itself when it is a CSS colour, else a neutral grey.
_COLOUR_WORDS = {
    "black": "#111", "white": "#f4f4f2", "off white": "#f1ede4", "cream": "#ece3d2",
    "ivory": "#f3eedf", "beige": "#d9c7a7", "sage": "#9aab8c", "navy": "#1f2b4a",
    "navy blue": "#1f2b4a", "rust": "#b5562f", "red": "#c4301c", "maroon": "#6d1d22",
    "pink": "#f3b7c2", "blush": "#efc4c4", "blue": "#7da2c9", "sky": "#7da2c9",
    "sky blue": "#7da2c9", "grey": "#9a9a9a", "gray": "#9a9a9a", "charcoal": "#3b3b3b",
    "olive": "#6d7246", "tan": "#c49a6c", "brown": "#6b4a33", "denim": "#46607e",
    "indigo": "#46607e", "yellow": "#e9c53a", "mustard": "#d4a52a", "green": "#2f7a4f",
    "bottle green": "#1f4d36", "mint": "#bfe3cf", "teal": "#2f8f8a", "purple": "#6e4a9e",
    "lavender": "#c8b8e8", "lilac": "#c8b8e8", "orange": "#e5792b", "peach": "#f2b99a",
    "gold": "#c9a227", "silver": "#c0c0c0", "khaki": "#b8a77a", "wine": "#5e1b2c",
    "multicolor": "linear-gradient(135deg,#e9c53a,#c4301c,#7da2c9)",
    "multicolour": "linear-gradient(135deg,#e9c53a,#c4301c,#7da2c9)",
}
_COLOUR_TYPE = re.compile(r"colou?r|shade|tone", re.I)
_HEX = re.compile(r"^#(?:[0-9a-f]{3}|[0-9a-f]{6})$", re.I)

# Font Awesome icon names the editor stores for "Why choose us" → the
# design's own line icons.
_WHY_ICONS = {
    "award": "shield", "certificate": "shield", "shield": "shield", "lock": "shield",
    "truck": "truck", "shipping": "truck", "box": "box", "gift": "box",
    "headset": "chat", "comment": "chat", "comments": "chat", "phone": "chat",
    "undo": "return", "rotate": "return", "exchange": "return", "return": "return",
    "leaf": "leaf", "seedling": "leaf", "recycle": "leaf", "heart": "heart",
    "star": "heart", "thumbs": "heart", "credit": "card", "wallet": "card",
    "money": "card", "rupee": "card",
}


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _url(name: str, **kwargs) -> str:
    try:
        return reverse(name, kwargs=kwargs or None)
    except NoReverseMatch:
        return ""


def _num(value):
    if value in (None, ""):
        return None
    try:
        return float(Decimal(str(value)))
    except Exception:
        return None


def _text(value, limit: int | None = None) -> str:
    out = " ".join(strip_tags(str(value or "")).split())
    if limit and len(out) > limit:
        out = Truncator(out).chars(limit)
    return out


def _get(obj, key, default=""):
    try:
        v = obj.get(key) if hasattr(obj, "get") else getattr(obj, key, default)
    except Exception:
        return default
    return default if v is None else v


def _media(value) -> str:
    """A stored media path → a servable URL (as ``absolute_media_url`` does)."""
    from .templatetags.website_filters import absolute_media_url

    return absolute_media_url(str(value or "").strip()) if value else ""


def _href(value, base: str) -> str:
    """An owner-entered link → a URL on this storefront (dev prefix aware)."""
    v = str(value or "").strip()
    if not v:
        return ""
    lv = v.lower()
    if lv.startswith(("http://", "https://", "mailto:", "tel:", "//", "#")):
        return v
    b = base if base.endswith("/") else base + "/"
    if v.startswith(b):
        return v
    return b + v.lstrip("/")


def _swatch(value: str) -> str:
    v = (value or "").strip()
    if _HEX.match(v):
        return v
    return _COLOUR_WORDS.get(v.casefold(), "") or (v if re.fullmatch(r"[a-zA-Z]{3,20}", v) else "#bdbdbd")


def _swatch_for(product, value: str) -> str:
    """The owner's picked swatch for a colour value (Variations step), else a
    colour derived from its name, as the other themes' product page does."""
    from .templatetags.website_filters import variant_swatch

    try:
        picked = str(variant_swatch(getattr(product, "variant_combinations", None), value) or "")
    except Exception:
        picked = ""
    return picked if _HEX.match(picked) else _swatch(value)


def _list(value) -> list:
    if hasattr(value, "_data"):
        value = value._data  # safe.wrap() containers
    return value if isinstance(value, list) else []


def _raw(value):
    """Unwrap a :mod:`storefront.safe` container to its plain data."""
    for attr in ("_data", "_d", "data"):
        inner = getattr(value, attr, None)
        if isinstance(inner, (dict, list)):
            return inner
    return value


# --------------------------------------------------------------------------
# Products
# --------------------------------------------------------------------------


def _variant_types(product) -> list[dict]:
    from .context import normalize_variant_types

    return [
        {"name": vt["name"], "values": [v["value"] for v in vt["values"]]}
        for vt in normalize_variant_types(product.variants or [])
        if vt["name"] and vt["values"]
    ]


def _option_rows(product) -> tuple[list[dict], list[dict]]:
    """The product's variation types split as (colour rows, other rows), each
    ``{"name", "values", "out"}`` where ``out`` lists values that are sold out
    in every combination carrying them."""
    from commerce.cart import attrs_of, combos_of
    from commerce.variant_profiles import combo_units

    combos = combos_of(product)
    colours, others = [], []
    for vt in _variant_types(product):
        out = []
        if combos:
            for val in vt["values"]:
                carrying = [
                    c for c in combos
                    if any(
                        a["name"].casefold() == vt["name"].casefold()
                        and a["value"].casefold() == val.casefold()
                        for a in attrs_of(c)
                    )
                ]
                if carrying and all(combo_units(c) <= 0 for c in carrying):
                    out.append(val)
        row = {"name": vt["name"], "values": vt["values"], "out": out}
        (colours if _COLOUR_TYPE.search(vt["name"]) else others).append(row)
    return colours, others


def _needs_pick(product) -> bool:
    """True when the cart refuses this product until a variation is chosen
    (combinations exist and none is marked default), so a listing's quick add
    must open the product page instead."""
    from commerce.cart import combos_of

    combos = combos_of(product)
    return bool(combos) and not any(c.get("default_selected") for c in combos)


def _badges(product) -> list[str]:
    out = []
    if product.is_recently_launched:
        out.append("new")
    if product.mrp and product.price and product.mrp > product.price:
        out.append("sale")
    if product.is_featured:
        out.append("hot")
    if product.is_best_seller:
        out.append("popular")
    return out


def _ratings(site, product_ids) -> dict:
    """``{product_id: (avg, count)}`` for approved reviews, one query."""
    from django.db.models import Avg, Count

    from commerce.models import Review

    if not product_ids:
        return {}
    rows = (
        Review.objects.for_site(site)
        .filter(product_id__in=list(product_ids), approved=True, is_deleted=False)
        .order_by()
        .values("product_id")
        .annotate(a=Avg("rating"), n=Count("id"))
    )
    return {str(r["product_id"]): (round(float(r["a"] or 0), 1), int(r["n"])) for r in rows}


def product_json(proxy, ratings: dict | None = None, cats: dict | None = None) -> dict:
    """One product in the shape ``T3.card()`` and the listing filters read."""
    p = proxy._p
    imgs = [str(getattr(i, "url", "") or "") for i in (proxy.gallery or [])]
    imgs = [u for u in imgs if u] or [""]
    colours, others = _option_rows(p)
    size_row = others[0] if others else None
    colour_row = colours[0] if colours else None
    avg, count = (ratings or {}).get(str(p.pk), (0, 0))
    price = _num(p.price) or 0
    mrp = _num(p.mrp)
    cat = (cats or {}).get(str(p.category_id or ""))
    return {
        "id": str(p.pk),
        "slug": p.slug,
        "url": proxy.url,
        "name": p.title,
        "price": price,
        "mrp": mrp if (mrp and mrp > price) else None,
        "img": imgs,
        "badges": _badges(p),
        "rating": avg,
        "reviews": count,
        "sizes": size_row["values"] if size_row else [],
        "sizeLabel": size_row["name"] if size_row else "",
        "out": size_row["out"] if size_row else [],
        "colors": [_swatch_for(p, v) for v in colour_row["values"]] if colour_row else [],
        "colorNames": colour_row["values"] if colour_row else [],
        "colorLabel": colour_row["name"] if colour_row else "",
        "cat": cat[0] if cat else "",
        "catName": cat[1] if cat else "",
        "inStock": bool(proxy.in_stock),
        "needsPick": _needs_pick(p),
        "created": p.created_at.timestamp() if getattr(p, "created_at", None) else 0,
        "brand": p.brand or "",
    }


def products_json(site, request, items) -> list[dict]:
    """``items`` (Products or ProductProxies) → product JSON, ratings batched."""
    from .context import ProductProxy

    proxies = [i if isinstance(i, ProductProxy) else ProductProxy(i, request, site) for i in items]
    ratings = _ratings(site, [px._p.pk for px in proxies])
    cats = {
        str(r["id"]): (r["slug"], r["name"])
        for r in Category.objects.for_site(site)
        .filter(is_deleted=False)
        .order_by()
        .values("id", "slug", "name")
    }
    return [product_json(px, ratings, cats) for px in proxies]


def _best_sellers(request, site, content) -> list:
    """Every live best seller, in the order the owner set on the dashboard
    Products page (Best Sellers > Reorder, ``best_sellers_order``); any not
    in that list trail, newest first. The same rule Template 1 uses
    (``storefront.context.home_extra``)."""
    from .scope import scope_products

    qs = scope_products(
        Product.objects.for_site(site)
        .filter(is_active=True, is_deleted=False, is_best_seller=True)
        .prefetch_related("images")
        .order_by("-created_at"),
        request,
    )
    saved = _raw(content.get("best_sellers_order"))
    saved = saved if isinstance(saved, list) else []
    rank = {str(pid): i for i, pid in enumerate(saved)}
    best = list(qs)
    best.sort(key=lambda p: rank.get(str(p.pk), len(rank)))  # stable: ties stay newest first
    return best


def _feed_products(request, site) -> list:
    from .scope import scope_products

    qs = scope_products(
        Product.objects.for_site(site)
        .filter(is_active=True, is_deleted=False)
        .prefetch_related("images")
        .order_by("-created_at"),
        request,
    )
    return list(qs[:FEED_LIMIT])


# --------------------------------------------------------------------------
# Content sections
# --------------------------------------------------------------------------


def _categories_json(request, site, categories) -> list[dict]:
    ids = [c.pk for c in categories]
    members: dict[str, set] = {}
    if ids:
        live = Product.objects.for_site(site).filter(is_active=True, is_deleted=False).order_by()
        # A product belongs to its primary category and to every extra one it
        # is tagged into (the same rule the category page uses).
        for pid, cid in live.filter(category_id__in=ids).values_list("id", "category_id"):
            members.setdefault(str(cid), set()).add(pid)
        for pid, cid in live.filter(categories__id__in=ids).values_list("id", "categories__id"):
            members.setdefault(str(cid), set()).add(pid)
    counts = {k: len(v) for k, v in members.items()}
    out = []
    for i, c in enumerate(categories):
        img = c.image.url if getattr(c, "image", None) else ""
        out.append({
            "id": str(c.pk),
            "slug": c.slug,
            "name": c.name,
            "count": counts.get(str(c.pk), 0),
            "img": img or f"@cat{i % 9}",
            "desc": _text(c.description, 90),
            "icon": "",
            "url": _url("public_category_detail", category_slug=c.slug),
            "featured": bool(getattr(c, "show_on_homepage", False)),
        })
    return out


def _category_menu(content) -> dict:
    """The two categories the owner featured on the dashboard Categories page
    (``d2c_config.t3_featured_category_ids``), shown on top of the Template 2
    Categories page and as the Shop mega menu's cards. The mega-menu toggle
    and star are Template 1 settings and are not read here. Ids are strings;
    the pages match them against ``categories[].id`` so a deleted or hidden
    pick is just skipped."""
    cfg = _raw(content.get("d2c_config"))
    cfg = cfg if isinstance(cfg, dict) else {}
    pair = cfg.get("t3_featured_category_ids")
    pair = [str(x).strip() for x in pair if str(x or "").strip()] if isinstance(pair, list) else []
    return {"featuredPair": pair[:2]}


def _hex(value) -> str:
    """An owner colour as ``#rrggbb``, or "" when blank or not a colour (so a
    typo can never inject CSS into the inline styles the hero builds)."""
    from .hero_colors import parse_hex

    rgb = parse_hex(value)
    return "#%02x%02x%02x" % rgb if rgb else ""


def _hero_json(content, base) -> list[dict]:
    """Every field of each Edit Content hero banner, as the Template 2 hero
    renders it: image or video (with its mobile twin), title, description,
    overlay, text colours and the button's text, link and colours."""
    from .templatetags.website_filters import banner_overlay_opacity

    slides = []
    palette = ["#d9b27a", "#3fa59a", "#e7b9b2"]
    for i, b in enumerate(_list(_raw(content.get("hero_banners")))):
        if not isinstance(b, dict):
            continue
        img = str(b.get("image") or "").strip()
        mob = str(b.get("mobile_image") or "").strip()
        if not mob and not b.get("mobile_image_removed"):
            mob = img
        video = mobile_video = ""
        if str(b.get("media_type") or "image") == "video":
            video = str(b.get("video") or "").strip()
            mobile_video = str(b.get("mobile_video") or "").strip()
            if not mobile_video and not b.get("mobile_video_removed"):
                mobile_video = video
        title = _text(b.get("title"))
        desc = _text(b.get("description"), 300)
        if not (title or desc or img or video):
            continue
        slides.append({
            "eyebrow": _text(b.get("subtitle") or b.get("eyebrow") or b.get("tag"), 80),
            "title": title,
            "text": desc,
            "cta": _text(b.get("button_text"), 40),
            "url": _href(b.get("button_url"), base) or _href("/shop/", base),
            # The owner removed the image: no reference photo either.
            "img": _media(img) if img else ("" if b.get("image_removed") else f"@hero{i % 3}"),
            "mobileImg": _media(mob) if mob else "",
            "video": _media(video) if video else "",
            "mobileVideo": _media(mobile_video) if mobile_video else "",
            "bg": _hex(b.get("bg_color")) or palette[i % 3],
            "pos": "30%",
            "alt": _text(b.get("alt_text") or title, 120),
            "titleColor": _hex(b.get("title_color")),
            "textColor": _hex(b.get("description_color")),
            "btnColor": _hex(b.get("button_text_color")),
            "btnBg": _hex(b.get("button_bg_color")),
            "overlayColor": _hex(b.get("overlay_color")) or "#000000",
            # 0–10 slider → CSS opacity, exactly as the other themes read it
            # (a real 0 means no tint at all).
            "overlay": banner_overlay_opacity(b.get("overlay_opacity")),
        })
    return slides


def _about_home(content, base: str, urls: dict) -> dict:
    """The homepage About block from Edit Content → Home sections → About:
    subheading as the small label, heading as the title, body (line breaks
    kept), button label + link and the image."""
    body = strip_tags(str(content.get("about_section_content") or content.get("about_content") or ""))
    lines = [" ".join(line.split()) for line in body.replace("\r", "").split("\n")]
    text = "\n".join(lines).strip()
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    legacy_btn = _raw(content.get("about_button"))
    legacy_btn = legacy_btn if isinstance(legacy_btn, dict) else {}
    return {
        "aboutLabel": _text(content.get("about_section_subtitle"), 60),
        "aboutTitle": _text(content.get("about_section_title") or content.get("about_title"), 140),
        "aboutText": Truncator(text).chars(900) if len(text) > 900 else text,
        "aboutImage": "" if content.get("about_image_removed") else _media(content.get("about_image") or ""),
        # The owner removed the image with the editor's trash icon: show no
        # reference photo in its place either.
        "aboutImageRemoved": bool(content.get("about_image_removed")),
        "aboutBtnText": _text(content.get("about_button_text") or legacy_btn.get("label"), 40) or "Learn More",
        "aboutBtnUrl": _href(content.get("about_button_url") or legacy_btn.get("url"), base) or urls.get("about") or "",
    }


def _reels_json(content, products) -> list[dict]:
    """Owner reels (Edit Content → Videos) or the bundled demo reels."""
    from .demo_media import demo_reel_videos

    section = _raw(content.get("video_section"))
    videos = _list(_raw(section.get("videos"))) if isinstance(section, dict) else []
    out = []
    for i, v in enumerate(videos):
        if not isinstance(v, dict):
            continue
        url = _media(v.get("url") or v.get("video_url") or "")
        if not url:
            continue
        pid = str(v.get("product_id") or v.get("product") or "")
        out.append({
            "video": url,
            "img": _media(v.get("thumbnail") or v.get("poster") or "") or f"@reel{i % 7}",
            "views": _text(v.get("views") or v.get("title"), 24),
            "product": pid if any(p["id"] == pid for p in products) else "",
        })
    if out or content.get("reels_removed"):
        return out
    demo = demo_reel_videos()
    return [
        {
            "video": url,
            "img": f"@reel{i % 7}",
            "views": "",
            "product": products[i]["id"] if i < len(products) else "",
        }
        for i, url in enumerate(demo)
    ]


def _testimonials_json(content) -> list[dict]:
    out = []
    for i, t in enumerate(_list(_raw(content.get("testimonials")))):
        if not isinstance(t, dict):
            continue
        text = _text(t.get("content") or t.get("text") or t.get("review"), 400)
        if not text:
            continue
        try:
            rating = max(1, min(5, int(t.get("rating") or 5)))
        except (TypeError, ValueError):
            rating = 5
        out.append({
            "title": _text(t.get("title"), 80),
            "text": text,
            "name": _text(t.get("name"), 60) or "Verified buyer",
            "city": _text(t.get("location") or t.get("city") or t.get("role"), 60),
            "avatar": _media(t.get("image") or t.get("avatar") or "") or f"@avatar{i % 5}",
            "rating": rating,
            "product": "",
        })
    return out


def _blog_date(raw) -> str:
    from .context import BlogProxy

    d = BlogProxy({"date": raw}).created_at
    return d.strftime("%b %d, %Y") if d else _text(raw, 30)


def blog_json(post: dict, i: int = 0) -> dict:
    # The editor stores the article HTML under ``description``.
    body = str(post.get("description") or post.get("content") or post.get("body") or "")
    words = len(_text(body).split())
    excerpt = _text(post.get("excerpt") or post.get("summary") or post.get("meta_description"), 180)
    if not excerpt:
        excerpt = _text(body, 160)
    slug = str(post.get("slug") or "").strip()
    return {
        "slug": slug,
        "title": _text(post.get("title"), 160) or "Untitled",
        "tag": _text(post.get("category") or post.get("tag"), 40) or "Journal",
        "date": _blog_date(post.get("date")),
        "read": f"{max(1, round(words / 200))} min read",
        "img": _media(post.get("image") or "") or f"@blog{i % 6}",
        "excerpt": excerpt,
        "author": _text(post.get("author"), 60),
        "url": _url("public_blog_detail", blog_slug=slug) if slug else _url("public_blogs"),
    }


def _blogs_json(content) -> list[dict]:
    def has_content(p) -> bool:
        # Template 1's homepage lists every published post; skip only one with
        # nothing to show at all (no title, image, excerpt or body).
        return any(
            str(p.get(k) or "").strip()
            for k in ("title", "slug", "image", "excerpt", "description", "content")
        )

    posts = [
        p for p in _list(_raw(content.get("blog_posts")))
        # One saved without a slug links to the blog listing instead of its
        # own page (an untitled post gets no slug from the editor).
        if isinstance(p, dict) and (p.get("status") or "published") != "draft" and has_content(p)
    ]
    return [blog_json(p, i) for i, p in enumerate(posts)]


def _faqs_json(items) -> list[dict]:
    out = []
    for f in _list(_raw(items)):
        if not isinstance(f, dict):
            continue
        q = _text(f.get("question") or f.get("q"), 240)
        a = str(f.get("answer") or f.get("a") or "").strip()
        if q and a:
            out.append({"q": q, "a": a})
    return out


def _why_json(content) -> list[dict]:
    out = []
    for w in _list(_raw(content.get("why_choose_us"))):
        if not isinstance(w, dict) or not (w.get("title") or w.get("description")):
            continue
        icon_raw = str(w.get("icon") or "").lower().replace("fa-", "").replace("fas ", "").strip()
        icon = next((v for k, v in _WHY_ICONS.items() if k in icon_raw), "heart")
        out.append({
            "title": _text(w.get("title"), 60),
            "text": _text(w.get("description") or w.get("text"), 200),
            "icon": icon,
        })
    return out


def _socials(content) -> dict:
    seo = _raw(content.get("seo"))
    links = _raw(seo.get("social_links")) if isinstance(seo, dict) else None
    if not isinstance(links, dict):
        links = _raw(content.get("social_links"))
    links = links if isinstance(links, dict) else {}
    out = {}
    for key in ("instagram", "facebook", "x", "twitter", "youtube", "pinterest", "linkedin"):
        v = str(links.get(key) or "").strip()
        if v:
            out["x" if key == "twitter" else key] = v
    return out


def _contact(site, content) -> dict:
    info = _raw(content.get("contact_info"))
    info = info if isinstance(info, dict) else {}

    def g(*keys):
        for k in keys:
            v = str(info.get(k) or "").strip()
            if v:
                return v
        return ""

    addr = ", ".join(x for x in [g("address"), g("city"), g("state"), g("postal_code")] if x)
    days = " to ".join(x for x in [g("office_start_day"), g("office_end_day")] if x)
    times = " to ".join(x for x in [g("office_start_time"), g("office_end_time")] if x)
    wa = re.sub(r"\D", "", g("whatsapp_number"))
    return {
        "email": g("contact_email", "email") or (site.support_email or ""),
        "phone": g("mobile_number", "phone") or (site.support_phone or ""),
        "whatsapp": g("whatsapp_number"),
        "whatsappUrl": f"https://wa.me/{wa}" if wa else "",
        "address": addr,
        "hours": ", ".join(x for x in [days, times] if x),
        "holidays": g("office_holidays"),
        "ceoName": g("ceo_name"),
        "ceoRole": g("ceo_designation"),
        # Edit Content > Contact > Contact person photo ("" when none was
        # uploaded or the owner removed it; the page then shows initials).
        "ceoPhoto": "" if info.get("ceo_profile_picture_removed") else _media(g("ceo_profile_picture")),
        "ceoMessage": g("ceo_message"),
        "map": g("map_location"),
        "grievance": _grievance(content),
    }


def _grievance(content) -> dict:
    """The Contact tab's grievance officer, availability and helpline."""
    from .templatetags.website_filters import schedule_groups

    go = _raw(content.get("grievance_officer"))
    go = go if isinstance(go, dict) else {}
    hours = schedule_groups(_raw(content.get("grievance_availability")) or [])
    return {
        "name": _text(go.get("name"), 80),
        "email": _text(go.get("email"), 120),
        "phone": _text(go.get("mobile") or go.get("phone"), 30),
        "hours": "; ".join(f"{h['days']} {h['hours']}" for h in hours),
        "helpline": _text(content.get("national_consumer_helpline"), 40),
    }


def _announcements(content, base) -> list[dict]:
    from .announcement import bar_context

    ctx = bar_context(_raw(content) if isinstance(_raw(content), dict) else {}, base)
    return [
        {"before": s["before"], "word": s["word"], "after": s["after"], "href": s["href"]}
        for s in ctx["items"]
        if (s["before"] or s["word"] or s["after"]).strip()
    ]


def _announce_mode(content) -> str:
    from .announcement import clean_mode

    raw = _raw(content)
    return clean_mode(raw.get("announcement_mode") if isinstance(raw, dict) else "")


# Edit Content → Brand → Logo sizes: the units the editor offers, and the
# widths Template 1 renders when a width is left blank (the editor prefills the
# same numbers, see commerce.views.editor).
_LOGO_UNITS = {"px", "%", "em", "rem", "vw"}
_LOGO_DEFAULT_WIDTHS = {"desktop": 140, "mobile": 100, "footer": 120}


def _logo_width(content, key: str, default: int) -> str:
    """``<number><unit>`` for one logo-size pair, or the theme default when the
    width is blank, not a positive number, or the unit is not one the editor
    offers (so nothing but a clean length ever reaches the page's CSS)."""
    try:
        n = float(str(content.get(f"{key}_max_width") or "").strip())
    except ValueError:
        n = 0
    if not (0 < n <= 4000):
        return f"{default}px"
    unit = str(content.get(f"{key}_max_width_unit") or "px").strip()
    if unit not in _LOGO_UNITS:
        unit = "px"
    return f"{n:g}{unit}"


def _logo(content) -> dict:
    """Edit Content → Brand: each logo (header desktop / phone, footer), its alt
    text and the owner's width for each, as Template 1's navigation and footer
    use them. The phone header falls back to the desktop logo; the footer logo
    shows only when the owner uploaded one."""
    return {
        "desktop": _media(content.get("desktop_logo") or ""),
        "mobile": _media(content.get("mobile_logo") or content.get("desktop_logo") or ""),
        "footer": _media(content.get("footer_logomark") or ""),
        "alt": _text(content.get("desktop_logo_alt"), 120),
        "sizes": {
            "desktop": _logo_width(content, "desktop_logo", _LOGO_DEFAULT_WIDTHS["desktop"]),
            "mobile": _logo_width(content, "mobile_logo", _LOGO_DEFAULT_WIDTHS["mobile"]),
            "footer": _logo_width(content, "footer_logomark", _LOGO_DEFAULT_WIDTHS["footer"]),
        },
    }


def _newsletter(site, request) -> dict | None:
    try:
        from .newsletter_popup import config

        cfg = config(site, request)
    except Exception:
        cfg = None
    if not cfg:
        return None
    return {
        "heading": cfg.get("heading") or "",
        "text": cfg.get("text") or "",
        "button": cfg.get("button") or "Subscribe",
        "success": cfg.get("success") or "Thanks for subscribing!",
        "image": _media(cfg.get("image") or ""),
        "mobileImage": _media(cfg.get("mobile_image") or ""),
        "delay": int(cfg.get("delay_ms") or 6000),
        "offer": cfg.get("offer") or "",
        "version": cfg.get("version") or "",
    }


def _urls(site) -> dict:
    base = _url("public_website") or "/"
    return {
        "base": base,
        "home": base,
        "shop": _url("public_all_products"),
        "categories": _url("public_categories"),
        "about": _url("public_about"),
        "contact": _url("public_enquiry"),
        "blogs": _url("public_blogs"),
        "account": _url("public_user_account"),
        "orders": _url("public_user_orders"),
        "cart": _url("public_cart"),
        "checkout": _url("public_checkout"),
        "cartData": _url("public_cart_data"),
        "cartUpdate": _url("public_cart_update_body"),
        "cartAdd": base + "cart/add/",
        "wishAdd": base + "wishlist/add/",
        "wishRemove": base + "wishlist/remove/",
        "newsletter": _url("subscribe_newsletter"),
        "coupon": _url("public_cart_coupon"),
        "pincode": _url("public_pincode_serviceability"),
        "api": base + "api/product/",
        "certificates": _url("public_certificates"),
        "corePages": _url("public_core_pages"),
        "policy": {
            "privacy": _url("privacy_policy_page"),
            "terms": _url("public_terms"),
            "refund": _url("public_refund"),
            "shipping": _url("public_shipping"),
        },
    }


def _wishlist(request, site) -> list[str]:
    from .customer_accounts import current_customer_email

    try:
        email = current_customer_email(request)
    except Exception:
        email = ""
    if not email:
        return []
    from commerce.models import WishlistItem

    return [
        str(pid)
        for pid in WishlistItem.objects.for_site(site)
        .filter(customer_email=email)
        .order_by()
        .values_list("product_id", flat=True)
    ]


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def build(request, site, merged: dict, ctx: dict) -> dict:
    """The ``window.T3`` object for this request (see the module docstring)."""
    from .scope import scope_categories

    content = merged
    urls = _urls(site)
    base = urls["base"]

    feed = _feed_products(request, site)
    # Best sellers always ride along, even when older than the newest
    # FEED_LIMIT products, so the home slider never loses one.
    best = _best_sellers(request, site, content)
    have = {p.pk for p in feed}
    feed += [p for p in best if p.pk not in have]
    products = products_json(site, request, feed)

    categories = list(
        display_ordered_categories(
            scope_categories(
                Category.objects.for_site(site).filter(is_active=True, is_deleted=False),
                request,
            )
        )
    )
    brand = str(content.get("site_name") or site.name or "Store").strip()
    from .policies import custom_policies

    show_price = bool(ctx.get("show_price_in_lists", content.get("show_price_in_lists", site.is_d2c)))
    data = {
        "brand": brand,
        "tagline": _text(site.tagline or content.get("footer_description"), 160),
        "logo": _logo(content),
        "currency": site.currency or "INR",
        "isB2B": bool(site.is_b2b),
        "isD2C": bool(site.is_d2c),
        "showPrice": show_price,
        # Products page "Swap product image on hover" toggle.
        "hoverSwap": bool(ctx.get("enable_product_hover_swap", content.get("enable_product_hover_swap", False))),
        "freeShip": _num(content.get("free_shipping_threshold")) or 0,
        "csrf": get_token(request),
        "signedIn": bool(ctx.get("customer_email")),
        "urls": urls,
        "announce": _announcements(content, base),
        # Edit Content > Brand > announcement bar: "static" (statement 1, no
        # motion) or "dynamic" (left-to-right marquee of every statement).
        "announceMode": _announce_mode(content),
        "categories": _categories_json(request, site, categories),
        **_category_menu(content),
        # Owner's best-seller ranking (product ids, rank 1 first): the home
        # slider shows the first 8, and "Best selling" sorts by it.
        "bestOrder": [str(p.pk) for p in best],
        "products": products,
        "heroSlides": _hero_json(content, base),
        "reels": _reels_json(content, products),
        "reviews": _testimonials_json(content),
        "reviewsTitle": _text(content.get("testimonials_title"), 80),
        "reviewsSubtitle": _text(content.get("testimonials_subtitle"), 160),
        "blogs": _blogs_json(content),
        "faqs": _faqs_json(content.get("faqs")),
        "contactFaqs": _faqs_json(content.get("contact_faqs")),
        "why": _why_json(content),
        "whySubtitle": _text(content.get("why_choose_us_subtitle"), 120),
        "socials": _socials(content),
        "contact": _contact(site, content),
        "footerText": _text(content.get("footer_description"), 240),
        "newsletter": _newsletter(site, request),
        "wish": _wishlist(request, site),
        "corePages": [
            {"title": p.get("title"), "url": _url("public_core_page", page_slug=p.get("slug"))}
            for p in ctx.get("core_nav_pages") or []
        ],
        "customPolicies": [
            {"title": cp["title"], "url": _url("public_policy_page", policy_slug=cp["slug"])}
            for cp in custom_policies(content)
        ],
        "hasCertificates": bool(ctx.get("has_certificates")),
        "home": {
            # Edit Content > Home > "Category section heading" / "subtext".
            "categoriesTitle": _text(content.get("category_section_heading") or content.get("categories_title"), 80),
            "categoriesSubtitle": _text(content.get("category_section_subheading"), 200),
            "bestTitle": _text(content.get("top_seller_title"), 80),
            "bestSubtitle": _text(content.get("best_sellers_subtitle") or content.get("top_seller_subtitle"), 160),
            "reelsTitle": _text(_get(_raw(content.get("video_section")) or {}, "title"), 80),
            # Where the owner placed the videos (Edit Content): directly below
            # Best Sellers, or at the bottom after the FAQs (the default), the
            # same two places the other themes use.
            "reelsPosition": _text(_get(_raw(content.get("video_section")) or {}, "position"), 40) or "bottom",
            **_about_home(content, base, urls),
            "blogTitle": _text(content.get("blog_title"), 80),
            "blogSubtitle": _text(content.get("blog_subtitle"), 160),
            "faqTitle": _text(content.get("faq_title"), 80),
            "faqSubtitle": _text(content.get("faq_subtitle") or content.get("faqs_subtitle"), 200),
            "totalProducts": len(products),
        },
    }
    data["page"] = page_data(request, site, content, ctx, data)
    return data


# --------------------------------------------------------------------------
# Per-page data (``T3.page``)
# --------------------------------------------------------------------------

_POLICY_ROUTES = {
    "privacy_policy_page": ("privacy", "Privacy Policy", "privacy_policy"),
    "public_terms": ("terms", "Terms & Conditions", "terms_conditions"),
    "public_refund": ("refund", "Cancellation & Refund Policy", "refund_policy"),
    "public_shipping": ("shipping", "Shipping Policy", "shipping_policy"),
}


# --------------------------------------------------------------------------
# Product page details: everything the other themes' product page shows
# (storefront/_pdp_detail_panels.html, _pdp_badges.html, _pdp_bullets.html,
# _pdp_features.html), for the product and for each variation profile.
# --------------------------------------------------------------------------

# Built-in badge names → the design's icon keys (mirrors _pdp_badges.html).
def _badge_icon(name: str, label: str) -> str:
    low = (name or "").lower()
    if "delivery" in low and "fast" in low:
        return "bolt"
    if "delivery" in low or "shipping" in low:
        return "truck"
    if "warranty" in low:
        return "shield"
    if label == "Non-Returnable":
        return "ban"
    if name == "Cash On Delivery":
        return "rupee"
    if name == "Secure Payment":
        return "lock"
    if "return" in low or "replace" in low:
        return "return"
    if name == "Secure Packaging":
        return "box"
    return "check"


def _badge_maps(site) -> dict:
    """Admin badge images and the store's own badge icons, fetched once."""
    from .templatetags.website_filters import trust_badge_icons, trust_badges_map

    return {"img": trust_badges_map() or {}, "svg": trust_badge_icons(site) or {}}


def _trust_badges(view, maps, refund_url: str) -> list[dict]:
    from .templatetags.website_filters import (
        is_return_badge,
        return_policy_badge,
        trust_badge_label,
    )

    out = []
    rb = return_policy_badge(view)
    if rb:
        out.append({
            "label": rb["label"], "title": rb["title"], "icon": "replacement" if rb["kind"] == "replacement" else "return",
            "img": "", "svg": "", "url": refund_url,
        })
    for name in _list(_raw(getattr(view, "trusted_icons", None))):
        name = str(name or "").strip()
        if not name:
            continue
        label = str(trust_badge_label(name) or name)
        linked = bool(refund_url) and is_return_badge(name)
        out.append({
            "label": label,
            "title": f"{label}. Read the return and refund policy." if linked else label,
            "icon": _badge_icon(name, label),
            "img": str(maps["img"].get(name) or ""),
            "svg": str(maps["svg"].get(name) or ""),
            "url": refund_url if linked else "",
        })
    return out


def _dims(a, b, c, unit) -> str:
    return f"{a} x {b} x {c} {unit or ''}".strip() if a and b and c else ""


def _detail_view(view, details, maps, refund_url: str) -> dict:
    """The product page's content blocks for ``view`` (a ProductProxy, or a
    variation seen through its profile) and its ``d2c_details``."""
    d = _raw(details)
    d = d if isinstance(d, dict) else {}

    def g(name):
        v = getattr(view, name, "")
        v = _raw(v)
        return "" if v is None or v.__class__.__name__ == "_Missing" else v

    def txt(value, limit=600):
        if isinstance(value, (list, tuple)):
            return ", ".join(_text(x, 120) for x in value if _text(x, 120))
        return _text(value, limit)

    support = d.get("support") if isinstance(d.get("support"), dict) else {}
    item = [
        ("Material", txt(d.get("material"))),
        ("Colour", txt(d.get("colour"))),
        ("Recommended Use", txt(d.get("recommended_use"))),
        ("Handling Time", txt(d.get("handling_time"))),
        ("Accessories", txt(d.get("accessories"))),
        ("Includes", txt(d.get("includes"))),
        ("Product Dimensions", _dims(g("product_length"), g("product_breadth"), g("product_height"), g("product_unit"))),
        ("Package Dimensions", _dims(g("package_length"), g("package_breadth"), g("package_height"), g("package_unit"))),
        ("Product Weight", f"{g('weight_value')} {g('weight_unit') or ''}".strip() if g("weight_value") else ""),
        ("Package Weight", f"{g('package_weight_value')} {g('package_weight_unit') or ''}".strip() if g("package_weight_value") else ""),
        ("HSN Code", txt(g("hsn_code"))),
        ("GST", f"{g('gst_percentage')}%" if g("gst_percentage") not in ("", None) else ""),
        ("Return Type", txt(g("return_policy_label"))),
        ("Dangerous Good", txt(g("dangerous_good"))),
    ]
    specs = _raw(getattr(view, "specifications", None))
    for sp in specs if isinstance(specs, list) else []:
        if isinstance(sp, dict) and sp.get("name") and sp.get("value"):
            item.append((_text(sp.get("name"), 80), _text(sp.get("value"), 300)))
    origin = [
        ("Country of Origin", txt(g("country_of_origin"))),
        ("Manufacturer Contact", txt(g("manufacturer_contact"))),
        ("Packer Contact", txt(g("packer_contact"))),
        ("Importer Contact", txt(g("importer_contact"))),
        ("Warranty", txt(g("guarantee_warranty"))),
        ("Customer Support Number", txt(d.get("support_phone") or support.get("phone"))),
        ("Customer Support Email", txt(d.get("support_email") or support.get("email"))),
    ]
    extras = []
    for e in d.get("extras") or []:
        if isinstance(e, dict) and (e.get("value") or e.get("text")):
            name = _text(e.get("name") or e.get("type"), 80)
            extras.append({"label": name[:1].upper() + name[1:] if name else "Details", "value": str(e.get("value") or e.get("text") or "")})
    return {
        "title": str(g("title") or ""),
        "desc": str(g("description") or ""),
        "bullets": [str(b) for b in (d.get("bullets") or []) if str(b).strip()],
        "features": [str(f) for f in (d.get("features") or []) if str(f).strip()],
        "itemDetails": [{"label": k, "value": v} for k, v in item if v],
        "warning": txt(d.get("warning")),
        "origin": [{"label": k, "value": v} for k, v in origin if v],
        "extras": extras,
        "badges": _trust_badges(view, maps, refund_url),
        "returnLabel": txt(g("return_policy_label")),
        "warranty": txt(g("guarantee_warranty"), 400),
        "brand": str(g("brand") or ""),
        "sku": str(g("sku") or ""),
    }


def _variation_profiles(ctx, maps, refund_url: str) -> dict:
    """``{combo key: {...}}`` for every variation with its own details (title,
    description, badges, panels, FAQs, video, category), so picking it swaps
    them in exactly as the other themes' product page does."""
    out = {}
    for vp in ctx.get("variant_profiles") or []:
        key = str(vp.get("key") or "")
        if not key:
            continue
        view = vp.get("product")
        entry = {
            "ownTitle": bool(vp.get("own_title")),
            "detail": _detail_view(view, vp.get("details"), maps, refund_url),
        }
        if vp.get("faqs") is not None:
            entry["faqs"] = [
                {"q": _text(f.get("question"), 240), "a": str(f.get("answer") or "")}
                for f in vp["faqs"] if isinstance(f, dict) and f.get("question") and f.get("answer")
            ]
        video = vp.get("video")
        if video is False:
            entry["video"] = None
        elif video:
            entry["video"] = {"type": video.get("type"), "url": video.get("url"), "embed": video.get("embed_url"), "thumb": video.get("thumb_url"), "orientation": video.get("orientation") or "landscape"}
        cat = vp.get("category")
        if cat is False:
            entry["category"] = None
        elif cat is not None:
            entry["category"] = {"name": cat.name, "url": _url("public_category_detail", category_slug=cat.slug)}
        out[key] = entry
    return out


def _product_detail(request, site, ctx, data) -> dict:
    from commerce.cart import attrs_of, combos_of, variant_key_of
    from commerce.variant_profiles import combo_active, combo_units

    proxy = ctx["product"]
    p = proxy._p
    base = products_json(site, request, [proxy])[0]
    details = p.d2c_details if isinstance(p.d2c_details, dict) else {}

    options = []
    for vt in _variant_types(p):
        is_colour = bool(_COLOUR_TYPE.search(vt["name"]))
        options.append({
            "name": vt["name"],
            "kind": "color" if is_colour else "size",
            "values": [{"value": v, "css": _swatch_for(p, v) if is_colour else ""} for v in vt["values"]],
        })

    combos = []
    for c in combos_of(p):
        imgs = [_media(u) for u in (c.get("images") or []) if isinstance(u, str) and u.strip()]
        combos.append({
            "key": variant_key_of(c),
            "attrs": attrs_of(c),
            "price": _num(c.get("price")) or base["price"],
            "mrp": _num(c.get("mrp")) or base["mrp"],
            "stock": combo_units(c),
            "active": bool(combo_active(c)),
            "images": imgs,
            "default": bool(c.get("default_selected")),
        })

    reviews = []
    dist = {str(n): 0 for n in range(1, 6)}
    for r in ctx.get("reviews") or []:
        try:
            dist[str(max(1, min(5, int(r.rating))))] += 1
        except (TypeError, ValueError):
            pass
        reviews.append({
            "name": r.name or "Customer",
            "rating": int(r.rating or 0),
            "title": r.title or "",
            "text": r.comment or "",
            "date": r.created_at.strftime("%b %d, %Y") if getattr(r, "created_at", None) else "",
            "photo": r.photo.url if r.photo else "",
        })

    video = ctx.get("product_video") or None
    stock_left = None
    if not combos and p.inventory is not None:
        stock_left = int(p.inventory)

    my = ctx.get("my_review")
    refund_url = data["urls"]["policy"]["refund"]
    maps = _badge_maps(site)
    detail = _detail_view(proxy, details, maps, refund_url)
    return {
        **base,
        "detail": detail,
        "profiles": _variation_profiles(ctx, maps, refund_url),
        "category": next(({"name": c["name"], "url": c["url"]} for c in data["categories"] if c["slug"] == base["cat"]), None),
        "sku": p.sku or "",
        "desc": str(p.description or ""),
        "bullets": [str(b) for b in (details.get("bullets") or []) if str(b).strip()],
        "features": [str(f) for f in (details.get("features") or []) if str(f).strip()],
        "extras": [
            {"name": _text(e.get("name") or e.get("type"), 80), "text": str(e.get("text") or e.get("value") or "")}
            for e in (details.get("extras") or [])
            if isinstance(e, dict) and (e.get("text") or e.get("value"))
        ],
        "specs": [
            {"name": _text(sp.get("name"), 80), "value": _text(sp.get("value"), 300)}
            for sp in (p.specifications if isinstance(p.specifications, list) else [])
            if isinstance(sp, dict) and sp.get("name") and sp.get("value")
        ],
        "options": options,
        "combos": combos,
        "stockLeft": stock_left,
        "lowStock": bool(p.is_low_stock) if not combos else False,
        "moq": proxy.moq,
        "faqs": [
            {"q": _text(f.get("question"), 240), "a": str(f.get("answer") or "")}
            for f in (ctx.get("product_faqs") or [])
            if f.get("question") and f.get("answer")
        ],
        "faqsSubtitle": ctx.get("product_faqs_subtitle") or "",
        "reviewsList": reviews,
        "reviewDist": dist,
        "totalReviews": int(ctx.get("total_reviews") or 0),
        "avgRating": float(ctx.get("avg_rating") or 0),
        "canReview": bool(ctx.get("can_review")),
        "myReview": {"rating": my.rating, "title": my.title, "text": my.comment} if my else None,
        "reviewUrl": _url("submit_product_review", product_id=str(p.pk)),
        "related": products_json(site, request, ctx.get("related_products") or []),
        "video": {
            "type": video.get("type"),
            "url": video.get("url"),
            "embed": video.get("embed_url"),
            "thumb": video.get("thumb_url"),
            "orientation": video.get("orientation") or "landscape",
        } if video else None,
        "returnLabel": p.return_policy_label or "",
        "warranty": _text(p.guarantee_warranty, 400),
        "manufacturer": _text(p.manufacturer_contact, 400),
        "packer": _text(p.packer_contact, 400),
        "importer": _text(p.importer_contact, 400),
        "origin": p.country_of_origin or "",
        "gstInclusive": bool(p.gst_inclusive),
        "whatsapp": _url("public_product_whatsapp", product_id=str(p.pk)),
        "priceHistory": data["urls"]["api"] + str(p.pk) + "/price-history/",
        "zoom": bool(ctx.get("enable_product_detail_zoom")),
    }


# Template 2's reference photography (the same Unsplash ids as ``IMG`` in
# assets/js/boot.js), for About page image slots the owner has not filled.
_REF_PHOTOS = {
    "store": "1441986300917-64674bd600d8",
    "flatlay": "1556905055-8f358a7a47b2",
    "store2": "1445205170230-053b83016050",
    "rack2": "1512436991641-6745cdb1723f",
}
# Template 4's (owner-facing "Template 3", Eurus) botanical reference photos
# for the same slots (the ids in template4/assets/js/boot.js).
_REF_PHOTOS_T4 = {
    "store": "1544161515-4ab6ce6db874",
    "flatlay": "1612817288484-6f916006741a",
    "store2": "1601049541289-9b1b7bbbfe19",
    "rack2": "1556228578-8c89e6adf883",
}


def _ref_photo(key: str, w: int, h: int, theme: str = "") -> str:
    photos = _REF_PHOTOS_T4 if theme == "template4" else _REF_PHOTOS
    return f"https://images.unsplash.com/photo-{photos[key]}?auto=format&fit=crop&w={w}&h={h}&q=75"


def _multiline(value, limit: int) -> str:
    """Owner text with its line breaks kept (tags stripped, runs of blank
    lines collapsed), for ``|linebreaks`` in the template."""
    text = strip_tags(str(value or "")).replace("\r", "")
    text = "\n".join(" ".join(line.split()) for line in text.split("\n")).strip()
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return Truncator(text).chars(limit) if len(text) > limit else text


def _about(content, brand: str, theme: str = "") -> dict:
    """Edit Content → About page, field for field, in the order the page
    renders them (Template 1's order): the editorial intro (two paragraphs +
    banner), feature block 1 (title, description, image, three metrics) and
    the brand section (eyebrow, title, description, main + secondary image).

    A blank image shows a reference photo, as Template 1 does, unless the
    owner removed it with the editor's trash icon (``<field>_removed``)."""
    def g(k):
        return content.get(k) or ""

    def img(key, ref, w, h):
        if content.get(f"{key}_removed"):
            return ""
        return _media(g(key)) or _ref_photo(ref, w, h, theme)

    metrics = [
        {"value": _text(g(f"cowork_metric{n}_value"), 20), "label": _text(g(f"cowork_metric{n}_label"), 40)}
        for n in (1, 2, 3)
        if _text(g(f"cowork_metric{n}_value")) or _text(g(f"cowork_metric{n}_label"))
    ]
    return {
        "brand": brand,
        "intro1": _multiline(g("editorial_intro_1"), 1500),
        "intro2": _multiline(g("editorial_intro_2"), 1500),
        "banner": img("editorial_banner_image", "store", 1920, 800),
        "blockTitle": _text(g("cowork_block1_title"), 160),
        "blockText": _multiline(g("cowork_block1_desc"), 1200),
        "blockImage": img("cowork_block1_image", "flatlay", 1000, 900),
        "metrics": metrics,
        # Word values ("Ostomy Care") need a text size, not the big-number
        # size made for "10k+" or "4.8/5".
        "metricsText": any(len(m["value"]) > 6 for m in metrics),
        "label": _text(g("aus_brand_section_label"), 60),
        "title": _text(g("aus_brand_section_title"), 160),
        "text": _multiline(g("aus_brand_section_description"), 1500),
        "mainImage": img("aus_brand_main_image", "store2", 900, 1100),
        "secondImage": img("aus_brand_secondary_image", "rack2", 700, 700),
    }


def page_data(request, site, content, ctx, data) -> dict:
    """What the current page needs beyond the shared feed."""
    match = getattr(request, "resolver_match", None)
    route = getattr(match, "url_name", "") or ""
    page: dict = {"route": route, "title": str(ctx.get("page_title") or "")}

    if route == "public_product_detail" and ctx.get("product") is not None:
        page["product"] = _product_detail(request, site, ctx, data)

    elif route in ("public_all_products", "public_products_index", "public_category_detail"):
        page["products"] = products_json(site, request, ctx.get("products") or [])
        page["query"] = str(ctx.get("search_query") or "")
        page["sort"] = str(ctx.get("sort") or "")
        page["selected"] = [str(n) for n in (ctx.get("selected_category_names") or [])]
        cat = ctx.get("category")
        if cat is not None:
            found = next((c for c in data["categories"] if c["slug"] == cat.slug), None)
            page["category"] = dict(found) if found else {
                "slug": cat.slug,
                "name": cat.name,
                "count": len(page["products"]),
                "img": cat.image.url if cat.image else "@cat0",
                "desc": _text(cat.description, 200),
                "url": _url("public_category_detail", category_slug=cat.slug),
            }
            page["category"]["descFull"] = _text(cat.description, 600)
            # Banner: the first 10 words show, the rest stays in the page's
            # HTML (whole description, uncut) behind a "Read more" toggle.
            words = _text(cat.description).split()
            page["category"]["descLead"] = " ".join(words[:10])
            page["category"]["descRest"] = " ".join(words[10:])

    elif route == "public_blog_detail" and ctx.get("blog") is not None:
        post = getattr(ctx["blog"], "_d", {}) or {}
        b = blog_json(post)
        b["html"] = str(post.get("description") or post.get("content") or "")
        page["blog"] = b

    elif route in _POLICY_ROUTES:
        key, title, field = _POLICY_ROUTES[route]
        page["policy"] = {"key": key, "title": title, "html": str(content.get(field) or "")}
        terms = ctx.get("return_terms")
        if key == "refund" and terms:
            page["policy"]["returnTerms"] = {
                k: v for k, v in terms.items() if k in ("non_returnable", "replacement", "returnable")
            }

    elif route in ("public_policy_page", "public_custom_policy"):
        pol = ctx.get("policy") or {}
        slug = str(_get(pol, "slug") or "")
        page["policy"] = {
            "key": "custom:" + slug,
            "title": str(_get(pol, "title") or "Policy"),
            "html": str(_get(pol, "content") or content.get("custom_policy") or ""),
        }

    elif route in ("public_about", "public_about_us"):
        page["about"] = _about(content, data["brand"], getattr(site, "template_dir", ""))

    elif route == "public_user_account" and ctx.get("customer_email"):
        # The shopper's saved products (may lie outside the shared feed).
        page["wishlist"] = products_json(site, request, ctx.get("wishlist") or [])

    elif route in ("public_enquiry", "public_enquiry_alias"):
        page["contact"] = {
            "title": _text(content.get("contact_form_title"), 80),
            "subtitle": _text(content.get("contact_form_subtitle"), 300),
            "faqTitle": _text(
                f"{content.get('contact_faqs_title') or ''}{content.get('contact_faqs_title_suffix') or ''}", 80
            ),
            "faqSubtitle": _text(content.get("contact_faqs_subtitle"), 200),
            "success": bool(ctx.get("enquiry_success")),
            "error": str(ctx.get("enquiry_error") or ""),
            "product": str(request.GET.get("product") or "")[:64],
            "mapEmbed": "",
        }
        try:
            from .templatetags.website_filters import contact_map

            m = contact_map(_raw(content.get("contact_info")) or {})
            page["contact"]["mapEmbed"] = str((m or {}).get("embed") or (m or {}).get("src") or "")
        except Exception:
            pass

    return page
