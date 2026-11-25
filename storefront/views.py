"""Public storefront views.

Every view is served for one resolved store (``request.site``, set by
:class:`storefront.middleware.CurrentSiteMiddleware`). Content pages read the
CMS blob and the live catalogue; the transactional surface (cart, checkout,
customer accounts, wishlist, reviews) is deliberately stubbed here  those land
with the commerce phase  but each route exists so the ported templates' links
resolve and the storefront stays fully browsable today.
"""

from __future__ import annotations

import json
import logging

from django.http import (
    Http404,
    HttpResponse,
    HttpResponsePermanentRedirect,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from commerce.models import Category, Enquiry, Product, SlugRedirect, renamed_slug
from core import ratelimit

from . import checkout_prefill
from .context import (
    BlogProxy,
    ProductProxy,
    _SiteContentPage,
    build_context,
    home_extra,
    proxy_blogs,
    proxy_products,
)

logger = logging.getLogger("onewooz.audit")

# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def _site_or_404(request):
    site = getattr(request, "site", None)
    if site is None:
        raise Http404("No such store.")
    return site


_RENAMED_ROUTES = {
    SlugRedirect.Kind.CATEGORY: ("public_category_detail", "category_slug"),
    SlugRedirect.Kind.PRODUCT: ("public_product_detail", "product_slug"),
}


def renamed_redirect(request, site, kind: str, slug: str):
    """A 301 to the category/product's current URL if it was renamed away from
    ``slug`` (query string kept), else ``None``. Shared with the referral views,
    whose urlconf reverses the same route names to its own host."""
    current = renamed_slug(site, kind, slug)
    if not current:
        return None
    route, kwarg = _RENAMED_ROUTES[kind]
    url = reverse(route, kwargs={kwarg: current})
    query = request.META.get("QUERY_STRING", "")
    return HttpResponsePermanentRedirect(f"{url}?{query}" if query else url)


# Template 3 (shown to owners as "Template 2") names its pages after what they
# are rather than after the reference's file names, and serves D2C and B2B
# from one set of pages. Map the view-level names onto its files.
_TEMPLATE3_PAGES = {
    "home.html": "index.html",
    "all_products.html": "shop.html",
    "categories.html": "categories.html",
    "category_detail.html": "category.html",
    "product_detail.html": "product.html",
    "about-us.html": "about.html",
    "blogs.html": "blogs.html",
    "blog_detail.html": "blog.html",
    "enquiry.html": "contact.html",
    "privacy-policy.html": "policy.html",
    "terms-conditions.html": "policy.html",
    "refund-policy.html": "policy.html",
    "shipping-policy.html": "policy.html",
    "custom-policy.html": "policy.html",
    "certificates.html": "certificates.html",
    "core_page.html": "core_page.html",
    "core_pages.html": "core_pages.html",
    "cart.html": "cart.html",
}


#: The designs rendered from the ``window.T3`` data feed (storefront.template3).
_JS_THEMES = ("template3", "template4")


def theme_template(site, name: str) -> str:
    """The template path for page ``name`` in ``site``'s own design."""
    tdir = site.template_dir
    if tdir in _JS_THEMES:
        # Template 4 (owner-facing "Template 3") shares Template 3's page set.
        name = _TEMPLATE3_PAGES.get(name, name)
    return f"storefront/{tdir}/{name}"


def _template(site, name: str) -> str:
    return theme_template(site, name)


# The shared transactional pages (checkout, account and sign-in, order
# confirmation) render from storefront/<name> in every theme except Template 3,
# which has its own designs for them. A UGC collaborator storefront always
# keeps the shared pages (they sit inside the referral chrome).
_TEMPLATE3_FLOWS = {
    "checkout.html": "checkout.html",
    "account.html": "account.html",
    "login.html": "login.html",
    "order_confirmation.html": "order-confirmation.html",
}


def _flow_template(request, site, name: str) -> str:
    if (
        site.template_dir in _JS_THEMES
        and getattr(request, "collaboration", None) is None
        and name in _TEMPLATE3_FLOWS
    ):
        return f"storefront/{site.template_dir}/{_TEMPLATE3_FLOWS[name]}"
    # The shared account page draws its own sign-in form when signed out.
    return f"storefront/{'account.html' if name == 'login.html' else name}"


def _render(request, name: str, **extra) -> HttpResponse:
    site = _site_or_404(request)
    if site.maintenance_mode:
        return render(
            request,
            "storefront/maintenance.html",
            build_context(request, site),
            status=503,
        )
    ctx = build_context(request, site, **extra)
    return render(request, _template(site, name), ctx)


def _active_products(request, site):
    # Scoped to the collaborator's assigned catalogue on a referral storefront;
    # a pass-through on the owner's own store.
    from .scope import scope_products

    return scope_products(
        Product.objects.for_site(site)
        .filter(is_active=True, is_deleted=False)
        .order_by("-created_at"),
        request,
    )


# --------------------------------------------------------------------------
# Content pages
# --------------------------------------------------------------------------


def home(request):
    site = _site_or_404(request)
    # On a collaboration (UGC referral) subdomain, render the dedicated creator
    # storefront instead of the owner's themed home  the collaborator gets their
    # own branded storefront over the parent store's (scoped) catalogue.
    if getattr(request, "collaboration", None) is not None and not site.maintenance_mode:
        from .referral import referral_storefront

        rendered = referral_storefront(request, site)
        if rendered is not None:
            return rendered
    return _render(request, "home.html", page_title="Home", **home_extra(request, site))


def all_products(request):
    site = _site_or_404(request)
    qs = _active_products(request, site)

    from django.db.models import Q

    from commerce.models import display_ordered_categories

    # The storefront's own filter form and the nav "Categories" drawer submit
    # category *ids* under ``categories``; some legacy/referral links pass
    # category *slugs* under ``category``. Accept both, so a chosen category
    # actually narrows the grid; and, when nothing is chosen, every active
    # product shows (the page's whole point). ``selected_category_ids`` feeds the
    # sidebar's "checked" state and the heading, so it always holds *ids*.
    import uuid

    def _valid_uuids(values):
        out = []
        for v in values:
            v = (v or "").strip()
            try:
                uuid.UUID(v)
            except (ValueError, AttributeError, TypeError):
                continue
            out.append(v)
        return out

    selected_ids = _valid_uuids(request.GET.getlist("categories"))
    selected_slugs = [s.strip() for s in request.GET.getlist("category") if s.strip()]

    # Resolve slug links to ids so the sidebar and heading light up consistently.
    if selected_slugs and not selected_ids:
        selected_ids = [
            str(pk)
            for pk in Category.objects.for_site(site)
            .filter(slug__in=selected_slugs, is_deleted=False)
            .values_list("id", flat=True)
        ]

    if selected_ids:
        # Match the primary category (FK) or any additional category (M2M), so a
        # product tagged into a category via the multi-select is included too.
        qs = qs.filter(
            Q(category_id__in=selected_ids) | Q(categories__id__in=selected_ids)
        ).distinct()

    query = (request.GET.get("q") or "").strip()
    if query:
        qs = qs.filter(title__icontains=query)

    # Sort control offered in the sidebar. Newest is the default (the queryset
    # already arrives ordered by -created_at).
    sort = (request.GET.get("sort") or "").strip()
    if sort == "price_asc":
        qs = qs.order_by("price")
    elif sort == "price_desc":
        qs = qs.order_by("-price")
    else:
        sort = "newest"

    # Names of the chosen categories, in display order, for a server-rendered
    # heading (the ported inline script cannot build a valid JS object from UUID
    # keys, so the heading is computed here instead).
    selected_category_names = []
    if selected_ids:
        selected_category_names = [
            c.name
            for c in display_ordered_categories(
                Category.objects.for_site(site).filter(
                    id__in=selected_ids, is_deleted=False
                )
            )
        ]

    return _render(
        request,
        "all_products.html",
        page_title="All products",
        products=proxy_products(qs, request, site),
        selected_category_ids=selected_ids,
        selected_category_names=selected_category_names,
        search_query=query,
        sort=sort,
        is_d2c=site.is_d2c,
    )


def categories(request):
    return _render(request, "categories.html", page_title="Categories")


def category_detail(request, category_slug):
    site = _site_or_404(request)
    category = (
        Category.objects.for_site(site)
        .filter(slug=category_slug, is_active=True, is_deleted=False)
        .first()
    )
    if category is None:
        moved = renamed_redirect(request, site, SlugRedirect.Kind.CATEGORY, category_slug)
        if moved is not None:
            return moved
        raise Http404("No such category.")
    # A product belongs to this category via its primary category (the FK) OR
    # via the additional categories multi-select (the M2M), so a product tagged
    # with several categories shows on every one of their pages. distinct()
    # collapses the duplicate rows the M2M join can produce.
    from django.db.models import Q

    qs = _active_products(request, site).filter(
        Q(category=category) | Q(categories=category)
    ).distinct()
    extra = {}
    # A category with no live products is thin content ("No products in this
    # category yet"): keep it out of search until it has something to sell. It
    # is also left out of the sitemap (storefront.seo).
    if not qs.exists():
        extra["noindex"] = True
    return _render(
        request,
        "category_detail.html",
        page_title=category.name,
        category=category,
        products=proxy_products(qs, request, site),
        **extra,
    )


def _build_product_video(product):
    """Assemble the PDP video object the ported template reads (``product_video``).

    Returns ``None`` when the product has no video, so the play button only
    appears when there's something to play  exactly where the reference shows
    it (bottom-right of the main image + a thumbnail). A YouTube link becomes an
    embed; anything else (e.g. a library MP4) is treated as a direct file.
    """
    raw = (getattr(product, "video_link", "") or "").strip()
    if not raw:
        return None
    if raw.startswith("https:/") and not raw.startswith("https://"):
        raw = "https://" + raw[7:]
    elif raw.startswith("http:/") and not raw.startswith("http://"):
        raw = "http://" + raw[6:]
    elif not raw.startswith(("http://", "https://", "//", "/", "data:")):
        raw = "https://" + raw

    yt_id = None
    if "youtu.be/" in raw:
        yt_id = raw.split("youtu.be/")[1].split("?")[0].split("/")[0]
    elif "youtube.com" in raw:
        if "/watch" in raw and "v=" in raw:
            yt_id = raw.split("v=")[1].split("&")[0]
        elif "/embed/" in raw:
            yt_id = raw.split("/embed/")[1].split("?")[0].split("/")[0]
        elif "/shorts/" in raw:
            yt_id = raw.split("/shorts/")[1].split("?")[0].split("/")[0]

    ori = (getattr(product, "video_orientation", "") or "landscape").strip().lower()
    if ori not in ("landscape", "portrait"):
        ori = "landscape"

    if yt_id:
        return {
            "type": "youtube",
            "url": raw,
            "video_id": yt_id,
            "embed_url": f"https://www.youtube.com/embed/{yt_id}",
            "thumb_url": f"https://img.youtube.com/vi/{yt_id}/hqdefault.jpg",
            "orientation": ori,
        }
    # An uploaded file saved as a raw S3 URL plays from the CDN instead.
    from core.cdn import cdn_url

    return {"type": "file", "url": cdn_url(raw), "embed_url": "", "thumb_url": "", "orientation": ori}


def default_product_faqs(site) -> list:
    """The curated FAQ set a product page shows when the product (or the
    picked variation) has none of its own, matched to the store's model."""
    from .content_seed import DEFAULT_B2B_FAQS, DEFAULT_FAQS

    return [dict(x) for x in (DEFAULT_B2B_FAQS if site.is_b2b else DEFAULT_FAQS)]


def product_faqs_for(product, site) -> list:
    """Product-page FAQs: the product's own (added at creation) if any, else
    :func:`default_product_faqs`."""
    own = [
        {"question": (f.get("question") or "").strip(), "answer": (f.get("answer") or "").strip()}
        for f in (product.faqs or [])
        if isinstance(f, dict) and ((f.get("question") or "").strip() or (f.get("answer") or "").strip())
    ]
    return own or default_product_faqs(site)


def product_detail(request, product_slug):
    site = _site_or_404(request)
    product = (
        _active_products(request, site).filter(slug=product_slug).first()
    )
    if product is None:
        moved = renamed_redirect(request, site, SlugRedirect.Kind.PRODUCT, product_slug)
        if moved is not None:
            return moved
        raise Http404("No such product.")

    related = _active_products(request, site).exclude(pk=product.pk)
    if product.category_id:
        related = related.filter(category_id=product.category_id)
    related = related[:8]

    # The ported PDP reads a set of bare context vars for its highlights,
    # reviews, variant selector and cart-vs-enquiry action. Supply them all so
    # every section renders (rather than silently collapsing to empty).
    from django.db.models import Avg

    from commerce.models import Review

    reviews_qs = (
        Review.objects.for_site(site)
        .filter(product=product, approved=True, is_deleted=False)
        .order_by("-created_at")
    )
    total_reviews = reviews_qs.count()
    avg = float(reviews_qs.aggregate(a=Avg("rating"))["a"] or 0)
    avg_rating = round(avg, 1)

    variant_type = ""
    _vs = product.variants
    if isinstance(_vs, list) and _vs and isinstance(_vs[0], dict):
        variant_type = _vs[0].get("name") or ""

    content = site.content or {}
    # Which fields the B2B enquiry modal shows: mobile_only / mobile_qty /
    # email_qty / all. (Cart-vs-enquiry is decided by the store's theme, which
    # follows its business model  not by this value.) Default matches the
    # dashboard's default so the modal always has usable fields.
    enquiry_mode = content.get("enquiry_form_mode") or "mobile_qty"
    if enquiry_mode not in ("mobile_only", "mobile_qty", "email_qty", "all"):
        enquiry_mode = "mobile_qty"

    # Review eligibility. D2C shoppers must be signed in and have a delivered
    # order for this product (or already have a review) to write one; B2B uses
    # the email typed into the form. ``customer_email`` itself comes from
    # build_context (the signed-in shopper's session email).
    from .customer_accounts import current_customer_email

    _cust_email = current_customer_email(request)
    my_review = None
    can_review = site.is_b2b
    if _cust_email:
        my_review = (
            Review.objects.for_site(site)
            .filter(product=product, email__iexact=_cust_email, is_deleted=False)
            .first()
        )
        if site.is_d2c:
            can_review = _can_review_d2c(site, product, _cust_email)

    # Product-page FAQs: the product's own (added at creation) if any, else a
    # curated default set matched to the store's business model. Rendered in the
    # exact same UI as the home-page FAQ section (above the reviews).
    product_faqs = product_faqs_for(product, site)
    product_faqs_subtitle = (
        "Common questions from trade buyers about ordering with us."
        if site.is_b2b
        else "Here are some common questions about this product."
    )

    # Wrap the JSON blob so the ported template's null-safe reads
    # (``d2c_details.extras`` items, ``extra.name|default:extra.type``) never
    # raise on a missing key  the same protection build_context gives content.
    from .safe import wrap

    from commerce.variant_profiles import storefront_profiles

    proxy = ProductProxy(product, request, site)
    # Variations the owner gave their own details (title, bullets, panels,
    # FAQs…), swapped in on the page when the shopper picks one.
    variant_profiles = storefront_profiles(
        product,
        proxy,
        default_faqs=default_product_faqs(site),
    )

    return _render(
        request,
        "product_detail.html",
        page_title=product.title,
        product=proxy,
        variant_profiles=variant_profiles,
        product_video=_build_product_video(product),
        product_faqs=product_faqs,
        product_faqs_subtitle=product_faqs_subtitle,
        related_products=proxy_products(related, request, site),
        d2c_details=wrap(product.d2c_details or {}),
        reviews=list(reviews_qs[:20]),
        total_reviews=total_reviews,
        avg_rating=avg_rating,
        rating_half=(avg_rating - int(avg_rating)) >= 0.5,
        my_review=my_review,
        can_review=can_review,
        variant_type=variant_type,
        enquiry_form_mode=enquiry_mode,
        # Presentation toggles from the Products page. Zoom controls on the main
        # image, price on the related-product cards, both honour their switch.
        show_price_in_lists=bool(content.get("show_price_in_lists", site.is_d2c)),
        enable_product_detail_zoom=bool(content.get("enable_product_detail_zoom", False)),
        is_d2c=site.is_d2c,
        is_b2b=site.is_b2b,
        has_delhivery=False,
    )


def about(request):
    # Pass the store's own content as the "page" so the ported About template 
    # which reads every field off ``page.content.X``  renders the owner's saved
    # About fields instead of only its hard-coded placeholder copy.
    site = _site_or_404(request)
    return _render(
        request, "about-us.html", page_title="About us", page=_SiteContentPage(site)
    )


def certificates(request):
    """The B2B certificates page: every certificate the owner added in the
    editor's Certificates tab, each opening its document (PDF or image) in the
    preview modal. Trade buyers vet suppliers on these; a consumer store has no
    such page, so it 404s there (as in the reference)."""
    site = _site_or_404(request)
    if not site.is_b2b:
        raise Http404("No such page.")
    from .certificates import public_certificates

    return _render(
        request,
        "certificates.html",
        page_title="Certificates",
        certificates=public_certificates(site.content or {}),
    )


def _published_posts(site):
    """The store's blog posts that are visible to the public. A post is public
    unless its status is explicitly ``draft`` (posts saved before the status
    field existed have no status and are treated as published)."""
    posts = (site.content or {}).get("blog_posts") or []
    return [
        p for p in posts
        if isinstance(p, dict) and (p.get("status") or "published") != "draft"
    ]


def blogs(request):
    site = _site_or_404(request)
    return _render(request, "blogs.html", page_title="Blog", blogs=proxy_blogs(_published_posts(site)))


def blog_detail(request, blog_slug):
    site = _site_or_404(request)
    match = next(
        (p for p in _published_posts(site) if p.get("slug") == blog_slug), None
    )
    if match is None:
        raise Http404("No such post.")
    return _render(
        request, "blog_detail.html", page_title=match.get("title") or "Post", blog=BlogProxy(match)
    )


def _find_page(items, slug):
    return next(
        (p for p in (items or []) if isinstance(p, dict) and p.get("slug") == slug), None
    )


def core_page(request, page_slug, sub_slug=None, child_slug=None):
    """Render a core page (and its whole subtree) in the store's own design.

    Core pages nest three levels deep (page → subpage → child), mirroring the
    reference. The themed ``core_page.html`` receives the top-level page and all
    of its subpages/children and switches between them client-side, so the store
    keeps its nav, footer and look. A direct hit on a subpage/child URL  a
    shared link or a refresh  still resolves here (so the node is addressable
    and crawlable); an invalid deep link 404s rather than silently showing the
    overview.
    """
    site = _site_or_404(request)
    if site.maintenance_mode:
        return render(
            request, "storefront/maintenance.html", build_context(request, site), status=503
        )

    pages = (site.content or {}).get("core_pages") or []
    page = _find_page(pages, page_slug)
    if page is None:
        raise Http404("No such page.")

    subpages = [
        s for s in (page.get("subpages") or []) if isinstance(s, dict) and s.get("slug")
    ]

    if sub_slug is not None:
        sub = _find_page(subpages, sub_slug)
        if sub is None:
            raise Http404("No such subpage.")
        if child_slug is not None and _find_page(sub.get("children"), child_slug) is None:
            raise Http404("No such child page.")

    ctx = build_context(
        request, site,
        page_title=page.get("title") or "Page",
        core_page=page,
        subpages=subpages,
    )
    return render(request, _template(site, "core_page.html"), ctx)


def core_pages_index(request):
    """The /core-pages/ hub  every top-level core page, in the store's design."""
    site = _site_or_404(request)
    if site.maintenance_mode:
        return render(
            request, "storefront/maintenance.html", build_context(request, site), status=503
        )
    pages = [
        p
        for p in ((site.content or {}).get("core_pages") or [])
        if isinstance(p, dict) and p.get("slug") and p.get("title")
    ]
    # A hub with no pages is thin content: keep it out of search until it has some.
    extra = {} if pages else {"noindex": True}
    ctx = build_context(request, site, page_title="Guides and resources", pages=pages, **extra)
    return render(request, _template(site, "core_pages.html"), ctx)


# -- policy pages ----------------------------------------------------------


def privacy_policy(request):
    return _render(request, "privacy-policy.html", page_title="Privacy policy")


def terms_conditions(request):
    return _render(request, "terms-conditions.html", page_title="Terms & conditions")


def _return_terms(request, site) -> dict | None:
    """Products grouped by their own return terms, for the Returns & Refunds
    page (storefront/shared/_return_terms.html). Uses the exact properties the
    product page badge and MerchantReturnPolicy markup use, so the three always
    agree. None when no product sets its own terms (the general policy covers
    everything)."""
    rt = Product.ReturnType
    groups = {"non_returnable": [], "replacement": [], "returnable": []}
    rows = (
        _active_products(request, site)
        .order_by()
        .only("id", "title", "slug", "return_type", "return_days", "replacement_days", "trusted_icons")
    )
    for product in rows:
        slug = (product.slug or "").strip()
        kind = product.effective_return_type
        if not slug or not kind:
            continue
        if kind == rt.NON_RETURNABLE:
            days = product.effective_replacement_days
        else:
            days = product.effective_return_days
        groups.get(str(kind), []).append({"title": product.title, "slug": slug, "days": days})
    if not any(groups.values()):
        return None
    for items in groups.values():
        items.sort(key=lambda p: (p["title"] or "").lower())
    groups["has_replacement_window"] = any(p["days"] for p in groups["non_returnable"])
    return groups


def refund_policy(request):
    site = _site_or_404(request)
    return _render(
        request,
        "refund-policy.html",
        page_title="Refund policy",
        return_terms=_return_terms(request, site),
    )


def shipping_policy(request):
    return _render(request, "shipping-policy.html", page_title="Shipping policy")


def custom_policy(request):
    return _render(request, "custom-policy.html", page_title="Policy")


def custom_policy_page(request, policy_slug):
    """One of the owner's extra policies (Edit Content → Policies), at
    ``/policies/<slug>/``. Renders through the theme's custom-policy template, so
    it looks exactly like the built-in policy pages."""
    site = _site_or_404(request)
    from .policies import find

    policy = find(site.content or {}, policy_slug)
    if policy is None:
        raise Http404("No such policy.")
    return _render(
        request, "custom-policy.html", page_title=policy["title"], policy=policy,
    )


# -- crawler files ---------------------------------------------------------


def robots_txt(request):
    """``/robots.txt`` for this store  published only on a verified custom domain."""
    site = _site_or_404(request)
    from . import seo

    if not getattr(request, "is_custom_domain", False):
        # Still on the onewooz.site subdomain: not indexable, so nothing to serve.
        return HttpResponse(
            seo.CUSTOM_DOMAIN_REQUIRED_NOTICE, content_type="text/plain; charset=utf-8"
        )
    try:
        body = seo.robots_txt(request, site)
    except Exception:
        # Fail open: a valid, permissive robots.txt beats a 500 that a crawler
        # may treat as "disallow everything".
        body = "User-agent: *\nAllow: /\n"
    return HttpResponse(body, content_type="text/plain; charset=utf-8")


def llms_txt(request):
    """``/llms.txt``: the store's AI-generated guide for answer engines. Served
    only on its verified custom domain, and only once it has been generated
    (see prompts.llms_txt for the content it needs first); otherwise a 404."""
    site = _site_or_404(request)
    from . import seo

    if not getattr(request, "is_custom_domain", False) or not seo._indexable(site):
        raise Http404("No llms.txt for this store.")
    try:
        from prompts.llms_txt import published_body

        body = published_body(site)
    except Exception:
        body = ""
    if not body:
        raise Http404("No llms.txt for this store.")
    response = HttpResponse(body, content_type="text/plain; charset=utf-8")
    response["Cache-Control"] = "public, max-age=3600"
    return response


def sitemap_xml(request):
    """``/sitemap.xml`` for this store  published only on a verified custom domain."""
    site = _site_or_404(request)
    from . import seo

    if not getattr(request, "is_custom_domain", False):
        # Still on the onewooz.site subdomain: not indexable, so nothing to serve.
        return HttpResponse(
            seo.CUSTOM_DOMAIN_REQUIRED_NOTICE, content_type="text/plain; charset=utf-8"
        )
    try:
        body = seo.sitemap_xml(request, site)
    except Exception:
        # Return an empty but valid sitemap rather than break the crawl.
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>'
        )
    response = HttpResponse(body, content_type="application/xml; charset=utf-8")
    # Built live from the catalogue: a renamed category or product shows its
    # new URL at once, so caches must revalidate rather than serve an old copy.
    response["Cache-Control"] = "no-cache"
    return response


