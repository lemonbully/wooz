from __future__ import annotations

from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.crypto import get_random_string
from django.utils.html import strip_tags


def _abs_crumbs(request, crumbs):
    """Turn the view's ``[{'title','url'} …]`` breadcrumb list into the
    ``[{'name','url'} …]`` shape web.schema expects, with absolute URLs (a
    BreadcrumbList item must be a fully-qualified URL to be valid)."""
    return [
        {"name": c["title"], "url": request.build_absolute_uri(c["url"])}
        for c in crumbs
    ]


def _desc_from_html(html, fallback=""):
    """A clean, single-line meta description from authored HTML content."""
    from .core_tree import meta_description

    text = " ".join(strip_tags(html or "").split())
    if not text:
        return fallback
    return meta_description(text)

from .models import MarketingBlog

#: Long-form legal pages that still share the generic prose shell. Everything
#: else now has a bespoke page. Restricting the converter to these two means an
#: unknown slug never reaches the generic view.
PAGES = {
    "privacy": "Privacy",
    "terms": "Terms of service",
}


#: Admin-authored /legal/ policies that supersede the short built-in URLs. When
#: one of these is published, /privacy/ or /terms/ 301s to it, so only one dated
#: version of each document is ever live.
LEGAL_SUCCESSORS = {
    "privacy": ("privacy-policy",),
    "terms": ("terms-of-service", "terms-and-conditions"),
}


def legal_url(slug: str) -> str:
    """The live URL of the privacy/terms document: its /legal/ successor when
    one is published, else the built-in short URL."""
    for candidate in LEGAL_SUCCESSORS.get(slug, ()):
        found = _published_policy(candidate)
        if found is not None:
            return found.get_absolute_url()
    return reverse("web:page", kwargs={"slug": slug})


def _visible_faq(key: str):
    from .schema import visible_faq

    try:
        return visible_faq(key)
    except Exception:
        return []


def legacy_redirect(request, **_kwargs):
    """Old or mistyped marketing paths (``/signup``, renamed /pages/ slugs) that
    still receive links: 301 to the live page (see :mod:`web.legacy_links`)."""
    from .legacy_links import redirect_for

    target = redirect_for(request)
    if target is None:
        raise Http404
    return redirect(target, permanent=True)


def home(request):
    from django.db import DatabaseError

    from .models import DemoSiteLink

    # Any active demo link swaps the hero's "Deploy a store" for "See demo".
    try:
        has_demos = DemoSiteLink.any_active()
    except DatabaseError:
        has_demos = False
    return render(request, "web/home.html", {"has_demos": has_demos})


def demo(request):
    """``/demo/``: every active demo store as a card, in the admin's order."""
    from django.db import DatabaseError

    from .models import DemoSiteLink

    try:
        demos = DemoSiteLink.cards()
    except DatabaseError:
        demos = []
    return render(request, "web/demo.html", {"demos": demos})


def platform(request):
    return render(request, "web/platform.html")


def pricing(request):
    # Plans are managed in the admin (billing app). When none exist yet  or the
    # billing tables have not been migrated yet  the template falls back to its
    # static placeholder tiers, so the page is never blank or broken.
    from django.db import DatabaseError

    from billing.pricing import available_durations, plan_cards

    from .models import DemoSiteLink

    try:
        plans = plan_cards()
    except DatabaseError:
        plans = []

    # Feature tabs under the pricing cards (billing.PricingFeature, admin-managed).
    from billing.models import PricingFeature

    try:
        pricing_features = PricingFeature.active()
    except DatabaseError:
        pricing_features = []

    # Operator-curated "see a live store" links (D2C / B2B), managed in the admin.
    try:
        demo_links = DemoSiteLink.active()
    except DatabaseError:
        demo_links = []
    demo_links_d2c = [d for d in demo_links if d.kind == DemoSiteLink.Kind.D2C]
    demo_links_b2b = [d for d in demo_links if d.kind == DemoSiteLink.Kind.B2B]

    # The purchase modal hands off to Cashfree hosted checkout via their JS SDK,
    # loaded on this page  opt this one response into the payment-CSP relaxation
    # (see core.middleware). Only when there is a paid plan to buy.
    has_paid = any(getattr(p, "is_purchasable", False) for p in plans)
    if has_paid:
        request.csp_payment = True

    # Free plans run the *same* details → agreement → OTP flow (settled without a
    # gateway) so they, too, land on onboarding. A free plan that overrides its
    # CTA to a custom URL keeps that link instead.
    has_free_flow = any(
        getattr(p, "plan_type", "") == "free" and not getattr(p, "custom_cta_url", "")
        for p in plans
    )

    # The cadence toggle filters recurring plans by their duration. Show only the
    # durations that actually have a plan (so no toggle leads to an empty view);
    # the first is the default selection.
    durations = available_durations(plans)
    default_duration = durations[0]["key"] if durations else ""

    # Feed the live plans to the structured-data layer so the pricing page's
    # SoftwareApplication offers reflect the real prices (see web.schema).
    request.seo_extra = {"plans": plans}

    return render(
        request,
        "web/pricing.html",
        {
            "plans": plans,
            "has_paid": has_paid,
            "has_free_flow": has_free_flow,
            "has_recurring": bool(durations),
            "durations": durations,
            "default_duration": default_duration,
            "demo_links": demo_links,
            "demo_links_d2c": demo_links_d2c,
            "demo_links_b2b": demo_links_b2b,
            "pricing_features": pricing_features,
            "page_faq": _visible_faq("pricing"),
        },
    )


