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

from django.db.models import Q

from xml.sax.saxutils import escape

from django.urls import reverse

from .models import LegalPolicy, MarketingBlog

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
    lines.append("")
    lines.append(f"# Plain-text guide to this site for AI assistants: {_abs(request, 'web:llms_txt')}")

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
        ("ask:ask_home", "daily", "0.7"),
        ("ask:topics", "weekly", "0.5"),
        ("ask:weekly", "weekly", "0.5"),
        ("web:blog_list", "weekly", "0.7"),
        ("web:core_pages", "weekly", "0.6"),
    ]
    # Pages hidden from search in the admin (onewooz.com SEO → Page SEO).
    from .seo_overrides import noindex_keys

    hidden = noindex_keys()

    # The blog index is noindex while it has no posts (thin content), so it is
    # left out of the sitemap until the first post is published.
    has_posts = MarketingBlog.objects.filter(is_published=True).exists()

    for name, changefreq, priority in static_pages:
        if name.split(":", 1)[1] in hidden:
            continue
        if name == "web:blog_list" and not has_posts:
            continue
        out.append(_url(_abs(request, name), changefreq=changefreq, priority=priority))

    # Legal pages served through the restricted ``<page:slug>`` converter. When
    # a newer /legal/ document supersedes one, the short URL 301s there and the
    # /legal/ page is listed by the policy loop below instead.
    from .views import legal_url

    for slug in ("privacy", "terms"):
        if f"page:{slug}" in hidden:
            continue
        if legal_url(slug) != reverse("web:page", kwargs={"slug": slug}):
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

    # Published core pages, subpages and child pages (sorted in Python: the rows
    # carry article bodies, see web.core_tree).
    from .core_tree import load_tree

    for page in load_tree():
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
        for sub in page.subs:
            if f"core:{page.slug}/{sub.slug}" not in hidden:
                out.append(
                    _url(
                        request.build_absolute_uri(sub.get_absolute_url()),
                        changefreq="weekly",
                        priority="0.5",
                        lastmod=getattr(sub, "updated_at", None),
                    )
                )
            for child in sub.kids:
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

    # Ask OneWooz community: topic rooms, and every published question that
    # has an answer (unanswered threads are noindex until someone answers).
    try:
        from community.models import Question, Topic, Visibility

        for room in Topic.objects.filter(is_active=True).only("slug"):
            out.append(_url(request.build_absolute_uri(room.get_absolute_url()),
                            changefreq="daily", priority="0.5"))
        answered = (
            Question.objects.filter(status=Visibility.PUBLISHED)
            .filter(Q(answer_count__gt=0) | ~Q(ai_answer=""))
            .order_by("-last_activity_at")
            .values_list("slug", "last_activity_at")[:5000]
        )
        for slug, touched in answered:
            out.append(_url(
                request.build_absolute_uri(reverse("ask:question", kwargs={"slug": slug})),
                changefreq="weekly", priority="0.5", lastmod=touched,
            ))
    except Exception:
        pass

    out.append("</urlset>")
    return "".join(out)


# --------------------------------------------------------------------------
# llms.txt
# --------------------------------------------------------------------------
# The proposed llms.txt convention (llmstxt.org): a Markdown file at the site
# root that tells AI assistants and answer engines, in plain text, what the
# site is and which pages hold the answers, with a one-line summary per link.
# A GEO signal: it makes the Help Centre and guides easy to cite accurately.


def _md(text: str) -> str:
    return " ".join((text or "").split()).replace("[", "(").replace("]", ")")


def _node_summary(node, key: str) -> str:
    """One-line llms.txt summary for a Resource-centre page: the meta
    description set for it in the admin (onewooz.com SEO → Page SEO) when there
    is one, else its opening text cut at a whole sentence. The opening paragraph
    of a guide is often a scene-setting line, so the admin description wins."""
    from .core_tree import meta_description, summary_of
    from .seo_overrides import snapshot

    try:
        own = ((snapshot().get("pages") or {}).get(key) or {}).get("meta_description")
    except Exception:
        own = ""
    return meta_description(own or summary_of(node.content), 180)


def llms_txt(request) -> str:
    from .core_tree import flatten, load_tree
    from .schema import BRAND_LINE, BRAND_NAME, BRAND_PROMISE, PRODUCT_DESCRIPTION
    from .seo_overrides import noindex_keys
    from .views import legal_url

    hidden = noindex_keys()
    out = [
        f"# {BRAND_NAME}",
        "",
        # The site's identity, in the agreed brand lines (verbatim).
        f"> {BRAND_LINE}",
        f"> {BRAND_PROMISE}",
        "",
        _md(PRODUCT_DESCRIPTION),
        "",
        "## Product",
        "",
        f"- [Platform]({_abs(request, 'web:platform')}): what a OneWooz store includes",
        f"- [Pricing]({_abs(request, 'web:pricing')}): plans and the commerce fee",
        f"- [Solutions]({_abs(request, 'web:solutions')}): D2C brands and B2B retailers",
        f"- [Security]({_abs(request, 'web:security')}): how stores and data are protected",
        f"- [About]({_abs(request, 'web:about')}): who runs OneWooz",
        f"- [Contact]({_abs(request, 'web:contact')}): sales and support",
        (
            f"- [Resources]({_abs(request, 'web:core_pages')}): the Help Centre, selling "
            "guides and feature guides in one place"
        ),
        "",
    ]

    for root in load_tree():
        out += [f"## {_md(root.title)}", ""]
        for node in flatten(root):
            if node.level == "page":
                key = f"core:{root.slug}"
            elif node.level == "subpage":
                key = f"core:{root.slug}/{node.slug}"
            else:
                key = f"core:{root.slug}/{node.subpage.slug}/{node.slug}"
            if key in hidden:
                continue
            title = node.title if node.level != "page" else f"{node.title} overview"
            if node.level == "child":
                title = f"{node.subpage.title}: {node.title}"
            line = f"- [{_md(title)}]({request.build_absolute_uri(node.get_absolute_url())})"
            summary = _node_summary(node, key)
            out.append(f"{line}: {_md(summary)}" if summary else line)
        out.append("")

    from core.queries import sorted_rows

    posts = sorted_rows(MarketingBlog.objects.filter(is_published=True).order_by("-published_at"))
    posts = [p for p in posts if f"blog:{p.slug}" not in hidden]
    if posts:
        out += ["## Blog", ""]
        for post in posts[:50]:
            line = f"- [{_md(post.title)}]({request.build_absolute_uri(post.get_absolute_url())})"
            out.append(f"{line}: {_md(post.excerpt)}" if post.excerpt else line)
        out.append("")

    out += [
        "## Optional",
        "",
        f"- [Sitemap]({_abs(request, 'web:sitemap_xml')}): every public page",
        f"- [Privacy policy]({request.build_absolute_uri(legal_url('privacy'))})",
        f"- [Terms of service]({request.build_absolute_uri(legal_url('terms'))})",
    ]
    return "\n".join(out) + "\n"
