"""Public storefront views.

Every view is served for one resolved store (``request.site``, set by
:class:`storefront.middleware.CurrentSiteMiddleware`). Content pages read the
CMS blob and the live catalogue; the transactional surface (cart, checkout,
customer accounts, wishlist, reviews) is deliberately stubbed here — those land
with the commerce phase — but each route exists so the ported templates' links
resolve and the storefront stays fully browsable today.
"""

from __future__ import annotations

from django.http import (
    Http404,
    HttpResponse,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from commerce.models import Category, Enquiry, Product
from core import ratelimit

from .context import (
    BlogProxy,
    ProductProxy,
    build_context,
    home_extra,
    proxy_blogs,
    proxy_products,
)

# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def _site_or_404(request):
    site = getattr(request, "site", None)
    if site is None:
        raise Http404("No such store.")
    return site


def _template(site, name: str) -> str:
    return f"storefront/{site.template_dir}/{name}"


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


def _active_products(site):
    return (
        Product.objects.for_site(site)
        .filter(is_active=True, is_deleted=False)
        .order_by("-created_at")
    )


# --------------------------------------------------------------------------
# Content pages
# --------------------------------------------------------------------------


def home(request):
    site = _site_or_404(request)
    return _render(request, "home.html", page_title="Home", **home_extra(request, site))


def all_products(request):
    site = _site_or_404(request)
    qs = _active_products(site)

    selected = [s for s in request.GET.getlist("category") if s]
    if selected:
        qs = qs.filter(category__slug__in=selected).distinct()

    query = (request.GET.get("q") or "").strip()
    if query:
        qs = qs.filter(title__icontains=query)

    return _render(
        request,
        "all_products.html",
        page_title="All products",
        products=proxy_products(qs, request, site),
        selected_category_ids=selected,
        search_query=query,
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
        raise Http404("No such category.")
    qs = _active_products(site).filter(category=category)
    return _render(
        request,
        "category_detail.html",
        page_title=category.name,
        category=category,
        products=proxy_products(qs, request, site),
    )


def product_detail(request, product_slug):
    site = _site_or_404(request)
    product = (
        _active_products(site).filter(slug=product_slug).first()
    )
    if product is None:
        raise Http404("No such product.")

    related = _active_products(site).exclude(pk=product.pk)
    if product.category_id:
        related = related.filter(category_id=product.category_id)
    related = related[:8]

    return _render(
        request,
        "product_detail.html",
        page_title=product.title,
        product=ProductProxy(product, request, site),
        related_products=proxy_products(related, request, site),
    )


def about(request):
    return _render(request, "about-us.html", page_title="About us")


def certificates(request):
    return _render(request, "certificates.html", page_title="Certificates")


def blogs(request):
    site = _site_or_404(request)
    posts = (site.content or {}).get("blog_posts") or []
    return _render(request, "blogs.html", page_title="Blog", blogs=proxy_blogs(posts))


def blog_detail(request, blog_slug):
    site = _site_or_404(request)
    posts = (site.content or {}).get("blog_posts") or []
    match = next((p for p in posts if isinstance(p, dict) and p.get("slug") == blog_slug), None)
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
    """Render a core page, subpage or child page from the content tree.

    Core pages nest three levels deep (page → subpage → child), mirroring the
    reference. The breadcrumb and the list of the node's own children are built
    from the tree so a visitor can navigate down and back up.
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

    node = page
    crumbs = [{"title": page.get("title") or "Page", "url": reverse("public_core_page", kwargs={"page_slug": page_slug})}]
    children = []

    if sub_slug:
        sub = _find_page(page.get("subpages"), sub_slug)
        if sub is None:
            raise Http404("No such subpage.")
        node = sub
        crumbs.append({"title": sub.get("title") or "Subpage", "url": reverse("public_core_subpage", kwargs={"page_slug": page_slug, "sub_slug": sub_slug})})
        if child_slug:
            child = _find_page(sub.get("children"), child_slug)
            if child is None:
                raise Http404("No such child page.")
            node = child
            crumbs.append({"title": child.get("title") or "Page", "url": reverse("public_core_child", kwargs={"page_slug": page_slug, "sub_slug": sub_slug, "child_slug": child_slug})})
        else:
            children = [
                {"title": c.get("title"), "url": reverse("public_core_child", kwargs={"page_slug": page_slug, "sub_slug": sub_slug, "child_slug": c.get("slug")})}
                for c in (sub.get("children") or []) if c.get("slug")
            ]
    else:
        children = [
            {"title": s.get("title"), "url": reverse("public_core_subpage", kwargs={"page_slug": page_slug, "sub_slug": s.get("slug")})}
            for s in (page.get("subpages") or []) if s.get("slug")
        ]

    ctx = build_context(
        request, site, page_title=node.get("title") or "Page",
        core_page=node, cp_crumbs=crumbs, cp_children=children,
    )
    return render(request, "storefront/core_page_simple.html", ctx)


# -- policy pages ----------------------------------------------------------


def privacy_policy(request):
    return _render(request, "privacy-policy.html", page_title="Privacy policy")


def terms_conditions(request):
    return _render(request, "terms-conditions.html", page_title="Terms & conditions")


def refund_policy(request):
    return _render(request, "refund-policy.html", page_title="Refund policy")


def shipping_policy(request):
    return _render(request, "shipping-policy.html", page_title="Shipping policy")


def custom_policy(request):
    return _render(request, "custom-policy.html", page_title="Policy")


# -- enquiry / contact -----------------------------------------------------


@require_http_methods(["GET", "POST"])
def enquiry(request):
    site = _site_or_404(request)

    if request.method == "POST":
        identity = ratelimit.client_identity(request)
        if not ratelimit.consume("enquiry.submit", identity).allowed:
            return _render(
                request,
                "enquiry.html",
                page_title="Contact",
                enquiry_error="Too many enquiries just now. Please try again shortly.",
            )

        name = (request.POST.get("name") or "").strip()[:100]
        email = (request.POST.get("email") or "").strip()[:254]
        mobile = (request.POST.get("mobile_number") or request.POST.get("phone") or "").strip()[:20]
        subject = (request.POST.get("subject") or "").strip()[:200]
        message = (request.POST.get("message") or "").strip()

        if not (name or email or mobile) or not message:
            return _render(
                request,
                "enquiry.html",
                page_title="Contact",
                enquiry_error="Please add your contact details and a message.",
            )

        obj = Enquiry.objects.create(
            site=site,
            name=name,
            email=email,
            mobile_number=mobile,
            subject=subject,
            message=message,
        )

        product_id = request.POST.get("product_id")
        if product_id:
            product = _active_products(site).filter(pk=product_id).first()
            if product is not None:
                obj.products.add(product)

        return _render(
            request,
            "enquiry.html",
            page_title="Contact",
            enquiry_success=True,
        )

    return _render(request, "enquiry.html", page_title="Contact")


# --------------------------------------------------------------------------
# Lightweight JSON APIs the storefront JS calls
# --------------------------------------------------------------------------


def api_product_suggest(request):
    site = _site_or_404(request)
    query = (request.GET.get("q") or "").strip()
    results = []
    if query:
        qs = _active_products(site).filter(title__icontains=query)[:8]
        for p in qs:
            results.append(
                {
                    "title": p.title,
                    "slug": p.slug,
                    "url": reverse("public_product_detail", kwargs={"product_slug": p.slug}),
                    "price": str(p.price),
                }
            )
    return JsonResponse({"results": results})


def api_product_variants(request, product_id):
    site = _site_or_404(request)
    product = _active_products(site).filter(pk=product_id).first()
    if product is None:
        raise Http404("No such product.")
    return JsonResponse(
        {
            "variants": product.variants or [],
            "combinations": product.variant_combinations or [],
        }
    )


def pincode_serviceability(request):
    # Real serviceability lands with shipping; report unknown gracefully.
    return JsonResponse({"serviceable": None, "message": ""})


# --------------------------------------------------------------------------
# Transactional stubs (cart, checkout, account, wishlist, reviews)
# --------------------------------------------------------------------------


def _coming_soon(request, feature: str) -> HttpResponse:
    site = _site_or_404(request)
    ctx = build_context(request, site, page_title=feature, feature_name=feature)
    return render(request, "storefront/coming_soon.html", ctx, status=200)


def cart_add(request, product_id):
    return _coming_soon(request, "Cart")


def checkout(request):
    return _coming_soon(request, "Checkout")


def wishlist_add(request, product_id):
    return _coming_soon(request, "Wishlist")


def wishlist_remove(request, product_id):
    return _coming_soon(request, "Wishlist")


def user_account(request):
    return _coming_soon(request, "Your account")


def user_orders(request):
    return _coming_soon(request, "Your orders")


def order_confirmation(request, order_id):
    return _coming_soon(request, "Order confirmation")


def submit_product_review(request, product_id):
    return _coming_soon(request, "Reviews")


def review_delete(request, product_id):
    return _coming_soon(request, "Reviews")


@require_POST
def subscribe_newsletter(request):
    return JsonResponse({"ok": True, "message": "Thanks for subscribing!"})


def account_logout(request):
    return HttpResponseRedirect(reverse("public_website"))


def product_whatsapp(request, product_id):
    """Record the intent and bounce to the store's WhatsApp, if configured."""
    site = _site_or_404(request)
    product = _active_products(site).filter(pk=product_id).first()
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
