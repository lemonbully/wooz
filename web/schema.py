"""Structured data, canonical + social meta for onewooz.com  the SEO/AEO/GEO layer.

This is the one place that decides, per marketing page, what a search engine, an
answer engine (AEO  Google's AI Overviews, Bing Copilot), and a generative
engine (GEO  ChatGPT, Claude, Perplexity) are told about the page. Everything
is data: pure functions here build dictionaries, a context processor
(:mod:`web.context_processors`) turns them into ``seo`` + ``schema_jsonld`` for
the marketing shell head, and nothing outside the marketing surface is touched.

Two products come out of this module:

* :func:`head_meta`  the canonical URL, robots directive, keywords and the
  Open-Graph / Twitter-card values for one page.
* :func:`json_ld`  a single JSON-LD ``@graph`` for the page: an interlinked set
  of Schema.org nodes (Organization, WebSite, SoftwareApplication, WebPage,
  BreadcrumbList, FAQPage, BlogPosting …) cross-referenced by stable ``@id`` so
  the engines can resolve *entities*, not just keywords. Entity graphs are what
  earn the knowledge-panel, the "AI Overview" citation and the rich result.

Design rules kept deliberately:

* **One brand fact, one place.** Name, operator, address and topical expertise
  live in the constants below and nowhere else, so the schema can never drift
  from the footer.
* **Never assert what we can't stand behind.** No invented ``aggregateRating``,
  no star reviews, no ``sameAs`` to profiles we don't own  fabricated review
  markup is a manual-action risk and a trust cost. Prices come from the real
  paid plans only; no ₹0 offer is ever advertised (nothing is sold as free).
* **Fail open.** Any error here must degrade to "no structured data", never to a
  broken page  the caller wraps every entry point in try/except.

The registry (:data:`PAGE_SEO`) is the human-editable heart: one keyword-tuned
record per marketing page (title, description, keywords, FAQ, entity focus).
"""

from __future__ import annotations

import json
import re
from typing import Any

from django.templatetags.static import static
from django.utils import timezone

# ---------------------------------------------------------------------------
# Brand facts  the single source of truth for every schema node
# ---------------------------------------------------------------------------
BRAND_NAME = "OneWooz"
#: Other spellings people type for the brand. Google's site-name system reads
#: WebSite.alternateName when BRAND_NAME alone is not enough to displace the bare
#: domain it shows for new sites. Never list the domain itself here: Google may
#: pick an alternate as the displayed name.
BRAND_ALT_NAMES = ["One Wooz", "Onewooz"]
LEGAL_NAME = "The Artless"
LOGO_STATIC = "img/final_onewooz.png"

#: Fallback social-share image. Reused as the Organization/logo image until a
#: purpose-built 1200×630 OG card exists. Swap this static path when one does.
OG_IMAGE_STATIC = "img/final_onewooz.png"

SUPPORT_EMAIL = "hello@onewooz.com"

#: Postal address of the operating company (mirrors the marketing footer).
ADDRESS = {
    "street": "H.no. 866, Street no. 11, Mustafabad New",
    "locality": "Delhi",
    "region": "Delhi",
    "postal_code": "110094",
    "country": "IN",
}

#: The agreed brand lines. Used verbatim as the Organization slogan, the site
#: description, the marketing footer and the llms.txt identity, so every surface
#: that describes OneWooz says the same thing.
BRAND_LINE = "Run your business, not just your website."
BRAND_PROMISE = (
    "Your own online store in India, built-in SEO AEO GEO and marketing so "
    "you're not stuck paying for ads."
)

#: One-line positioning reused as the Organization/WebSite description default.
TAGLINE = f"{BRAND_LINE} {BRAND_PROMISE}"

#: Plain factual description of the product (SoftwareApplication, llms.txt).
#: OneWooz is an ecommerce platform that runs the store, not a website builder.
PRODUCT_DESCRIPTION = (
    "OneWooz is an ecommerce platform that runs your own online store in India: "
    "products, checkout, payments, shipping, SEO and marketing, on your own "
    "domain, for D2C brands and B2B retailers, with no developer."
)

#: Topical expertise the Organization is declared to know about. This is a
#: primary GEO signal: it tells generative engines which subjects to associate
#: the brand with.
KNOWS_ABOUT = [
    "Ecommerce platform",
    "Online store for D2C brands",
    "B2B ecommerce",
    "Online payments and checkout",
    "Shipping and order fulfilment",
    "Custom domains and SSL",
    "Ecommerce SEO",
    "Answer engine optimisation (AEO)",
    "Generative engine optimisation (GEO)",
    "Email marketing for online stores",
]