def favicon(request):
    """``/favicon.ico`` for this store.

    The theme's ``<link rel="icon">`` only reaches HTML pages; a browser (and
    many crawlers) still requests ``/favicon.ico`` at the site root for every
    response that has no HTML head  notably ``/robots.txt`` and
    ``/sitemap.xml``. Without this route that request 404s on a custom domain,
    so the owner's chosen favicon never shows on those pages.

    We resolve the owner-set favicon from the CMS blob and 302 to it (made
    absolute against the current host, so a stored ``/media/…`` path stays
    correct on a subdomain, a verified custom domain and the ``/s/<slug>/``
    dev prefix alike). The redirect is cached briefly to spare the origin
    the per-page favicon hit.

    With no favicon set we answer ``204 No Content`` so the browser shows its
    own neutral default icon. A store is the owner's brand: it must never
    carry the OneWooz mark, not even as a fallback. That empty answer is only
    cached for a few minutes, so a newly uploaded favicon shows up quickly.
    """
    site = _site_or_404(request)
    content = getattr(site, "content", None) or {}
    favicon_url = (content.get("favicon") or "").strip()
    if not favicon_url:
        response = HttpResponse(status=204)
        response["Cache-Control"] = "public, max-age=300"
        return response

    response = HttpResponseRedirect(request.build_absolute_uri(favicon_url))
    # A favicon rarely changes; let browsers reuse the redirect for a day.
    response["Cache-Control"] = "public, max-age=86400"
    return response


