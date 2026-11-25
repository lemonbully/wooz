"""``robots.txt`` and ``sitemap.xml`` for a generated storefront.

Ported from the reference platform's D2C ``public_robots_txt`` /
``public_sitemap_xml`` (1matrix ``website`` app)  same policy shape and the
same crawl signals  but rebuilt to OneWooz conventions:

* every URL is produced with ``reverse()`` + ``request.build_absolute_uri`` so
  it stays correct on a ``<slug>.onewooz.site`` subdomain, on a verified custom
  domain, and under the single-host ``/s/<slug>/`` development prefix. Nothing
  is hardcoded (see the project's host-routing note).
* the catalogue is read through the tenant-scoped managers (``.for_site``), so a
  sitemap can never leak another store's products.

A store that is not live, or is in maintenance, is served a hard no-index
``robots.txt`` and an empty sitemap: draft and paused stores stay out of the
index until their owner publishes.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from django.urls import reverse

from commerce.models import Category, Product

# Shown at /robots.txt and /sitemap.xml while a store is still on its
# <slug>.onewooz.site subdomain. These files are only published once the owner
# points a real domain at the store, so search engines are given crawl
# directives only for the canonical custom-domain site.
CUSTOM_DOMAIN_REQUIRED_NOTICE = (
    "Please configure and verify your custom domain first. Until then, "
    "robots.txt and sitemap.xml are not published for your store. Once your "
    "custom domain is live, these files will be served here with their full "
    "contents.\n"
)

# Storefront paths a crawler has no business indexing: the transactional
# surface, the customer's own account, and every machine endpoint. These are
# the OneWooz equivalents of the reference's ``/admin/`` /``/backend/`` block 
# named as they actually exist on the storefront host.
_DISALLOW = (
    "/cart/",
    "/checkout/",
    "/account/",
    "/wishlist/",
    "/api/",
    "/webhooks/",
    "/_owner/",
    "/*?session=",
    "/*?token=",
)

# Search and AI/answer-engine crawlers we explicitly welcome. Naming the AI
# agents (Google-Extended, GPTBot, ClaudeBot, PerplexityBot, …) is the GEO
# signal that lets a store be cited in AI Overviews, ChatGPT, Claude and
# Perplexity answers  not just ranked in classic search.
_WELCOME_BOTS = (
    "Googlebot",
    "Bingbot",
    "DuckDuckBot",
    "Applebot",
    "Google-Extended",
    "GPTBot",
    "OAI-SearchBot",
    "ChatGPT-User",
    "ClaudeBot",
    "Claude-User",
    "Claude-SearchBot",
    "Anthropic-AI",
    "PerplexityBot",
    "Perplexity-User",
    "Applebot-Extended",
    "Amazonbot",
    "meta-externalagent",
)


def _abs(request, name: str, **kwargs) -> str:
    """Absolute URL for a storefront route, host- and prefix-correct."""
    return request.build_absolute_uri(reverse(name, kwargs=kwargs) if kwargs else reverse(name))


def _indexable(site) -> bool:
    # A store is publicly crawlable once it is live OR once its custom domain is
    # verified. robots.txt and sitemap.xml are only ever served on that verified
    # custom domain (the subdomain returns the "custom domain required" notice),
    # so a store reachable there is, by construction, published to the public 
    # its sitemap should list its pages. Maintenance mode always holds it out.
    live = bool(site.is_live) or bool(getattr(site, "custom_domain_verified", False))
    return live and not site.maintenance_mode


# --------------------------------------------------------------------------
# robots.txt
# --------------------------------------------------------------------------


def robots_txt(request, site) -> str:
    if not _indexable(site):
        # Draft or paused store: keep it entirely out of the index.
        return "User-agent: *\nDisallow: /\n"

    lines: list[str] = []
    for bot in _WELCOME_BOTS:
        lines += [f"User-agent: {bot}", "Allow: /", ""]

    lines.append("# Private, transactional and machine-only areas")
    lines.append("User-agent: *")
    lines += [f"Disallow: {path}" for path in _DISALLOW]
    lines.append("Allow: /")
    lines.append("")
    lines.append(f"Sitemap: {_abs(request, 'public_sitemap_xml')}")
    # Point AI assistants at the store's llms.txt once it has been generated.
    try:
        from prompts.llms_txt import published_body

        if published_body(site):
            lines.append("")
            lines.append(f"# Plain-text guide to this store for AI assistants: {_abs(request, 'public_llms_txt')}")
    except Exception:
        pass

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# sitemap.xml
# --------------------------------------------------------------------------


def _url(loc: str, *, changefreq: str, priority: str, lastmod: str | None = None) -> str:
    parts = [f"    <loc>{escape(loc)}</loc>"]
    if lastmod:
        parts.append(f"    <lastmod>{lastmod}</lastmod>")
    parts.append(f"    <changefreq>{changefreq}</changefreq>")
    parts.append(f"    <priority>{priority}</priority>")
    body = "\n".join(parts)
    return f"  <url>\n{body}\n  </url>\n"


def _date(value) -> str | None:
    try:
        return value.date().isoformat()
    except Exception:
        return None


def _hidden_page_keys(content) -> set:
    """Page keys the owner marked "Hide this page from search engines" in the
    SEO tab (Different-per-page mode)  left out of the sitemap, matching the
    page's own ``noindex`` tag."""
    if str((content or {}).get("seo_mode") or "same") != "custom":
        return set()
    pages = (content or {}).get("seo_pages")
    if not isinstance(pages, dict):
        return set()
    return {k for k, v in pages.items() if isinstance(v, dict) and v.get("noindex")}


