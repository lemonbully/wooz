"""The storefront content editor.

A section-based editor over ``Site.content`` with per-section autosave. Each
section owns a whitelisted set of content keys (``SECTIONS``); a save merges only
those keys, so one section can never clobber another's data, and unknown keys
posted by a stale client are dropped rather than stored.

The option set mirrors the reference tool's edit page — brand, appearance,
hero, home sections, about, testimonials, FAQ, footer/social, contact, SEO,
policies, B2B certifications, and custom scripts — so owners have the same
control over their storefront.
"""

from __future__ import annotations

import json

from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from commerce.context import get_owned_site, store_context
from storefront.context import build_context

# --------------------------------------------------------------------------
# Section schema — the single source of truth for what the editor exposes.
#
# Field types the client understands:
#   text · textarea · richtext · url · number · color · image · checkbox
#   select (options) · object (fixed subfields) · list (repeatable rows)
# A section may declare "audience": "b2b" | "d2c" to show only for that model.
# --------------------------------------------------------------------------

_HERO_STYLES = [
    {"value": "style1", "label": "Classic slider"},
    {"value": "style2", "label": "Full-background"},
    {"value": "style3", "label": "Split hero"},
]
_CATEGORY_STYLES = [
    {"value": "style1", "label": "Grid"},
    {"value": "style2", "label": "Carousel"},
    {"value": "style3", "label": "Circular"},
]
_VIDEO_POSITIONS = [
    {"value": "top", "label": "Above products"},
    {"value": "bottom", "label": "Below products"},
]

# A content page's own fields (shared by core pages, their subpages and the
# nested child pages — a 3-level tree, mirroring the reference's CorePage →
# CorePageSubpage → CorePageNestedSubpage).
def _page_fields(extra=None):
    fields = [
        {"name": "title", "label": "Title", "type": "text"},
        {"name": "slug", "label": "Slug (URL)", "type": "text", "help": "Lowercase words joined by hyphens, e.g. size-guide. Leave blank to generate from the title."},
        {"name": "subtitle", "label": "Subtitle", "type": "text"},
        {"name": "image", "label": "Header image", "type": "image"},
        {"name": "content", "label": "Content", "type": "richtext"},
    ]
    if extra:
        fields = fields + extra
    return fields