# -- enquiry / contact -----------------------------------------------------


def _valid_ids(values) -> list[str]:
    """The well-formed UUIDs among ``values`` (anything else is dropped, so a
    tampered field can never raise on the lookup)."""
    import uuid

    out = []
    for v in values:
        try:
            out.append(str(uuid.UUID(str(v).strip())))
        except (ValueError, AttributeError, TypeError):
            continue
    return out


def _enquiry_products(request, site) -> list:
    """The store's live products an enquiry is about: ``product_id`` (product
    page / contact forms) and ``products`` (the homepage multi-select)."""
    ids = _valid_ids([request.POST.get("product_id") or ""] + request.POST.getlist("products"))
    if not ids:
        return []
    return list(_active_products(request, site).filter(pk__in=ids[:50]).order_by())


def _enquiry_needs_message(request, site, products) -> str:
    """The message for a "Discuss your needs" enquiry: the categories and
    products picked and the quantity, one per line. "" when nothing was picked."""
    lines = []
    cat_ids = _valid_ids(request.POST.getlist("categories"))
    if cat_ids:
        names = list(
            Category.objects.for_site(site)
            .filter(pk__in=cat_ids[:50], is_deleted=False)
            .order_by()
            .values_list("name", flat=True)
        )
        if names:
            lines.append("Categories: " + ", ".join(sorted(names)))
    if products:
        lines.append("Products: " + ", ".join(sorted(p.title for p in products)))
    qty = (request.POST.get("quantity") or "").strip()[:12]
    if qty:
        lines.append(f"Quantity: {qty}")
    return "\n".join(lines) if (qty or products or cat_ids) else ""


@require_http_methods(["GET", "POST"])
def enquiry(request):
    site = _site_or_404(request)

    if request.method == "POST":
        # Both storefront contact forms (template1/B2B and template2/D2C) submit
        # via fetch() and parse the response as JSON. Answer AJAX with JSON  a
        # rendered HTML page can't be parsed there and surfaces as a false
        # "Network error" even after the enquiry is saved.
        ajax = _is_ajax(request)

        identity = ratelimit.client_identity(request)
        if not ratelimit.consume("enquiry.submit", identity).allowed:
            msg = "Too many enquiries just now. Please try again shortly."
            if ajax:
                return JsonResponse({"status": "error", "message": msg}, status=429)
            return _render(request, "enquiry.html", page_title="Contact", enquiry_error=msg)

        # The contact page's form carries consent_required and can't be sent
        # until its privacy-policy box is ticked. Other forms posting here
        # (product enquiry popups, page footers) don't carry the flag.
        if request.POST.get("consent_required") and not request.POST.get("privacy_consent"):
            msg = "Please tick the box to agree to the privacy policy before sending your message."
            if ajax:
                return JsonResponse({"status": "error", "message": msg}, status=400)
            return _render(request, "enquiry.html", page_title="Contact", enquiry_error=msg)

        # Google reCAPTCHA (Edit Content > Contact): when the store has its
        # keys set, EVERY enquiry must carry a token Google confirms. Every
        # storefront form that posts here renders the checkbox
        # (storefront/_recaptcha.html + _recaptcha_loader.html), so there is
        # no unprotected way in. See storefront.recaptcha.
        from . import recaptcha

        if recaptcha.enabled(site):
            ok, msg = recaptcha.verify(
                site, request.POST.get("g-recaptcha-response", ""), identity
            )
            if not ok:
                if ajax:
                    return JsonResponse({"status": "error", "message": msg, "captcha": True}, status=400)
                return _render(request, "enquiry.html", page_title="Contact", enquiry_error=msg)

        name = (request.POST.get("name") or "").strip()[:100]
        email = (request.POST.get("email") or "").strip()[:254]
        # ``mobile`` is what the catalogue homepage's "Discuss your needs"
        # form (template1) sends; the contact forms send ``mobile_number``.
        mobile = (
            request.POST.get("mobile_number") or request.POST.get("phone")
            or request.POST.get("mobile") or ""
        ).strip()[:20]
        subject = (request.POST.get("subject") or "").strip()[:200]
        message = (request.POST.get("message") or "").strip()

        # "Discuss your needs" sends no message of its own, only what the buyer
        # picked: categories, products and a quantity. Write that up as the
        # message (and a subject) so the enquiry is accepted and reads clearly
        # in the owner's Enquiries inbox.
        picked_products = _enquiry_products(request, site)
        if not message:
            message = _enquiry_needs_message(request, site, picked_products)
            if message and not subject:
                subject = "Bulk enquiry from the homepage"

        if not (name or email or mobile) or not message:
            msg = "Please add your contact details and a message."
            if ajax:
                return JsonResponse({"status": "error", "message": msg}, status=400)
            return _render(request, "enquiry.html", page_title="Contact", enquiry_error=msg)

        obj = Enquiry.objects.create(
            site=site,
            name=name,
            email=email,
            mobile_number=mobile,
            subject=subject,
            message=message,
        )

        if picked_products:
            obj.products.add(*picked_products)

        from commerce import activity

        activity.record(
            "store.enquiry.received", request=request, site=site,
            target=obj, target_kind="Enquiry",
            label=name or subject or "New enquiry", actor_kind="customer",
        )

        if ajax:
            return JsonResponse(
                {"status": "success", "message": "Thanks! Your message has been sent  we'll be in touch shortly."}
            )
        return _render(request, "enquiry.html", page_title="Contact", enquiry_success=True)

    return _render(request, "enquiry.html", page_title="Contact")


# --------------------------------------------------------------------------
# Lightweight JSON APIs the storefront JS calls
# --------------------------------------------------------------------------


def api_product_suggest(request):
    site = _site_or_404(request)
    query = (request.GET.get("q") or "").strip()
    results = []
    if query:
        from .demo_media import demo_image

        qs = (
            _active_products(request, site)
            .filter(title__icontains=query)
            .prefetch_related("images")[:8]
        )
        for idx, p in enumerate(qs):
            # The suggestion row shows a thumbnail; the templates read ``image``.
            # Use the product's first uploaded image, and fall back to the same
            # demo reference photo the rest of the storefront shows for an empty
            # image slot, so the dropdown never renders a blank square.
            image = ""
            first = next(iter(p.images.all()), None)
            if first is not None and first.image:
                try:
                    image = first.image.url
                except Exception:
                    image = ""
            if not image:
                image = demo_image("product", idx)
            results.append(
                {
                    "title": p.title,
                    "slug": p.slug,
                    "url": reverse("public_product_detail", kwargs={"product_slug": p.slug}),
                    "price": str(p.price),
                    "image": image,
                }
            )
    return JsonResponse({"results": results})


def api_product_variants(request, product_id):
    site = _site_or_404(request)
    product = _active_products(request, site).filter(pk=product_id).first()
    if product is None:
        raise Http404("No such product.")
    from .context import normalize_variant_types

    return JsonResponse(
        {
            "variants": normalize_variant_types(product.variants or []),
            "combinations": product.variant_combinations or [],
        }
    )


def api_product_price_history(request, product_id):
    """The last 30 days of a product's selling price, for the product page's
    "Price history" drawer. ``?variant_key=`` narrows it to one variation
    (its own price track); unknown keys fall back to the base price."""
    site = _site_or_404(request)
    from django.core.exceptions import ValidationError

    try:
        product = _active_products(request, site).filter(pk=product_id).first()
    except (ValidationError, ValueError):
        product = None
    if product is None:
        raise Http404("No such product.")
    from commerce import price_history

    data = price_history.series(product, (request.GET.get("variant_key") or "").strip()[:255])
    response = JsonResponse({"ok": True, **data})
    response["Cache-Control"] = "public, max-age=60"
    return response


def pincode_serviceability(request):
    """Check whether the store's enabled courier delivers to a PIN code.

    Uses Shiprocket's serviceability (couriers + ETD) or Delhivery's pin-code
    API, picking up from the store's default warehouse pincode. Responds
    ``{ok, serviceable, message}``  the shape the product page's checker reads.
    """
    site = _site_or_404(request)
    pin = "".join(ch for ch in (request.GET.get("pincode") or "") if ch.isdigit())[:6]
    if len(pin) < 6:
        return JsonResponse({"ok": False, "serviceable": False, "message": "Enter a valid 6-digit PIN code."})

    from commerce import shipping as shipping_svc

    provider = shipping_svc.enabled_provider(site)
    if provider is None:
        return JsonResponse(
            {"ok": False, "serviceable": None, "message": "Delivery availability can't be checked right now."}
        )

    from commerce.models import Warehouse

    wh = (
        Warehouse.objects.for_site(site).filter(is_default=True).first()
        or Warehouse.objects.for_site(site).first()
    )
    pickup_pin = (wh.pincode if wh else "") or ""
    key = (provider.provider or "").strip().lower()

    try:
        if key == "shiprocket":
            from commerce import shipping_shiprocket as sr

            if not sr.is_configured(provider):
                raise RuntimeError("not configured")
            couriers = sr.check_serviceability(
                provider, pickup_postcode=pickup_pin, delivery_postcode=pin, cod=0, weight=0.5
            )
            if couriers:
                days = None
                for c in couriers:
                    d = c.get("days")
                    if str(d).isdigit():
                        days = min(days, int(d)) if days is not None else int(d)
                extra = f" Estimated delivery in {days} day(s)." if days else ""
                return JsonResponse({"ok": True, "serviceable": True, "message": f"Good news! We deliver to {pin}.{extra}"})
            return JsonResponse({"ok": True, "serviceable": False, "message": f"Sorry, we don't deliver to {pin} yet."})

        if key == "delhivery":
            from commerce import shipping_delhivery as dl

            res = dl.check_pincode_serviceability(provider, pin)
            if res.get("serviceable"):
                modes = []
                if res.get("prepaid"):
                    modes.append("Prepaid")
                if res.get("cod"):
                    modes.append("COD")
                extra = f" ({', '.join(modes)})" if modes else ""
                return JsonResponse({"ok": True, "serviceable": True, "message": f"Good news! We deliver to {pin}.{extra}"})
            return JsonResponse({"ok": True, "serviceable": False, "message": f"Sorry, we don't deliver to {pin} yet."})
    except Exception:
        pass

    return JsonResponse(
        {"ok": False, "serviceable": None, "message": "Couldn't check serviceability right now. Please try again."}
    )