def solutions(request):
    return render(request, "web/solutions.html")


def customers(request):
    from django.db import DatabaseError

    from .models import DEFAULT_TESTIMONIALS, CustomerTestimonial

    # Testimonials are managed in the admin; until any exist (or before the
    # table is migrated) the page shows its original three.
    try:
        testimonials = list(CustomerTestimonial.objects.filter(is_active=True))
    except DatabaseError:
        testimonials = []
    return render(
        request,
        "web/customers.html",
        {"testimonials": testimonials or list(DEFAULT_TESTIMONIALS)},
    )


def about(request):
    return render(request, "web/about.html")


def security(request):
    return render(request, "web/security.html")


def contact(request):
    """Contact page: FAQs plus a categorised enquiry form.

    The POST branch only acknowledges the submission and mints a reference  no
    persistence yet; the routing/inbox wiring lands with the tenancy phase.
    """
    context = {"page_faq": _visible_faq("contact")}
    if request.method == "POST":
        context["sent"] = True
        context["ref"] = "ENQ-" + get_random_string(6, "0123456789")
        context["category"] = request.POST.get("category", "")
    return render(request, "web/contact.html", context)


def support(request):
    """Support desk: raise a ticket by problem type and account identity.

    Same scaffold posture as ``contact``  acknowledges and mints a ticket
    reference; the ticketing backend is a later phase.
    """
    context = {}
    if request.method == "POST":
        context["sent"] = True
        context["ref"] = "TCK-" + get_random_string(6, "0123456789")
        context["topic"] = request.POST.get("topic", "")
    return render(request, "web/support.html", context)


def _published_policy(slug: str):
    """The published admin-authored policy with ``slug``, or None. Tolerates the
    policy table not being migrated yet, so the legal pages never break."""
    from django.db import DatabaseError

    from .models import LegalPolicy

    try:
        return LegalPolicy.objects.filter(slug=slug, is_published=True).first()
    except DatabaseError:
        return None


def _render_policy(request, policy):
    request.seo_extra = {
        "title": f"{policy.title} | OneWooz",
        "description": _desc_from_html(
            policy.content, fallback=f"{policy.title} for OneWooz."
        ),
        "breadcrumbs": _abs_crumbs(request, [
            {"title": policy.title, "url": policy.get_absolute_url()},
        ]),
    }
    return render(
        request,
        "web/pages/policy.html",
        {"page_title": policy.title, "policy": policy},
    )


def page(request, slug: str):
    if slug not in PAGES:
        raise Http404
    # A newer /legal/ document replaces the short URL outright.
    live = legal_url(slug)
    if live != reverse("web:page", kwargs={"slug": slug}):
        return redirect(live, permanent=True)
    # A published policy authored in the admin with this slug replaces the
    # built-in page's content; otherwise the built-in page renders unchanged.
    policy = _published_policy(slug)
    if policy is not None:
        return _render_policy(request, policy)
    return render(
        request,
        f"web/pages/{slug}.html",
        {"page_title": PAGES[slug]},
    )


def policy(request, slug: str):
    """An admin-authored legal policy at /legal/<slug>/."""
    if slug in PAGES:
        # Privacy / Terms keep their canonical URLs.
        return redirect("web:page", slug=slug, permanent=True)
    found = _published_policy(slug)
    if found is None:
        raise Http404
    return _render_policy(request, found)


