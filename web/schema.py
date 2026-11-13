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
  plans; the free plan is real, so a ₹0 offer is honest.
* **Fail open.** Any error here must degrade to "no structured data", never to a
  broken page  the caller wraps every entry point in try/except.

The registry (:data:`PAGE_SEO`) is the human-editable heart: one keyword-tuned
record per marketing page (title, description, keywords, FAQ, entity focus).
"""

from __future__ import annotations

import json
from typing import Any

from django.templatetags.static import static
from django.utils import timezone

# ---------------------------------------------------------------------------
# Brand facts  the single source of truth for every schema node
# ---------------------------------------------------------------------------
BRAND_NAME = "OneWooz"
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

#: One-line positioning reused as the Organization/description default.
TAGLINE = (
    "OneWooz builds and publishes a complete, working online store for D2C "
    "brands and B2B retailers  products, checkout, payments and shipping, live "
    "on your own domain, with no developer and no page builder."
)

#: Topical expertise the Organization is declared to know about. This is a
#: primary GEO signal: it tells generative engines which subjects to associate
#: the brand with when a user asks "what's the best platform to build an
#: ecommerce website".
KNOWS_ABOUT = [
    "Ecommerce website builder",
    "Online store builder",
    "Ecommerce platform",
    "No-code website builder",
    "D2C ecommerce",
    "B2B ecommerce",
    "Online payments and checkout",
    "Shipping and order fulfilment",
    "Custom domains and SSL",
    "Ecommerce SEO",
]

#: The product's feature list, surfaced on the SoftwareApplication node. Written
#: as benefit statements an answer engine can quote verbatim.
FEATURE_LIST = [
    "One-click store creation tailored to your industry",
    "Products, cart and secure checkout out of the box",
    "Online payments (cards, UPI, cash on delivery)",
    "Live shipping rates and label printing",
    "Your own custom domain with automatic HTTPS",
    "Built-in SEO, analytics and content pages",
    "No developer, no page builder, no code",
    "We never take a cut of your sales",
]


# ---------------------------------------------------------------------------
# Per-page SEO registry
# ---------------------------------------------------------------------------
# Keyed by the resolved ``url_name`` on the marketing (web) urlconf. Each record
# drives the <title>, meta description, keywords, Open-Graph/Twitter values and
# the page-type JSON-LD. Titles stay ≤ ~60 chars and descriptions ≤ ~155 so they
# render un-truncated in the SERP. ``kind`` selects the primary WebPage subtype;
# ``focus`` is the entity a WebPage is chiefly ``about`` ("org" or "app").
PAGE_SEO: dict[str, dict[str, Any]] = {
    "home": {
        "title": "OneWooz  Ecommerce Website Builder for D2C & B2B",
        "description": (
            "OneWooz is the ecommerce website builder that publishes a complete, "
            "working online store  products, checkout, payments and shipping  "
            "on your own domain. Start free."
        ),
        "keywords": [
            "website builder", "ecommerce website builder", "online store builder",
            "best platform for building ecommerce websites", "ecommerce platform",
            "build an online store", "no-code store builder",
        ],
        "focus": "app",
        "og_type": "website",
        "faq": [
            {
                "q": "What is OneWooz?",
                "a": (
                    "OneWooz is an ecommerce website builder that creates and "
                    "publishes a complete online store for you  products, cart, "
                    "checkout, payments and shipping  on a OneWooz address or your "
                    "own custom domain, with no developer and no page builder."
                ),
            },
            {
                "q": "Is OneWooz a good platform for building an ecommerce website?",
                "a": (
                    "OneWooz is purpose-built for ecommerce: instead of a blank "
                    "page builder, you answer a few questions and it assembles a "
                    "ready-to-sell store for your industry, complete with payments, "
                    "shipping and SEO. You can see it live before paying anything."
                ),
            },
            {
                "q": "Do I need to know how to code to use OneWooz?",
                "a": (
                    "No. OneWooz needs no developer, no code and no page builder. "
                    "You answer a few questions, click once, and get a live store "
                    "you can start selling from."
                ),
            },
            {
                "q": "How much does OneWooz cost?",
                "a": (
                    "You can build a store and see it live for free. You only pay "
                    "when you connect a custom domain and go to market, and OneWooz "
                    "never takes a cut of your sales."
                ),
            },
        ],
    },
    "platform": {
        "title": "Platform  The Ecommerce Engine Behind Your Store | OneWooz",
        "description": (
            "See how the OneWooz platform builds a working online store: one-click "
            "creation, payments, live shipping, custom domains with HTTPS, SEO and "
            "analytics  no code required."
        ),
        "keywords": [
            "ecommerce platform", "website builder platform", "online store platform",
            "ecommerce website builder", "no-code ecommerce",
        ],
        "focus": "app",
        "og_type": "website",
        "faq": [
            {
                "q": "What can the OneWooz platform do?",
                "a": (
                    "It builds and runs a full commerce stack for you: an "
                    "industry-tailored storefront, product catalogue, cart and "
                    "checkout, online payments, live shipping rates and labels, "
                    "custom domains with automatic HTTPS, plus built-in SEO and "
                    "analytics."
                ),
            },
            {
                "q": "Does OneWooz support online payments and shipping?",
                "a": (
                    "Yes. Stores take cards, UPI and cash on delivery, and get live "
                    "shipping rates with label printing  settlement goes directly "
                    "to your own account through your payment provider."
                ),
            },
        ],
    },
    "pricing": {
        "title": "Pricing  Start Free, Pay When You Sell | OneWooz",
        "description": (
            "OneWooz pricing: build your ecommerce store and see it live for free. "
            "Pay only when you go to market on your own domain. Transparent plans, "
            "no cut of your sales."
        ),
        "keywords": [
            "ecommerce website builder pricing", "online store builder pricing",
            "website builder cost", "ecommerce platform pricing",
        ],
        "focus": "app",
        "og_type": "website",
        "faq": [
            {
                "q": "How much does it cost to build a store on OneWooz?",
                "a": (
                    "Building a store and seeing it live on a OneWooz address is "
                    "free  no card required. You pay only when you connect a custom "
                    "domain and go to market."
                ),
            },
            {
                "q": "Does OneWooz take a commission on my sales?",
                "a": (
                    "No. OneWooz never takes a cut of your sales. Payment settlement "
                    "goes straight to your own account through your payment provider."
                ),
            },
            {
                "q": "Can I cancel or leave anytime?",
                "a": (
                    "Yes. You can export your data and point your domain elsewhere "
                    "at any time. There is no lock-in."
                ),
            },
        ],
    },
    "solutions": {
        "title": "Solutions  Ecommerce for D2C Brands & B2B Retailers | OneWooz",
        "description": (
            "OneWooz solutions for D2C brands and B2B retailers: launch a "
            "conversion-ready ecommerce website tailored to your industry, with "
            "payments, shipping and catalogue built in."
        ),
        "keywords": [
            "D2C ecommerce platform", "B2B ecommerce website", "ecommerce for brands",
            "ecommerce website builder", "online store for retailers",
        ],
        "focus": "app",
        "og_type": "website",
        "faq": [
            {
                "q": "Does OneWooz work for both D2C brands and B2B retailers?",
                "a": (
                    "Yes. OneWooz tailors the store to your business  a "
                    "conversion-focused D2C storefront, or a catalogue and enquiry "
                    "flow for B2B and multi-location retailers."
                ),
            },
        ],
    },
    "customers": {
        "title": "Customers  Stores Built & Launched on OneWooz",
        "description": (
            "See the kinds of D2C and B2B stores merchants launch on OneWooz  "
            "live, on their own domains, without developers or page builders."
        ),
        "keywords": [
            "ecommerce website examples", "online store examples", "OneWooz customers",
        ],
        "focus": "org",
        "og_type": "website",
    },
    "about": {
        "title": "About OneWooz  Commerce Infrastructure for Merchants",
        "description": (
            "OneWooz, operated by The Artless, is commerce infrastructure for "
            "people who would rather run their business than manage a website. "
            "Learn who we are and what we build."
        ),
        "keywords": ["about OneWooz", "The Artless", "ecommerce company"],
        "focus": "org",
        "og_type": "website",
    },
    "security": {
        "title": "Security  How OneWooz Protects Your Store & Data",
        "description": (
            "How OneWooz keeps stores and customer data safe: encrypted "
            "credentials, tenant isolation, HTTPS everywhere, passkeys and a full "
            "audit trail."
        ),
        "keywords": [
            "ecommerce security", "secure online store", "PCI secure checkout",
            "website builder security",
        ],
        "focus": "org",
        "og_type": "website",
        "faq": [
            {
                "q": "Is a store built on OneWooz secure?",
                "a": (
                    "Yes. Every store runs on isolated tenant infrastructure with "
                    "HTTPS everywhere, encrypted third-party credentials, "
                    "passkey-protected accounts and an append-only audit trail. "
                    "Card details are handled by the payment provider, never stored "
                    "by OneWooz."
                ),
            },
        ],
    },
    "contact": {
        "title": "Contact OneWooz  Talk to the Ecommerce Team",
        "description": (
            "Get in touch with OneWooz. Ask about building your ecommerce store, "
            "pricing, custom domains or B2B  we usually reply within one business "
            "day."
        ),
        "keywords": ["contact OneWooz", "ecommerce support", "sales enquiry"],
        "focus": "org",
        "kind": "ContactPage",
        "og_type": "website",
        "faq": [
            {
                "q": "How do I contact OneWooz?",
                "a": (
                    "Use the enquiry form on this page or email "
                    f"{SUPPORT_EMAIL}. We usually reply within one business day."
                ),
            },
        ],
    },
    "support": {
        "title": "Support  Help With Your OneWooz Store",
        "description": (
            "OneWooz support: raise a ticket for help with your store, payments, "
            "shipping, custom domains or your account. We're here to get you "
            "selling."
        ),
        "keywords": ["OneWooz support", "ecommerce help", "store help desk"],
        "focus": "org",
        "og_type": "website",
    },
    "blog_list": {
        "title": "Blog  Ecommerce Guides & Store-Building Tips | OneWooz",
        "description": (
            "Practical guides on building and growing an online store: ecommerce "
            "website tips, payments, shipping, SEO and D2C/B2B playbooks from "
            "OneWooz."
        ),
        "keywords": [
            "ecommerce blog", "online store guides", "how to build an ecommerce website",
            "ecommerce tips",
        ],
        "focus": "org",
        "kind": "CollectionPage",
        "og_type": "website",
    },
    "core_pages": {
        "title": "Resources & Help  OneWooz",
        "description": (
            "Guides, help articles and reference pages for building, launching and "
            "running your OneWooz ecommerce store."
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
        "title": "Privacy Policy  OneWooz",
        "description": (
            "How OneWooz collects, uses and protects personal data across the "
            "platform and the stores it hosts."
        ),
        "keywords": ["OneWooz privacy policy", "data protection"],
    },
    "terms": {
        "title": "Terms of Service  OneWooz",
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
    "keywords": ["ecommerce website builder", "online store builder"],
    "focus": "org",
    "og_type": "website",
}


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
        "slogan": "Build it free. Decide later.",
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
        "applicationSubCategory": "Ecommerce website builder",
        "operatingSystem": "Web-based (cloud)",
        "url": f"{origin}/",
        "description": (
            "OneWooz is an ecommerce website builder and online store platform: it "
            "creates a complete, working store  products, checkout, payments and "
            "shipping  on your own domain, with no developer and no page builder."
        ),
        "featureList": FEATURE_LIST,
        "screenshot": _abs_static(request, OG_IMAGE_STATIC),
        "image": {"@id": f"{origin}/#logo"},
        "provider": {"@id": f"{origin}/#organization"},
        "publisher": {"@id": f"{origin}/#organization"},
        "audience": {
            "@type": "BusinessAudience",
            "name": "D2C brands and B2B retailers",
        },
        "offers": _offers(plans),
    }
    return node


def _offers(plans=None) -> dict[str, Any]:
    """An AggregateOffer for the product.

    Off the pricing page (``plans`` is None) we still state the honest baseline:
    a real free plan, priced 0, in INR. On the pricing page we build one Offer
    per purchasable plan from the live figures and expose the low price.
    """
    currency = "INR"
    individual: list[dict[str, Any]] = [
        {
            "@type": "Offer",
            "name": "Free plan",
            "price": "0",
            "priceCurrency": currency,
            "description": "Build a store and see it live free  no card required.",
        }
    ]
    low = 0.0
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
    prices = [float(o["price"]) for o in individual]
    return {
        "@type": "AggregateOffer",
        "priceCurrency": currency,
        "lowPrice": f"{min(prices):.2f}" if prices else "0",
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
    graph.append(_webpage(request, rec))

    # Breadcrumbs  from the view (core pages / blog detail) when present.
    crumbs = extra.get("breadcrumbs")
    if crumbs:
        graph.append(_breadcrumb(request, crumbs))

    # FAQ  from the registry (AEO). A view may also supply its own.
    faqs = extra.get("faq") or rec.get("faq")
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