@csrf_exempt
@require_POST
def track(request):
    """Analytics beacon sink. The storefront tracker posts page views, product
    views, cart and checkout events here (via ``navigator.sendBeacon``, which
    can't attach a CSRF token  hence exempt; the endpoint only writes rate-
    limited, bot-filtered, first-party telemetry and returns no content).
    """
    site = getattr(request, "site", None)
    if site is None:
        return HttpResponse(status=204)
    if not ratelimit.consume("analytics.ingest", ratelimit.client_identity(request)).allowed:
        return HttpResponse(status=429)
    try:
        body = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return HttpResponse(status=204)
    if isinstance(body, dict):
        try:
            from .analytics import ingest

            ingest(request, site, body)
        except Exception:
            # Telemetry is best-effort; never surface an error to the page.
            pass
    return HttpResponse(status=204)


# --------------------------------------------------------------------------
# Transactional stubs (cart, checkout, account, wishlist, reviews)
# --------------------------------------------------------------------------


def _coming_soon(request, feature: str) -> HttpResponse:
    site = _site_or_404(request)
    ctx = build_context(request, site, page_title=feature, feature_name=feature)
    return render(request, "storefront/coming_soon.html", ctx, status=200)


# --------------------------------------------------------------------------
# Cart
# --------------------------------------------------------------------------
def _cart(request, site):
    from commerce.cart import Cart

    return Cart(request, site)


def _cart_landing(site) -> str:
    """Where a non-AJAX cart action should land: the drawer (D2C) or the cart
    page (B2B). D2C has no standalone cart page."""
    if site.is_d2c:
        return reverse("public_website") + "?open_cart=1"
    return reverse("public_cart")


def _verified_email(request) -> str:
    """The email the shopper proved with a one-time code (their storefront
    account session), or "". Store credit only ever follows this, never a
    typed checkout email."""
    from .customer_accounts import current_customer_email

    try:
        return current_customer_email(request)
    except Exception:
        return ""


def _with_credit(request, site, summary: dict, items) -> dict:
    """``cart.summary()`` plus the signed-in shopper's store credit.

    Adds ``credit_discount`` and ``total_before_credit``; ``total`` becomes the
    amount after the credit. A guest gets the plain summary (credit 0)."""
    from decimal import Decimal

    from commerce import credits

    out = dict(summary)
    out["credit_discount"] = Decimal("0.00")
    out["total_before_credit"] = summary["total"]
    email = _verified_email(request)
    if not email:
        return out
    try:
        result = credits.compute(
            site, email, lines=items, subtotal=summary["subtotal"], discount=summary["discount"]
        )
    except Exception:
        logger.exception("store credit preview failed site=%s", site.pk)
        return out
    if result.amount > 0:
        goods = max(Decimal("0.00"), summary["subtotal"] - summary["discount"] - result.amount)
        out["credit_discount"] = result.amount
        out["total"] = (goods + (summary.get("tax") or Decimal("0.00"))).quantize(Decimal("0.01"))
    return out


def _cart_json(cart, site, request=None) -> dict:
    s = cart.summary()
    if request is not None:
        s = _with_credit(request, site, s, cart.items())
    return {
        "count": s["count"],
        "subtotal": str(s["subtotal"]),
        "discount": str(s["discount"]),
        "tax": str(s.get("tax", "0")),
        "credit_discount": str(s.get("credit_discount", "0")),
        "total_before_credit": str(s.get("total_before_credit", s["total"])),
        "total": str(s["total"]),
        "coupon_code": s["coupon_code"],
        "free_shipping": s.get("free_shipping", False),
        "currency": site.currency,
    }


def _is_ajax(request) -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def cart_add(request, product_id):
    """Add a product to the cart. Returns JSON to AJAX callers; a plain link
    (or non-AJAX POST) adds and redirects to the cart page."""
    site = _site_or_404(request)
    if not ratelimit.consume("cart.mutate", ratelimit.client_identity(request)).allowed:
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "Too many requests."}, status=429)
        return HttpResponseRedirect(_cart_landing(site))

    product = _active_products(request, site).filter(pk=product_id).first()
    if product is None:
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "This product is unavailable."}, status=404)
        raise Http404("No such product.")

    raw_qty = (
        request.POST.get("quantity")
        or request.POST.get("qty")
        or request.GET.get("quantity")
        or request.GET.get("qty")
        or 1
    )
    try:
        qty = max(1, int(raw_qty))
    except (TypeError, ValueError):
        qty = 1

    # Variation gate: a product with variation combinations can only be added
    # once a specific combination is chosen  either the shopper picked one (a
    # valid ``variant_key``) or the owner marked one "Default selected". With no
    # default and nothing picked, adding is refused so the storefront's disabled
    # buttons cannot be bypassed by hitting the URL directly.
    import json

    from commerce.cart import combos_of, resolve_variant, variant_key_of

    variant_key = (
        request.POST.get("variant_key") or request.GET.get("variant_key") or ""
    ).strip()
    # The product page also sends the selection itself (``variant_attrs``, a
    # JSON list of {name, value}), so a pick still resolves when its key has
    # drifted from the stored combinations (renamed values, case/spacing) or
    # the combination has no stored row at all.
    raw_attrs = request.POST.get("variant_attrs") or request.GET.get("variant_attrs") or ""
    try:
        picked_attrs = json.loads(raw_attrs) if raw_attrs else []
    except (TypeError, ValueError):
        picked_attrs = []
    variant_attrs = []
    combos = combos_of(product)
    has_types = any(
        isinstance(vt, dict) and vt.get("name") and vt.get("values")
        for vt in (product.variants if isinstance(product.variants, list) else [])
    )
    if combos or has_types:
        has_default = any(c.get("default_selected") for c in combos)
        # An unknown pick never belongs on the line; resolve_variant returns ""
        # for it so the gate below falls back to the owner's default (if any).
        if variant_key or picked_attrs:
            variant_key, variant_attrs = resolve_variant(product, variant_key, picked_attrs)
        # Only products with stored combinations insist on a pick; one with
        # variation types but no combination rows still adds without one (as
        # before) when nothing was chosen, e.g. from a listing's quick-add.
        must_select = bool(combos) and not variant_key and not has_default
        if must_select:
            msg = "Please select a variation before adding this product to your cart."
            if _is_ajax(request):
                return JsonResponse(
                    {"ok": False, "error": msg, "select_variant": True}, status=400
                )
            return HttpResponseRedirect(
                reverse("public_product_detail", kwargs={"product_slug": product.slug})
            )
        # With no explicit pick but a default set, add the default combination.
        if not variant_key and has_default:
            for c in combos:
                if c.get("default_selected"):
                    variant_key = variant_key_of(c)
                    break
    else:
        variant_key = ""

    cart = _cart(request, site)

    # Stock gate: an out-of-stock product (or chosen variation) can't be added,
    # and the line can't grow beyond what is left. Checkout re-checks too.
    from commerce import stock

    left = stock.available_for(product, variant_key, variant_attrs or None)
    in_cart = cart.qty_of(product.pk, variant_key)
    if left <= 0 or in_cart >= left:
        msg = (
            "Sorry, this item is out of stock."
            if left <= 0
            else f"Only {left} left in stock, and they're already in your cart."
        )
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": msg, "out_of_stock": True}, status=409)
        return HttpResponseRedirect(
            reverse("public_product_detail", kwargs={"product_slug": product.slug})
        )
    qty = min(qty, left - in_cart)

    # Quantity Discounts app: the owner may cap units per order ("don't sell
    # more than N" / "talk to us above N"). The cart caps too; this explains it.
    from commerce import quantity_pricing as qp

    room, qplan = qp.allowed_more(site, product, cart.data, variant_key)
    if room is not None:
        if room <= 0:
            msg = f"You can order up to {qplan.max_qty} of this item online."
            if qplan.beyond in ("whatsapp", "custom"):
                msg += " Contact the store for a bulk price."
            if _is_ajax(request):
                return JsonResponse({"ok": False, "error": msg, "quantity_limit": True}, status=409)
            return HttpResponseRedirect(
                reverse("public_product_detail", kwargs={"product_slug": product.slug})
            )
        qty = min(qty, room)

    cart.add(product.pk, qty, variant_key=variant_key, attributes=variant_attrs)
    buy_now = bool(request.GET.get("buy") or request.POST.get("buy"))
    if _is_ajax(request):
        resp = {
            "ok": True,
            "message": "Added to cart.",
            "cart": _cart_json(cart, site, request),
            "cart_count": cart.count(),
        }
        # "Buy now" (?buy=1) goes straight to checkout.
        if buy_now:
            resp["buy_now"] = True
            resp["redirect_url"] = reverse("public_checkout")
        return JsonResponse(resp)
    # Buy Now is a plain link (the AJAX cart handler skips it), so honour it here.
    if buy_now:
        return HttpResponseRedirect(reverse("public_checkout"))
    return HttpResponseRedirect(_cart_landing(site))


def _cart_drawer_json(cart, site, request=None) -> dict:
    """The shape the template2 nav cart drawer expects."""
    items = []
    lines = cart.items()
    for it in lines:
        try:
            url = reverse("public_product_detail", kwargs={"product_slug": it["slug"]})
        except Exception:
            url = ""
        items.append({
            "product_id": it["id"],
            "quantity": it["quantity"],
            "image": it["image"].url if it["image"] else "",
            "url": url,
            "title": it["title"],
            "variant_key": it.get("variant_key", ""),
            "variant_label": it.get("variant_label", ""),
            "line_total": str(it["line_total"]),
            "unit_price": str(it["unit_price"]),
            "in_stock": it.get("in_stock", True),
            "available": it.get("available"),
            "short": it.get("short", False),
            # Extra fields the Template 3 drawer reads (others ignore them).
            "pid": str(getattr(it.get("product"), "pk", "") or ""),
            "mrp": str(it["mrp"]) if it.get("mrp") else "",
            "image_fallback": "" if it["image"] else _demo_product_image(it.get("product")),
        })
    s = cart.summary()
    if request is not None:
        s = _with_credit(request, site, s, lines)
    return {
        "items": items,
        "count": s["count"],
        "cart_count": s["count"],
        "totals": {
            "subtotal": str(s["subtotal"]),
            "discount": str(s["discount"]),
            "tax": str(s.get("tax", "0")),
            "total": str(s["total"]),
            "credit_discount": str(s.get("credit_discount", "0")),
            "total_before_credit": str(s.get("total_before_credit", s["total"])),
            "coupon_code": s.get("coupon_code") or "",
            "free_shipping": bool(s.get("free_shipping", False)),
        },
    }


def _demo_product_image(product) -> str:
    """The reference photo a product without uploads shows everywhere else
    (ProductProxy.image1), so a cart line is never imageless."""
    if product is None:
        return ""
    try:
        return ProductProxy(product).image1.url or ""
    except Exception:
        return ""


def cart_data(request):
    """JSON feed for the slide-out cart drawer in the nav."""
    site = _site_or_404(request)
    return JsonResponse(_cart_drawer_json(_cart(request, site), site, request))