SECTIONS = [
    {
        "key": "brand",
        "label": "Brand",
        "icon": "brand",
        "hint": "Your store name, logo and favicon.",
        "fields": [
            {"name": "site_name", "label": "Store name", "type": "text", "help": "Shown in the header, title bar and footer."},
            {"name": "desktop_logo", "label": "Logo", "type": "image", "help": "Displayed in the header. A transparent PNG or SVG works best."},
            {"name": "desktop_logo_alt", "label": "Logo alt text", "type": "text", "help": "Describes the logo for screen readers and SEO."},
            {"name": "mobile_logo", "label": "Mobile logo", "type": "image", "help": "Optional smaller logo for phones."},
            {"name": "favicon", "label": "Favicon", "type": "image", "help": "The little icon in the browser tab."},
            {"name": "footer_logomark", "label": "Footer logo", "type": "image", "help": "Optional logo shown in the footer."},
        ],
    },
    {
        "key": "appearance",
        "label": "Appearance",
        "icon": "appearance",
        "hint": "Colours and layout styles for your storefront.",
        "fields": [
            {"name": "primary_color", "label": "Primary colour", "type": "color", "help": "Buttons and key accents."},
            {"name": "secondary_color", "label": "Secondary colour", "type": "color"},
            {"name": "accent_color", "label": "Accent colour", "type": "color"},
            {"name": "text_color", "label": "Text colour", "type": "color"},
            {"name": "bg_color", "label": "Background colour", "type": "color"},
            {"name": "banner_title_color", "label": "Banner title colour", "type": "color"},
            {"name": "banner_description_color", "label": "Banner text colour", "type": "color"},
            {"name": "hero_banner_style", "label": "Hero style", "type": "select", "options": _HERO_STYLES},
            {"name": "hero_style3_left_bg", "label": "Split hero panel colour (Style 3)", "type": "color"},
            {"name": "hero_style3_left_bg_text", "label": "Split hero panel text (Style 3)", "type": "text"},
            {"name": "category_section_style", "label": "Category layout", "type": "select", "options": _CATEGORY_STYLES},
            {"name": "enable_product_hover_swap", "label": "Swap product image on hover", "type": "checkbox"},
        ],
    },
    {
        "key": "hero",
        "label": "Hero banners",
        "icon": "hero",
        "hint": "The large rotating banners at the top of your homepage. Every banner carries its own copy, colours, images and call-to-action.",
        "fields": [
            {
                "name": "hero_banners",
                "label": "Banners",
                "type": "list",
                "item_label": "Banner",
                "item_fields": [
                    {"name": "image", "label": "Banner image (desktop, 16:9)", "type": "image"},
                    {"name": "mobile_image", "label": "Mobile image (9:16)", "type": "image", "help": "Optional — used on phones."},
                    {"name": "alt_text", "label": "Image alt text", "type": "text"},
                    {"name": "overlay_opacity", "label": "Overlay darkness (0–100)", "type": "number", "help": "Darkens the image so text stays readable."},
                    {"name": "title", "label": "Banner title", "type": "text"},
                    {"name": "title_color", "label": "Title colour", "type": "color"},
                    {"name": "accent_text", "label": "Accent text (Style 3 only)", "type": "text", "help": "Small text above the heading in the split hero."},
                    {"name": "description", "label": "Description", "type": "textarea"},
                    {"name": "description_color", "label": "Description colour", "type": "color"},
                    {"name": "description_mobile_bg_color", "label": "Mobile subheading background (Style 2)", "type": "color"},
                    {"name": "button_text", "label": "Button text", "type": "text"},
                    {"name": "button_bg_color", "label": "Button background", "type": "color"},
                    {"name": "button_text_color", "label": "Button text colour", "type": "color"},
                    {
                        "name": "button_link_type", "label": "Button links to", "type": "select",
                        "options": [
                            {"value": "home", "label": "Home"},
                            {"value": "about", "label": "About us"},
                            {"value": "shop", "label": "Shop"},
                            {"value": "categories", "label": "Categories page"},
                            {"value": "category", "label": "A particular category"},
                            {"value": "product", "label": "A particular product"},
                            {"value": "custom", "label": "Custom URL"},
                        ],
                    },
                    {"name": "button_link_category_id", "label": "Category", "type": "select", "options_from": "categories", "show_if": {"field": "button_link_type", "in": ["category"]}},
                    {"name": "button_link_product_id", "label": "Product", "type": "select", "options_from": "products", "show_if": {"field": "button_link_type", "in": ["product"]}},
                    {"name": "button_custom_url", "label": "Custom URL", "type": "url", "show_if": {"field": "button_link_type", "in": ["custom"]}},
                ],
            },
        ],
    },
    {
        "key": "home",
        "label": "Home sections",
        "icon": "home",
        "hint": "Headings for the category, best-seller and video sections.",
        "fields": [
            {"name": "category_section_heading", "label": "Category section heading", "type": "text"},
            {"name": "category_section_subheading", "label": "Category section subtext", "type": "text"},
            {"name": "best_sellers_subtitle", "label": "Best-sellers subtext", "type": "text", "help": "Best-sellers are the products you flag in your catalogue."},
            {"name": "video_title", "label": "Video section title", "type": "text"},
            {"name": "video_url", "label": "Video URL", "type": "url", "help": "A YouTube/Vimeo link or a hosted video. Leave blank to hide the section."},
            {"name": "video_thumbnail", "label": "Video thumbnail", "type": "image"},
            {"name": "video_description", "label": "Video description", "type": "textarea"},
            {"name": "video_section_position", "label": "Video position", "type": "select", "options": _VIDEO_POSITIONS},
        ],
    },
    {
        "key": "blogs",
        "label": "Blogs",
        "icon": "blogs",
        "hint": "Articles shown on your storefront's blog. Add as many as you like.",
        "fields": [
            {
                "name": "blog_posts",
                "label": "Posts",
                "type": "list",
                "item_label": "Post",
                "item_fields": [
                    {"name": "title", "label": "Title", "type": "text"},
                    {"name": "slug", "label": "Slug (URL)", "type": "text", "help": "Lowercase words joined by hyphens, e.g. our-story."},
                    {"name": "category", "label": "Category label", "type": "text"},
                    {"name": "author", "label": "Author", "type": "text"},
                    {"name": "date", "label": "Date (e.g. 2026-08-01)", "type": "text"},
                    {"name": "image", "label": "Cover image", "type": "image"},
                    {"name": "excerpt", "label": "Excerpt", "type": "textarea"},
                    {"name": "description", "label": "Body", "type": "richtext"},
                ],
            },
        ],
    },
    {
        "key": "about",
        "label": "About",
        "icon": "about",
        "hint": "Tell customers who you are.",
        "fields": [
            {"name": "about_section_title", "label": "Heading", "type": "text"},
            {"name": "about_section_subtitle", "label": "Subheading", "type": "text"},
            {"name": "about_section_content", "label": "Body", "type": "textarea"},
            {"name": "about_image", "label": "Image", "type": "image"},
            {"name": "about_video_url", "label": "Video URL", "type": "url"},
            {"name": "about_button_text", "label": "Button label", "type": "text"},
            {"name": "about_button_url", "label": "Button link", "type": "url"},
        ],
    },
    {
        "key": "why",
        "label": "Why choose us",
        "icon": "why",
        "hint": "Reasons to buy from you.",
        "fields": [
            {"name": "why_choose_us_subtitle", "label": "Subtext", "type": "text"},
            {
                "name": "why_choose_us",
                "label": "Points",
                "type": "list",
                "item_label": "Point",
                "item_fields": [
                    {"name": "icon", "label": "Icon (Font Awesome class, e.g. fa-truck)", "type": "text"},
                    {"name": "title", "label": "Title", "type": "text"},
                    {"name": "description", "label": "Description", "type": "textarea"},
                ],
            },
        ],
    },
    {
        "key": "testimonials",
        "label": "Testimonials",
        "icon": "testimonials",
        "hint": "What your customers say.",
        "fields": [
            {"name": "testimonials_title", "label": "Heading", "type": "text"},
            {"name": "testimonials_subtitle", "label": "Subtext", "type": "text"},
            {
                "name": "testimonials",
                "label": "Quotes",
                "type": "list",
                "item_label": "Testimonial",
                "item_fields": [
                    {"name": "name", "label": "Name", "type": "text"},
                    {"name": "role", "label": "Role / location", "type": "text"},
                    {"name": "content", "label": "Quote", "type": "textarea"},
                    {"name": "rating", "label": "Rating (1–5)", "type": "number"},
                    {"name": "image", "label": "Photo", "type": "image"},
                ],
            },
        ],
    },
    {
        "key": "faq",
        "label": "FAQ",
        "icon": "faq",
        "hint": "Answer common questions.",
        "fields": [
            {"name": "faq_title", "label": "Heading", "type": "text"},
            {"name": "faq_subtitle", "label": "Subtext", "type": "text"},
            {
                "name": "faqs",
                "label": "Questions",
                "type": "list",
                "item_label": "Question",
                "item_fields": [
                    {"name": "question", "label": "Question", "type": "text"},
                    {"name": "answer", "label": "Answer", "type": "textarea"},
                ],
            },
        ],
    },
    {
        "key": "footer",
        "label": "Footer & social",
        "icon": "footer",
        "hint": "Footer text and social links.",
        "fields": [
            {"name": "footer_description", "label": "Footer text", "type": "textarea"},
            {
                "name": "social_links",
                "label": "Social links",
                "type": "object",
                "subfields": [
                    {"name": "facebook", "label": "Facebook URL", "type": "url"},
                    {"name": "instagram", "label": "Instagram URL", "type": "url"},
                    {"name": "twitter", "label": "Twitter / X URL", "type": "url"},
                    {"name": "youtube", "label": "YouTube URL", "type": "url"},
                    {"name": "linkedin", "label": "LinkedIn URL", "type": "url"},
                ],
            },
        ],
    },
    {
        "key": "contact",
        "label": "Contact",
        "icon": "contact",
        "hint": "How customers reach you.",
        "fields": [
            {
                "name": "contact_info",
                "label": "Contact details",
                "type": "object",
                "subfields": [
                    {"name": "email", "label": "Email", "type": "text"},
                    {"name": "phone", "label": "Phone / WhatsApp", "type": "text"},
                    {"name": "address", "label": "Address", "type": "textarea"},
                    {"name": "city", "label": "City", "type": "text"},
                    {"name": "state", "label": "State", "type": "text"},
                    {"name": "postal_code", "label": "Postal code", "type": "text"},
                    {"name": "country", "label": "Country", "type": "text"},
                ],
            },
        ],
    },
    {
        "key": "seo",
        "label": "SEO",
        "icon": "seo",
        "hint": "How your store appears in search results and social shares.",
        "fields": [
            {
                "name": "seo",
                "label": "Search & social",
                "type": "object",
                "subfields": [
                    {"name": "meta_title", "label": "Meta title", "type": "text", "help": "Aim for 50–60 characters."},
                    {"name": "meta_description", "label": "Meta description", "type": "textarea", "help": "Aim for 150–160 characters."},
                    {"name": "keywords", "label": "Keywords", "type": "text"},
                    {"name": "og_title", "label": "Social title", "type": "text"},
                    {"name": "og_description", "label": "Social description", "type": "textarea"},
                    {"name": "og_image", "label": "Social image", "type": "image"},
                    {"name": "canonical_url", "label": "Canonical URL", "type": "url"},
                    {"name": "raw_schema_json", "label": "Custom schema (JSON-LD)", "type": "textarea"},
                ],
            },
        ],
    },
    {
        "key": "policies",
        "label": "Policies",
        "icon": "policies",
        "layout": "accordion",
        "hint": "Your store's policy pages. Open one to edit it — only one stays open at a time.",
        "fields": [
            {"name": "privacy_policy", "label": "Privacy policy", "type": "richtext"},
            {"name": "terms_conditions", "label": "Terms & conditions", "type": "richtext"},
            {"name": "refund_policy", "label": "Refund policy", "type": "richtext"},
            {"name": "shipping_policy", "label": "Shipping policy", "type": "richtext"},
        ],
    },
    {
        "key": "certifications",
        "label": "Certifications",
        "icon": "certifications",
        "audience": "b2b",
        "hint": "Certificates and quality badges shown to trade buyers.",
        "fields": [
            {
                "name": "certificates",
                "label": "Certificates",
                "type": "list",
                "item_label": "Certificate",
                "item_fields": [
                    {"name": "title", "label": "Title", "type": "text"},
                    {"name": "image", "label": "Image / badge", "type": "image"},
                ],
            },
        ],
    },
    {
        "key": "core_pages",
        "label": "Core pages",
        "icon": "pages",
        "hint": "Custom content pages, each with optional subpages and child pages — a three-level tree, like Services → Consulting → Onboarding. A page lives at /page/<slug>/, a subpage at /page/<slug>/<subpage>/, and a child at /page/<slug>/<subpage>/<child>/.",
        "fields": [
            {
                "name": "core_pages",
                "label": "Pages",
                "type": "list",
                "item_label": "Page",
                "item_fields": _page_fields([
                    {
                        "name": "subpages",
                        "label": "Subpages",
                        "type": "list",
                        "item_label": "Subpage",
                        "item_fields": _page_fields([
                            {
                                "name": "children",
                                "label": "Child pages",
                                "type": "list",
                                "item_label": "Child page",
                                "item_fields": _page_fields(),
                            },
                        ]),
                    },
                ]),
            },
        ],
    },
    {
        "key": "scripts",
        "label": "Custom scripts",
        "icon": "scripts",
        "hint": "Advanced: inject tracking or custom CSS/JS. Only enable if you trust the code.",
        "fields": [
            {"name": "allows_custom_scripts", "label": "Allow custom scripts", "type": "checkbox", "site_field": True},
            {"name": "head_scripts", "label": "Head scripts", "type": "textarea", "help": "e.g. Meta Pixel or Google Analytics snippets."},
            {"name": "custom_css", "label": "Custom CSS", "type": "textarea"},
            {"name": "custom_js", "label": "Custom JS", "type": "textarea"},
        ],
    },
]