#: The product's feature list, surfaced on the SoftwareApplication node. Written
#: as benefit statements an answer engine can quote verbatim.
FEATURE_LIST = [
    "One-click store creation tailored to your industry",
    "Products, cart and secure checkout out of the box",
    "Online payments (cards, UPI, cash on delivery)",
    "Live shipping rates and label printing",
    "Your own custom domain with automatic HTTPS",
    "Built-in SEO, AEO and GEO, analytics and content pages",
    "Built-in email marketing to your own customers",
    "Transparent plans with a small commerce fee per order",
]


# ---------------------------------------------------------------------------
# Per-page SEO registry
# ---------------------------------------------------------------------------
# Keyed by the resolved ``url_name`` on the marketing (web) urlconf. Each record
# drives the <title>, meta description, keywords, Open-Graph/Twitter values and
# the page-type JSON-LD. Titles stay at or under ~60 chars and descriptions at
# or under ~155 so they render un-truncated in the SERP. ``focus`` is the entity
# a WebPage is chiefly ``about`` ("org" or "app").
#
# FAQPage markup is only ever emitted for questions the reader can see on the
# page (Google's structured-data policy): see VISIBLE_FAQ below. A record never
# carries its own hidden "faq" list.
PAGE_SEO: dict[str, dict[str, Any]] = {
    "home": {
        "title": "OneWooz: Run Your Business, Not Just Your Website",
        "description": (
            "Your own online store in India with built-in SEO, AEO, GEO and "
            "marketing, so you're not stuck paying for ads. Products, checkout, "
            "payments and shipping included."
        ),
        "keywords": [
            "online store India", "ecommerce platform India", "D2C ecommerce platform",
            "B2B ecommerce platform", "build an online store", "ecommerce SEO",
        ],
        "focus": "app",
        "og_type": "website",
    },
    "platform": {
        "title": "Platform: The Ecommerce Engine Behind Your Store | OneWooz",
        "description": (
            "See how OneWooz runs a working online store: one-click creation, "
            "payments, live shipping, custom domains with HTTPS, SEO, analytics "
            "and email marketing, no code required."
        ),
        "keywords": [
            "ecommerce platform", "online store platform", "no-code ecommerce",
            "ecommerce SEO", "online store India",
        ],
        "focus": "app",
        "og_type": "website",
    },
    "pricing": {
        "title": "Pricing: Simple Plans, Small Commerce Fee | OneWooz",
        "description": (
            "OneWooz pricing: transparent plans for your online store, plus a "
            "small percentage of each order as a commerce fee. See every price "
            "before you subscribe."
        ),
        "keywords": [
            "ecommerce platform pricing", "online store pricing India",
            "commerce fee", "online store plans",
        ],
        "focus": "app",
        "og_type": "website",
    },
    "demo": {
        "title": "Demo: Live Stores Built on OneWooz",
        "description": (
            "Open a live demo store built on OneWooz. Browse products, add to "
            "cart and walk through checkout, just as your shoppers would."
        ),
        "keywords": [
            "online store demo", "ecommerce demo store", "OneWooz demo",
        ],
        "focus": "app",
        "og_type": "website",
    },
    "solutions": {
        "title": "Solutions: Ecommerce for D2C Brands & B2B Retailers | OneWooz",
        "description": (
            "OneWooz for D2C brands and B2B retailers: a store tailored to your "
            "industry, with payments, shipping, catalogue and enquiry flows "
            "built in."
        ),
        "keywords": [
            "D2C ecommerce platform", "B2B ecommerce website", "ecommerce for brands",
            "online store for retailers",
        ],
        "focus": "app",
        "og_type": "website",
    },
    "customers": {
        "title": "Customers: Stores Launched on OneWooz",
        "description": (
            "See the kinds of D2C and B2B stores merchants run on OneWooz: live, "
            "on their own domains, without developers."
        ),
        "keywords": [
            "online store examples", "ecommerce store examples India", "OneWooz customers",
        ],
        "focus": "org",
        "og_type": "website",
    },
    "about": {
        "title": "About OneWooz: Run Your Business, Not Just Your Website",
        "description": (
            "OneWooz, operated by The Artless, gives Indian brands their own "
            "online store with built-in SEO, AEO, GEO and marketing. Learn who we "
            "are and what we build."
        ),
        "keywords": ["about OneWooz", "The Artless", "ecommerce company India"],
        "focus": "org",
        "og_type": "website",
    },
    "security": {
        "title": "Security: How OneWooz Protects Your Store & Data",
        "description": (
            "How OneWooz keeps stores and customer data safe: encrypted "
            "credentials, tenant isolation, HTTPS everywhere, passkeys and a full "
            "audit trail."
        ),
        "keywords": [
            "ecommerce security", "secure online store", "secure checkout",
        ],
        "focus": "org",
        "og_type": "website",
    },
    "contact": {
        "title": "Contact OneWooz: Talk to the Ecommerce Team",
        "description": (
            "Get in touch with OneWooz. Ask about your online store, pricing, "
            "custom domains or B2B. We usually reply within one business day."
        ),
        "keywords": ["contact OneWooz", "ecommerce support", "sales enquiry"],
        "focus": "org",
        "kind": "ContactPage",
        "og_type": "website",
    },
    "support": {
        "title": "Support: Help With Your OneWooz Store",
        "description": (
            "OneWooz support: raise a ticket for help with your store, payments, "
            "shipping, custom domains or your account. We're here to get you "
            "selling."
        ),
        "keywords": ["OneWooz support", "ecommerce help", "store help desk"],
        "focus": "org",
        "og_type": "website",
    },
    "topics": {
        "title": "Topics | Ask OneWooz",
        "description": (
            "Browse Ask OneWooz by topic: payments, shipping, GST, domains, "
            "marketing and more, answered by sellers and the OneWooz team."
        ),
        "keywords": ["ecommerce community", "online seller questions"],
        "focus": "org",
        "og_type": "website",
    },
    "live": {
        "title": "Live: What Sellers Are Asking Now | Ask OneWooz",
        "description": (
            "See the questions online sellers are asking right now, what's hot "
            "today, and which questions still need an answer."
        ),
        "keywords": ["ecommerce community", "seller questions live"],
        "focus": "org",
        "og_type": "website",
    },
    "ask_home": {
        "title": "Ask OneWooz: Questions About Selling Online, Answered",
        "description": (
            "The community for Indian online sellers. Ask about payments, "
            "shipping, GST or your store and get answers from sellers and the "
            "OneWooz team."
        ),
        "keywords": ["ask OneWooz", "ecommerce questions", "online store help"],
        "focus": "org",
        "og_type": "website",
    },
    "blog_list": {
        "title": "Blog: Ecommerce Guides for Indian Brands | OneWooz",
        "description": (
            "Practical guides on running and growing an online store: payments, "
            "shipping, SEO and D2C/B2B playbooks from OneWooz."
        ),
        "keywords": [
            "ecommerce blog", "online store guides", "ecommerce tips India",
        ],
        "focus": "org",
        "kind": "CollectionPage",
        "og_type": "website",
    },
    "core_pages": {
        "title": "Resources & Help | OneWooz",
        "description": (
            "Guides, help articles and reference pages for launching and running "
            "your OneWooz online store."
        ),
        "keywords": ["OneWooz help", "ecommerce resources", "store setup guide"],
        "focus": "org",
        "kind": "CollectionPage",
        "og_type": "website",
    },
}