@require_POST
def cart_update_body(request):
    """Drawer update/remove  the product id and action ride in the body."""
    site = _site_or_404(request)
    cart = _cart(request, site)
    action = (request.POST.get("action") or "set").lower()
    pid = request.POST.get("product_id") or ""
    if action == "remove":
        cart.remove(pid)
    else:
        try:
            qty = int(request.POST.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0
        cart.set_qty(pid, qty)
    return JsonResponse(_cart_drawer_json(cart, site, request))


def cart_view(request):
    site = _site_or_404(request)
    if site.maintenance_mode:
        return render(
            request, "storefront/maintenance.html", build_context(request, site), status=503
        )
    # D2C has no standalone cart page  the cart is the slide-out drawer in the
    # nav. Any hit on /cart/ (a bookmark, a JS-disabled click) bounces home with
    # the drawer opened rather than rendering a separate page.
    # Template 3 has its own cart page (the drawer's "View Cart") for D2C and
    # B2B alike; it renders from the live cart JSON.
    if site.template_dir in _JS_THEMES:
        ctx = build_context(request, site, page_title="Your cart", noindex=True)
        return render(request, theme_template(site, "cart.html"), ctx)
    if site.is_d2c:
        return HttpResponseRedirect(_cart_landing(site))
    cart = _cart(request, site)
    ctx = build_context(
        request, site, page_title="Your cart",
        cart_items=cart.items(), cart_summary=cart.summary(),
    )
    # Shared template (not per-theme), rendered directly like the core pages.
    return render(request, "storefront/cart.html", ctx)


def cart_recover(request, token):
    """A shopper clicked "Return to your cart" in an abandoned cart reminder.

    That counts as responding: the reminder sequence stops (until they change
    their cart again), the reminded items are put back into this browser's
    cart, and they land on the cart. A bad or expired link still lands on the
    cart, just without restoring anything.
    """
    site = _site_or_404(request)
    from commerce import abandoned_cart

    row = abandoned_cart.read_link(site, token, abandoned_cart.ACTION_OPEN)
    if row is not None:
        abandoned_cart.respond(row)
        abandoned_cart.restore_into(_cart(request, site), row)
    return HttpResponseRedirect(_cart_landing(site))


def cart_reminders_stop(request, token):
    """"Stop these reminders" from an abandoned cart email.

    A GET only shows a confirm button (mail scanners open links on their own,
    and must not switch reminders off); the POST stops them.
    """
    site = _site_or_404(request)
    from commerce import abandoned_cart

    row = abandoned_cart.read_link(site, token, abandoned_cart.ACTION_STOP)
    stopped = False
    if row is not None and request.method == "POST":
        abandoned_cart.respond(row, stop=True)
        stopped = True
    ctx = build_context(
        request, site, page_title="Cart reminders", noindex=True,
        reminder_link_ok=row is not None, reminders_stopped=stopped,
    )
    return render(request, "storefront/cart_reminders_stop.html", ctx)


@require_POST
def cart_update(request, product_id):
    site = _site_or_404(request)
    cart = _cart(request, site)
    try:
        qty = int(request.POST.get("quantity") or 0)
    except (TypeError, ValueError):
        qty = 0
    cart.set_qty(product_id, qty)
    return JsonResponse({"ok": True, "cart": _cart_json(cart, site, request)})


@require_POST
def cart_remove(request, product_id):
    site = _site_or_404(request)
    cart = _cart(request, site)
    cart.remove(product_id)
    return JsonResponse({"ok": True, "cart": _cart_json(cart, site, request)})


@require_POST
def cart_apply_coupon(request):
    site = _site_or_404(request)
    cart = _cart(request, site)
    ok, message = cart.apply_coupon(request.POST.get("code", ""))
    return JsonResponse(
        {"ok": ok, "message": message, "cart": _cart_json(cart, site, request)},
        status=200 if ok else 400,
    )


# --------------------------------------------------------------------------
# Checkout + payment
# --------------------------------------------------------------------------
def _gateway_flags(site):
    from commerce.payments import gateway_config

    cfg = gateway_config(site)
    gateway_ready = bool(cfg and cfg.enabled and cfg.gateway and cfg.key_id and cfg.key_secret)
    return cfg, gateway_ready, bool(cfg and cfg.cod_enabled)


def _partial_enabled(cfg, gateway_ready: bool, cod_enabled: bool = False) -> bool:
    """Partial payment needs a live gateway (for the online part) and a
    configured, positive split. It does NOT depend on the store offering full
    Cash on Delivery: the balance of a partial order is always collected by the
    courier as COD, whether or not plain COD is a checkout option.
    ``cod_enabled`` is accepted for call-site compatibility and ignored."""
    return bool(
        cfg
        and cfg.partial_payment_enabled
        and gateway_ready
        and cfg.partial_payment_value
        and cfg.partial_payment_value > 0
    )


def _partial_online_amount(cfg, total):
    """The amount to charge online now for a partial-payment order, computed
    authoritatively from the store's config (never trusted from the client)."""
    from decimal import ROUND_HALF_UP, Decimal

    total = Decimal(total)
    value = Decimal(cfg.partial_payment_value or 0)
    if cfg.partial_payment_type == cfg.PartialType.FIXED:
        online = value
    else:  # percent
        online = total * value / Decimal("100")
    online = online.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    # Clamp so there is always a real online part and a real COD balance.
    if online < Decimal("0.00"):
        online = Decimal("0.00")
    if online > total:
        online = total
    return online


def _partial_split(cfg, total, enabled: bool) -> tuple[str, str]:
    """("pay now", "rest on COD") amounts, as 2dp strings, for the checkout's
    partial-payment card ("Pay X now, rest Y on COD"). Rendered server-side so
    the card is right before any script runs; the page's script keeps them in
    step as coupons or store credit change the total. Display only: the charge
    itself is recomputed by ``_partial_online_amount`` when the order is placed."""
    if not enabled:
        return "0.00", "0.00"
    try:
        from decimal import Decimal

        order_total = Decimal(str(total or "0"))
        online_now = _partial_online_amount(cfg, order_total)
        rest = max(Decimal("0.00"), order_total - online_now)
        return f"{online_now:.2f}", f"{rest:.2f}"
    except Exception:
        return "0.00", "0.00"


def _whatsapp_bulk_url(site, items, summary) -> str:
    """A ``wa.me`` link to the store's support number, pre-filled with an itemised
    bulk-order enquiry: every line (title, chosen variation, quantity and line
    total) plus the cart total, so the owner receives the full context."""
    phone = "".join(ch for ch in str(site.support_phone or "") if ch.isdigit())
    if not phone:
        return ""
    from .templatetags.website_filters import currency_symbol

    sym = currency_symbol(getattr(site, "currency", "INR"))
    lines = ["Hi, I'm interested in a bulk order for the following:", ""]
    for it in items:
        label = f" ({it['variant_label']})" if it.get("variant_label") else ""
        lines.append(
            f"- {it['title']}{label} | Qty {it['quantity']} | {sym}{it['line_total']}"
        )
    lines.append("")
    lines.append(f"Total: {sym}{summary.get('total')}")
    lines.append("")
    lines.append("Could you share your best bulk pricing? Thank you.")
    from urllib.parse import quote

    return f"https://wa.me/{phone}?text=" + quote("\n".join(lines))


def checkout(request):
    site = _site_or_404(request)
    if site.maintenance_mode:
        return render(
            request, "storefront/maintenance.html", build_context(request, site), status=503
        )
    cart = _cart(request, site)
    if cart.is_empty():
        return HttpResponseRedirect(_cart_landing(site))

    cfg, gateway_ready, cod_enabled = _gateway_flags(site)
    partial_enabled = _partial_enabled(cfg, gateway_ready, cod_enabled)
    items = cart.items()
    # Store credit comes off for the signed-in (email-verified) shopper.
    summary = _with_credit(request, site, cart.summary(), items)
    from commerce import stock

    short = stock.shortages(items)
    partial_now, partial_rest = _partial_split(cfg, summary.get("total"), partial_enabled)
    # Signed-in shopper: their saved addresses as pickers under the billing and
    # ship-to-different blocks, each block pre-filled with its default.
    prefill = checkout_prefill.prefill_for(request, site)
    address_book = checkout_prefill.address_book(request, site, prefill)
    ctx = build_context(
        request, site, page_title="Checkout",
        cart_items=items, cart_summary=summary,
        credit_email=_verified_email(request),
        stock_message=stock.shortage_message(short) if short else "",
        whatsapp_bulk_url=_whatsapp_bulk_url(site, items, summary),
        gateway_ready=gateway_ready, cod_enabled=cod_enabled,
        gateway_name=(cfg.gateway if cfg else ""),
        partial_enabled=partial_enabled,
        partial_type=(cfg.partial_payment_type if cfg else "percent"),
        partial_value=(str(cfg.partial_payment_value) if partial_enabled else "0"),
        partial_now=partial_now,
        partial_rest=partial_rest,
        prefill=prefill,
        address_book=address_book,
    )
    return render(request, _flow_template(request, site, "checkout.html"), ctx)


@require_POST
def checkout_place(request):
    """Create the order from the cart, then either confirm it (COD) or hand back
    the gateway params for the browser to open the hosted checkout."""
    site = _site_or_404(request)
    if not ratelimit.consume("checkout.submit", ratelimit.client_identity(request)).allowed:
        return JsonResponse({"ok": False, "error": "Too many attempts. Please wait a moment."}, status=429)

    cart = _cart(request, site)
    if cart.is_empty():
        return JsonResponse({"ok": False, "error": "Your cart is empty."}, status=400)

    details = {
        "name": (request.POST.get("name") or "").strip(),
        "email": (request.POST.get("email") or "").strip(),
        "phone": (request.POST.get("phone") or "").strip(),
        "address": (request.POST.get("address") or "").strip(),
        "city": (request.POST.get("city") or "").strip(),
        "state": (request.POST.get("state") or "").strip(),
        "pincode": (request.POST.get("pincode") or "").strip(),
        "company_name": (request.POST.get("company_name") or "").strip(),
        "gstin": (request.POST.get("gstin") or "").strip(),
        # Sent only when the order ships to a different address; otherwise the
        # billing address is the delivery address (commerce.checkout).
        "billing_address": (request.POST.get("billing_address") or "").strip()[:1000],
    }
    if not details["name"] or not (details["email"] or details["phone"]) or not details["address"]:
        return JsonResponse({"ok": False, "error": "Please fill your name, contact and address."}, status=400)

    method = (request.POST.get("method") or "").strip().lower()
    cfg, gateway_ready, cod_enabled = _gateway_flags(site)
    partial_enabled = _partial_enabled(cfg, gateway_ready, cod_enabled)
    is_cod = method == "cod" and cod_enabled

    # Store credit follows the email the shopper proved with a one-time code,
    # and only when they order under that same email. A basket the credit
    # covers in full needs no payment at all.
    verified = _verified_email(request)
    credit_email = verified if verified and verified == details["email"].lower() else ""
    covered_by_credit = False
    if credit_email:
        preview = _with_credit(request, site, cart.summary(), cart.items())
        covered_by_credit = preview["credit_discount"] > 0 and preview["total"] <= 0

    # Refuse an online payment we can't take *before* creating anything.
    if not is_cod and not covered_by_credit:
        if not gateway_ready:
            return JsonResponse({"ok": False, "error": "Online payment isn’t available right now."}, status=400)
        if method == "partial" and not partial_enabled:
            return JsonResponse({"ok": False, "error": "Partial payment isn’t available for this store."}, status=400)

    from commerce.checkout import (
        CouponError,
        EmptyCartError,
        create_order_from_cart,
        discard_unpaid_order,
    )
    from commerce.stock import OutOfStockError

    # A COD order is placed now. An online (full or partial) order is created
    # hidden, awaiting payment: it becomes a real order only when the gateway
    # confirms the payment, so a failed or abandoned payment leaves no order.
    try:
        order = create_order_from_cart(
            request, site, details=details, cart=cart, awaiting_payment=not is_cod,
            credit_email=credit_email,
        )
    except EmptyCartError:
        return JsonResponse({"ok": False, "error": "Your cart is empty."}, status=400)
    except CouponError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except OutOfStockError as exc:
        return JsonResponse({"ok": False, "error": str(exc), "out_of_stock": True}, status=409)

    # Remember what was typed (in this shopper's own session) so their next
    # checkout on this store starts filled in.
    checkout_prefill.remember(request, site, {
        **details,
        "house": request.POST.get("house"),
        "street": request.POST.get("street"),
        "notes": request.POST.get("notes"),
    })
    # A signed-in shopper's new address(es) go into their address book, so
    # the next checkout (and their account page) offers them as saved cards.
    checkout_prefill.save_to_address_book(request, site, request.POST)

    if is_cod:
        return _place_cod_order(request, site, cart, order)

    if order.total <= 0 and order.credit_discount_amount > 0:
        # Store credit paid for all of it: nothing to charge online.
        return _place_credit_paid_order(request, site, cart, order)
    if not gateway_ready or (method == "partial" and not partial_enabled):
        # Expected the credit to cover it, but it no longer does.
        discard_unpaid_order(order)
        return JsonResponse(
            {"ok": False, "error": "Your store credit no longer covers this order. Please choose another payment method."},
            status=400,
        )

    try:
        return _start_online_payment(
            request, site, order, cfg, method=method, cod_enabled=cod_enabled
        )
    except Exception:
        # Anything going wrong on our side before the shopper reaches the
        # gateway must not leave an order behind either.
        discard_unpaid_order(order)
        logger.exception("starting online payment failed: order=%s", order.pk)
        return JsonResponse(
            {"ok": False, "error": "We couldn’t start the online payment. Please try again."},
            status=500,
        )


def _attribute_referral(request, order) -> None:
    # UGC referral store: attribute the order to the collaborator and price the
    # commission now. It is credited to their earnings when the order settles.
    collaboration = getattr(request, "collaboration", None)
    if collaboration is not None:
        order.attribute_to(collaboration)
        order.save(update_fields=["collaboration", "commission_amount", "updated_at"])


def _confirmation_url(request, order) -> str:
    return request.build_absolute_uri(
        reverse("public_order_confirmation", kwargs={"order_id": order.id})
    )


def _place_cod_order(request, site, cart, order):
    """Cash on delivery  no gateway; the order is placed right away."""
    from commerce import activity

    _attribute_referral(request, order)
    # Nothing to collect on delivery when store credit paid for all of it.
    paid_by_credit = order.total <= 0 and order.credit_discount_amount > 0
    order.payment_method = "store_credit" if paid_by_credit else "cod"
    order.save(update_fields=["payment_method", "updated_at"])
    # Auto-create shipping on -> book with the courier now (the order is sent
    # to the Shiprocket dashboard) and it moves to the courier tab. Off -> the
    # order stays in Pending until the owner accepts it. A COD order is never
    # fully paid, so its status stays CREATED (pending) here.
    try:
        from commerce import shipping as shipping_svc

        shipping_svc.auto_ship_if_enabled(site, order)
    except Exception:
        pass
    activity.record(
        "store.order.received", request=request, site=site,
        target=order, target_kind="Order", label=f"#{order.number}",
        actor_kind="customer",
    )
    # Email the shopper their itemised confirmation (with variations). Best-effort
    # so a mail hiccup never fails a placed order.
    try:
        from commerce.emails import send_order_confirmation

        send_order_confirmation(order, confirmation_url=_confirmation_url(request, order))
    except Exception:
        logger.exception("order confirmation email failed: order=%s", order.pk)
    cart.clear()
    return JsonResponse({"ok": True, "redirect": _confirmation_url(request, order)})


def _place_credit_paid_order(request, site, cart, order):
    """Store credit covered the whole order: place it as paid, no gateway.

    The order was created hidden (``awaiting_payment``) like any online one;
    this promotes it the way a confirmed payment would, so it is placed (stock,
    credit drawn down, owner told, shopper emailed) exactly once.
    """
    from commerce.checkout import place_order
    from commerce.models import Order

    _attribute_referral(request, order)
    placed = Order.all_objects.filter(
        pk=order.pk, status=Order.Status.AWAITING_PAYMENT
    ).update(status=Order.Status.PAID, payment_method="store_credit")
    if placed:
        order.status = Order.Status.PAID
        order.payment_method = "store_credit"
        order.assign_placed_number()
        # A real save too, so the status hooks (timestamps, collaborator
        # credit, platform charge) run for the paid status.
        order.save(update_fields=["status", "payment_method", "confirmed_at", "updated_at"])
        place_order(order)
        try:
            from commerce import shipping as shipping_svc

            shipping_svc.auto_ship_if_enabled(site, order)
        except Exception:
            pass
    cart.clear()
    return JsonResponse({"ok": True, "redirect": _confirmation_url(request, order)})


def _start_online_payment(request, site, order, cfg, *, method: str, cod_enabled: bool):
    """Open the gateway checkout for a hidden ``awaiting_payment`` order.

    On any refusal the order is deleted (it was never shown or booked). On
    success nothing is announced yet  :func:`commerce.checkout.place_order`
    does that once the payment is confirmed.
    """
    from commerce import activity
    from commerce.checkout import discard_unpaid_order
    from commerce.payments import PaymentError, create_gateway_order

    _attribute_referral(request, order)
    confirmation = _confirmation_url(request, order)
    notify_url = request.build_absolute_uri(reverse(f"public_webhook_{cfg.gateway}"))

    # Partial payment  charge a part online now; the balance is collected as COD.
    amount = None
    if method == "partial":
        online = _partial_online_amount(cfg, order.total)
        if online <= 0 or online >= order.total:
            discard_unpaid_order(order)
            return JsonResponse(
                {"ok": False, "error": "Partial payment isn’t available for this order amount."},
                status=400,
            )
        order.is_partial_payment = True
        order.partial_paid_amount = online
        order.partial_cod_amount = order.total - online
        order.save(update_fields=["is_partial_payment", "partial_paid_amount", "partial_cod_amount", "updated_at"])
        amount = online

    # A UGC store can't take Cashfree / Razorpay payments on its own
    # <sub>.myugcstore.com domain (each gateway only trusts the websites the
    # owner registered), so the payment step runs on the owner's own store
    # domain instead; Cashfree's return and webhook point there too. See
    # storefront.hosted_pay.
    from . import hosted_pay

    hosted = hosted_pay.applies(request, cfg)
    gateway_return, gateway_notify = confirmation, notify_url
    if hosted:
        token = hosted_pay.make_token(
            site, order,
            return_url=confirmation,
            back_url=request.build_absolute_uri(reverse("public_checkout")),
        )
        hosted_urls = hosted_pay.urls(site, request, token)
        gateway_return, gateway_notify = hosted_urls["done"], hosted_urls["notify"]

    try:
        params = create_gateway_order(
            site, order, cfg, return_url=gateway_return, notify_url=gateway_notify, amount=amount
        )
    except PaymentError as exc:
        # The gateway refused (bad/mismatched keys, a validation error…). No
        # order is left behind, and the owner is told *why* on their dashboard;
        # the shopper only gets a safe, actionable message.
        discard_unpaid_order(order)
        activity.record(
            "store.payment.failed", request=request, site=site,
            target_kind="Order",
            label=(
                f"{cfg.get_gateway_display()}: "
                f"{exc.gateway_message or 'the gateway refused the order'}"
            )[:250],
            actor_kind="customer",
        )
        error = "We couldn’t start the online payment. Please try again in a moment"
        error += ", or choose Cash on Delivery." if cod_enabled else "."
        return JsonResponse({"ok": False, "error": error}, status=502)

    if hosted:
        hosted_pay.remember_session(order, params.get("payment_session_id", ""), params.get("mode", ""))
        # Back on this UGC domain, the confirmation page clears the cart for it.
        request.session[hosted_pay.SESSION_KEY] = str(order.pk)
        return JsonResponse({"ok": True, "redirect": hosted_urls["pay"]})

    params["ok"] = True
    params["confirmation"] = confirmation
    return JsonResponse(params)


@require_POST
def payment_verify(request):
    """Called by the browser after the gateway reports success. Confirms the
    payment authoritatively (signature for Razorpay, fetch for Cashfree), marks
    the order paid, and clears the cart."""
    site = _site_or_404(request)
    from commerce.models import Payment
    from commerce.payments import (
        confirm_cashfree_by_fetch,
        gateway_config,
        verify_razorpay_return,
    )

    payment_id = (request.POST.get("payment_id") or "").strip()
    payment = Payment.objects.for_site(site).filter(pk=payment_id).select_related("order").first() if payment_id else None
    if payment is None:
        return JsonResponse({"ok": False, "error": "Unknown payment."}, status=404)

    cfg = gateway_config(site)
    ok = False
    if payment.gateway == Payment.Gateway.RAZORPAY:
        ok = verify_razorpay_return(
            payment, cfg,
            razorpay_payment_id=(request.POST.get("razorpay_payment_id") or ""),
            razorpay_signature=(request.POST.get("razorpay_signature") or ""),
        )
    elif payment.gateway == Payment.Gateway.CASHFREE:
        ok = confirm_cashfree_by_fetch(payment, cfg)

    if not ok:
        return JsonResponse({"ok": False, "error": "Payment not confirmed yet."}, status=400)

    _cart(request, site).clear()
    return JsonResponse(
        {"ok": True, "redirect": request.build_absolute_uri(
            reverse("public_order_confirmation", kwargs={"order_id": payment.order_id})
        )}
    )


def _webhook(request, gateway: str):
    """Shared webhook sink: find the payment by the gateway order id in the body,
    then confirm by fetching authoritative status. Never trusts the body's word."""
    try:
        body = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return HttpResponse(status=400)

    # Pull the gateway order id out of either provider's payload shape.
    order_id = ""
    if gateway == "razorpay":
        entity = (((body.get("payload") or {}).get("payment") or {}).get("entity")) or {}
        order_id = entity.get("order_id") or ""
    else:  # cashfree
        data = body.get("data") or body
        order_id = (data.get("order") or {}).get("order_id") or data.get("order_id") or ""

    from commerce.payments import confirm_by_fetch, gateway_config, payment_for_provider_order

    payment = payment_for_provider_order(order_id)
    if payment is not None:
        cfg = gateway_config(payment.site)
        if cfg is not None:
            confirm_by_fetch(payment, cfg)
    # Always 200 so the gateway stops retrying a webhook we've handled/ignored.
    return HttpResponse(status=200)


@csrf_exempt
@require_POST
def webhook_razorpay(request):
    return _webhook(request, "razorpay")


@csrf_exempt
@require_POST
def webhook_cashfree(request):
    return _webhook(request, "cashfree")


def _courier_webhook(request, token: str = ""):
    """Inbound courier tracking webhook (Shiprocket parity, see
    :mod:`commerce.courier_webhook`). The store is resolved from the host; the
    provider defaults to Shiprocket and may be overridden with ``?provider=``."""
    site = getattr(request, "site", None)
    if site is None:
        return JsonResponse({"status": "error", "message": "unknown store"}, status=404)
    if not ratelimit.consume("webhook.courier", ratelimit.client_identity(request)).allowed:
        return JsonResponse({"status": "rate_limited"}, status=429)
    from commerce import courier_webhook

    provider = (request.GET.get("provider") or "shiprocket").strip().lower()
    headers = {k.lower(): v for k, v in request.headers.items()}
    if token:
        headers["_path_token"] = token
    try:
        result = courier_webhook.process(site, provider, request.body or b"{}", headers)
    except Exception:
        # Never 500 a courier  it would retry forever. Log and accept.
        import logging

        logging.getLogger(__name__).exception("courier webhook failed")
        return JsonResponse({"status": "error"}, status=200)
    code = result.pop("code", 200)
    return JsonResponse(result, status=code)


@csrf_exempt
@require_POST
def webhook_courier(request):
    return _courier_webhook(request)


@csrf_exempt
@require_POST
def webhook_courier_token(request, token):
    # Token-in-path variant  Shiprocket's dashboard rejects query-string tokens
    # (and URLs containing courier keywords) during webhook-URL validation, so a
    # keyword-free path like /webhooks/tracking/<token>/ is offered too.
    return _courier_webhook(request, token=token)


# --------------------------------------------------------------------------
# Customer account (email-OTP shopper session)
# --------------------------------------------------------------------------
@require_POST
def account_send_otp(request):
    """Email a one-time sign-in code. Responds identically for any well-formed
    address so it can't be used to discover which emails have shopped here."""
    site = _site_or_404(request)
    from .customer_accounts import start_challenge

    email = (request.POST.get("email") or "").strip()
    ok = start_challenge(request, site, email)
    if not ok:
        return JsonResponse({"success": False, "error": "Enter a valid email address."}, status=400)
    return JsonResponse({"success": True})


@require_POST
def account_verify_otp(request):
    """Verify a submitted code and, on success, start the customer session."""
    site = _site_or_404(request)
    from .customer_accounts import verify_challenge

    email = (request.POST.get("email") or "").strip()
    code = (request.POST.get("otp") or request.POST.get("code") or "").strip()
    if verify_challenge(request, email, code):
        # Items added before signing in now belong to a known shopper: start
        # the abandoned cart reminders for them (when the store opted in).
        try:
            _cart(request, site).track_signed_in()
        except Exception:
            logger.exception("abandoned cart: sign-in tracking failed")
        return JsonResponse({"success": True, "redirect": reverse("public_user_account")})
    return JsonResponse({"success": False, "error": "That code is incorrect or has expired."}, status=400)


def user_account(request):
    """The shopper's account: their orders and wishlist, or a sign-in prompt."""
    site = _site_or_404(request)
    if site.maintenance_mode:
        return render(
            request, "storefront/maintenance.html", build_context(request, site), status=503
        )

    from decimal import Decimal

    from .customer_accounts import current_customer_email

    from commerce.models import CustomerCredit, Order, WishlistItem

    from . import account_profile

    email = current_customer_email(request)
    orders, wishlist, customer_credits = [], [], []
    profile, saved_addresses = None, []
    orders_count, first_name = 0, ""
    credit_total = Decimal("0")
    status_filter = (request.GET.get("status") or "").strip().lower()
    if status_filter not in {c[0] for c in account_profile.ORDER_CHIPS}:
        status_filter = ""
    if email:
        profile = account_profile.find_customer(site, email)
        saved_addresses = account_profile.addresses(site, profile)
        qs = account_profile.account_orders(site, email, profile)
        orders_count = qs.count()
        first_name = ((profile.name if profile else "") or "").strip().split(" ")[0]
        qs = account_profile.filter_orders_by_chip(site, qs, status_filter)
        orders = list(qs.prefetch_related("items").order_by("-created_at")[:100])
        # Cancel (until shipped) / replacement (delivered, within the window)
        # options and any request already raised, per order.
        from commerce.order_requests import annotate_for_account

        annotate_for_account(orders)
        # Card details: first line + its product photo, how many more lines,
        # returned (RTO) flag and the courier tracking link (shipped only).
        for o in orders:
            lines = list(o.items.all())
            o.first_item = lines[0] if lines else None
            o.more_items = max(len(lines) - 1, 0)
            o.search_text = " ".join([str(o.number), o.display_number] + [ln.title or "" for ln in lines]).lower()
        _attach_item_thumbs(site, [o.first_item for o in orders if o.first_item is not None])
        account_profile.annotate_order_cards(site, orders)

        wl = (
            WishlistItem.objects.for_site(site)
            .filter(customer_email=email)
            .select_related("product")
            .order_by("-created_at")
        )
        wishlist = [
            ProductProxy(w.product, request, site)
            for w in wl
            if w.product is not None and not w.product.is_deleted
        ]

        # Store credits the owner granted this shopper  shown in the sidebar.
        from django.db.models import Q
        from django.utils import timezone as _tz

        customer_credits = list(
            CustomerCredit.objects.for_site(site)
            .filter(customer_email__iexact=email, is_active=True)
            .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=_tz.now().date()))
            .order_by("-created_at")
        )
        # Spendable balance across amount credits (percent credits are offers,
        # listed on the Store credit tab but not added to a rupee figure).
        from commerce.credits import _balance

        credit_total = sum(
            (_balance(cr) for cr in customer_credits if cr.credit_type != "percent"),
            Decimal("0"),
        )

    ctx = build_context(
        request, site, page_title="Your account",
        orders=orders, wishlist=wishlist, status_filter=status_filter,
        order_status_choices=Order.owner_status_choices(),
        order_chips=account_profile.ORDER_CHIPS,
        order_chips_more=account_profile.ORDER_CHIPS_MORE,
        credit_total=credit_total,
        customer_credits=customer_credits,
        account_profile=profile,
        saved_addresses=saved_addresses,
        orders_count=orders_count,
        account_first_name=first_name,
    )
    return render(
        request,
        _flow_template(request, site, "account.html" if email else "login.html"),
        ctx,
    )