_SECTION_BY_KEY = {s["key"]: s for s in SECTIONS}


def _sections_for(site):
    """Sections applicable to this store's business model."""
    out = []
    for section in SECTIONS:
        audience = section.get("audience", "all")
        if audience == "all" or (audience == "b2b" and site.is_b2b) or (audience == "d2c" and site.is_d2c):
            out.append(section)
    return out


# --------------------------------------------------------------------------
# Value coercion / validation against the schema
# --------------------------------------------------------------------------


def _coerce_scalar(field_type, value):
    if value is None:
        return "" if field_type != "checkbox" else False
    if field_type == "checkbox":
        return bool(value)
    if field_type == "number":
        try:
            return int(value)
        except (TypeError, ValueError):
            try:
                return float(value)
            except (TypeError, ValueError):
                return ""
    return str(value)


def _clean_item(item_fields, raw):
    if not isinstance(raw, dict):
        return {}
    # Recurse through _clean_field so a list item can itself contain lists or
    # objects (e.g. a core page holding subpages holding child pages).
    return {f["name"]: _clean_field(f, raw.get(f["name"])) for f in item_fields}


def _clean_field(field, raw):
    ftype = field["type"]
    if ftype == "list":
        items = raw if isinstance(raw, list) else []
        return [_clean_item(field["item_fields"], it) for it in items]
    if ftype == "object":
        obj = raw if isinstance(raw, dict) else {}
        return {sf["name"]: _coerce_scalar(sf["type"], obj.get(sf["name"])) for sf in field["subfields"]}
    return _coerce_scalar(ftype, raw)