#: Titles/descriptions for the two legal pages served through the restricted
#: ``<page:slug>`` route (url_name ``page``), keyed by slug.
LEGAL_SEO = {
    "privacy": {
        "title": "Privacy Policy | OneWooz",
        "description": (
            "How OneWooz collects, uses and protects personal data across the "
            "platform and the stores it hosts."
        ),
        "keywords": ["OneWooz privacy policy", "data protection"],
    },
    "terms": {
        "title": "Terms of Service | OneWooz",
        "description": (
            "The terms that govern use of OneWooz and the stores published on the "
            "platform."
        ),
        "keywords": ["OneWooz terms of service", "terms and conditions"],
    },
}

_DEFAULT = {
    "title": BRAND_NAME,
    "description": TAGLINE,
    "keywords": ["ecommerce platform", "online store India"],
    "focus": "org",
    "og_type": "website",
}


# ---------------------------------------------------------------------------
# Visible FAQs: one source for the on-page Q&A and its FAQPage markup
# ---------------------------------------------------------------------------
# Each answer is HTML (it may carry a link); ``[[web:<name>]]`` tokens resolve to
# that marketing URL at render time. The JSON-LD answer is the same HTML reduced
# to text, so the markup can never drift from what the reader sees.
VISIBLE_FAQ: dict[str, list[dict[str, str]]] = {
    "pricing": [
        {
            "q": "Do you charge anything on my orders?",
            "a": (
                "Yes, a small commerce fee. Alongside your plan, we charge a small "
                "percentage of each order as a commerce fee. Payment settlement goes "
                "to your own account through your payment provider."
            ),
        },
        {
            "q": "What does it cost to get started?",
            "a": (
                "Choose the plan that fits your store. Every plan shows its price "
                "before you subscribe, and a small percentage of each order is "
                "charged as a commerce fee. GST is added on bills."
            ),
        },
        {
            "q": "Can I leave?",
            "a": (
                "Any time. Export your data and point your domain elsewhere. There "
                "is no lock-in and nothing is held hostage."
            ),
        },
        {
            "q": "Who owns the domain?",
            "a": (
                "You do. Connect a domain you already own, or one we help you "
                "register in your name."
            ),
        },
        {
            "q": "Is email marketing included?",
            "a": (
                "Yes, it is built into the dashboard: campaigns, automatic welcome "
                "series, templates and AI writing, with opens and clicks tracked. "
                "Plans that list it include a monthly sending allowance. "
                '<a href="[[web:platform]]#email-marketing">See how it works</a>.'
            ),
        },
    ],
    "contact": [
        {
            "q": "How quickly will I hear back?",
            "a": (
                "Within one working day, usually much sooner. Security reports are "
                "acknowledged within one working day too."
            ),
        },
        {
            "q": "Can I talk to someone before I buy a plan?",
            "a": (
                f'Yes. Email <a href="mailto:{SUPPORT_EMAIL}">{SUPPORT_EMAIL}</a> or '
                "use the enquiry form above and pick “Sales”. We'll tell you honestly "
                "whether OneWooz fits what you sell."
            ),
        },
        {
            "q": "I already sell somewhere else. Can I move my store over?",
            "a": (
                "Usually, yes. Send an enquiry with “Migrating an existing store” and "
                "tell us where you're selling now and roughly how many products you "
                "have. We'll walk you through moving your catalogue, domain and "
                "customers."
            ),
        },
        {
            "q": "Do you charge anything on my orders?",
            "a": (
                "Yes, a small commerce fee. Alongside your plan, we charge a small "
                "percentage of each order as a commerce fee. Your revenue settles to "
                "your own account through your payment provider."
            ),
        },
        {
            "q": "My live store has a problem right now. Where do I go?",
            "a": (
                'Raise a ticket on the <a href="[[web:support]]">support desk</a>. '
                "Tell us what's broken and, if you can, an order or store reference. "
                "It reaches us faster than email."
            ),
        },
        {
            "q": "Do you sell to businesses (B2B), not just shoppers?",
            "a": (
                "Yes. OneWooz builds both a shop that takes money (D2C) and a "
                "catalogue that captures enquiries and quotes (B2B). See "
                '<a href="[[web:solutions]]">Solutions</a> for both paths.'
            ),
        },
        {
            "q": "Which currencies and payment methods do you support?",
            "a": (
                "India first: rupee pricing with cards, UPI and cash on delivery, "
                "plus part-payment. More regions are on the way; ask if you need one "
                "that isn't listed."
            ),
        },
        {
            "q": "How do I report a security issue?",
            "a": (
                'Email <a href="mailto:security@onewooz.com">security@onewooz.com</a>. '
                "We won't pursue anyone who reports in good faith and doesn't access "
                "other people's data. More on our "
                '<a href="[[web:security]]">security page</a>.'
            ),
        },
    ],
}