# --------------------------------------------------------------------------
# Account self-service: details, email, address book, delete account (JSON)
# --------------------------------------------------------------------------
def _account_session(request):
    """(site, signed-in email or "") for the account JSON endpoints."""
    site = _site_or_404(request)
    from .customer_accounts import current_customer_email

    return site, current_customer_email(request)


def _account_fail(message, status=400):
    return JsonResponse({"ok": False, "error": message}, status=status)


def _account_limited(request) -> bool:
    return not ratelimit.consume("account.profile", ratelimit.client_identity(request)).allowed


@require_POST
def account_profile_save(request):
    site, email = _account_session(request)
    if not email:
        return _account_fail("Please sign in again.", 401)
    if _account_limited(request):
        return _account_fail("Too many changes. Please wait a moment.", 429)
    from . import account_profile

    try:
        customer = account_profile.save_profile(site, email, request.POST)
    except account_profile.ProfileError as exc:
        return _account_fail(str(exc))
    return JsonResponse({
        "ok": True, "message": "Your details are saved.",
        "name": customer.name, "phone": customer.phone,
    })


@require_POST
def account_email_send_otp(request):
    site, email = _account_session(request)
    if not email:
        return _account_fail("Please sign in again.", 401)
    from . import account_profile
    from .customer_accounts import ACTION_EMAIL_CHANGE, start_action_challenge

    try:
        new = account_profile.check_new_email(site, email, request.POST.get("email"))
    except account_profile.ProfileError as exc:
        return _account_fail(str(exc))
    start_action_challenge(request, site, new, ACTION_EMAIL_CHANGE)
    return JsonResponse({"ok": True, "message": f"We sent a 6-digit code to {new}. Enter it to confirm."})


