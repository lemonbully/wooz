"""Per-page SEO for a storefront: which page is this, and what should its
search / Open Graph / Twitter tags say?

Two modes, chosen in the editor's SEO tab (``content["seo_mode"]``):

* ``"same"``  one brand-level set (``content["seo"]``). The home page uses it
  as-is; every other page gets a *smart* set generated from its own content (a
  product's name, description and photo; a blog post's title, excerpt and cover;
  a policy's name …), with the brand set filling whatever the page can't supply
  (keywords, a fallback share image). So every page is distinct without the
  owner writing anything.
* ``"custom"``  the owner edits each page on its own tab
  (``content["seo_pages"][<page key>]``). A field left blank still falls back
  to that page's smart value, so a half-filled tab never empties a tag.

Page keys are stable identifiers, never paths: ``home``, ``about``,
``all_products``, ``categories``, ``blogs``, ``contact``, ``core_pages``,
``certificates``, ``privacy`` / ``terms`` / ``refund`` / ``shipping``,
``policy:<n>`` (an owner's extra policy, slot 1-3),
``core:<page>[/<sub>[/<child>]]``, ``blog:<slug>``, ``product:<uuid>`` and
``category:<uuid>`` (catalogue pages key on the id, so renaming a product's slug
keeps its SEO). The current page is identified from the resolved URL name and
kwargs  see :func:`page_key_for_request`.
"""

from __future__ import annotations

import re
from html import unescape

#: Every SEO attribute a page can carry, in editor order.
SEO_FIELDS = (
    "meta_title",
    "meta_description",
    "keywords",
    "og_title",
    "og_description",
    "og_image",
    "twitter_title",
    "twitter_description",
    "twitter_image",
    "canonical_url",
    "noindex",
)

# Fixed (non-catalogue) pages: key → (label, url name, B2B only).
_MAIN_PAGES = (
    ("home", "Home", "public_website", False),
    ("about", "About", "public_about", False),
    ("all_products", "All products", "public_all_products", False),
    ("categories", "All categories", "public_categories", False),
    ("blogs", "Blog", "public_blogs", False),
    ("contact", "Contact", "public_enquiry", False),
    ("core_pages", "Pages index", "public_core_pages", False),
    ("certificates", "Certificates", "public_certificates", True),
)
_POLICY_PAGES = (
    ("privacy", "Privacy policy", "privacy_policy_page", "privacy_policy"),
    ("terms", "Terms & conditions", "public_terms", "terms_conditions"),
    ("refund", "Refund policy", "public_refund", "refund_policy"),
    ("shipping", "Shipping policy", "public_shipping", "shipping_policy"),
)
_FIXED_LABELS = {k: label for k, label, _u, _b in _MAIN_PAGES}
_FIXED_LABELS.update({k: label for k, label, _u, _c in _POLICY_PAGES})

# URL name → fixed page key.
_URL_TO_KEY = {
    "public_website": "home",
    "public_about": "about",
    "public_about_us": "about",
    "public_all_products": "all_products",
    "public_products_index": "all_products",
    "public_categories": "categories",
    "public_blogs": "blogs",
    "public_blogs_alias": "blogs",
    "public_enquiry": "contact",
    "public_enquiry_alias": "contact",
    "public_core_pages": "core_pages",
    "public_certificates": "certificates",
    "privacy_policy_page": "privacy",
    "public_terms": "terms",
    "public_refund": "refund",
    "public_shipping": "shipping",
}

# Legacy per-group overrides (before per-page tabs existed). Still honoured as a
# fallback beneath a page's own tab, so stores that set them keep their SEO.
_LEGACY_GROUP = {
    "about": "seo_about",
    "all_products": "seo_products",
    "categories": "seo_products",
    "product": "seo_products",
    "category": "seo_products",
    "blogs": "seo_blogs",
    "blog": "seo_blogs",
    "core_pages": "seo_core",
    "core": "seo_core",
}

