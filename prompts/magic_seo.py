"""Magic SEO: one click fills every field of the SEO tab in Edit content.

What the pencil does
--------------------
1. The owner clicks the pencil in Edit content > SEO (shown only while the
   store's owner has the Magic SEO marketplace app installed and licensed) and
   optionally gives a brief: focus keywords, audience, location, tone, language.
2. :func:`start` records a :class:`~prompts.models.MagicSeoRun` and queues a
   Celery job. One run per store at a time.
3. :func:`execute` (the job) collects the store's facts and every page the SEO
   tab lists (``storefront.seo_pages.page_catalog``: main pages, policies,
   content pages, blog posts, products, categories), and sends them in small
   batches, in parallel, to ChatGPT with the active admin prompt (Django admin
   > Prompts, type "Magic SEO"). The output contract is enforced here, in code,
   whatever the admin prompt says.
4. Every reply is validated field by field (lengths, no HTML, no em dashes, no
   placeholder text). Anything missing or unusable falls back to the page's
   own auto value, so every field of every page ends up filled.
5. :func:`apply` writes the brand set (``content["seo"]``) and every page
   (``content["seo_pages"]``), switches the tab to "Different for every page",
   and keeps the previous SEO tab on the run so :func:`undo` restores it.

Fields and where their values come from
---------------------------------------
* Meta title, meta description, keywords, OG title and description, Twitter
  title and description: written by the AI from the page's own content.
* OG image and Twitter image: the page's own image (product photo, category
  image, blog cover, About image), else the owner's brand share image. A page
  left with no share image at all (or only the logo) gets one drawn with
  ChatGPT images (:mod:`prompts.seo_images`), unless the owner switched that
  off in Options; product pages never get a drawn image.
* Canonical URL: the page's own path (``/products/<slug>/``). Stored relative,
  so it follows the store to a custom domain; ``storefront.seo_pages.resolve``
  makes it absolute on the domain the page is served from.
* Hide from search engines: never changed (an owner's choice to hide a page
  is kept as it is).
* Custom schema (home page): a FAQPage built from the store's own FAQ section,
  written only when the field is empty.
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.html import strip_tags

logger = logging.getLogger("onewooz.magic_seo")

#: The fields the AI writes, and the most characters each may hold.
FIELD_MAX = {
    "meta_title": 60,
    "meta_description": 160,
    "keywords": 200,
    "og_title": 90,
    "og_description": 200,
    "twitter_title": 70,
    "twitter_description": 200,
}
TEXT_FIELDS = tuple(FIELD_MAX)
#: Every per-page SEO key the editor stores (storefront.seo_pages.SEO_FIELDS).
SEO_KEYS = TEXT_FIELDS + ("og_image", "twitter_image", "canonical_url", "noindex")
#: Never written over, in either mode: the owner's share images, canonical
#: and "hide from search" choice (the AI writes text, not these decisions).
OWNER_KEPT = ("og_image", "twitter_image", "canonical_url", "noindex")
#: Shorter than this, a description is not worth keeping (the auto one is used).
MIN_DESCRIPTION = 50
MAX_KEYWORDS = 8

#: Bounds on every run, whatever the store's size.
LIMITS = {
    "max_pages": 400,        # pages written by the AI; any beyond get their auto values
    "batch_pages": 12,       # pages per API call
    "workers": 4,            # API calls in flight at once
    "page_text_chars": 500,  # of a page's own text sent to the model
    "store_text_chars": 900, # of the About text sent with every batch
    "category_names": 40,
    "used_titles": 80,       # titles already written, sent so later batches avoid them
    "faq_items": 10,
}

#: The owner's brief (all optional) and its caps.
BRIEF_FIELDS = {"keywords": 240, "audience": 160, "location": 80}
TONES = {
    "": "Match the store's own voice",
    "friendly": "Warm and friendly",
    "premium": "Premium and refined",
    "professional": "Professional and trustworthy",
    "playful": "Playful and energetic",
    "minimal": "Clear and minimal",
}
LANGUAGES = {
    "en": "English",
    "hi": "Hindi (Devanagari script)",
    "hinglish": "Hinglish (Hindi written in Latin letters, mixed with English)",
}

#: A run with no progress for this long is treated as dead (worker restart).
STALE_MINUTES = 30
_STOREFRONT_URLCONF = "onewooz.urls.storefront"

#: Used to create the admin prompt automatically the first time Magic SEO
#: runs, when no "Magic SEO" prompt exists yet (an admin can edit it, or add
#: their own and switch this one off). The output format is not described
#: here: it is added by code (:data:`OUTPUT_CONTRACT`) on every request.
DEFAULT_PROMPT = """You are a senior e-commerce SEO specialist writing search and social metadata for an Indian online store built on OneWooz.

Goal: every page should earn the click in Google results and look trustworthy when shared on WhatsApp, Facebook, LinkedIn and X, while describing only what the store really offers.

