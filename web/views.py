from __future__ import annotations

from django.db.models import Prefetch
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
    text = " ".join(strip_tags(html or "").split())
    if not text:
        return fallback
    return text[:157] + "…" if len(text) > 158 else text

from .models import (
    MarketingBlog,
    MarketingCoreChildPage,
    MarketingCorePage,
    MarketingCoreSubpage,
)

#: Long-form legal pages that still share the generic prose shell. Everything
#: else now has a bespoke page. Restricting the converter to these two means an
#: unknown slug never reaches the generic view.
PAGES = {
    "privacy": "Privacy",
    "terms": "Terms of service",
}


def home(request):
    return render(request, "web/home.html")


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
    context = {}
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
        "title": f"{policy.title}  OneWooz",
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
# The public display mirrors the storefront's: a sidebar tree (the core page,
# its subpages and their child pages) alongside the current node's content, plus
# a /pages/ index that shows the whole tree as links. Everything is
# server-rendered, one real URL per node, styled with the  marketing
# system (onewooz_design.md).
# --------------------------------------------------------------------------
def _published_tree():
    """Published core pages with their published subpages and child pages,
    prefetched so the whole tree renders without per-node queries."""
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


def _render_core(request, root, node, active_sub, children, crumbs):
    # Structured data for a CMS node: a breadcrumb trail and a description drawn
    # from the node's own content (see web.schema).
    request.seo_extra = {
        "title": f"{node.title}  OneWooz",
        "description": _desc_from_html(
            getattr(node, "content", ""),
            fallback=f"{node.title}  help and resources from OneWooz.",
        ),
        "breadcrumbs": _abs_crumbs(request, crumbs),
    }
    return render(
        request,
        "web/core_page.html",
        {
            "page_title": node.title,
            "root": root,
            "node": node,
            "active_sub": active_sub,
            "subpages": list(root.subpages.all()),  # published (prefetched)
            "children": children,
            "crumbs": crumbs,
        },
    )


def _pages_crumb():
    return {"title": "Pages", "url": reverse("web:core_pages")}


def core_pages_index(request):
    """The /pages/ index  every core page shown as a tree of links."""
    pages = list(_published_tree().order_by("order", "title"))
    return render(
        request,
        "web/core_pages.html",
        {"page_title": "Pages", "pages": pages},
    )


def core_page(request, slug: str):
    root = get_object_or_404(_published_tree(), slug=slug)
    crumbs = [_pages_crumb(), {"title": root.title, "url": root.get_absolute_url()}]
    return _render_core(
        request, root, node=root, active_sub=None,
        children=list(root.subpages.all()), crumbs=crumbs,
    )


def core_subpage(request, slug: str, sub_slug: str):
    root = get_object_or_404(_published_tree(), slug=slug)
    sub = next((s for s in root.subpages.all() if s.slug == sub_slug), None)
    if sub is None:
        raise Http404
    crumbs = [
        _pages_crumb(),
        {"title": root.title, "url": root.get_absolute_url()},
        {"title": sub.title, "url": sub.get_absolute_url()},
    ]
    return _render_core(
        request, root, node=sub, active_sub=sub,
        children=list(sub.children.all()), crumbs=crumbs,
    )


def core_child(request, slug: str, sub_slug: str, child_slug: str):
    root = get_object_or_404(_published_tree(), slug=slug)
    sub = next((s for s in root.subpages.all() if s.slug == sub_slug), None)
    if sub is None:
        raise Http404
    child = next((c for c in sub.children.all() if c.slug == child_slug), None)
    if child is None:
        raise Http404
    crumbs = [
        _pages_crumb(),
        {"title": root.title, "url": root.get_absolute_url()},
        {"title": sub.title, "url": sub.get_absolute_url()},
        {"title": child.title, "url": child.get_absolute_url()},
    ]
    return _render_core(
        request, root, node=child, active_sub=sub, children=[], crumbs=crumbs,
    )


def blog_list(request):
    from core.queries import sorted_rows

    # Posts carry their full article body; sort keys only (MySQL error 1038).
    posts = sorted_rows(MarketingBlog.objects.filter(is_published=True))
    request.seo_extra = {
        "posts": posts,
        "breadcrumbs": _abs_crumbs(request, [
            {"title": "Blog", "url": reverse("web:blog_list")},
        ]),
    }
    return render(request, "web/blog_list.html", {"page_title": "Blog", "posts": posts})


def blog_detail(request, slug: str):
    post = get_object_or_404(MarketingBlog, slug=slug, is_published=True)
    # A full BlogPosting/Article node plus a Home > Blog > post breadcrumb.
    request.seo_extra = {
        "title": f"{post.title}  OneWooz Blog",
        "description": post.excerpt or _desc_from_html(post.content, fallback=post.title),
        "post": post,
        "breadcrumbs": _abs_crumbs(request, [
            {"title": "Blog", "url": reverse("web:blog_list")},
            {"title": post.title, "url": post.get_absolute_url()},
        ]),
    }
    return render(
        request,
        "web/blog_detail.html",
        {"page_title": post.title, "post": post},
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