def _catalog_datasets(site):
    """Category/product option lists for the banner link-target dropdowns."""
    from commerce.models import Category, Product

    cats = (
        Category.objects.for_site(site)
        .filter(is_active=True, is_deleted=False)
        .order_by("order", "name")
    )
    prods = (
        Product.objects.for_site(site)
        .filter(is_active=True, is_deleted=False)
        .order_by("title")
    )
    return {
        "categories": [{"value": str(c.id), "label": c.name, "slug": c.slug} for c in cats],
        "products": [{"value": str(p.id), "label": p.title, "slug": p.slug} for p in prods],
    }


def _ensure_slug(node):
    from django.utils.text import slugify

    if isinstance(node, dict) and not str(node.get("slug") or "").strip() and node.get("title"):
        node["slug"] = slugify(node["title"])[:200]


def _fill_core_page_slugs(pages):
    """Auto-generate blank slugs from titles, down the whole tree."""
    for page in pages or []:
        _ensure_slug(page)
        for sub in (page.get("subpages") or []) if isinstance(page, dict) else []:
            _ensure_slug(sub)
            for child in (sub.get("children") or []) if isinstance(sub, dict) else []:
                _ensure_slug(child)


def _resolve_banner_urls(banners, datasets):
    """Turn each banner's link type + target into the ``button_url`` the
    storefront reads — mirroring the reference's hidden resolved URL."""
    cat_slug = {c["value"]: c["slug"] for c in datasets.get("categories", [])}
    prod_slug = {p["value"]: p["slug"] for p in datasets.get("products", [])}
    for b in banners:
        link_type = (b.get("button_link_type") or "shop").strip()
        if link_type == "home":
            b["button_url"] = "/"
        elif link_type == "about":
            b["button_url"] = "/about/"
        elif link_type == "categories":
            b["button_url"] = "/categories/"
        elif link_type == "category":
            slug = cat_slug.get(str(b.get("button_link_category_id") or ""))
            b["button_url"] = f"/category/{slug}/" if slug else "/categories/"
        elif link_type == "product":
            slug = prod_slug.get(str(b.get("button_link_product_id") or ""))
            b["button_url"] = f"/products/{slug}/" if slug else "/shop/"
        elif link_type == "custom":
            b["button_url"] = (b.get("button_custom_url") or "").strip() or "#"
        else:  # shop / default
            b["button_url"] = "/shop/"
    return banners


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------