# --------------------------------------------------------------------------
# Marketing CMS  core pages (page → subpage → child) and the blog
#
# /pages/ is a resource hub: every core page as a section of topic cards. Each
# node renders as a documentation article: a section tree on the left, the
# article (answer-first lead, anchored headings, child cards, previous/next) in
# the middle and an "On this page" outline on the right. Loading and content
# analysis live in web.core_tree; the structured data it feeds (TechArticle,
# HowTo, FAQPage, ItemList, BreadcrumbList) is built by web.schema.
# --------------------------------------------------------------------------
def _pages_crumb():
    return {"title": "Resources", "url": reverse("web:core_pages")}


def core_pages_index(request):
    """The /pages/ hub: every published core page with its topics and articles."""
    from . import core_tree

    roots = core_tree.load_tree()
    latest = None
    for root in roots:
        root.summary = core_tree.summary_of(root.content)
        for sub in root.subs:
            sub.summary = core_tree.summary_of(sub.content)
            for node in [sub, *sub.kids]:
                if latest is None or node.updated_at > latest:
                    latest = node.updated_at
        if latest is None or root.updated_at > latest:
            latest = root.updated_at

    # "Start here": the first topic of the first page that has articles.
    quick_topic = next((s for r in roots[:1] for s in r.subs if s.kids), None)

    topics = sum(len(r.subs) for r in roots)
    articles = core_tree.article_count(roots)
    names = ", ".join(r.title for r in roots[:3])
    request.seo_extra = {
        "title": "Help Centre, Guides & Resources | OneWooz",
        "description": core_tree.meta_description(
            f"{articles} step-by-step articles on building, launching and growing an online "
            f"store with OneWooz: {names}. Payments, shipping, domains, GST and more.",
            158,
        ),
        "breadcrumbs": _abs_crumbs(request, [_pages_crumb()]),
        "itemlist": [
            {"name": n.title, "url": n.get_absolute_url()}
            for r in roots
            for n in core_tree.flatten(r)
        ],
    }
    return render(
        request,
        "web/core_pages.html",
        {
            "page_title": "Resources",
            "pages": roots,
            "topic_count": topics,
            "article_count": articles,
            "updated_at": latest,
            "quick_topic": quick_topic,
            "quick": quick_topic.kids[:4] if quick_topic else [],
        },
    )


def _core_node(request, slug, sub_slug=None, child_slug=None):
    from . import core_tree

    roots = core_tree.load_tree(slug)
    if not roots:
        raise Http404
    root = roots[0]
    sub = child = None
    if sub_slug is not None:
        sub = next((s for s in root.subs if s.slug == sub_slug), None)
        if sub is None:
            raise Http404
    if child_slug is not None:
        child = next((c for c in sub.kids if c.slug == child_slug), None)
        if child is None:
            raise Http404
    node = child or sub or root

    crumbs = [_pages_crumb(), {"title": root.title, "url": root.get_absolute_url()}]
    if sub is not None:
        crumbs.append({"title": sub.title, "url": sub.get_absolute_url()})
    if child is not None:
        crumbs.append({"title": child.title, "url": child.get_absolute_url()})

    # The cards under the article: a page lists its topics, a topic its articles.
    if node is root:
        children = root.subs
    elif node is sub:
        children = sub.kids
    else:
        children = []
    for c in children:
        c.summary = core_tree.summary_of(c.content)

    flat = core_tree.flatten(root)
    index = next(i for i, n in enumerate(flat) if n is node)
    prev_node = flat[index - 1] if index > 0 else None
    next_node = flat[index + 1] if index + 1 < len(flat) else None

    info = core_tree.analyse(node.content)
    # Hand-typed links to pages that do not exist (old slugs, typos, guides not
    # published yet) are pointed at the live page before the body is served.
    from .legacy_links import LinkIndex, fix_links

    info.body = fix_links(info.body, LinkIndex(), request)
    section = sub.title if child is not None else root.title
    if node is root:
        seo_title = f"{root.title} | OneWooz"
    elif child is None:
        seo_title = f"{sub.title} | {root.title} | OneWooz"
    else:
        seo_title = f"{child.title} | {sub.title} | OneWooz"
    # Keep titles inside the ~60 characters a results page shows.
    if len(seo_title) > 60:
        seo_title = f"{node.title} | OneWooz"
    description = core_tree.meta_description(
        info.summary or f"{node.title}: help and resources from OneWooz."
    )

    request.seo_extra = {
        "title": seo_title,
        "description": description,
        "og_type": "article",
        "kind": "CollectionPage" if children else "WebPage",
        "keywords": [node.title, section, root.title, "OneWooz", "online store"],
        "breadcrumbs": _abs_crumbs(request, crumbs),
        "faq": info.faqs or None,
        "core_article": {
            "headline": node.title,
            "description": description,
            "section": section,
            "words": info.words,
            "minutes": info.minutes,
            "created": node.created_at,
            "modified": node.updated_at,
            "toc": info.toc,
            "steps": info.steps,
            "parent_url": crumbs[-2]["url"],
        },
        "itemlist": [{"name": c.title, "url": c.get_absolute_url()} for c in children] or None,
    }
    return render(
        request,
        "web/core_page.html",
        {
            "page_title": node.title,
            "root": root,
            "node": node,
            "active_sub": sub,
            "section_title": section,
            "subpages": root.subs,
            "children": children,
            "crumbs": crumbs,
            "body": info.body,
            "toc": info.toc,
            "minutes": info.minutes,
            "prev_node": prev_node,
            "next_node": next_node,
            "node_number": index,
            "node_total": len(flat) - 1,
        },
    )