@require_POST
def account_email_verify(request):
    site, email = _account_session(request)
    if not email:
        return _account_fail("Please sign in again.", 401)
    from . import account_profile
    from .customer_accounts import ACTION_EMAIL_CHANGE, SESSION_CUSTOMER, verify_action_challenge

    new = verify_action_challenge(request, ACTION_EMAIL_CHANGE, request.POST.get("code"))
    if not new:
        return _account_fail("That code is incorrect or has expired.")
    try:
        account_profile.change_email(site, email, new)
    except account_profile.ProfileError as exc:
        return _account_fail(str(exc))
    request.session[SESSION_CUSTOMER] = new
    request.session.modified = True
    return JsonResponse({"ok": True, "message": "Your email is updated.", "email": new})


@require_POST
def account_address_save(request):
    site, email = _account_session(request)
    if not email:
        return _account_fail("Please sign in again.", 401)
    if _account_limited(request):
        return _account_fail("Too many changes. Please wait a moment.", 429)
    import uuid as _uuid

    from . import account_profile

    raw_id = (request.POST.get("id") or "").strip()
    address_id = None
    if raw_id:
        try:
            address_id = _uuid.UUID(raw_id)
        except ValueError:
            return _account_fail("That address no longer exists.", 404)
    try:
        account_profile.save_address(site, email, request.POST, address_id=address_id)
    except account_profile.ProfileError as exc:
        return _account_fail(str(exc))
    return JsonResponse({"ok": True, "message": "Address saved."})


@require_POST
def account_address_delete(request, address_id):
    site, email = _account_session(request)
    if not email:
        return _account_fail("Please sign in again.", 401)
    from . import account_profile

    if not account_profile.delete_address(site, email, address_id):
        return _account_fail("That address no longer exists.", 404)
    return JsonResponse({"ok": True, "message": "Address removed."})


@require_POST
def account_delete_send_otp(request):
    site, email = _account_session(request)
    if not email:
        return _account_fail("Please sign in again.", 401)
    from .customer_accounts import ACTION_DELETE_ACCOUNT, start_action_challenge

    start_action_challenge(request, site, email, ACTION_DELETE_ACCOUNT)
    return JsonResponse({"ok": True, "message": f"We sent a 6-digit code to {email}."})


@require_POST
def account_delete_confirm(request):
    site, email = _account_session(request)
    if not email:
        return _account_fail("Please sign in again.", 401)
    from commerce import activity

    from . import account_profile
    from .customer_accounts import ACTION_DELETE_ACCOUNT, sign_out, verify_action_challenge

    verified = verify_action_challenge(request, ACTION_DELETE_ACCOUNT, request.POST.get("code"))
    if not verified or verified != email:
        return _account_fail("That code is incorrect or has expired.")
    customer = account_profile.delete_account(site, email)
    activity.record(
        "store.customer.account_deleted", site=site,
        target=customer, target_kind="Customer", label=customer.name or "A customer",
        actor_kind="customer",
    )
    sign_out(request)
    return JsonResponse({
        "ok": True, "message": "Your account has been deleted.",
        "redirect": reverse("public_website"),
    })


@require_POST
def account_order_request(request, order_id):
    """A signed-in shopper raises a cancellation (until the order ships) or a
    replacement request (delivered, within the replacement window) on one of
    their orders. It lands on the owner's Requests page. JSON in, JSON out."""
    site = _site_or_404(request)
    from django.core.exceptions import ValidationError

    from commerce import order_requests
    from commerce.models import Order, OrderRequest

    from .customer_accounts import current_customer_email

    email = current_customer_email(request)
    if not email:
        return JsonResponse({"ok": False, "error": "Please sign in again."}, status=401)
    if not ratelimit.consume("checkout.submit", ratelimit.client_identity(request)).allowed:
        return JsonResponse({"ok": False, "error": "Too many attempts. Please wait a moment."}, status=429)
    from .account_profile import account_orders

    try:
        order = account_orders(site, email).filter(pk=order_id).first()
    except (ValidationError, ValueError):
        order = None
    if order is None:
        return JsonResponse({"ok": False, "error": "Order not found."}, status=404)

    kind = (request.POST.get("type") or "").strip().lower()
    request_type = {
        "cancel": OrderRequest.RequestType.CANCEL,
        "replace": OrderRequest.RequestType.REPLACE,
        "support": OrderRequest.RequestType.SUPPORT,
    }.get(kind)
    if request_type is None:
        return JsonResponse({"ok": False, "error": "Unknown request."}, status=400)
    reason = (request.POST.get("reason") or "").strip()
    description = (request.POST.get("description") or "").strip()
    if not reason:
        return JsonResponse(
            {"ok": False, "error": "Please choose what you need help with." if kind == "support" else "Please tell us the reason."},
            status=400,
        )
    if request_type == OrderRequest.RequestType.SUPPORT and len(description) < 5:
        return JsonResponse({"ok": False, "error": "Please describe how the store can help."}, status=400)
    try:
        req = order_requests.raise_request(
            order, request_type, reason=reason, description=description,
        )
    except order_requests.RequestError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    if request_type == OrderRequest.RequestType.SUPPORT:
        message = "Your support request has been sent. The store will reply to you by email."
    else:
        label = "Cancellation" if request_type == OrderRequest.RequestType.CANCEL else "Replacement"
        message = f"{label} requested. The store will review it and update you by email."
    return JsonResponse({
        "ok": True,
        "success": True,
        "status": req.get_status_display(),
        "message": message,
    })


def order_invoice(request, order_id):
    """The signed-in shopper's tax invoice for one of their orders, as a
    printable page (Print / Save as PDF). Only the shopper whose email is on the
    order may open it; anyone else gets a 404, so ids cannot be probed."""
    site = _site_or_404(request)
    from django.core.exceptions import ValidationError

    from commerce import order_invoice as inv
    from commerce.models import Order

    from .customer_accounts import current_customer_email

    email = current_customer_email(request)
    if not email:
        return HttpResponseRedirect(reverse("public_user_account") + "?next=" + request.path)
    from .account_profile import account_orders

    try:
        order = account_orders(site, email).filter(pk=order_id).first()
    except (ValidationError, ValueError):
        order = None
    if order is None or not inv.can_invoice(order):
        raise Http404("No such invoice.")

    ctx = {
        "website": site,
        "invoice": inv.build(order, site),
        "website_base": reverse("public_website"),
    }
    response = render(request, "storefront/order_invoice.html", ctx)
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Cache-Control"] = "private, no-store"
    return response


def user_orders(request):
    # Orders live on the account page; keep this legacy route pointing there.
    return HttpResponseRedirect(reverse("public_user_account"))


def _wishlist_json(request, site):
    from commerce.models import WishlistItem
    from .customer_accounts import current_customer_email

    email = current_customer_email(request)
    count = (
        WishlistItem.objects.for_site(site).filter(customer_email=email).count()
        if email else 0
    )
    return {"count": count, "signed_in": bool(email)}


@csrf_exempt
def wishlist_add(request, product_id):
    # CSRF-exempt like the storefront's other shopper actions: this runs on the
    # isolated tenant / custom-domain surface (a separate public-suffix domain,
    # so it cannot touch a dashboard session), the caller must already be a
    # signed-in customer, and adding a product to one's own wishlist carries no
    # security or financial risk. Requiring a CSRF token here 403'd every
    # wishlist button, because the __Host-prefixed CSRF cookie is not reliably
    # present across the many tenant/custom hosts the storefront is served on.
    site = _site_or_404(request)
    from commerce.models import WishlistItem
    from .customer_accounts import current_customer_email

    email = current_customer_email(request)
    if not email:
        if _is_ajax(request):
            # Keys the storefront JS reads: ``login_required`` triggers the
            # sign-in redirect; ``success`` gates the success branch.
            return JsonResponse(
                {
                    "ok": False,
                    "success": False,
                    "login_required": True,
                    "signed_in": False,
                    "redirect": reverse("public_user_account"),
                },
                status=401,
            )
        return HttpResponseRedirect(reverse("public_user_account"))

    added = False
    product = _active_products(request, site).filter(pk=product_id).first()
    if product is not None:
        exists = (
            WishlistItem.objects.for_site(site)
            .filter(product=product, customer_email=email)
            .exists()
        )
        if not exists:
            WishlistItem.objects.create(site=site, product=product, customer_email=email)
            added = True
    if _is_ajax(request):
        return JsonResponse({"ok": True, "success": True, "added": added, **_wishlist_json(request, site)})
    return HttpResponseRedirect(reverse("public_user_account"))