_TAG_RE = re.compile(r"<[^>]+>")
# A page body usually opens with its own heading ("<h2>Privacy Policy</h2>…");
# that is not description material, so headings are skipped when there is text.
_HEADING_RE = re.compile(r"<h[1-3][^>]*>.*?</h[1-3]>", re.S | re.I)
_WS_RE = re.compile(r"\s+")


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def _text(html, limit=160) -> str:
    """Plain, single-spaced text from (possibly HTML) content, cut on a word
    boundary to ``limit`` characters with an ellipsis."""
    raw = str(html or "")
    body = _HEADING_RE.sub(" ", raw)
    s = _WS_RE.sub(" ", unescape(_TAG_RE.sub(" ", body))).strip()
    if not s:
        s = _WS_RE.sub(" ", unescape(_TAG_RE.sub(" ", raw))).strip()
    if len(s) <= limit:
        return s
    cut = s[: limit - 1].rsplit(" ", 1)[0].rstrip(",.;:-")
    return (cut or s[: limit - 1]) + "…"


def _first(*values) -> str:
    for v in values:
        if v not in (None, "") and str(v).strip():
            return str(v).strip()
    return ""


def _site_name(content) -> str:
    return _first(content.get("site_name"), content.get("websiteName"), "Our store")


def _titled(name, site_name) -> str:
    name = str(name or "").strip()
    if not name or name.lower() == site_name.lower():
        return site_name
    return f"{name} | {site_name}"


def _walk_core(pages):
    """Yield (key, label, node) for every core page, subpage and child."""
    for page in pages if isinstance(pages, list) else []:
        if not isinstance(page, dict) or not str(page.get("slug") or "").strip():
            continue
        p = str(page["slug"]).strip("/")
        yield f"core:{p}", _first(page.get("title"), p), page
        for sub in page.get("subpages") or []:
            if not isinstance(sub, dict) or not str(sub.get("slug") or "").strip():
                continue
            s = str(sub["slug"]).strip("/")
            yield f"core:{p}/{s}", f"{_first(page.get('title'), p)} › {_first(sub.get('title'), s)}", sub
            for child in sub.get("children") or []:
                if not isinstance(child, dict) or not str(child.get("slug") or "").strip():
                    continue
                c = str(child["slug"]).strip("/")
                yield (
                    f"core:{p}/{s}/{c}",
                    f"{_first(page.get('title'), p)} › {_first(sub.get('title'), s)} › {_first(child.get('title'), c)}",
                    child,
                )


def _blog_posts(content):
    return [
        p for p in (content.get("blog_posts") or [])
        if isinstance(p, dict) and str(p.get("slug") or "").strip()
    ]


# --------------------------------------------------------------------------
# The page catalogue (the editor's per-page tabs)
# --------------------------------------------------------------------------