How to write:
- Use only the facts given for the store and for each page. Never invent prices, discounts, offers, delivery times, certifications, awards, ratings, reviews or stock levels.
- Meta title: lead with the page's main search phrase (what a shopper would actually type), then a differentiator. Add " | Store name" at the end only when it still fits in 60 characters. Every page's title must be different.
- Meta description: 120 to 155 characters, one or two complete sentences. Lead with the benefit to the shopper, end with a soft call to action (Shop now, Explore the range, Read the guide, Get in touch). No quotation marks, emoji or ALL CAPS.
- Keywords: 5 to 8 comma-separated lowercase search phrases shoppers in India really use for this page: the main term, two or three long-tail variants, the category and the brand. No repeats, no stuffing.
- Open Graph title and description: written for a social feed, a little warmer and more conversational than the search snippet, still accurate.
- Twitter title and description: short and punchy.
- Product pages: name the product type and its key attribute (material, size, use, flavour, fit) from its description, and the brand when given. Category pages: describe the range and who it is for. Policy pages: plain and reassuring, say what the policy covers, no marketing hype. Blog posts and guides: promise the answer the article gives. Contact page: invite the visitor to get in touch and say how.
- Follow the owner's brief (focus keywords, audience, location, tone, language) wherever it fits the page. Never force a focus keyword onto a page it does not describe.
- Plain text only: no HTML, no markdown, no em dashes or en dashes (use a comma, a colon or a plain hyphen), no placeholder text such as [Brand] or "Insert keyword"."""

#: Appended to every request by code, so the reply is always parseable.
OUTPUT_CONTRACT = """OUTPUT RULES (these override anything above):
Return ONLY one JSON object of exactly this shape, with one entry for every page key listed in PAGES and no other keys:
{{"pages": {{"<page key>": {{"meta_title": "", "meta_description": "", "keywords": "", "og_title": "", "og_description": "", "twitter_title": "", "twitter_description": ""}}}}}}
Hard limits: meta_title at most 60 characters; meta_description 120 to 160 characters; keywords 5 to {max_keywords} comma-separated phrases; og_title at most 90; og_description at most 200; twitter_title at most 70; twitter_description at most 200.
Write every value in {language}. Plain text, no em dashes or en dashes, no placeholders.
Every meta_title must be unique, and must not repeat any of these titles already used on other pages of this store: {used}"""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
_WS = re.compile(r"\s+")
_PLACEHOLDER = re.compile(
    r"\[[^\]]{1,40}\]|\{\{|\}\}|<[a-z/]|lorem ipsum|insert (?:your|keyword|brand|faq|text)|xxx",
    re.I,
)


def _text(value) -> str:
    return _WS.sub(" ", html_lib.unescape(strip_tags(str(value or "")))).strip()


def _clip(value, limit: int) -> str:
    """Plain text cut to ``limit`` on a word boundary, with an ellipsis (for
    facts sent to the model, never for output)."""
    text = _text(value)
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:.")
    return f"{cut}…"


def _fit(text: str, limit: int) -> str:
    """Output text within ``limit``: at the last sentence end when that keeps
    most of it, else the last whole word. Never an ellipsis."""
    text = _WS.sub(" ", text).strip()
    if len(text) <= limit:
        return text
    head = text[:limit]
    stop = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    if stop >= int(limit * 0.6):
        return head[: stop + 1].strip()
    cut = head.rsplit(" ", 1)[0] if " " in head else head
    return cut.rstrip(" ,;:-|")


def _nodash(text: str) -> str:
    """No em or en dashes anywhere in what OneWooz publishes."""
    # A dash inside a range (500 to 900, S to XL) becomes a plain hyphen; a
    # dash used as a pause becomes a comma.
    text = re.sub(r"(?<=\w)[—–](?=\w)", "-", text)
    text = re.sub(r"\s*[—–]\s*(?=\S)", ", ", text)
    text = text.replace("—", "-").replace("–", "-")
    return re.sub(r",\s*,", ",", text)


def _first(*values) -> str:
    for v in values:
        if v not in (None, "") and str(v).strip():
            return str(v).strip()
    return ""


def _path(name: str, **kwargs) -> str:
    from django.urls import reverse

    try:
        return reverse(name, urlconf=_STOREFRONT_URLCONF, kwargs=kwargs or None)
    except Exception:
        return ""


def _money(value) -> str:
    try:
        return f"{float(value):,.0f}" if value is not None else ""
    except (TypeError, ValueError):
        return ""


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------
def ensure_prompt():
    """The active "Magic SEO" prompt. The first time (no Magic SEO prompt at
    all), :data:`DEFAULT_PROMPT` is saved as one, so the app works without a
    setup step; an admin who switches it off is respected."""
    from .models import Prompt

    prompt = Prompt.active(Prompt.Kind.MAGIC_SEO)
    if prompt is not None:
        return prompt
    try:
        if not Prompt.objects.filter(kind=Prompt.Kind.MAGIC_SEO).exists():
            return Prompt.objects.create(
                kind=Prompt.Kind.MAGIC_SEO, name="Magic SEO (default)", prompt=DEFAULT_PROMPT, is_active=True,
            )
    except Exception:
        logger.warning("could not create the default Magic SEO prompt", exc_info=True)
    return None


def ai_credentials():
    """The key and models of the Magic SEO app (Django admin > Apps > Magic
    SEO > AI provider), with the server's OPENAI_* settings as fallback."""
    from marketplace.ai import credentials
    from marketplace.modules.magic_seo import SLUG

    return credentials(SLUG)


def unavailable_reason() -> str:
    """'' when runs can start, else a reason for staff (owners see a generic one)."""
    if not ai_credentials().available:
        return "No AI API key: set one on the Magic SEO app (admin > Apps) or OPENAI_API_KEY on the server."
    if ensure_prompt() is None:
        return "There is no active 'Magic SEO' prompt in Django admin > Prompts."
    return ""