_URL_TOKEN = re.compile(r"\[\[(web:[a-z_]+)\]\]")


def visible_faq(key: str) -> list[dict[str, Any]]:
    """The page's visible FAQ: ``q``, ``a_html`` (safe, links resolved) and
    ``a`` (the same answer as plain text, for FAQPage markup)."""
    from django.urls import NoReverseMatch, reverse
    from django.utils.safestring import mark_safe

    from .core_tree import text_of

    def resolve(match):
        try:
            return reverse(match.group(1))
        except NoReverseMatch:
            return "/"

    out = []
    for item in VISIBLE_FAQ.get(key, []):
        html = _URL_TOKEN.sub(resolve, item["a"])
        out.append({"q": item["q"], "a_html": mark_safe(html), "a": text_of(html)})
    return out


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _abs(request, path: str) -> str:
    """Absolutise a path against the current (marketing) origin.

    Uses ``build_absolute_uri`` so the URL is correct on onewooz.com and under
    the single-host development prefix alike  host and scheme are never
    hardcoded.
    """
    return request.build_absolute_uri(path)


def _abs_static(request, static_path: str) -> str:
    # The OneWooz logo (also the fallback share image) follows the one
    # uploaded in admin → onewooz.com SEO, when set.
    if static_path in (LOGO_STATIC, OG_IMAGE_STATIC):
        try:
            from .seo_overrides import brand_assets

            uploaded = brand_assets().get("logo")
        except Exception:
            uploaded = ""
        if uploaded:
            return request.build_absolute_uri(uploaded) if uploaded.startswith("/") else uploaded
    return _abs(request, static(static_path))


def _origin(request) -> str:
    """The scheme+host (+dev prefix) all site-wide ``@id`` anchors hang off."""
    return _abs(request, "/").rstrip("/")


def _canonical(request) -> str:
    """Canonical URL for this page: the path only, query string dropped."""
    return _abs(request, request.path)