def page_catalog(site) -> list[dict]:
    """Every page that can carry its own SEO, grouped for the editor:
    ``[{"group", "pages": [{"key", "label", "auto": {...}}]}]``. ``auto`` is
    the smart default shown as each field's placeholder."""
    from commerce.models import Category, Product, ProductImage, display_ordered_categories

    content = site.content or {}
    groups = []

    from .certificates import public_certificates

    # B2B-only pages (Certificates) get a tab only on a B2B store, and only
    # once there is something on them to describe.
    has_certs = site.is_b2b and bool(public_certificates(content))
    main = [
        {"key": k, "label": label, "auto": auto_seo(site, content, k)}
        for k, label, _u, b2b_only in _MAIN_PAGES
        if not b2b_only or has_certs
    ]
    groups.append({"group": "Main pages", "pages": main})

    from .policies import custom_policies

    groups.append({
        "group": "Policies",
        "pages": [{"key": k, "label": label, "auto": auto_seo(site, content, k)} for k, label, _u, _c in _POLICY_PAGES]
        # The owner's extra policies (keyed by slot, so a renamed title keeps its SEO).
        + [{"key": cp["key"], "label": cp["title"], "auto": auto_seo(site, content, cp["key"], node=cp)}
           for cp in custom_policies(content)],
    })

    core = [
        {"key": key, "label": label, "auto": auto_seo(site, content, key, node=node)}
        for key, label, node in _walk_core(content.get("core_pages"))
    ]
    if core:
        groups.append({"group": "Content pages", "pages": core})

    blogs = [
        {"key": f"blog:{p['slug']}", "label": _first(p.get("title"), p["slug"]),
         "auto": auto_seo(site, content, f"blog:{p['slug']}", node=p)}
        for p in _blog_posts(content)
    ]
    if blogs:
        groups.append({"group": "Blog posts", "pages": blogs})

    from core.queries import sorted_rows

    # No index serves ORDER BY title; sort keys only, not whole product rows.
    products = sorted_rows(
        Product.objects.for_site(site).filter(is_deleted=False).order_by("title")
    )
    if products:
        first_img = {}
        for img in ProductImage.objects.for_site(site).filter(product__in=products).order_by("product_id", "order", "id"):
            first_img.setdefault(img.product_id, img)
        groups.append({
            "group": "Products",
            "pages": [
                {"key": f"product:{p.id}", "label": p.title or "Untitled product",
                 "auto": auto_seo(site, content, f"product:{p.id}", node=p, image=first_img.get(p.id))}
                for p in products
            ],
        })

    cats = list(display_ordered_categories(Category.objects.for_site(site).filter(is_deleted=False)))
    if cats:
        groups.append({
            "group": "Categories",
            "pages": [
                {"key": f"category:{c.id}", "label": c.name or "Untitled category",
                 "auto": auto_seo(site, content, f"category:{c.id}", node=c)}
                for c in cats
            ],
        })
    return groups


def home_auto(site) -> dict:
    """The home page's generated SEO *without* the brand set, i.e. what each
    brand-set field falls back to when left blank. The editor's "Same for every
    page" form edits the brand set itself, so its placeholders must not echo it."""
    content = dict(site.content or {})
    content.pop("seo", None)
    return auto_seo(site, content, "home")


# --------------------------------------------------------------------------
# Which page is this request?
# --------------------------------------------------------------------------

def page_key_for_request(request, site, content=None):
    """``(key, node, image)`` for the page being rendered, or ``(None, None, None)``
    for pages that carry no SEO of their own (cart, checkout, account …)."""
    match = getattr(request, "resolver_match", None)
    if match is None:
        return None, None, None
    name = match.url_name or ""
    kw = match.kwargs or {}
    if content is None:
        content = site.content or {}

    if name in _URL_TO_KEY:
        return _URL_TO_KEY[name], None, None

    if name == "public_product_detail":
        from commerce.models import Product, ProductImage

        product = (
            Product.objects.for_site(site)
            .filter(slug=kw.get("product_slug"), is_deleted=False)
            .first()
        )
        if product is None:
            return None, None, None
        image = ProductImage.objects.for_site(site).filter(product=product).order_by("order", "id").first()
        return f"product:{product.id}", product, image

    if name == "public_category_detail":
        from commerce.models import Category

        cat = (
            Category.objects.for_site(site)
            .filter(slug=kw.get("category_slug"), is_deleted=False)
            .first()
        )
        return (f"category:{cat.id}", cat, None) if cat else (None, None, None)

    if name in ("public_blog_detail", "public_blog_detail_alias"):
        slug = kw.get("blog_slug")
        post = next((p for p in _blog_posts(content) if p.get("slug") == slug), None)
        return (f"blog:{slug}", post, None) if post else (None, None, None)

    if name == "public_policy_page":
        from .policies import find

        policy = find(content, kw.get("policy_slug"))
        return (policy["key"], policy, None) if policy else (None, None, None)

    if name in ("public_core_page", "public_core_subpage", "public_core_child"):
        parts = [kw.get("page_slug"), kw.get("sub_slug"), kw.get("child_slug")]
        key = "core:" + "/".join(p for p in parts if p)
        node = next((n for k, _l, n in _walk_core(content.get("core_pages")) if k == key), None)
        return (key, node, None) if node is not None else (None, None, None)

    return None, None, None