@login_required
def editor(request, site_id):
    site = get_owned_site(request, site_id)
    if not site.content:
        from storefront.content_seed import default_content

        site.content = default_content(site)
        site.save(update_fields=["content", "updated_at"])

    sections = _sections_for(site)
    context = store_context(request, site, "editor")
    context.update(
        {
            "crumb": "Edit content",
            # Passed as objects; the template serialises them with json_script so
            # the browser JSON.parses an array/object, not a re-encoded string.
            "sections": sections,
            "content_data": site.content,
            "datasets": _catalog_datasets(site),
            "allows_custom_scripts": site.allows_custom_scripts,
            "storefront_url": site.storefront_url(request),
        }
    )
    return render(request, "store/editor.html", context)


@require_POST
@login_required
def save_section(request, site_id, section):
    site = get_owned_site(request, site_id)
    spec = _SECTION_BY_KEY.get(section)
    if spec is None:
        return HttpResponseBadRequest("Unknown section.")

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON.")

    values = payload.get("values") or {}
    content = dict(site.content or {})
    update_fields = ["content", "updated_at"]

    for field in spec["fields"]:
        name = field["name"]
        if name not in values:
            continue
        cleaned = _clean_field(field, values[name])
        if field.get("site_field"):
            if name == "allows_custom_scripts":
                site.allows_custom_scripts = bool(cleaned)
                if "allows_custom_scripts" not in update_fields:
                    update_fields.append("allows_custom_scripts")
            continue
        # Resolve each banner's link type/target into a concrete button_url.
        if name == "hero_banners" and isinstance(cleaned, list):
            cleaned = _resolve_banner_urls(cleaned, _catalog_datasets(site))
        # Fill blank slugs from titles so pages/posts are addressable.
        elif name == "core_pages" and isinstance(cleaned, list):
            _fill_core_page_slugs(cleaned)
        elif name == "blog_posts" and isinstance(cleaned, list):
            for post in cleaned:
                _ensure_slug(post)
        content[name] = cleaned

    # Keep name/about-title mirrors in step, as the templates read either key.
    if "site_name" in content:
        content["websiteName"] = content["site_name"]
    if "about_section_title" in content:
        content["about_title"] = content["about_section_title"]
    if "about_section_content" in content:
        content["about_content"] = content["about_section_content"]

    site.content = content
    site.save(update_fields=update_fields)

    return JsonResponse({"ok": True, "saved_at": timezone.now().strftime("%H:%M:%S")})