def _url_name(request) -> str:
    match = getattr(request, "resolver_match", None)
    return getattr(match, "url_name", "") or ""


def _record(request) -> dict[str, Any]:
    """The registry record for the current page, resolving legal sub-pages and
    honouring any per-request overrides the view attached as ``seo_extra``."""
    name = _url_name(request)
    if name == "page":
        slug = (getattr(request, "resolver_match", None).kwargs or {}).get("slug", "")
        rec = {**_DEFAULT, **LEGAL_SEO.get(slug, {})}
    else:
        rec = {**_DEFAULT, **PAGE_SEO.get(name, {})}
    extra = getattr(request, "seo_extra", None)
    if isinstance(extra, dict):
        rec = {**rec, **extra}
    # SEO OneWooz staff set in the Django admin (onewooz.com SEO) wins over the
    # built-in registry; it fails open to the record unchanged.
    try:
        from .seo_overrides import apply

        rec = apply(request, rec)
    except Exception:
        pass
    return rec


# ---------------------------------------------------------------------------
# Head meta  canonical, robots, keywords, Open Graph, Twitter
# ---------------------------------------------------------------------------
def head_meta(request) -> dict[str, Any]:
    """Everything the shell head needs for one page except the JSON-LD graph."""
    rec = _record(request)
    canonical = rec.get("canonical_url") or _canonical(request)
    image = rec.get("og_image") or _abs_static(request, OG_IMAGE_STATIC)
    keywords = rec.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]
    og_title = rec.get("og_title") or rec["title"]
    og_description = rec.get("og_description") or rec["description"]
    return {
        "title": rec["title"],
        "description": rec["description"],
        # True when the admin set this page's meta title, so templates that
        # build their own <title> (blog posts, core pages) use it instead.
        "custom_title": bool(rec.get("custom_title")),
        "canonical": canonical,
        # Invite full-size image previews and untruncated snippets, which is what
        # AI Overviews and rich results draw on. A page hidden in the admin
        # (onewooz.com SEO → Page SEO → Hide from search) is noindex instead.
        "robots": (
            "noindex, follow"
            if rec.get("noindex")
            else "index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1"
        ),
        "keywords": ", ".join(keywords) if keywords else "",
        "og_type": rec.get("og_type", "website"),
        "og_title": og_title,
        "og_description": og_description,
        "og_image": image,
        "twitter_title": rec.get("twitter_title") or og_title,
        "twitter_description": rec.get("twitter_description") or og_description,
        "twitter_image": rec.get("twitter_image") or image,
        "site_name": BRAND_NAME,
        "locale": "en_IN",
        # JSON-LD blocks pasted in the admin (already validated, compacted and
        # "<"-escaped by web.seo_overrides.parse_schema).
        "custom_schema": list(rec.get("custom_schema") or []),
    }


# ---------------------------------------------------------------------------
# JSON-LD @graph nodes
# ---------------------------------------------------------------------------
def _organization(request) -> dict[str, Any]:
    origin = _origin(request)
    return {
        "@type": "Organization",
        "@id": f"{origin}/#organization",
        "name": BRAND_NAME,
        "alternateName": BRAND_ALT_NAMES,
        "legalName": LEGAL_NAME,
        "url": f"{origin}/",
        "logo": {
            "@type": "ImageObject",
            "@id": f"{origin}/#logo",
            "url": _abs_static(request, LOGO_STATIC),
            "caption": BRAND_NAME,
        },
        "image": {"@id": f"{origin}/#logo"},
        "description": TAGLINE,
        "slogan": BRAND_LINE,
        "email": SUPPORT_EMAIL,
        "knowsAbout": KNOWS_ABOUT,
        "address": {
            "@type": "PostalAddress",
            "streetAddress": ADDRESS["street"],
            "addressLocality": ADDRESS["locality"],
            "addressRegion": ADDRESS["region"],
            "postalCode": ADDRESS["postal_code"],
            "addressCountry": ADDRESS["country"],
        },
        "contactPoint": {
            "@type": "ContactPoint",
            "email": SUPPORT_EMAIL,
            "contactType": "customer support",
            "availableLanguage": ["English", "Hindi"],
            "areaServed": "IN",
        },
        "areaServed": {"@type": "Country", "name": "India"},
    }


def _website(request) -> dict[str, Any]:
    origin = _origin(request)
    return {
        "@type": "WebSite",
        "@id": f"{origin}/#website",
        "url": f"{origin}/",
        "name": BRAND_NAME,
        "alternateName": BRAND_ALT_NAMES,
        "description": TAGLINE,
        "inLanguage": "en",
        "publisher": {"@id": f"{origin}/#organization"},
    }