@csrf_exempt
def wishlist_remove(request, product_id):
    # CSRF-exempt for the same reasons as wishlist_add above.
    site = _site_or_404(request)
    from commerce.models import WishlistItem
    from .customer_accounts import current_customer_email

    email = current_customer_email(request)
    if email:
        WishlistItem.objects.for_site(site).filter(
            customer_email=email, product_id=product_id
        ).delete()
    if _is_ajax(request):
        return JsonResponse({"ok": True, "success": True, "removed": True, **_wishlist_json(request, site)})
    return HttpResponseRedirect(reverse("public_user_account"))


def order_confirmation(request, order_id):
    site = _site_or_404(request)
    from commerce.models import Order

    # all_objects: the gateway's return URL points here while the order is
    # still hidden awaiting its payment confirmation.
    order = Order.all_objects.filter(site=site, pk=order_id).first()
    if order is None:
        raise Http404("No such order.")

    # Cashfree's return_url lands here when its checkout redirects instead of
    # resolving the in-page modal (UPI apps, some bank pages). Confirm a still
    # pending online payment authoritatively (fetch, never the query string),
    # so the shopper sees the right state without waiting for the webhook.
    if order.status in (Order.Status.AWAITING_PAYMENT, Order.Status.CREATED):
        from commerce.models import Payment
        from commerce.payments import confirm_by_fetch, gateway_config

        pending = (
            Payment.objects.for_site(site)
            .filter(order=order, status=Payment.Status.PROCESSING)
            .exclude(provider_order_id="")
            .first()
        )
        cfg = gateway_config(site) if pending is not None else None
        if pending is not None and cfg is not None:
            try:
                if confirm_by_fetch(pending, cfg):
                    _cart(request, site).clear()
                    order.refresh_from_db()
            except Exception:
                logger.warning("return-url confirm failed order=%s", order.pk, exc_info=True)

    # Paid on the owner's domain (UGC store, storefront.hosted_pay): this
    # domain's cart was never cleared by the payment, so clear it now.
    from .hosted_pay import SESSION_KEY as _HOSTED_KEY

    if order.status != Order.Status.AWAITING_PAYMENT and request.session.get(_HOSTED_KEY) == str(order.pk):
        _cart(request, site).clear()
        request.session.pop(_HOSTED_KEY, None)

    if order.status == Order.Status.AWAITING_PAYMENT:
        # Payment failed, was cancelled or hasn't completed: there is no order.
        # Back to checkout  the cart is untouched, so they can simply retry.
        # (A payment still settling at the bank is placed by the webhook.)
        return HttpResponseRedirect(reverse("public_checkout") + "?payment=incomplete")

    order_items = list(order.items.all())
    _attach_item_thumbs(site, order_items)
    ctx = build_context(
        request, site, page_title=f"Order #{order.display_number}",
        order=order, order_items=order_items,
        confirm_payment=_confirmation_payment(order),
        confirm_steps=_confirmation_steps(order),
        confirm_first_name=((order.customer_name or "").strip().split() or [""])[0],
    )
    return render(request, _flow_template(request, site, "order_confirmation.html"), ctx)


def _attach_item_thumbs(site, items) -> None:
    """Give each order line ``thumb`` (its product's first image, or None) for
    the confirmation page. Best-effort: a missing product just has no image."""
    for it in items:
        it.thumb = None
    try:
        from commerce.models import ProductImage

        pids = {it.product_id for it in items if it.product_id}
        if not pids:
            return
        first = {}
        for img in (
            ProductImage.objects.for_site(site)
            .filter(product_id__in=pids)
            .order_by("product_id", "order", "id")
        ):
            first.setdefault(img.product_id, img)
        for it in items:
            img = first.get(it.product_id)
            it.thumb = img.image if img is not None and img.image else None
    except Exception:
        logger.warning("confirmation thumbnails failed", exc_info=True)


def _confirmation_payment(order) -> dict:
    """How the shopper paid / will pay, in plain words, for the confirmation."""
    method = (order.payment_method or "").lower()
    if order.is_partial_payment or method == "partial":
        return {
            "label": "Part paid online, rest on delivery",
            "paid": order.partial_paid_amount,
            "due": order.partial_cod_amount,
        }
    if method == "cod":
        return {"label": "Cash on Delivery", "paid": None, "due": order.total}
    if method == "store_credit":
        return {"label": "Paid with store credit", "paid": order.total, "due": None}
    if method == "replacement":
        return {"label": "Replacement order", "paid": None, "due": None}
    if method:
        return {"label": f"Paid online ({method.capitalize()})", "paid": order.total, "due": None}
    return {"label": "", "paid": None, "due": None}


def _confirmation_steps(order) -> list[dict]:
    """Ordered → Confirmed → Shipped → Delivered, with how far this order got."""
    from commerce.models import Order

    S = Order.Status
    reached = {
        S.CREATED: 1, S.CONFIRMED: 2, S.PAID: 2, S.SHIPPED: 3, S.DELIVERED: 4,
    }.get(order.status, 1)
    labels = [
        ("Ordered", order.created_at),
        ("Confirmed", order.confirmed_at),
        ("Shipped", order.shipped_at),
        ("Delivered", order.delivered_at),
    ]
    return [
        {"label": label, "done": i <= reached, "current": i == reached, "at": at}
        for i, (label, at) in enumerate(labels, start=1)
    ]


def _can_review_d2c(site, product, email) -> bool:
    """A D2C shopper may review a product they've had delivered  or one they've
    already reviewed (so they can update it)."""
    from commerce.models import Order, OrderItem, Review

    if not email:
        return False
    delivered = (
        OrderItem.objects.for_site(site)
        .filter(
            product=product,
            order__customer_email__iexact=email,
            order__status=Order.Status.DELIVERED,
        )
        .exists()
    )
    if delivered:
        return True
    return (
        Review.objects.for_site(site)
        .filter(product=product, email__iexact=email, is_deleted=False)
        .exists()
    )


def _review_back(product):
    """Redirect to the product page's reviews section (or home if unknown)."""
    if product is not None:
        return HttpResponseRedirect(
            reverse("public_product_detail", args=[product.slug]) + "#reviews-section"
        )
    return HttpResponseRedirect(reverse("public_website"))


@require_POST
def submit_product_review(request, product_id):
    """Create or update the shopper's review, then hold it for owner approval.

    D2C requires a signed-in shopper with a delivered order for the product;
    B2B accepts a typed email. One review per (product, email)  resubmitting
    updates it and sends it back to moderation.
    """
    site = _site_or_404(request)
    product = _active_products(request, site).filter(pk=product_id).first()
    if product is None:
        raise Http404("No such product.")

    from .customer_accounts import current_customer_email
    from commerce.models import Review

    signed = (current_customer_email(request) or "").strip().lower()
    if site.is_d2c:
        if not signed:
            return _review_back(product)  # template gates this; guard anyway
        email = signed
        if not _can_review_d2c(site, product, email):
            return _review_back(product)
    else:
        email = (request.POST.get("email") or "").strip().lower()
        if not email:
            return _review_back(product)

    # Throttle submissions (declared in settings.RATE_LIMITS).
    if not ratelimit.consume("review.submit", email or ratelimit.client_identity(request)).allowed:
        return _review_back(product)

    try:
        rating = int(request.POST.get("rating") or 0)
    except (TypeError, ValueError):
        rating = 0
    rating = min(5, max(1, rating))
    comment = (request.POST.get("comment") or "").strip()
    if not comment:
        return _review_back(product)

    review, _created = Review.objects.for_site(site).get_or_create(
        site=site, product=product, email=email, defaults={"rating": rating}
    )
    review.name = (request.POST.get("name") or "").strip() or review.name
    review.title = (request.POST.get("title") or "").strip()
    review.comment = comment
    review.rating = rating
    # Every edit goes back to the owner for approval; clear any prior flag.
    review.approved = False
    review.reported = False
    review.is_deleted = False
    photo = request.FILES.get("photo")
    if photo and str(getattr(photo, "content_type", "")).lower().startswith("image/"):
        review.photo = photo
    review.save()
    return _review_back(product)


@require_POST
def review_delete(request, product_id):
    """Soft-delete the signed-in shopper's own review for this product."""
    site = _site_or_404(request)
    product = _active_products(request, site).filter(pk=product_id).first()

    from .customer_accounts import current_customer_email
    from commerce.models import Review

    email = (current_customer_email(request) or "").strip().lower()
    if email and product is not None:
        for r in Review.objects.for_site(site).filter(
            product=product, email__iexact=email, is_deleted=False
        ):
            r.soft_delete()
    return _review_back(product)


@require_POST
def subscribe_newsletter(request):
    """Record a newsletter subscription from the storefront footer form.

    Idempotent per (site, email). Always responds with a friendly success so the
    form never leaks whether an email was already subscribed. Best-effort  a
    storage hiccup still returns ok so the shopper isn't shown an error.
    """
    site = _site_or_404(request)
    from . import newsletter_popup

    # The popup (``source=popup``) tags its signups "Subscribed from newsletter"
    # and, when the owner attached an offer, gets the code back to reveal.
    from_popup = (request.POST.get("source") or "").strip().lower() == "popup"
    source = newsletter_popup.SOURCE if from_popup else "footer"

    def _ok(message="Thanks for subscribing!"):
        body = {"ok": True, "status": "success", "message": message}
        if from_popup:
            coupon = newsletter_popup.selected_coupon(site)
            if coupon is not None:
                body["code"] = coupon.code
        return JsonResponse(body)

    if not ratelimit.consume("newsletter.subscribe", ratelimit.client_identity(request)).allowed:
        return _ok()

    email = (request.POST.get("email") or "").strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        return JsonResponse(
            {"ok": False, "status": "error", "message": "Please enter a valid email."},
            status=400,
        )
    try:
        # ``site.newsletter_subscribers`` is the pre-scoped related manager, so
        # its ``get_or_create`` both satisfies tenant-scoping and sets ``site``
        # automatically. Using the bare ``NewsletterSubscriber.objects`` manager
        # here raises ``UnscopedQueryError`` on the internal lookup  which the
        # broad ``except`` below would swallow, silently dropping every signup.
        _sub, created = site.newsletter_subscribers.get_or_create(
            email=email, defaults={"source": source}
        )
        if not created and not _sub.is_active:
            # Someone who unsubscribed (or was removed) and signs up again.
            _sub.is_active = True
            _sub.source = source
            _sub.save(update_fields=["is_active", "source", "updated_at"])
        if created:
            from commerce import activity

            activity.record(
                "store.subscriber.subscribed",
                request=request,
                site=site,
                target=_sub,
                target_kind="newsletter_subscriber",
                label=email,
                actor_kind="shopper",
            )
    except Exception:
        pass
    return _ok()


def account_logout(request):
    from .customer_accounts import sign_out

    sign_out(request)
    return HttpResponseRedirect(reverse("public_website"))


def product_whatsapp(request, product_id):
    """Record the intent and bounce to the store's WhatsApp, if configured."""
    site = _site_or_404(request)
    product = _active_products(request, site).filter(pk=product_id).first()
    if product is not None:
        Product.objects.for_site(site).filter(pk=product.pk).update(
            whatsapp_clicks=(product.whatsapp_clicks or 0) + 1
        )
    phone = (site.support_phone or "").lstrip("+").replace(" ", "")
    if phone:
        text = "Hi, I'm interested in your products."
        return HttpResponseRedirect(f"https://wa.me/{phone}?text={text}")
    return HttpResponseRedirect(reverse("public_website"))


# -- owner-preview no-ops (guarded in templates; safe if ever reached) -----


def owner_noop(request, site_id=None):
    return HttpResponseRedirect(reverse("public_website"))