@require_POST
@login_required
def upload_media(request, site_id):
    site = get_owned_site(request, site_id)
    upload = request.FILES.get("file")
    if upload is None:
        return HttpResponseBadRequest("No file.")

    content_type = (upload.content_type or "").lower()
    if not content_type.startswith("image/"):
        return HttpResponseBadRequest("Images only.")

    path = default_storage.save(f"sites/{site.id}/content/{upload.name}", upload)
    return JsonResponse({"ok": True, "url": default_storage.url(path)})


# -- preview (kept for external use; not embedded in the editor UI) ---------


@xframe_options_sameorigin
@login_required
def preview(request, site_id):
    """Render the live storefront homepage (same-origin, frameable)."""
    from django.conf import settings
    from django.http import HttpResponse
    from django.urls import get_urlconf, set_urlconf

    from storefront.context import home_extra

    site = get_owned_site(request, site_id)
    template = f"storefront/{site.template_dir}/home.html"
    ctx = build_context(
        request, site, page_title="Preview", embedded=False, **home_extra(request, site)
    )

    previous_urlconf = get_urlconf()
    set_urlconf("onewooz.urls.storefront")
    try:
        response = render(request, template, ctx)
        _ = response.content
    except Exception as exc:  # pragma: no cover - dev aid
        message = str(exc) if settings.DEBUG else "Preview is unavailable right now."
        response = HttpResponse(
            "<!doctype html><meta charset='utf-8'>"
            "<div style=\"font:14px/1.6 ui-sans-serif,system-ui;padding:2rem;color:#b42318\">"
            "<strong>Preview error</strong><br>" + message + "</div>",
            content_type="text/html; charset=utf-8",
        )
    finally:
        set_urlconf(previous_urlconf)

    response["X-Frame-Options"] = "SAMEORIGIN"
    response["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
        "font-src 'self' data: https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
        "img-src 'self' data: blob: https:; frame-ancestors 'self'"
    )
    return response