def _software_application(request, plans=None) -> dict[str, Any]:
    """The product itself  the node that carries the ranking keywords and the
    real plan pricing. ``plans`` (from the pricing view) enriches the offers."""
    origin = _origin(request)
    node: dict[str, Any] = {
        "@type": "SoftwareApplication",
        "@id": f"{origin}/#software",
        "name": BRAND_NAME,
        "applicationCategory": "BusinessApplication",
        "applicationSubCategory": "Ecommerce platform",
        "operatingSystem": "Web-based (cloud)",
        "url": f"{origin}/",
        "description": PRODUCT_DESCRIPTION,
        "featureList": FEATURE_LIST,
        "screenshot": _abs_static(request, OG_IMAGE_STATIC),
        "image": {"@id": f"{origin}/#logo"},
        "provider": {"@id": f"{origin}/#organization"},
        "publisher": {"@id": f"{origin}/#organization"},
        "audience": {
            "@type": "BusinessAudience",
            "name": "D2C brands and B2B retailers",
        },
    }
    offers = _offers(plans) if plans is not None else _sitewide_offers()
    if offers:
        node["offers"] = offers
    return node


_OFFERS_CACHE_KEY = "web:schema:offers:v1"


def _sitewide_offers() -> dict[str, Any] | None:
    """The same paid-plan AggregateOffer the pricing page emits, for every other
    page that carries the SoftwareApplication node. Cached for ten minutes so a
    marketing page render does not re-read the plan tables each time."""
    from django.core.cache import cache

    try:
        cached = cache.get(_OFFERS_CACHE_KEY)
    except Exception:
        cached = None
    if cached is not None:
        return cached or None
    try:
        from billing.pricing import plan_cards

        offers = _offers(plan_cards())
    except Exception:
        return None
    try:
        cache.set(_OFFERS_CACHE_KEY, offers or {}, 600)
    except Exception:
        pass
    return offers


def _offers(plans=None) -> dict[str, Any] | None:
    """An AggregateOffer for the product, or None when there is nothing to offer.

    One Offer per purchasable plan with a price above zero, from the live plan
    figures. Nothing on onewooz.com is advertised as free, so no ₹0 offer is
    ever emitted.
    """
    currency = "INR"
    individual: list[dict[str, Any]] = []
    if plans:
        for plan in plans:
            try:
                if not getattr(plan, "is_purchasable", False):
                    continue
                if getattr(plan, "is_one_time", False):
                    amount = float(getattr(plan, "one_time_total", 0) or 0)
                else:
                    amount = float(getattr(plan, "card_total", 0) or 0)
                if amount <= 0:
                    continue
                individual.append({
                    "@type": "Offer",
                    "name": getattr(plan, "name", "Plan"),
                    "price": f"{amount:.2f}",
                    "priceCurrency": getattr(plan, "currency", "") or currency,
                    "category": "subscription",
                })
            except Exception:
                continue
    if not individual:
        return None
    prices = [float(o["price"]) for o in individual]
    return {
        "@type": "AggregateOffer",
        "priceCurrency": currency,
        "lowPrice": f"{min(prices):.2f}",
        "offerCount": len(individual),
        "offers": individual,
    }


def _breadcrumb(request, crumbs: list[dict[str, str]]) -> dict[str, Any]:
    """A BreadcrumbList. ``crumbs`` is [{'name', 'url'} …]; a leading Home is
    prepended automatically."""
    origin = _origin(request)
    items = [{"name": "Home", "url": f"{origin}/"}] + crumbs
    return {
        "@type": "BreadcrumbList",
        "@id": f"{_canonical(request)}#breadcrumb",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i,
                "name": c["name"],
                "item": c["url"],
            }
            for i, c in enumerate(items, start=1)
        ],
    }


def _webpage(request, rec: dict[str, Any]) -> dict[str, Any]:
    origin = _origin(request)
    canonical = _canonical(request)
    focus_id = f"{origin}/#software" if rec.get("focus") == "app" else f"{origin}/#organization"
    types = rec.get("kind", "WebPage")
    type_value: Any = [types, "WebPage"] if types != "WebPage" else "WebPage"
    node: dict[str, Any] = {
        "@type": type_value,
        "@id": f"{canonical}#webpage",
        "url": canonical,
        "name": rec["title"],
        "description": rec["description"],
        "inLanguage": "en",
        "isPartOf": {"@id": f"{origin}/#website"},
        "about": {"@id": focus_id},
        "primaryImageOfPage": {"@id": f"{origin}/#logo"},
        "publisher": {"@id": f"{origin}/#organization"},
    }
    return node


def _faqpage(request, faqs: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "@type": "FAQPage",
        "@id": f"{_canonical(request)}#faq",
        "isPartOf": {"@id": f"{_origin(request)}/#website"},
        "mainEntity": [
            {
                "@type": "Question",
                "name": f["q"],
                "acceptedAnswer": {"@type": "Answer", "text": f["a"]},
            }
            for f in faqs
        ],
    }