# --------------------------------------------------------------------------
# Smart defaults
# --------------------------------------------------------------------------

def auto_seo(site, content, key, *, node=None, image=None) -> dict:
    """The generated SEO for one page, from its own content. Never empty for a
    known page: title and description always resolve."""
    content = content or {}
    name = _site_name(content)
    brand = content.get("seo") if isinstance(content.get("seo"), dict) else {}
    brand_desc = _first(brand.get("meta_description"), content.get("meta_description"), content.get("description"))
    kind = key.split(":", 1)[0]
    out = {"og_type": "website"}

    if key == "home":
        out["meta_title"] = _first(brand.get("meta_title"), name)
        out["meta_description"] = brand_desc or _text(content.get("about_content"))
    elif key == "about":
        out["meta_title"] = _titled(_first(content.get("about_title"), "About us"), name)
        out["meta_description"] = _text(content.get("about_section_content") or content.get("about_content")) or brand_desc
        out["og_image"] = _first(content.get("about_image"))
    elif key == "all_products":
        out["meta_title"] = _titled("Shop all products", name)
        out["meta_description"] = f"Browse every product from {name}. " + (brand_desc or "")
    elif key == "categories":
        out["meta_title"] = _titled("Shop by category", name)
        out["meta_description"] = f"Explore all product categories at {name}. " + (brand_desc or "")
    elif key == "blogs":
        out["meta_title"] = _titled("Blog", name)
        out["meta_description"] = f"Stories, guides and news from {name}."
    elif key == "contact":
        out["meta_title"] = _titled("Contact us", name)
        out["meta_description"] = f"Get in touch with {name}. We usually reply within one business day."
    elif key == "core_pages":
        out["meta_title"] = _titled("Pages", name)
        out["meta_description"] = f"More about {name}: guides, information and resources."
    elif key == "certificates":
        from .certificates import public_certificates

        certs = public_certificates(content)
        titles = [c["title"] for c in certs if c["title"] != "Certificate"]
        out["meta_title"] = _titled("Certificates & quality credentials", name)
        if titles:
            listed = ", ".join(titles[:4]) + (f" and {len(titles) - 4} more" if len(titles) > 4 else "")
            out["meta_description"] = f"{name} is certified for {listed}. View and download each certificate."
        else:
            out["meta_description"] = f"Certifications and quality credentials held by {name}."
        out["og_image"] = next((c["thumb"] for c in certs if c["thumb"]), "")
        if titles:
            out["keywords"] = ", ".join(titles[:10])
    elif key in _FIXED_LABELS:  # policies
        label = _FIXED_LABELS[key]
        policy_key = next((c for k, _l, _u, c in _POLICY_PAGES if k == key), "")
        out["meta_title"] = _titled(label, name)
        out["meta_description"] = _text(content.get(policy_key)) or f"Read the {label.lower()} of {name}."
    elif kind == "policy":
        # An owner's extra policy (storefront.policies); ``node`` is its dict.
        if not isinstance(node, dict):
            from .policies import by_number

            node = by_number(content, key.split(":", 1)[1]) or {}
        label = _first(node.get("title"), "Policy")
        out["meta_title"] = _titled(label, name)
        out["meta_description"] = _text(node.get("content")) or f"Read the {label.lower()} of {name}."
    elif kind == "product" and node is not None:
        out["og_type"] = "product"
        out["meta_title"] = _titled(getattr(node, "title", ""), name)
        out["meta_description"] = _text(getattr(node, "description", "")) or f"Buy {getattr(node, 'title', 'this product')} from {name}."
        tags = getattr(node, "tags", None) or []
        if isinstance(tags, list) and tags:
            out["keywords"] = ", ".join(str(t) for t in tags if t)
        img = getattr(image, "image", None) if image is not None else None
        try:
            out["og_image"] = img.url if img else ""
        except Exception:
            out["og_image"] = ""
    elif kind == "category" and node is not None:
        cname = getattr(node, "name", "")
        out["meta_title"] = _titled(cname, name)
        out["meta_description"] = _text(getattr(node, "description", "")) or f"Shop {cname} at {name}."
        img = getattr(node, "image", None)
        try:
            out["og_image"] = img.url if img else ""
        except Exception:
            out["og_image"] = ""
    elif kind == "blog" and isinstance(node, dict):
        out["og_type"] = "article"
        out["meta_title"] = _titled(node.get("title"), name)
        out["meta_description"] = _text(node.get("excerpt")) or _text(node.get("description")) or f"Read more on the {name} blog."
        out["og_image"] = _first(node.get("image"))
    elif kind == "core" and isinstance(node, dict):
        out["meta_title"] = _titled(node.get("title"), name)
        out["meta_description"] = _first(_text(node.get("subtitle")), _text(node.get("content"))) or brand_desc
        out["og_image"] = _first(node.get("image"))
    else:
        out["meta_title"] = name
        out["meta_description"] = brand_desc

    out["meta_description"] = _text(out.get("meta_description"))
    # Brand-level values fill what the page itself can't supply.
    out.setdefault("keywords", "")
    if not out["keywords"]:
        out["keywords"] = _first(brand.get("keywords"))
    if not out.get("og_image"):
        out["og_image"] = _first(brand.get("og_image"), content.get("desktop_logo"))
    return out


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------

