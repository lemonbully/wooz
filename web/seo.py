"""``robots.txt`` and ``sitemap.xml`` for onewooz.com  the marketing site.

Same policy shape as the per-store crawler files (:mod:`storefront.seo`), tuned
for the platform's own front door: welcome classic search *and* the AI/answer
engines by name (the GEO signal for AI Overviews, ChatGPT, Claude, Perplexity),
keep the transactional checkout and health probes out of the index, and point
crawlers at a sitemap of every published marketing page.

URLs are built with ``reverse()`` + ``request.build_absolute_uri`` so the files
are correct on ``onewooz.com`` and under the single-host development prefix
alike  no host or path is hardcoded.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from django.db.models import Prefetch
from django.urls import reverse

from .models import (
    LegalPolicy,
    MarketingBlog,
    MarketingCoreChildPage,
    MarketingCorePage,
    MarketingCoreSubpage,
)

# Machine-only and transactional paths on the marketing host.
_DISALLOW = (
    "/pricing/checkout/",
    "/pricing/invite/",
    "/health/",
    "/*?session=",
    "/*?token=",
)

# Search and AI/answer-engine crawlers we explicitly welcome  see
# :mod:`storefront.seo` for why the AI agents are named individually.
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
    return request.build_absolute_uri(reverse(name, kwargs=kwargs) if kwargs else reverse(name))


# --------------------------------------------------------------------------
# robots.txt
# --------------------------------------------------------------------------


def robots_txt(request) -> str:
    lines: list[str] = []
    for bot in _WELCOME_BOTS:
        lines += [f"User-agent: {bot}", "Allow: /", ""]

    lines.append("# Transactional and machine-only areas")
    lines.append("User-agent: *")
    lines += [f"Disallow: {path}" for path in _DISALLOW]
    lines.append("Allow: /")
    lines.append("")
    lines.append(f"Sitemap: {_abs(request, 'web:sitemap_xml')}")

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# sitemap.xml
# --------------------------------------------------------------------------


def _url(loc: str, *, changefreq: str, priority: str, lastmod=None) -> str:
    parts = [f"    <loc>{escape(loc)}</loc>"]
    iso = _date(lastmod)
    if iso:
        parts.append(f"    <lastmod>{iso}</lastmod>")
    parts.append(f"    <changefreq>{changefreq}</changefreq>")
    parts.append(f"    <priority>{priority}</priority>")
    body = "\n".join(parts)
    return f"  <url>\n{body}\n  </url>\n"


def _date(value) -> str | None:
    try:
        return value.date().isoformat()
    except Exception:
        return None


def _published_tree():
    return MarketingCorePage.objects.filter(is_published=True).prefetch_related(
        Prefetch(
            "subpages",
            queryset=MarketingCoreSubpage.objects.filter(is_published=True).prefetch_related(
                Prefetch(
                    "children",
                    queryset=MarketingCoreChildPage.objects.filter(is_published=True),
                )
            ),
        )
    )


def sitemap_xml(request) -> str:
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    ]

    # Standing marketing pages. name, changefreq, priority.
    static_pages = [
        ("web:home", "weekly", "1.0"),
        ("web:platform", "monthly", "0.9"),
        ("web:pricing", "monthly", "0.9"),
        ("web:solutions", "monthly", "0.8"),
        ("web:customers", "monthly", "0.7"),
        ("web:about", "monthly", "0.6"),
        ("web:security", "monthly", "0.6"),
        ("web:contact", "monthly", "0.6"),
        ("web:support", "monthly", "0.6"),
        ("web:blog_list", "weekly", "0.7"),
        ("web:core_pages", "weekly", "0.6"),
    ]
    # Pages hidden from search in the admin (onewooz.com SEO → Page SEO).
    from .seo_overrides import noindex_keys

    hidden = noindex_keys()

    for name, changefreq, priority in static_pages:
        if name.split(":", 1)[1] in hidden:
            continue
        out.append(_url(_abs(request, name), changefreq=changefreq, priority=priority))

    # Legal pages served through the restricted ``<page:slug>`` converter.
    for slug in ("privacy", "terms"):
        if f"page:{slug}" in hidden:
            continue
        out.append(
            _url(
                _abs(request, "web:page", slug=slug),
                changefreq="yearly",
                priority="0.3",
            )
        )

    # Admin-authored legal policies (/legal/<slug>/). Privacy / Terms overrides
    # are already listed above at their own URLs.
    for policy in (
        LegalPolicy.objects.filter(is_published=True)
        .exclude(slug__in=LegalPolicy.BUILT_IN_SLUGS)
        .order_by("order", "title")
    ):
        if f"policy:{policy.slug}" in hidden:
            continue
        out.append(
            _url(
                request.build_absolute_uri(policy.get_absolute_url()),
                changefreq="yearly",
                priority="0.3",
                lastmod=getattr(policy, "updated_at", None),
            )
        )

    # Published blog posts.
    from core.queries import sorted_rows

    # Posts carry their full article body; sort keys only (MySQL error 1038).
    posts = sorted_rows(MarketingBlog.objects.filter(is_published=True).order_by("-published_at"))
    for post in posts:
        if f"blog:{post.slug}" in hidden:
            continue
        out.append(
            _url(
                request.build_absolute_uri(post.get_absolute_url()),
                changefreq="monthly",
                priority="0.6",
                lastmod=getattr(post, "updated_at", None) or getattr(post, "published_at", None),
            )
        )

    # Published core pages, subpages and child pages.
    for page in _published_tree().order_by("order", "title"):
        # A page hidden from search drops only itself; its subpages and child
        # pages keep their own setting.
        if f"core:{page.slug}" not in hidden:
            out.append(
                _url(
                    request.build_absolute_uri(page.get_absolute_url()),
                    changefreq="weekly",
                    priority="0.6",
                    lastmod=getattr(page, "updated_at", None),
                )
            )
        for sub in page.subpages.all():
            if f"core:{page.slug}/{sub.slug}" not in hidden:
                out.append(
                    _url(
                        request.build_absolute_uri(sub.get_absolute_url()),
                        changefreq="weekly",
                        priority="0.5",
                        lastmod=getattr(sub, "updated_at", None),
                    )
                )
            for child in sub.children.all():
                if f"core:{page.slug}/{sub.slug}/{child.slug}" in hidden:
                    continue
                out.append(
                    _url(
                        request.build_absolute_uri(child.get_absolute_url()),
                        changefreq="weekly",
                        priority="0.4",
                        lastmod=getattr(child, "updated_at", None),
                    )
                )

    out.append("</urlset>")
    return "".join(out)