def _blogposting(request, post) -> dict[str, Any]:
    """A BlogPosting for a marketing blog detail page."""
    origin = _origin(request)
    canonical = _canonical(request)
    published = getattr(post, "published_at", None)
    modified = getattr(post, "updated_at", None) or published
    image = None
    cover = getattr(post, "cover_image", None)
    try:
        if cover:
            image = _abs(request, cover.url)
    except Exception:
        image = None
    node: dict[str, Any] = {
        "@type": "BlogPosting",
        "@id": f"{canonical}#article",
        "mainEntityOfPage": {"@id": f"{canonical}#webpage"},
        "headline": (getattr(post, "title", "") or "")[:110],
        "description": getattr(post, "excerpt", "") or "",
        "image": image or _abs_static(request, OG_IMAGE_STATIC),
        "url": canonical,
        "inLanguage": "en",
        "author": {
            "@type": "Person" if getattr(post, "author", "") else "Organization",
            "name": getattr(post, "author", "") or BRAND_NAME,
        },
        "publisher": {"@id": f"{origin}/#organization"},
        "isPartOf": {"@id": f"{origin}/#website"},
    }
    if published is not None:
        node["datePublished"] = _iso(published)
    if modified is not None:
        node["dateModified"] = _iso(modified)
    return node


def _blog_itemlist(request, posts) -> dict[str, Any] | None:
    """An ItemList of blog posts for the blog index (helps AEO discover posts)."""
    origin = _origin(request)
    items = []
    for i, post in enumerate(posts or [], start=1):
        try:
            items.append({
                "@type": "ListItem",
                "position": i,
                "url": _abs(request, post.get_absolute_url()),
                "name": getattr(post, "title", "") or "",
            })
        except Exception:
            continue
    if not items:
        return None
    return {
        "@type": "ItemList",
        "@id": f"{_canonical(request)}#itemlist",
        "itemListElement": items,
    }


def _itemlist(request, items, *, suffix: str = "itemlist") -> dict[str, Any] | None:
    """An ItemList from ``[{'name', 'url'} …]`` (relative URLs are absolutised).

    Core page hubs and section pages list their articles this way, so answer
    engines see the whole tree from any entry point."""
    elements = []
    for i, item in enumerate(items or [], start=1):
        url = item.get("url") or ""
        if not url:
            continue
        elements.append({
            "@type": "ListItem",
            "position": i,
            "name": item.get("name", ""),
            "url": _abs(request, url) if url.startswith("/") else url,
        })
    if not elements:
        return None
    return {
        "@type": "ItemList",
        "@id": f"{_canonical(request)}#{suffix}",
        "numberOfItems": len(elements),
        "itemListElement": elements,
    }


def _core_article(request, data: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any]:
    """A TechArticle for one core page node (help article / guide).

    Carries what search and answer engines rank help content on: freshness
    (dateModified), depth (wordCount, timeRequired), where it sits in the tree
    (articleSection, isPartOf the parent page), its outline (hasPart, one
    WebPageElement per heading, matching the on-page anchors) and which text to
    read aloud (speakable: the title and the answer-first lead paragraph)."""
    origin = _origin(request)
    canonical = _canonical(request)
    keywords = rec.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]
    node: dict[str, Any] = {
        "@type": "TechArticle",
        "@id": f"{canonical}#article",
        "mainEntityOfPage": {"@id": f"{canonical}#webpage"},
        "headline": (data.get("headline") or "")[:110],
        "description": data.get("description") or rec.get("description", ""),
        "url": canonical,
        "inLanguage": "en",
        "articleSection": data.get("section") or "",
        "wordCount": int(data.get("words") or 0),
        "timeRequired": f"PT{int(data.get('minutes') or 1)}M",
        "image": rec.get("og_image") or _abs_static(request, OG_IMAGE_STATIC),
        "author": {"@id": f"{origin}/#organization"},
        "publisher": {"@id": f"{origin}/#organization"},
        "about": {"@id": f"{origin}/#software"},
        "isPartOf": [{"@id": f"{origin}/#website"}],
        "speakable": {
            "@type": "SpeakableSpecification",
            "cssSelector": [".cp-article__title", ".cp-article__body > p:first-of-type"],
        },
    }
    if keywords:
        node["keywords"] = ", ".join(dict.fromkeys(keywords))
    parent = data.get("parent_url")
    if parent:
        parent_abs = _abs(request, parent) if parent.startswith("/") else parent
        if parent_abs != canonical:
            node["isPartOf"].append({"@id": f"{parent_abs}#webpage", "url": parent_abs})
    for key, value in (("datePublished", data.get("created")), ("dateModified", data.get("modified"))):
        if value is not None:
            node[key] = _iso(value)
    toc = data.get("toc") or []
    if toc:
        node["hasPart"] = [
            {"@type": "WebPageElement", "name": t["title"], "url": f"{canonical}#{t['id']}"}
            for t in toc
        ]
    return node