def clean_brief(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out = {k: _text(raw.get(k))[:cap] for k, cap in BRIEF_FIELDS.items()}
    tone = str(raw.get("tone") or "").strip()
    out["tone"] = tone if tone in TONES else ""
    lang = str(raw.get("language") or "").strip()
    out["language"] = lang if lang in LANGUAGES else "en"
    # Draw share images for pages that have none (on unless switched off).
    out["images"] = raw.get("images") is not False
    return out


# ---------------------------------------------------------------------------
# What the model reads
# ---------------------------------------------------------------------------
_FIXED_URLS = {
    "home": "public_website",
    "about": "public_about",
    "all_products": "public_all_products",
    "categories": "public_categories",
    "blogs": "public_blogs",
    "contact": "public_enquiry",
    "core_pages": "public_core_pages",
    "certificates": "public_certificates",
    "privacy": "privacy_policy_page",
    "terms": "public_terms",
    "refund": "public_refund",
    "shipping": "public_shipping",
}
_POLICY_CONTENT = {
    "privacy": "privacy_policy",
    "terms": "terms_conditions",
    "refund": "refund_policy",
    "shipping": "shipping_policy",
}
_PAGE_TYPES = {
    "home": "home page", "about": "about page", "all_products": "all products listing",
    "categories": "category index", "blogs": "blog index", "contact": "contact page",
    "core_pages": "guides index", "certificates": "certificates page",
    "privacy": "policy page", "terms": "policy page", "refund": "policy page", "shipping": "policy page",
    "policy": "policy page", "core": "content page", "blog": "blog post",
    "product": "product page", "category": "category page",
}


def collect(site) -> tuple[dict, list[dict]]:
    """``(store facts, pages)``. Every page of the SEO tab, in the tab's order:
    ``{key, label, group, type, url, auto, facts}``."""
    from commerce.models import Category, Product
    from storefront.policies import custom_policies
    from storefront.seo_pages import _walk_core, page_catalog

    content = site.content if isinstance(site.content, dict) else {}
    name = _first(content.get("site_name"), content.get("websiteName"), site.name, "Our store")

    products = {
        str(p.id): p
        for p in Product.objects.for_site(site).filter(is_deleted=False).order_by()
        .only("id", "title", "slug", "description", "brand", "price", "mrp", "category_id", "tags", "is_active")[:2000]
    }
    categories = {
        str(c.id): c
        for c in Category.objects.for_site(site).filter(is_deleted=False).order_by()
        .only("id", "name", "slug", "description", "parent_id", "is_active")[:500]
    }
    live = [p for p in products.values() if p.is_active]
    by_cat: dict[str, list] = {}
    for p in live:
        if p.category_id:
            by_cat.setdefault(str(p.category_id), []).append(p)
    cat_names = [_text(c.name) for c in categories.values() if c.is_active and _text(c.name)]
    prices = [float(p.price) for p in live if p.price]

    about = content.get("about_section_content") or content.get("about_content")
    ci = content.get("contact_info") if isinstance(content.get("contact_info"), dict) else {}
    seo = content.get("seo") if isinstance(content.get("seo"), dict) else {}

    store = {
        "name": name,
        "business_model": "B2B (sells to businesses)" if site.is_b2b and not site.is_d2c
        else ("D2C and B2B" if site.is_b2b else "D2C (sells to shoppers)"),
        "tagline": _text(getattr(site, "tagline", "")),
        "description": _clip(seo.get("meta_description") or content.get("meta_description")
                             or content.get("description"), 320),
        "about": _clip(about, LIMITS["store_text_chars"]),
        "location": ", ".join(x for x in (_text(ci.get("city")), _text(ci.get("state"))) if x),
        "country": "India",
        "currency": "INR",
        "live_products": len(live),
        "categories": cat_names[: LIMITS["category_names"]],
        "price_range_inr": f"{_money(min(prices))} to {_money(max(prices))}" if prices else "",
    }

    posts = {str(p.get("slug")).strip(): p for p in (content.get("blog_posts") or [])
             if isinstance(p, dict) and str(p.get("slug") or "").strip()}
    core = {k: (label, node) for k, label, node in _walk_core(content.get("core_pages"))}
    policies = {cp["key"]: cp for cp in custom_policies(content)}

    def facts_for(key: str) -> tuple[dict, str]:
        kind, _, rest = key.partition(":")
        if key == "home":
            return {"about": _clip(about, 400), "categories": cat_names[:12],
                    "example_products": [_text(p.title) for p in live[:8]]}, _path("public_website")
        if key == "about":
            return {"text": _clip(about, LIMITS["page_text_chars"])}, _path("public_about")
        if key == "all_products":
            return {"live_products": len(live), "categories": cat_names[:15],
                    "price_range_inr": store["price_range_inr"]}, _path("public_all_products")
        if key == "categories":
            return {"categories": cat_names[:30]}, _path("public_categories")
        if key == "blogs":
            return {"posts": [_text(p.get("title")) for p in posts.values()][:10]}, _path("public_blogs")
        if key == "contact":
            return {"location": store["location"], "has_phone": bool(_text(ci.get("mobile_number") or ci.get("phone"))),
                    "has_email": bool(_text(ci.get("contact_email") or ci.get("email")))}, _path("public_enquiry")
        if key == "core_pages":
            return {"pages": [label for label, _n in core.values()][:15]}, _path("public_core_pages")
        if key == "certificates":
            from storefront.certificates import public_certificates

            return {"certificates": [c["title"] for c in public_certificates(content)][:15]}, _path("public_certificates")
        if key in _POLICY_CONTENT:
            return {"text": _clip(content.get(_POLICY_CONTENT[key]), LIMITS["page_text_chars"])}, _path(_FIXED_URLS[key])
        if kind == "policy":
            cp = policies.get(key) or {}
            return ({"title": cp.get("title", ""), "text": _clip(cp.get("content"), LIMITS["page_text_chars"])},
                    _path("public_policy_page", policy_slug=cp["slug"]) if cp.get("slug") else "")
        if kind == "core":
            _label, node = core.get(key, ("", {}))
            parts = rest.split("/")
            names = ("public_core_page", "public_core_subpage", "public_core_child")
            kw = dict(zip(("page_slug", "sub_slug", "child_slug"), parts))
            return ({"title": _text(node.get("title")), "subtitle": _text(node.get("subtitle")),
                     "text": _clip(node.get("content"), LIMITS["page_text_chars"])},
                    _path(names[len(parts) - 1], **kw) if 1 <= len(parts) <= 3 else "")
        if kind == "blog":
            post = posts.get(rest) or {}
            return ({"title": _text(post.get("title")), "excerpt": _clip(post.get("excerpt"), 300),
                     "text": _clip(post.get("content") or post.get("description"), LIMITS["page_text_chars"])},
                    _path("public_blog_detail", blog_slug=rest))
        if kind == "product":
            p = products.get(rest)
            if p is None:
                return {}, ""
            cat = categories.get(str(p.category_id)) if p.category_id else None
            tags = p.tags if isinstance(p.tags, list) else []
            return ({"name": _text(p.title), "brand": _text(p.brand), "category": _text(cat.name) if cat else "",
                     "price_inr": _money(p.price), "mrp_inr": _money(p.mrp) if p.mrp and p.mrp > p.price else "",
                     "tags": [_text(t) for t in tags if _text(t)][:10],
                     "description": _clip(p.description, LIMITS["page_text_chars"]),
                     "live": bool(p.is_active)},
                    _path("public_product_detail", product_slug=p.slug) if (p.slug or "").strip() else "")
        if kind == "category":
            c = categories.get(rest)
            if c is None:
                return {}, ""
            members = by_cat.get(rest, [])
            parent = categories.get(str(c.parent_id)) if c.parent_id else None
            return ({"name": _text(c.name), "description": _clip(c.description, LIMITS["page_text_chars"]),
                     "parent": _text(parent.name) if parent else "", "live_products": len(members),
                     "example_products": [_text(p.title) for p in members[:6]]},
                    _path("public_category_detail", category_slug=c.slug) if (c.slug or "").strip() else "")
        return {}, ""

    pages = []
    for group in page_catalog(site):
        for page in group.get("pages") or []:
            key = page["key"]
            facts, url = facts_for(key)
            pages.append({
                "key": key,
                "label": _text(page.get("label")) or key,
                "group": group.get("group", ""),
                "type": _PAGE_TYPES.get(key) or _PAGE_TYPES.get(key.partition(":")[0], "page"),
                "url": url,
                "auto": page.get("auto") or {},
                "facts": facts,
            })
    return store, pages


# ---------------------------------------------------------------------------
# Talking to the model
# ---------------------------------------------------------------------------
def _brief_text(brief: dict) -> dict:
    out = {}
    if brief.get("keywords"):
        out["focus_keywords"] = brief["keywords"]
    if brief.get("audience"):
        out["audience"] = brief["audience"]
    if brief.get("location"):
        out["target_location"] = brief["location"]
    if brief.get("tone"):
        out["tone"] = TONES.get(brief["tone"], "")
    return out


def _message(store: dict, brief: dict, batch: list[dict], used: list[str]) -> str:
    pages = [
        {"key": p["key"], "type": p["type"], "name": p["label"], "url_path": p["url"], "facts": p["facts"]}
        for p in batch
    ]
    language = LANGUAGES.get(brief.get("language") or "en", "English")
    return (
        "STORE (JSON):\n" + json.dumps(store, ensure_ascii=False)
        + "\n\nOWNER'S BRIEF (JSON, may be empty):\n" + json.dumps(_brief_text(brief), ensure_ascii=False)
        + "\n\nPAGES (JSON):\n" + json.dumps(pages, ensure_ascii=False, indent=1)
        + "\n\n" + OUTPUT_CONTRACT.format(
            max_keywords=MAX_KEYWORDS, language=language,
            used=json.dumps(used[-LIMITS["used_titles"]:], ensure_ascii=False) if used else "none yet",
        )
    )


def _parse(reply: str, keys: list[str]) -> dict:
    """``{page key: {field: raw value}}`` from the model's JSON, keeping only
    the keys that were asked for. Tolerates a fenced or wrapped object."""
    text = (reply or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("The reply is not JSON.")
    data = json.loads(text[start:end + 1])
    pages = data.get("pages") if isinstance(data, dict) else None
    if not isinstance(pages, dict):
        raise ValueError("The reply has no 'pages' object.")
    wanted = set(keys)
    return {k: v for k, v in pages.items() if k in wanted and isinstance(v, dict)}


def _generate_batch(prompt_text: str, model: str, store: dict, brief: dict, batch: list[dict],
                    used: list[str], creds=None) -> dict:
    """One API call (with one retry). Runs in a worker thread: no DB access."""
    from .llms_txt import _complete

    keys = [p["key"] for p in batch]
    last = None
    for _attempt in range(2):
        try:
            reply = _complete(prompt_text, _message(store, brief, batch, used), model, json_mode=True,
                              temperature=0.4, api_key=getattr(creds, "api_key", None),
                              base_url=getattr(creds, "base_url", None))
            return _parse(reply, keys)
        except Exception as exc:  # retried once, then the caller falls back to auto
            last = exc
    raise RuntimeError(str(last) or "No reply.")


# ---------------------------------------------------------------------------
# Cleaning what comes back
# ---------------------------------------------------------------------------
def clean_value(field: str, value, site_name: str = "") -> str:
    """A model value made safe to publish, or '' when it is unusable."""
    from storefront.seo_pages import fit_title

    text = _nodash(_text(value))
    if not text or _PLACEHOLDER.search(str(value or "")):
        return ""
    if field == "keywords":
        seen, out = set(), []
        for part in re.split(r"[,\n;|]", text):
            term = _WS.sub(" ", part).strip(" .-'\"").lower()
            if 2 <= len(term) <= 60 and term not in seen:
                seen.add(term)
                out.append(term)
            if len(out) >= MAX_KEYWORDS:
                break
        joined = ", ".join(out)
        while len(joined) > FIELD_MAX["keywords"] and out:
            out.pop()
            joined = ", ".join(out)
        return joined
    text = text.strip(" \"'")
    if field == "meta_title":
        text = fit_title(text, site_name, FIELD_MAX["meta_title"])
        return text if len(text) >= 8 else ""
    text = _fit(text, FIELD_MAX[field])
    if field in ("meta_description", "og_description", "twitter_description") and len(text) < MIN_DESCRIPTION:
        return ""
    return text if len(text) >= 3 else ""


def _fallback_keywords(page: dict, site_name: str) -> str:
    facts = page.get("facts") or {}
    label = page["label"].lower()
    terms = [label, facts.get("category", ""), facts.get("brand", ""), site_name]
    if page["type"] in ("product page", "category page"):
        terms.insert(1, f"buy {label} online")
    seen, out = set(), []
    for t in terms:
        t = _text(t).lower()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return ", ".join(out)[: FIELD_MAX["keywords"]]


def finalize(page: dict, ai: dict, existing: dict, mode: str, site_name: str,
             generated: str = "", logos: frozenset | set = frozenset()) -> dict:
    """The complete SEO set for one page: AI values where usable, else the
    page's auto value; in "fill" mode the owner's own values are kept.
    ``generated`` is a share image drawn for this page (prompts.seo_images),
    used where the page has none or only the store logo (``logos``)."""
    from storefront.seo_pages import fit_title

    auto = page.get("auto") or {}
    ai = ai or {}
    got = {f: clean_value(f, ai.get(f), site_name) for f in TEXT_FIELDS}

    meta_title = got["meta_title"] or fit_title(_nodash(_text(auto.get("meta_title"))) or page["label"], site_name)
    meta_desc = got["meta_description"] or _fit(_nodash(_text(auto.get("meta_description"))), FIELD_MAX["meta_description"])
    if not meta_desc:
        meta_desc = _fit(f"{page['label']} at {site_name}. Explore the details and shop online.", FIELD_MAX["meta_description"])
    keywords = got["keywords"] or clean_value("keywords", auto.get("keywords")) or _fallback_keywords(page, site_name)
    og_title = got["og_title"] or _fit(meta_title, FIELD_MAX["og_title"])
    og_desc = got["og_description"] or _fit(meta_desc, FIELD_MAX["og_description"])
    tw_title = got["twitter_title"] or _fit(og_title, FIELD_MAX["twitter_title"])
    tw_desc = got["twitter_description"] or _fit(og_desc, FIELD_MAX["twitter_description"])
    image = generated or _first(auto.get("og_image"))

    out = {
        "meta_title": meta_title,
        "keywords": keywords,
        "meta_description": meta_desc,
        "og_title": og_title,
        "og_image": image,
        "og_description": og_desc,
        "twitter_title": tw_title,
        "twitter_image": generated or _first(auto.get("twitter_image"), image),
        "twitter_description": tw_desc,
        "canonical_url": page.get("url") or "",
    }
    existing = existing if isinstance(existing, dict) else {}
    # "Fill" keeps every value the owner already has. In both modes the
    # fields the AI does not write (share images, canonical, hide from search)
    # keep the owner's own choice and are only filled where empty.
    keep = SEO_KEYS if mode == "fill" else OWNER_KEPT
    for k in keep:
        v = existing.get(k)
        if v not in ("", None, False) and str(v).strip():
            out[k] = True if k == "noindex" else v
    # The owner's own share image is the X card image too, unless they set one.
    own_og = str(existing.get("og_image") or "").strip()
    if own_og and not str(existing.get("twitter_image") or "").strip():
        out["twitter_image"] = own_og
    # The logo (copied into a page's share fields by an earlier run, or left
    # as its only image) is not a share image: a freshly drawn one replaces it.
    if generated:
        for k in ("og_image", "twitter_image"):
            current = str(out.get(k) or "").strip()
            if not current or current in logos:
                out[k] = generated
    return {k: v for k, v in out.items() if v not in ("", None, False)}


def faq_schema(content: dict) -> str:
    """A FAQPage JSON-LD object from the store's own FAQ section (shown on the
    home page), or '' when there are no FAQs. ``<``, ``>`` and ``&`` are
    escaped so the JSON can never close the ``<script>`` it is printed in."""
    items = []
    for f in content.get("faqs") or []:
        if not isinstance(f, dict):
            continue
        q, a = _text(f.get("question")), _text(f.get("answer"))
        if q and a:
            items.append({"@type": "Question", "name": q[:300],
                          "acceptedAnswer": {"@type": "Answer", "text": a[:1200]}})
        if len(items) >= LIMITS["faq_items"]:
            break
    if not items:
        return ""
    raw = json.dumps({"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": items},
                     ensure_ascii=False, indent=1)
    return raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


# ---------------------------------------------------------------------------
# What a run changed (the editor's "Changed / Not changed" view)
# ---------------------------------------------------------------------------
#: Every field the report lists, in the editor's order.
REPORT_FIELDS = (
    ("meta_title", "Meta title"),
    ("meta_description", "Meta description"),
    ("keywords", "Keywords"),
    ("og_title", "Share title (Open Graph)"),
    ("og_description", "Share description (Open Graph)"),
    ("og_image", "Share image (Open Graph)"),
    ("twitter_title", "X card title"),
    ("twitter_description", "X card description"),
    ("twitter_image", "X card image"),
    ("canonical_url", "Canonical URL"),
    ("noindex", "Hide from search engines"),
)
_REPORT_TEXT = 300   # characters of a value kept in the report


def _shown(field: str, value) -> str:
    if field == "noindex":
        return "On" if value else ""
    return _WS.sub(" ", str(value or "")).strip()


def _compare(before: dict, after: dict, *, fields=REPORT_FIELDS, ai=None, image: str = "",
             mode: str = "replace", site_name: str = "") -> tuple[list, list]:
    """``(changed, kept)`` rows for one page. A changed row says where its new
    value came from; a kept row says why it was left alone."""
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    ai_clean = {f: clean_value(f, ai.get(f), site_name) for f in TEXT_FIELDS} if isinstance(ai, dict) else {}
    changed, kept = [], []
    for f, label in fields:
        b, a = _shown(f, before.get(f)), _shown(f, after.get(f))
        if b == a:
            if not b:
                continue   # empty before and after: nothing to report
            if f == "noindex":
                reason = "Your choice to hide this page is never changed"
            elif f in OWNER_KEPT:
                reason = "Your own setting, kept"
            elif mode == "fill":
                reason = "Already filled, kept (Only empty fields)"
            else:
                reason = "Already right, nothing better to write"
            kept.append({"f": f, "label": label, "value": a[:_REPORT_TEXT], "reason": reason})
            continue
        if f in ("og_image", "twitter_image"):
            source = "ai_image" if image and a == image else "page"
        elif f in ("canonical_url", "noindex", "raw_schema_json", "seo_mode"):
            source = "page"
        elif ai_clean.get(f) and ai_clean[f] == a:
            source = "ai"
        else:
            source = "auto"
        changed.append({"f": f, "label": label, "before": b[:_REPORT_TEXT], "after": a[:_REPORT_TEXT],
                        "source": source})
    return changed, kept


def _report(pages, results, images, old_pages, new_pages, old_brand, brand, old_mode, mode, site_name) -> dict:
    out_pages = []
    for page in pages:
        key = page["key"]
        changed, kept = _compare(old_pages.get(key), new_pages.get(key), ai=results.get(key),
                                 image=images.get(key, ""), mode=mode, site_name=site_name)
        out_pages.append({"key": key, "label": page["label"], "group": page.get("group", ""),
                          "changed": changed, "kept": kept})
    # The brand set: the home page in "Same for every page", and the fallback
    # for every page; plus the tab's scope and the FAQ schema.
    store_fields = REPORT_FIELDS + (("raw_schema_json", "FAQ schema (JSON-LD)"),)
    changed, kept = _compare(old_brand, brand, fields=store_fields, ai=results.get("home"),
                             image=images.get("home", ""), mode=mode, site_name=site_name)
    scopes = {"same": "Same for every page", "custom": "Different for every page"}
    before_scope = scopes.get(str(old_mode or "same"), "Same for every page")
    if before_scope != scopes["custom"]:
        changed.insert(0, {"f": "seo_mode", "label": "SEO scope", "before": before_scope,
                           "after": scopes["custom"], "source": "page"})
    else:
        kept.insert(0, {"f": "seo_mode", "label": "SEO scope", "value": scopes["custom"],
                        "reason": "Already set to every page"})
    for row in changed + kept:
        if row["f"] == "raw_schema_json":
            for k in ("before", "after", "value"):
                if row.get(k):
                    row[k] = "FAQ schema with your store's questions"
    out_pages.insert(0, {"key": "_store", "label": "Whole store (brand SEO)", "group": "Store",
                         "changed": changed, "kept": kept})
    summary = {
        "pages": len(pages),
        "pages_changed": sum(1 for p in out_pages if p["changed"] and p["key"] != "_store"),
        "fields_changed": sum(len(p["changed"]) for p in out_pages),
        "fields_kept": sum(len(p["kept"]) for p in out_pages),
        "images_made": len(set(images.values())),
    }
    return {"pages": out_pages, "summary": summary}


def report_for(site, run_id) -> dict | None:
    """The stored report of one of ``site``'s runs, or ``None``."""
    import uuid

    from .models import MagicSeoRun

    try:
        run_id = uuid.UUID(str(run_id))
    except (TypeError, ValueError):
        return None
    row = (MagicSeoRun.objects.for_site(site).filter(pk=run_id)
           .only("id", "site", "status", "report", "finished_at").first())
    if row is None or not isinstance(row.report, dict) or not row.report.get("pages"):
        return None
    return {
        "status": row.status,
        "finished_at": timezone.localtime(row.finished_at).strftime("%d %b %Y, %H:%M") if row.finished_at else "",
        **row.report,
    }


def _log(run, event: str, message: str, level: str = "info", **context) -> None:
    """One line in the Magic SEO app's working log (marketplace console > Logs)."""
    try:
        from marketplace import applog
        from marketplace.modules.magic_seo import SLUG

        applog.log(SLUG, event, message, site=getattr(run, "site", None), level=level,
                   run=getattr(run, "pk", ""), **context)
    except Exception:
        logger.debug("magic seo log line skipped", exc_info=True)


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------
def latest_run(site):
    from .models import MagicSeoRun

    return (
        MagicSeoRun.objects.for_site(site)
        .order_by("-created_at")
        .only("id", "site", "status", "mode", "brief", "pages_total", "pages_done", "pages_written",
              "images_total", "images_done", "images_made", "summary",
              "error", "created_at", "updated_at", "started_at", "finished_at")
        .first()
    )


def _expire_if_stale(run) -> None:
    """A run that stopped moving (worker restart, crash) is closed as failed."""
    from .models import MagicSeoRun

    if run is not None and run.is_active and run.updated_at < timezone.now() - timedelta(minutes=STALE_MINUTES):
        MagicSeoRun.objects.for_site(run.site).filter(pk=run.pk).update(
            status=MagicSeoRun.Status.FAILED, error="The run stopped before it finished.",
            finished_at=timezone.now(), updated_at=timezone.now(),
        )
        run.status = MagicSeoRun.Status.FAILED
        run.error = "The run stopped before it finished."
        _log(run, "run.stalled", "The run stopped before it finished (worker restart or crash).", level="error")


def start(site, user, *, mode: str, brief) -> tuple[object, bool]:
    """Queue a run for ``site``. Returns ``(run, created)``; an active run is
    returned as is (one at a time per store)."""
    from .models import MagicSeoRun

    current = latest_run(site)
    _expire_if_stale(current)
    if current is not None and current.is_active:
        return current, False
    run = MagicSeoRun.objects.create(
        site=site,
        mode=mode if mode in MagicSeoRun.Mode.values else MagicSeoRun.Mode.REPLACE,
        brief=clean_brief(brief),
        requested_by=user if getattr(user, "pk", None) else None,
    )
    from .tasks import generate_magic_seo

    transaction.on_commit(lambda: generate_magic_seo.delay(str(run.pk)))
    _log(run, "run.queued", f"Queued ({run.get_mode_display()})",
         requested_by=getattr(user, "email", "") or "", mode=run.mode,
         images=run.brief.get("images", True), language=run.brief.get("language", ""),
         tone=run.brief.get("tone", ""), has_keywords=bool(run.brief.get("keywords")))
    return run, True


def execute(run) -> str:
    """The Celery job: write, validate and apply. Returns the final status."""
    from .models import MagicSeoRun

    S = MagicSeoRun.Status
    site = run.site
    qs = MagicSeoRun.objects.for_site(site).filter(pk=run.pk)

    def fail(message: str) -> str:
        qs.update(status=S.FAILED, error=message[:2000], finished_at=timezone.now(), updated_at=timezone.now())
        _log(run, "run.failed", message[:480], level="error",
             seconds=round(time.monotonic() - started, 1) if started else 0)
        return S.FAILED

    started = 0.0
    if run.status != S.QUEUED:
        return run.status
    started = time.monotonic()
    qs.update(status=S.RUNNING, started_at=timezone.now(), updated_at=timezone.now())

    try:
        from marketplace.modules.magic_seo import entitled

        if not entitled(site):
            return fail("Magic SEO is not active for this store (app not installed or licence ended).")
    except Exception:
        logger.warning("magic seo entitlement check failed", exc_info=True)
        return fail("Could not confirm the Magic SEO licence. Please try again.")

    reason = unavailable_reason()
    if reason:
        logger.error("magic seo unavailable: %s", reason)
        return fail(reason)
    prompt = ensure_prompt()
    creds = ai_credentials()
    # The app's model (admin > Apps > Magic SEO), else the prompt's, else the server's.
    model = creds.text(prompt.model or "")

    try:
        store, pages = collect(site)
    except Exception as exc:
        logger.exception("magic seo collect failed for site %s", site.pk)
        return fail(f"Could not read the store: {exc}")
    if not pages:
        return fail("The store has no pages to write SEO for yet.")

    to_write = pages[: LIMITS["max_pages"]]
    size = LIMITS["batch_pages"]
    batches = [to_write[i:i + size] for i in range(0, len(to_write), size)]
    qs.update(pages_total=len(pages), pages_done=0, updated_at=timezone.now(),
              prompt=prompt, model_used=model[:120])
    _log(run, "run.started", f"Writing SEO for {len(pages)} pages in {len(batches)} batches",
         pages=len(pages), batches=len(batches), model=model, key_source=creds.key_source,
         prompt=getattr(prompt, "name", ""))

    results: dict[str, dict] = {}
    errors: list[str] = []
    used: list[str] = []
    done = 0
    # The first batch runs alone so its titles seed the "already used" list
    # for the rest, which then run in parallel.
    first, rest = batches[:1], batches[1:]
    for group in (first, rest):
        if not group:
            continue
        with ThreadPoolExecutor(max_workers=max(1, min(LIMITS["workers"], len(group)))) as pool:
            futures = {
                pool.submit(_generate_batch, prompt.prompt, model, store, run.brief or {}, b, list(used), creds): b
                for b in group
            }
            for fut in as_completed(futures):
                batch = futures[fut]
                try:
                    got = fut.result()
                    results.update(got)
                except Exception as exc:
                    errors.append(str(exc)[:300])
                    logger.warning("magic seo batch failed for site %s: %s", site.pk, exc)
                    _log(run, "batch.failed", f"A batch of {len(batch)} pages failed; they use their auto SEO",
                         level="warning", error=str(exc)[:600], pages=[p["label"] for p in batch][:12])
                done += len(batch)
                qs.update(pages_done=done, updated_at=timezone.now())
        used.extend(_text(v.get("meta_title")) for v in results.values() if isinstance(v, dict) and v.get("meta_title"))

    if not results:
        return fail("The AI could not write the SEO right now. " + (errors[0] if errors else ""))
    _log(run, "text.done", f"AI wrote {len(results)} of {len(pages)} pages",
         written=len(results), failed_batches=len(errors), seconds=round(time.monotonic() - started, 1))

    # Share images for pages that have none (a failure here never fails the
    # run: those pages keep their fallback image).
    images: dict[str, str] = {}
    if (run.brief or {}).get("images", True):
        try:
            from . import seo_images

            images = seo_images.generate(run, store, pages, started, creds)
        except Exception:
            logger.warning("magic seo images failed for site %s", site.pk, exc_info=True)

    written = 0
    try:
        written = apply(run, pages, results, images)
    except Exception as exc:
        logger.exception("magic seo apply failed for site %s", site.pk)
        return fail(f"Could not save the SEO: {exc}")

    qs.update(
        status=S.DONE, pages_done=len(pages), pages_written=written, finished_at=timezone.now(),
        updated_at=timezone.now(),
        error=("Some pages used their auto SEO: " + errors[0]) if errors else "",
    )
    summary = (qs.values_list("summary", flat=True).first() or {})
    _log(run, "run.done",
         f"Done: {summary.get('fields_changed', 0)} fields changed on {summary.get('pages_changed', 0)} pages, "
         f"{summary.get('fields_kept', 0)} kept, {summary.get('images_made', 0)} share images",
         seconds=round(time.monotonic() - started, 1), written=written, **{k: v for k, v in summary.items()})
    _after_save(run, len(pages), written)
    return S.DONE


def apply(run, pages: list[dict], results: dict, images: dict | None = None) -> int:
    """Write the SEO tab. Returns how many pages carry AI-written text.
    ``images`` maps page keys to share images drawn for them."""
    from tenancy.models import Site

    from .models import MagicSeoRun
    from .seo_images import logo_urls

    mode = run.mode
    images = images or {}
    with transaction.atomic():
        site = Site.objects.select_for_update().get(pk=run.site_id)
        content = dict(site.content or {})
        name = _first(content.get("site_name"), content.get("websiteName"), site.name, "Our store")
        logos = logo_urls(content)
        previous = {k: content.get(k) for k in ("seo", "seo_pages", "seo_mode")}

        old_pages = content.get("seo_pages") if isinstance(content.get("seo_pages"), dict) else {}
        new_pages = dict(old_pages)
        written = 0
        for page in pages:
            ai = results.get(page["key"])
            if ai:
                written += 1
            new_pages[page["key"]] = finalize(page, ai, old_pages.get(page["key"]), mode, name,
                                              images.get(page["key"], ""), logos)

        # The brand set ("Same for every page", and the fallback for any page
        # added later): the home page's set, plus the home-only extras.
        old_brand = content.get("seo") if isinstance(content.get("seo"), dict) else {}
        home = next((p for p in pages if p["key"] == "home"), None)
        brand = dict(old_brand)
        if home is not None:
            fresh = finalize(home, results.get("home"), old_brand, mode, name, images.get("home", ""), logos)
            for k in ("meta_title", "keywords", "meta_description", "og_title", "og_image", "og_description",
                      "twitter_title", "twitter_image", "twitter_description", "canonical_url"):
                brand[k] = fresh.get(k, "")
            brand["noindex"] = bool(old_brand.get("noindex"))
        if not str(brand.get("raw_schema_json") or "").strip():
            brand["raw_schema_json"] = faq_schema(content)

        report = _report(pages, results, images, old_pages, new_pages, old_brand, brand,
                         previous.get("seo_mode"), mode, name)
        content["seo"] = brand
        content["seo_pages"] = new_pages
        content["seo_mode"] = "custom"
        site.content = content
        site.save(update_fields=["content", "updated_at"])
        MagicSeoRun.objects.for_site(site).filter(pk=run.pk).update(
            previous=previous, report=report, summary=report["summary"],
        )
    return written


def _after_save(run, total: int, written: int) -> None:
    """Side effects of a finished run: llms.txt refresh, store activity, audit."""
    site = run.site
    try:
        from .llms_txt import schedule

        schedule(site)
    except Exception:
        pass
    try:
        from commerce import activity

        activity.record(
            "store.content.updated", site=site, target=site, target_kind="Site", label="SEO (Magic SEO)",
            actor=run.requested_by, changes={"section": "seo", "magic_seo": True, "pages": total, "written": written},
        )
    except Exception:
        logger.debug("magic seo activity record skipped", exc_info=True)


def undo(run) -> bool:
    """Put the SEO tab back as it was before ``run``. Only the store's latest
    finished run can be undone, once."""
    from tenancy.models import Site

    from .models import MagicSeoRun

    if run is None or run.status != MagicSeoRun.Status.DONE:
        return False
    latest = latest_run(run.site)
    if latest is None or latest.pk != run.pk:
        return False
    full = MagicSeoRun.objects.for_site(run.site).filter(pk=run.pk).only("id", "previous").first()
    previous = full.previous if full is not None and isinstance(full.previous, dict) else {}
    with transaction.atomic():
        site = Site.objects.select_for_update().get(pk=run.site_id)
        content = dict(site.content or {})
        for key in ("seo", "seo_pages", "seo_mode"):
            value = previous.get(key)
            if value is None:
                content.pop(key, None)
            else:
                content[key] = value
        site.content = content
        site.save(update_fields=["content", "updated_at"])
        MagicSeoRun.objects.for_site(site).filter(pk=run.pk).update(
            status=MagicSeoRun.Status.UNDONE, updated_at=timezone.now(),
        )
    _log(run, "run.undone", "The owner undid this run; the SEO tab is back to how it was")
    try:
        from .llms_txt import schedule

        schedule(run.site)
    except Exception:
        pass
    return True


def payload(run) -> dict:
    """What the editor polls: progress, and when finished, the new SEO tab."""
    if run is None:
        return {"status": "none"}
    _expire_if_stale(run)
    out = {
        "id": str(run.pk),
        "status": run.status,
        "mode": run.mode,
        "total": run.pages_total,
        "done": run.pages_done,
        "written": run.pages_written,
        "images_total": run.images_total,
        "images_done": run.images_done,
        "images_made": run.images_made,
        # "images" while share images are being drawn (after the text).
        "phase": "images" if run.is_active and run.images_total else "text",
        "brief": run.brief or {},
        "finished_at": timezone.localtime(run.finished_at).strftime("%d %b, %H:%M") if run.finished_at else "",
        # Counts for the done state ("312 fields changed, 48 kept").
        "summary": run.summary if isinstance(getattr(run, "summary", None), dict) else {},
    }
    if run.status == "failed":
        # Staff-facing details stay in admin; owners get a plain message.
        out["error"] = "Magic SEO couldn't finish this time. Nothing was changed. Please try again in a few minutes."
    if run.status == "done":
        notes = []
        if run.error:
            notes.append("A few pages used their auto SEO because the AI didn't answer for them.")
        if run.images_total and run.images_made < run.images_total:
            notes.append("Some share images couldn't be created; those pages keep their current image. Run again to retry.")
        if notes:
            out["note"] = " ".join(notes)
    return out