# Sitemap URL name → SEO page key, for the fixed pages.
_STATIC_PAGE_KEYS = {
    "public_all_products": "all_products",
    "public_categories": "categories",
    "public_about": "about",
    "public_enquiry": "contact",
    "public_blogs": "blogs",
    "privacy_policy_page": "privacy",
    "public_terms": "terms",
    "public_refund": "refund",
    "public_shipping": "shipping",
    "public_certificates": "certificates",
}


def sitemap_xml(request, site) -> str:
    head = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    )
    if not _indexable(site):
        return head + "</urlset>"

    out = [head]
    hidden = _hidden_page_keys(site.content or {})

    # Homepage.
    if "home" not in hidden:
        out.append(_url(_abs(request, "public_website"), changefreq="weekly", priority="1.0"))

    # Standing navigational and legal pages that always resolve. ``certificates``
    # is a B2B-only surface in the reference, so it is only listed for B2B stores.
    static_pages = [
        ("public_all_products", "weekly", "0.9"),
        ("public_categories", "weekly", "0.7"),
        ("public_about", "monthly", "0.6"),
        ("public_enquiry", "monthly", "0.6"),
        ("public_blogs", "weekly", "0.6"),
        ("privacy_policy_page", "yearly", "0.3"),
        ("public_terms", "yearly", "0.3"),
        ("public_refund", "yearly", "0.3"),
        ("public_shipping", "yearly", "0.3"),
    ]
    # Listed only once it has certificates: an empty page is thin content.
    from .certificates import public_certificates

    if site.is_b2b and public_certificates(site.content or {}):
        static_pages.append(("public_certificates", "monthly", "0.5"))
    for name, changefreq, priority in static_pages:
        if _STATIC_PAGE_KEYS.get(name) in hidden:
            continue
        out.append(_url(_abs(request, name), changefreq=changefreq, priority=priority))

    # The owner's extra policies (Edit Content → Policies), /policies/<slug>/.
    from .policies import custom_policies

    for policy in custom_policies(site.content or {}):
        if policy["key"] in hidden:
            continue
        out.append(
            _url(_abs(request, "public_policy_page", policy_slug=policy["slug"]), changefreq="yearly", priority="0.3")
        )

    # Products.
    products = (
        Product.objects.for_site(site)
        .filter(is_active=True, is_deleted=False)
        .order_by("-updated_at")
    )
    for product in products:
        slug = (product.slug or "").strip()
        if not slug or f"product:{product.id}" in hidden:
            continue
        out.append(
            _url(
                _abs(request, "public_product_detail", product_slug=slug),
                changefreq="weekly",
                priority="0.9",
                lastmod=_date(product.updated_at),
            )
        )

    # Categories.
    categories = (
        Category.objects.for_site(site)
        .filter(is_active=True, is_deleted=False)
        .order_by("name")
    )
    # Only categories with at least one live product: an empty category page is
    # noindex (storefront.views.category_detail), so listing it would send
    # crawlers to a page that asks not to be indexed.
    from django.db.models import Q

    stocked = Product.objects.for_site(site).filter(is_active=True, is_deleted=False)
    stocked_ids = set(
        stocked.exclude(category_id=None).values_list("category_id", flat=True)
    ) | set(stocked.values_list("categories__id", flat=True))
    stocked_ids.discard(None)

    for category in categories:
        slug = (category.slug or "").strip()
        if not slug or f"category:{category.id}" in hidden:
            continue
        if category.id not in stocked_ids:
            continue
        out.append(
            _url(
                _abs(request, "public_category_detail", category_slug=slug),
                changefreq="weekly",
                priority="0.7",
                lastmod=_date(category.updated_at),
            )
        )

    content = site.content or {}

    # Blog posts (CMS blob).
    for post in content.get("blog_posts") or []:
        if not isinstance(post, dict):
            continue
        slug = (post.get("slug") or "").strip()
        if not slug or f"blog:{slug}" in hidden:
            continue
        out.append(
            _url(
                _abs(request, "public_blog_detail", blog_slug=slug),
                changefreq="monthly",
                priority="0.6",
            )
        )

    # Core pages, subpages and nested children (CMS blob). The /core-pages/
    # index is listed too when there is at least one published core page.
    core_list = [p for p in (content.get("core_pages") or []) if isinstance(p, dict) and (p.get("slug") or "").strip("/")]
    if core_list and "core_pages" not in hidden:
        out.append(_url(_abs(request, "public_core_pages"), changefreq="weekly", priority="0.5"))
    for page in content.get("core_pages") or []:
        if not isinstance(page, dict):
            continue
        page_slug = (page.get("slug") or "").strip("/")
        if not page_slug:
            continue
        if f"core:{page_slug}" not in hidden:
            out.append(
                _url(
                    _abs(request, "public_core_page", page_slug=page_slug),
                    changefreq="weekly",
                    priority="0.7",
                )
            )
        for sub in page.get("subpages") or []:
            if not isinstance(sub, dict):
                continue
            sub_slug = (sub.get("slug") or "").strip("/")
            if not sub_slug:
                continue
            if f"core:{page_slug}/{sub_slug}" not in hidden:
                out.append(
                    _url(
                        _abs(
                            request,
                            "public_core_subpage",
                            page_slug=page_slug,
                            sub_slug=sub_slug,
                        ),
                        changefreq="weekly",
                        priority="0.6",
                    )
                )
            for child in sub.get("children") or []:
                if not isinstance(child, dict):
                    continue
                child_slug = (child.get("slug") or "").strip("/")
                if not child_slug or f"core:{page_slug}/{sub_slug}/{child_slug}" in hidden:
                    continue
                out.append(
                    _url(
                        _abs(
                            request,
                            "public_core_child",
                            page_slug=page_slug,
                            sub_slug=sub_slug,
                            child_slug=child_slug,
                        ),
                        changefreq="weekly",
                        priority="0.5",
                    )
                )

    out.append("</urlset>")
    return "".join(out)