def _howto(request, data: dict[str, Any]) -> dict[str, Any]:
    """A HowTo from the article's first ordered list (its step-by-step)."""
    canonical = _canonical(request)
    steps = data.get("steps") or []
    return {
        "@type": "HowTo",
        "@id": f"{canonical}#howto",
        "name": data.get("headline") or "",
        "description": data.get("description") or "",
        "totalTime": f"PT{int(data.get('minutes') or 1)}M",
        "inLanguage": "en",
        "mainEntityOfPage": {"@id": f"{canonical}#webpage"},
        "step": [
            {
                "@type": "HowToStep",
                "position": i,
                "name": step if len(step) <= 110 else step[:109].rsplit(" ", 1)[0] + "…",
                "text": step,
            }
            for i, step in enumerate(steps, start=1)
        ],
    }


def _iso(value) -> str | None:
    try:
        if timezone.is_naive(value):
            value = timezone.make_aware(value, timezone.get_default_timezone())
        return value.isoformat()
    except Exception:
        try:
            return value.isoformat()
        except Exception:
            return None


# ---------------------------------------------------------------------------
# The page graph
# ---------------------------------------------------------------------------
def json_ld(request) -> str:
    """The full JSON-LD ``@graph`` for the current marketing page, as a string.

    Site-wide nodes (Organization, WebSite, SoftwareApplication) appear on every
    page so the entity is reinforced everywhere it is crawled; the page-specific
    nodes (WebPage, BreadcrumbList, FAQPage, BlogPosting, offers) are added from
    the registry record and any ``request.seo_extra`` the view attached.
    """
    rec = _record(request)
    # The admin pasted this page's own schema and chose to use only that.
    if rec.get("replace_schema") and rec.get("custom_schema"):
        return ""
    extra = getattr(request, "seo_extra", None) or {}
    plans = extra.get("plans")

    graph: list[dict[str, Any]] = [
        _organization(request),
        _website(request),
        _software_application(request, plans=plans),
    ]

    # The page node itself.
    page_node = _webpage(request, rec)
    graph.append(page_node)

    # Breadcrumbs  from the view (core pages / blog detail) when present.
    crumbs = extra.get("breadcrumbs")
    if crumbs:
        graph.append(_breadcrumb(request, crumbs))
        page_node["breadcrumb"] = {"@id": f"{_canonical(request)}#breadcrumb"}

    # Core page node  a TechArticle, plus a HowTo when it has numbered steps.
    core = extra.get("core_article")
    if core:
        graph.append(_core_article(request, core, rec))
        page_node["mainEntity"] = {"@id": f"{_canonical(request)}#article"}
        if core.get("modified") is not None:
            page_node["dateModified"] = _iso(core["modified"])
        if core.get("steps"):
            graph.append(_howto(request, core))

    # A hub or section page  the articles it lists.
    items = extra.get("itemlist")
    if items:
        item_list = _itemlist(request, items)
        if item_list:
            graph.append(item_list)
            if not core:
                page_node["mainEntity"] = {"@id": item_list["@id"]}
            else:
                page_node["hasPart"] = {"@id": item_list["@id"]}

    # FAQ: only questions visible on the page. Core pages and blog posts supply
    # theirs (question headings in the article); Pricing and Contact render
    # VISIBLE_FAQ, so their markup is built from that same list.
    faqs = extra.get("faq")
    if not faqs and _url_name(request) in VISIBLE_FAQ:
        faqs = [{"q": f["q"], "a": f["a"]} for f in visible_faq(_url_name(request))]
    if faqs:
        graph.append(_faqpage(request, faqs))

    # Blog detail  a full article node.
    post = extra.get("post")
    if post is not None:
        graph.append(_blogposting(request, post))

    # Blog index  a list of posts.
    posts = extra.get("posts")
    if posts is not None:
        item_list = _blog_itemlist(request, posts)
        if item_list:
            graph.append(item_list)

    document = {"@context": "https://schema.org", "@graph": graph}
    # ensure_ascii=False keeps ₹ and accented copy intact; separators trim bytes.
    payload = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    # The string is emitted raw inside a <script> block, so any literal "<" from
    # authored content (a blog title, an excerpt) must not be able to close the
    # tag. Escaping "<" as its JSON unicode escape stays valid JSON-LD while
    # making a "</script>" breakout impossible.
    return payload.replace("<", "\\u003c")