def _clean(d) -> dict:
    return {k: v for k, v in (d or {}).items() if v not in (None, "") and not (isinstance(v, str) and not v.strip())}


def resolve(site, content, request) -> dict:
    """The final SEO dict for this request (what the ``<head>`` renders)."""
    content = content or {}
    brand = content.get("seo") if isinstance(content.get("seo"), dict) else {}
    key, node, image = page_key_for_request(request, site, content)

    if key is None:
        # A page with no SEO identity (cart, checkout, account, …): brand set.
        result = dict(_clean(brand))
        # The brand set's canonical/noindex belong to the home page only.
        result.pop("canonical_url", None)
        result.pop("noindex", None)
        result.setdefault("meta_title", _site_name(content))
    else:
        result = auto_seo(site, content, key, node=node, image=image)
        if key == "home":
            # The brand set *is* the home page's SEO in both modes.
            result.update(_clean(brand))
        if str(content.get("seo_mode") or "same") == "custom":
            kind = key.split(":", 1)[0]
            legacy = content.get(_LEGACY_GROUP.get(key) or _LEGACY_GROUP.get(kind) or "")
            if isinstance(legacy, dict):
                result.update(_clean(legacy))
            pages = content.get("seo_pages") if isinstance(content.get("seo_pages"), dict) else {}
            own = pages.get(key)
            if isinstance(own, dict):
                result.update(_clean(own))

    # Derive the social tags from the search tags where not set explicitly.
    result.setdefault("og_title", result.get("meta_title", ""))
    result.setdefault("og_description", result.get("meta_description", ""))
    result.setdefault("twitter_title", result.get("og_title", ""))
    result.setdefault("twitter_description", result.get("og_description", ""))
    if result.get("og_image") and not result.get("twitter_image"):
        result["twitter_image"] = result["og_image"]
    # Share images must be absolute for crawlers / link unfurlers.
    for k in ("og_image", "twitter_image"):
        v = str(result.get(k) or "").strip()
        if v and v.startswith("/") and not v.startswith("//"):
            try:
                result[k] = request.build_absolute_uri(v)
            except Exception:
                pass
    result["noindex"] = bool(result.get("noindex"))
    if result.get("meta_title"):
        result["page_meta_title"] = result["meta_title"]
    result["page_key"] = key or ""
    return result