def _core_or_legacy(request, *args):
    """Render a core node; a path that does not exist 301s to its live
    replacement when there is one (renamed slug, typo, unpublished guide)."""
    try:
        return _core_node(request, *args)
    except Http404:
        return legacy_redirect(request)


def core_page(request, slug: str):
    return _core_or_legacy(request, slug)


def core_subpage(request, slug: str, sub_slug: str):
    return _core_or_legacy(request, slug, sub_slug)


def core_child(request, slug: str, sub_slug: str, child_slug: str):
    return _core_or_legacy(request, slug, sub_slug, child_slug)


def blog_list(request):
    from core.queries import sorted_rows

    # Posts carry their full article body; sort keys only (MySQL error 1038).
    posts = sorted_rows(MarketingBlog.objects.filter(is_published=True))
    request.seo_extra = {
        # An empty blog index is thin content: keep it out of the index (and the
        # sitemap, see web.seo) until the first post is published.
        "noindex": not posts,
        "posts": posts,
        "breadcrumbs": _abs_crumbs(request, [
            {"title": "Blog", "url": reverse("web:blog_list")},
        ]),
    }
    return render(request, "web/blog_list.html", {"page_title": "Blog", "posts": posts})


def blog_detail(request, slug: str):
    from core.queries import sorted_rows

    from . import core_tree

    post = get_object_or_404(MarketingBlog, slug=slug, is_published=True)
    # Same article treatment as the core pages: anchored headings, outline,
    # reading time, and FAQ structured data from question headings.
    info = core_tree.analyse(post.content)
    from .legacy_links import LinkIndex, fix_links

    info.body = fix_links(info.body, LinkIndex(), request)
    more = sorted_rows(
        MarketingBlog.objects.filter(is_published=True).exclude(pk=post.pk).order_by("-published_at"),
        limit=3,
    )
    request.seo_extra = {
        "title": f"{post.title} | OneWooz Blog",
        "description": post.excerpt or _desc_from_html(post.content, fallback=post.title),
        "og_type": "article",
        "post": post,
        "faq": info.faqs or None,
        "breadcrumbs": _abs_crumbs(request, [
            {"title": "Blog", "url": reverse("web:blog_list")},
            {"title": post.title, "url": post.get_absolute_url()},
        ]),
    }
    if post.cover_image:
        try:
            request.seo_extra["og_image"] = request.build_absolute_uri(post.cover_image.url)
        except Exception:
            pass
    return render(
        request,
        "web/blog_detail.html",
        {
            "page_title": post.title,
            "post": post,
            "body": info.body,
            "toc": info.toc,
            "minutes": info.minutes,
            "more_posts": more,
        },
    )


# -- crawler files ---------------------------------------------------------


def robots_txt(request):
    """``/robots.txt`` for onewooz.com."""
    from . import seo

    try:
        body = seo.robots_txt(request)
    except Exception:
        body = "User-agent: *\nAllow: /\n"
    return HttpResponse(body, content_type="text/plain; charset=utf-8")


def sitemap_xml(request):
    """``/sitemap.xml`` for onewooz.com, built from every published page."""
    from . import seo

    try:
        body = seo.sitemap_xml(request)
    except Exception:
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>'
        )
    return HttpResponse(body, content_type="application/xml; charset=utf-8")


def llms_txt(request):
    """``/llms.txt``: a plain-text map of onewooz.com for AI assistants."""
    from . import seo

    try:
        body = seo.llms_txt(request)
    except Exception:
        body = "# OneWooz\n"
    return HttpResponse(body, content_type="text/plain; charset=utf-8")
