"""The dedicated collaboration (UGC referral) storefront.

A collaborator sells the parent store's catalogue on their own branded subdomain,
``<subdomain>.myugcstore.com``, in a storefront that is a faithful port of the
1matrix referral storefront (``storefront1.html``): a phone-card page with the
creator's cover + profile, an "about my store" blurb, autoplaying video reels, a
searchable/filterable product grid and a slide-in cart. It is served on the
STOREFRONT surface, whose CSP already admits the ported design's Tailwind CDN +
inline scripts.

Data comes from the collaborator's own customisations (set in their portal):
cover + reels (:class:`commerce.CollaborationMedia`), profile picture / title /
about (on :class:`commerce.Collaboration`), and per-product image overrides
(:class:`commerce.CollaborationProductImage`). The catalogue is scoped to the
collaborator (:mod:`storefront.scope`); product / cart / checkout reuse the
existing storefront routes, which already attribute orders and price commission.
"""

from __future__ import annotations

import json
from urllib.parse import quote

from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Avg, Q
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse

from commerce.models import (
    Category,
    CollaborationMedia,
    CollaborationProductImage,
    Product,
    SlugRedirect,
    display_ordered_categories,
    renamed_slug,
)
from commerce.models.marketing import COLLAB_IMAGE_SLOTS

from .context import BlogProxy, ProductProxy
from .scope import scope_categories, scope_products
from .views import renamed_redirect

PER_PAGE = 10


def _site_title(collab) -> str:
    for value in (
        getattr(collab, "storefront_title", ""),
        getattr(collab, "name", ""),
    ):
        value = (value or "").strip()
        if value:
            return value
    base = (getattr(collab, "subdomain", "") or "").replace("-", " ").replace("_", " ").strip()
    return base.title() or (getattr(collab, "handle", "") or "Store")


def _scoped_products(request, site):
    return scope_products(
        Product.objects.for_site(site)
        .filter(is_active=True, is_deleted=False)
        .order_by("-created_at"),
        request,
    )


def _override_map(collab, products):
    """{product_id: {slot: CollaborationProductImage}} for these products."""
    out: dict = {}
    qs = CollaborationProductImage.objects.for_site(collab.site).filter(
        collaboration=collab, product__in=[p.pk for p in products]
    )
    for ov in qs:
        out.setdefault(ov.product_id, {})[ov.slot] = ov
    return out


def _primary_image(product, overrides) -> str:
    """The card image: first visible override (by slot), else first product photo."""
    for slot in COLLAB_IMAGE_SLOTS:
        ov = overrides.get(slot) if overrides else None
        if ov is not None:
            if not ov.is_visible:
                continue
            if ov.image:
                return ov.image.url
    imgs = list(product.images.all()[:1])
    if imgs and imgs[0].image:
        return imgs[0].image.url
    from .demo_media import demo_image

    key = str(getattr(product, "slug", "") or product.id or "")
    return demo_image("product", sum(ord(c) for c in key))


def _card(product, overrides) -> dict:
    return {
        "id": str(product.id),
        "slug": product.slug,
        "title": product.title,
        "price_display": f"₹{product.price:.2f}",
        "image": _primary_image(product, overrides.get(product.pk, {})),
        "url": reverse("public_product_detail", kwargs={"product_slug": product.slug}),
    }


def _category_tabs(request, site, products_qs, active_category):
    available = set(
        products_qs.filter(category__isnull=False).values_list("category__slug", flat=True)
    )
    available.update(
        products_qs.filter(categories__isnull=False).values_list("categories__slug", flat=True)
    )
    scoped = display_ordered_categories(
        scope_categories(
            Category.objects.for_site(site).filter(is_active=True, is_deleted=False),
            request,
        )
    )
    tabs = [{"name": "All", "slug": "", "active": active_category == ""}]
    for category in scoped:
        if category.slug in available:
            tabs.append({
                "name": category.name,
                "slug": category.slug,
                "active": category.slug == active_category,
            })
        if len(tabs) >= 7:
            break
    return tabs


def referral_storefront(request, site):
    """Render the collaboration storefront for ``request.collaboration``."""
    collab = getattr(request, "collaboration", None)
    if collab is None:
        return None

    products_qs = _scoped_products(request, site)

    active_category = (request.GET.get("category") or "").strip()
    query = (request.GET.get("q") or "").strip()
    page_param = (request.GET.get("page") or "1").strip()

    if active_category:
        # A ``?category=`` link shared before the category was renamed still
        # filters to it (only consulted when no live category has the slug).
        if not Category.objects.for_site(site).filter(slug=active_category).exists():
            active_category = (
                renamed_slug(site, SlugRedirect.Kind.CATEGORY, active_category) or active_category
            )
        products_qs = products_qs.filter(
            Q(category__slug=active_category) | Q(categories__slug=active_category)
        )
    if query:
        products_qs = products_qs.filter(
            Q(title__icontains=query) | Q(description__icontains=query)
        )
    products_list = list(products_qs.distinct())

    overrides = _override_map(collab, products_list)
    cards = [_card(p, overrides) for p in products_list]

    paginator = Paginator(cards, PER_PAGE)
    try:
        page_obj = paginator.page(page_param)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({
            "success": True,
            "products": list(page_obj.object_list),
            "page": page_obj.number,
            "num_pages": paginator.num_pages,
            "has_previous": page_obj.has_previous(),
            "has_next": page_obj.has_next(),
            "previous_page": page_obj.previous_page_number() if page_obj.has_previous() else None,
            "next_page": page_obj.next_page_number() if page_obj.has_next() else None,
            "active_category": active_category,
            "query": query,
        })

    category_tabs = _category_tabs(request, site, _scoped_products(request, site), active_category)

    # Creator media: the single cover + the video reels the collaborator uploaded.
    cover = (
        CollaborationMedia.objects.for_site(site)
        .filter(collaboration=collab, cover=True)
        .order_by("-updated_at")
        .first()
    )
    cover_image = cover.file.url if (cover and cover.file) else None
    video_media = list(
        CollaborationMedia.objects.for_site(site)
        .filter(collaboration=collab, media_type=CollaborationMedia.Kind.VIDEO, show_on_storefront=True)
        .order_by("-created_at")
    )
    profile_image = collab.profile_picture.url if collab.profile_picture else None

    context = {
        "site": site,
        "website": site,
        "global_content": site.content if isinstance(getattr(site, "content", None), dict) else {},
        "collaboration": collab,
        "referral": collab,
        "site_title": _site_title(collab),
        "products_page": page_obj.object_list,
        "page_obj": page_obj,
        "paginator": paginator,
        "category_tabs": category_tabs,
        "active_category": active_category,
        "query": query,
        "cover_image": cover_image,
        "profile_image": profile_image,
        "video_media": video_media,
    }
    return render(request, "storefront/referral/storefront.html", context)


# ---------------------------------------------------------------------------
# Page views (served by onewooz.urls.referral_storefront). Each renders inside
# the collaborator's card shell  never the owner's theme.
# ---------------------------------------------------------------------------


def _site(request):
    site = getattr(request, "site", None)
    if site is None:
        raise Http404("No such store.")
    return site


def _shell_ctx(request, site, page_title=""):
    collab = getattr(request, "collaboration", None)
    return {
        "site": site,
        "website": site,
        "global_content": site.content if isinstance(getattr(site, "content", None), dict) else {},
        "collaboration": collab,
        "referral": collab,
        "site_title": _site_title(collab) if collab else site.name,
        "page_title": page_title,
    }


def home(request):
    """Referral storefront landing (the phone-card storefront)."""
    site = _site(request)
    rendered = referral_storefront(request, site)
    if rendered is not None:
        return rendered
    raise Http404("Storefront unavailable.")


def _grid_cards(request, site, qs):
    products = list(qs)
    overrides = _override_map(getattr(request, "collaboration", None), products) if getattr(request, "collaboration", None) else {}
    return [_card(p, overrides) for p in products]


def shop(request):
    site = _site(request)
    qs = _scoped_products(request, site)
    query = (request.GET.get("q") or "").strip()
    if query:
        qs = qs.filter(Q(title__icontains=query) | Q(description__icontains=query))
    ctx = _shell_ctx(request, site, "Shop")
    ctx.update({"products": _grid_cards(request, site, qs.distinct()), "query": query})
    return render(request, "storefront/referral/pages/grid.html", ctx)


def categories(request):
    site = _site(request)
    scoped = display_ordered_categories(
        scope_categories(
            Category.objects.for_site(site).filter(is_active=True, is_deleted=False),
            request,
        )
    )
    cats = [
        {
            "name": c.name,
            "url": reverse("public_category_detail", kwargs={"category_slug": c.slug}),
            "image": (c.image.url if getattr(c, "image", None) else ""),
        }
        for c in scoped
    ]
    ctx = _shell_ctx(request, site, "Categories")
    ctx.update({"categories": cats})
    return render(request, "storefront/referral/pages/categories.html", ctx)


def category_detail(request, category_slug):
    site = _site(request)
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
    qs = _scoped_products(request, site).filter(
        Q(category=category) | Q(categories=category)
    ).distinct()
    ctx = _shell_ctx(request, site, category.name)
    ctx.update({"products": _grid_cards(request, site, qs), "query": ""})
    return render(request, "storefront/referral/pages/grid.html", ctx)


def _gallery(product, overrides):
    """Override images (visible, slot order) then base product photos; demo last."""
    urls = []
    for slot in COLLAB_IMAGE_SLOTS:
        ov = overrides.get(slot)
        if ov is not None and ov.is_visible and ov.image:
            urls.append(ov.image.url)
    for img in product.images.all():
        if img.image:
            urls.append(img.image.url)
    if not urls:
        from .demo_media import demo_image

        key = str(getattr(product, "slug", "") or product.id or "")
        urls.append(demo_image("product", sum(ord(c) for c in key)))
    return urls


def _variant_attrs(product):
    out = []
    raw = getattr(product, "variants", None)
    if isinstance(raw, list):
        for v in raw:
            if not isinstance(v, dict):
                continue
            name = v.get("name") or v.get("type")
            values = v.get("values") or v.get("options") or []
            if name and isinstance(values, list) and values:
                # Option values are stored either as plain strings or as
                # {"value": ...} / {"name": ...} dicts (the dashboard editor).
                clean = []
                for x in values:
                    if isinstance(x, dict):
                        x = x.get("value") or x.get("name") or ""
                    x = str(x).strip()
                    if x:
                        clean.append(x)
                if clean:
                    out.append({"name": str(name), "values": clean})
    return out


def _variant_stock(product) -> list:
    """Each stored variation's attributes, stock and default flag, for the
    product page's stock gate (a sold-out variation can't be added)."""
    from commerce.cart import attrs_of, combos_of, variant_key_of

    from commerce.variant_profiles import combo_units, low_stock_level

    rows = []
    for combo in combos_of(product):
        # 0 for a variation the owner switched off (not for sale).
        inventory = combo_units(combo)
        rows.append(
            {
                "key": variant_key_of(combo),
                "attrs": attrs_of(combo),
                "inventory": inventory,
                "default": combo.get("default_selected") in (True, "true", 1, "1"),
                # Its own "Only N left" level (the product's unless set for it).
                "low": low_stock_level(product, combo),
                # The variation's own price / MRP (blank = the product's), the
                # same values the cart charges (commerce.cart._variant_price).
                "price": _money_str(combo.get("price")),
                "mrp": _money_str(combo.get("mrp")),
                # The variation's own photos (Variations step, up to 5): the
                # product page shows these in place of the gallery when picked.
                "images": [
                    u.strip()
                    for u in (combo.get("images") or [])
                    if isinstance(u, str) and u.strip()
                ][:5],
            }
        )
    return rows


def _money_str(value) -> str:
    """``"499.00"`` for a usable amount, ``""`` for blank / invalid."""
    from decimal import Decimal, InvalidOperation

    if value in (None, ""):
        return ""
    try:
        return f"{Decimal(str(value)):.2f}"
    except (InvalidOperation, ValueError):
        return ""


def _return_to(request):
    """Rebuild the storefront link (preserving search/category/page) for Back."""
    params = []
    q = (request.GET.get("q") or "").strip()
    category = (request.GET.get("category") or "").strip()
    page = (request.GET.get("page") or "").strip()
    if q:
        params.append(f"q={quote(q)}")
    if category:
        params.append(f"category={category}")
    if page:
        params.append(f"page={page}")
    base = reverse("public_website")
    return f"{base}?{'&'.join(params)}" if params else base


def product_detail(request, product_slug):
    """Exact port of 1matrix's product_detail_referral.html (phone-card PDP)."""
    site = _site(request)
    product_obj = _scoped_products(request, site).filter(slug=product_slug).first()
    if product_obj is None:
        moved = renamed_redirect(request, site, SlugRedirect.Kind.PRODUCT, product_slug)
        if moved is not None:
            return moved
        raise Http404("No such product.")

    collab = getattr(request, "collaboration", None)
    overrides = _override_map(collab, [product_obj]).get(product_obj.pk, {}) if collab else {}
    images = _gallery(product_obj, overrides)

    # The product's promo video (same normaliser the owner PDP uses): a YouTube
    # link becomes an embed, a hosted file plays inline. None → no video button.
    from storefront.views import _build_product_video

    # Ratings
    from commerce.models import Review, WishlistItem

    reviews_qs = (
        Review.objects.for_site(site)
        .filter(product=product_obj, approved=True, is_deleted=False)
    )
    rating_count = reviews_qs.count()
    rating_value = float(reviews_qs.aggregate(a=Avg("rating"))["a"] or 0.0)
    rating_value = max(0.0, min(5.0, rating_value))
    rating_percent = int(round((rating_value / 5.0) * 100))

    # Customer login + wishlist state
    from .customer_accounts import current_customer_email

    customer_email = (current_customer_email(request) or "").strip().lower()
    customer_logged_in = bool(customer_email)
    is_wishlisted = False
    if customer_logged_in:
        try:
            is_wishlisted = (
                WishlistItem.objects.for_site(site)
                .filter(product=product_obj, customer_email__iexact=customer_email)
                .exists()
            )
        except Exception:
            is_wishlisted = False

    from .safe import wrap

    from commerce.variant_profiles import sellable_stock, storefront_profiles
    from storefront.views import default_product_faqs, product_faqs_for

    product_proxy = ProductProxy(product_obj, request, site)

    context = {
        "website": site,
        "referral": collab,
        "site_title": _site_title(collab) if collab else site.name,
        "product": product_proxy,
        # Variations the owner gave their own details: swapped into the title
        # and the "View details" sheet (tabs + FAQs) when the shopper picks one.
        "variant_profiles": storefront_profiles(
            product_obj, product_proxy, default_faqs=default_product_faqs(site)
        ),
        # The product's FAQs (its own, else the store's default set), as on
        # the store's own product page.
        "product_faqs": product_faqs_for(product_obj, site),
        # Wrapped like the owner PDP so the Item Details / Others tabs can read
        # nested keys (extras, accessories, includes…) without raising on gaps.
        "d2c_details": wrap(product_obj.d2c_details or {}),
        "product_id": str(product_obj.id),
        "product_title": product_obj.title,
        "product_price_display": f"₹{product_obj.price:.2f}",
        # MRP struck through beside the price when it is higher (as on the
        # store's own product page); the variation picker updates both.
        "product_price": _money_str(product_obj.price),
        "product_mrp": _money_str(product_obj.mrp),
        "product_mrp_display": (
            f"₹{product_obj.mrp:.2f}"
            if product_obj.mrp and product_obj.price is not None and product_obj.mrp > product_obj.price
            else ""
        ),
        "product_hero_image": images[0] if images else "",
        "product_images": images,
        "product_video": _build_product_video(product_obj),
        "rating_value": f"{rating_value:.1f}",
        "rating_count": rating_count,
        "rating_percent": rating_percent,
        "variant_attributes": _variant_attrs(product_obj),
        # Stock gate: the product's total, and each variation's own stock.
        "product_in_stock": sellable_stock(product_obj) > 0,
        # Shown as "Only N left" at or below the product's low-stock level.
        "product_stock": sellable_stock(product_obj),
        "low_stock_threshold": product_obj.low_stock_threshold,
        "variant_stock": _variant_stock(product_obj),
        "variant_combinations_json": json.dumps([]),
        "return_to": _return_to(request),
        "active_category": (request.GET.get("category") or "").strip(),
        "query": (request.GET.get("q") or "").strip(),
        "page": (request.GET.get("page") or "").strip(),
        "customer_logged_in": customer_logged_in,
        "is_wishlisted": is_wishlisted,
    }
    return render(request, "storefront/referral/pages/product.html", context)


INDIAN_STATES = (
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
    "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
    "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Andaman and Nicobar Islands", "Chandigarh",
    "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Jammu and Kashmir",
    "Ladakh", "Lakshadweep", "Puducherry",
)


def checkout(request):
    """Referral checkout  1matrix's checkout_card UI wired to OneWooz's checkout
    backend (place-order + Razorpay/Cashfree flow), served inside the store."""
    from storefront import checkout_prefill
    from storefront import views as sfv

    site = _site(request)
    cart = sfv._cart(request, site)
    if cart.is_empty():
        return redirect_to_home()

    cfg, gateway_ready, cod_enabled = sfv._gateway_flags(site)
    partial_enabled = sfv._partial_enabled(cfg, gateway_ready, cod_enabled)
    items = cart.items()
    # Store credit comes off for the signed-in (email-verified) shopper.
    summary = sfv._with_credit(request, site, cart.summary(), items)
    partial_now, partial_rest = sfv._partial_split(cfg, summary.get("total"), partial_enabled)

    ctx = _shell_ctx(request, site, "Checkout")
    ctx.update({
        "cart_items": items,
        "totals": {
            "subtotal": summary.get("subtotal"),
            "discount": summary.get("discount"),
            "shipping": "0.00",
            "tax": "0.00",
            "credit_discount": summary.get("credit_discount"),
            "total_before_credit": summary.get("total_before_credit"),
            "total": summary.get("total"),
        },
        "credit_email": sfv._verified_email(request),
        "gateway_ready": gateway_ready,
        "cod_enabled": cod_enabled,
        "gateway_name": (cfg.gateway if cfg else ""),
        "has_payment_config": bool(gateway_ready or cod_enabled),
        "partial_enabled": partial_enabled,
        "partial_type": (cfg.partial_payment_type if cfg else "percent"),
        "partial_value": (str(cfg.partial_payment_value) if partial_enabled else "0"),
        "partial_now": partial_now,
        "partial_rest": partial_rest,
        "states": INDIAN_STATES,
        "prefill": checkout_prefill.prefill_for(request, site),
    })
    return render(request, "storefront/referral/pages/checkout.html", ctx)


def redirect_to_home():
    from django.shortcuts import redirect

    return redirect("public_website")


def _content_dict(site):
    return site.content if isinstance(getattr(site, "content", None), dict) else {}


def about(request):
    # Render the SAME About content the owner's own store shows  the template
    # reads ``website.content.*`` (editorial intro, brand/story sections, images)
    # exactly like storefront/template1/about-us.html  but laid out for the
    # phone card so it matches the owner store between nav and footer.
    site = _site(request)
    ctx = _shell_ctx(request, site, "About us")
    return render(request, "storefront/referral/pages/about.html", ctx)


def certificates(request):
    site = _site(request)
    ctx = _shell_ctx(request, site, "Certificates")
    ctx.update({"heading": "Certificates", "body_html": "", "empty_text": "No certificates to show."})
    return render(request, "storefront/referral/pages/content.html", ctx)


def account(request):
    """Shopper account, rendered inside the collaboration phone card.

    The catalogue's shared shopper records  orders, wishlist and store credits 
    are scoped by ``(site, customer_email)``, and a referral host resolves to the
    SAME owner site the collaborator sells for. So a shopper signed in with the
    same email sees the *same* orders and wishlist here and on the owner's own
    website store  this view deliberately runs the identical site+email queries
    as :func:`storefront.views.user_account` to keep that data in step.
    """
    site = _site(request)
    from django.db.models import Q
    from django.utils import timezone as _tz

    from commerce.models import CustomerCredit, Order, WishlistItem

    from .customer_accounts import current_customer_email

    from . import account_profile

    email = (current_customer_email(request) or "").strip().lower()
    orders, wishlist, customer_credits = [], [], []
    profile, saved_addresses = None, []
    status_filter = (request.GET.get("status") or "").strip().lower()
    if email:
        # Same account rules as the owner store (storefront.account_profile):
        # orders follow the account through an email change.
        profile = account_profile.find_customer(site, email)
        saved_addresses = account_profile.addresses(site, profile)
        qs = account_profile.account_orders(site, email, profile)
        valid_status = {c[0] for c in Order.owner_status_choices()}
        if status_filter in valid_status:
            qs = qs.filter(status=status_filter)
        orders = list(qs.prefetch_related("items").order_by("-created_at")[:100])
        from commerce.order_requests import annotate_for_account

        annotate_for_account(orders)

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

        customer_credits = list(
            CustomerCredit.objects.for_site(site)
            .filter(customer_email__iexact=email, is_active=True)
            .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=_tz.now().date()))
            .order_by("-created_at")
        )

    ctx = _shell_ctx(request, site, "Your account")
    ctx.update({
        "customer_email": email,
        "orders": orders,
        "wishlist": wishlist,
        "customer_credits": customer_credits,
        "status_filter": status_filter,
        "order_status_choices": Order.owner_status_choices(),
        "account_profile": profile,
        "saved_addresses": saved_addresses,
    })
    return render(request, "storefront/referral/pages/account.html", ctx)


def _published_posts(site):
    posts = _content_dict(site).get("blog_posts") or []
    return [p for p in posts if isinstance(p, dict) and (p.get("status") or "published") != "draft"]


def blogs(request):
    from django.utils.html import strip_tags
    from django.utils.text import Truncator

    site = _site(request)
    items = []
    for p in _published_posts(site):
        bp = BlogProxy(p)
        excerpt = (p.get("excerpt") or p.get("summary") or "").strip()
        if not excerpt:
            # Blog bodies live under ``description`` (TinyMCE HTML); derive a
            # short plain-text teaser so the listing isn't just titles.
            excerpt = Truncator(strip_tags(p.get("description") or "")).words(24)
        items.append({
            "title": bp.title,
            "excerpt": excerpt,
            "image": bp.image,
            "url": reverse("public_blog_detail", kwargs={"blog_slug": p.get("slug") or ""}) if p.get("slug") else "#",
        })
    ctx = _shell_ctx(request, site, "Blog")
    ctx.update({"blogs": items})
    return render(request, "storefront/referral/pages/blogs.html", ctx)


def blog_detail(request, blog_slug):
    site = _site(request)
    match = next((p for p in _published_posts(site) if p.get("slug") == blog_slug), None)
    if match is None:
        raise Http404("No such post.")
    bp = BlogProxy(match)
    # The post body is TinyMCE HTML stored under ``description`` (what the owner
    # store's blog_detail renders as ``blog.description``); older posts may use
    # ``content``/``body``. Render it as rich HTML in the card.
    body = match.get("description") or match.get("content") or match.get("body") or ""
    ctx = _shell_ctx(request, site, bp.title or "Post")
    ctx.update({
        "heading": bp.title or "",
        "subheading": match.get("author") or "",
        "hero_image": (bp.image.url if bp.image else ""),
        "body_html": body,
    })
    return render(request, "storefront/referral/pages/content.html", ctx)


def enquiry(request):
    """Contact page + submission, kept inside the referral shell."""
    site = _site(request)
    sent = False
    error = ""
    if request.method == "POST":
        from commerce.models import Enquiry

        name = (request.POST.get("name") or "").strip()
        message = (request.POST.get("message") or "").strip()
        if not name or not message:
            error = "Please enter your name and a message."
        else:
            try:
                Enquiry.objects.create(
                    site=site,
                    name=name,
                    email=(request.POST.get("email") or "").strip(),
                    mobile_number=(request.POST.get("phone") or "").strip(),
                    message=message,
                )
                sent = True
            except Exception:
                error = "Sorry, we couldn't send that. Please try again."
    ctx = _shell_ctx(request, site, "Contact")
    ctx.update({"sent": sent, "error": error})
    return render(request, "storefront/referral/pages/contact.html", ctx)


def _policy(request, key, title):
    site = _site(request)
    content = _content_dict(site)
    body = (content.get(key) or "")
    if isinstance(body, dict):
        body = body.get("content") or body.get("body") or ""
    ctx = _shell_ctx(request, site, title)
    ctx.update({"heading": title, "body_html": body, "empty_text": "This policy hasn't been added yet."})
    return render(request, "storefront/referral/pages/content.html", ctx)


def privacy_policy(request):
    return _policy(request, "privacy_policy", "Privacy policy")


def terms_conditions(request):
    return _policy(request, "terms_conditions", "Terms & conditions")


def refund_policy(request):
    return _policy(request, "refund_policy", "Refund policy")


def shipping_policy(request):
    return _policy(request, "shipping_policy", "Shipping policy")


def custom_policy(request):
    return _policy(request, "custom_policy", "Policy")


def custom_policy_page(request, policy_slug):
    """An owner's extra policy (Edit Content → Policies) inside the UGC store,
    at the same ``/policies/<slug>/`` path as on the owner's own store."""
    site = _site(request)
    from .policies import find

    policy = find(_content_dict(site), policy_slug)
    if policy is None:
        raise Http404("No such policy.")
    ctx = _shell_ctx(request, site, policy["title"])
    ctx.update({
        "heading": policy["title"],
        "body_html": policy["content"],
        "empty_text": "This policy hasn't been added yet.",
    })
    return render(request, "storefront/referral/pages/content.html", ctx)


def _core_pages(site):
    return [
        p for p in (_content_dict(site).get("core_pages") or [])
        if isinstance(p, dict) and p.get("slug") and p.get("title")
    ]


def core_pages_index(request):
    """The /core-pages/ hub  every core page as cards + a full tree, matching
    the owner store's core_pages.html, laid out for the card."""
    site = _site(request)
    ctx = _shell_ctx(request, site, "Pages")
    ctx.update({"pages": _core_pages(site)})
    return render(request, "storefront/referral/pages/core_pages.html", ctx)


def core_page(request, page_slug, sub_slug=None, child_slug=None):
    """One core page and its whole subtree, rendered as the same tree-navigated
    SPA the owner store uses (core_page.html)  page → subpage → child switched
    client-side  but inside the collaboration card. A direct deep link to a
    subpage/child still resolves (addressable); an invalid one 404s.
    """
    site = _site(request)
    page = next((p for p in _core_pages(site) if p.get("slug") == page_slug), None)
    if page is None:
        raise Http404("No such page.")

    subpages = [
        s for s in (page.get("subpages") or []) if isinstance(s, dict) and s.get("slug")
    ]

    if sub_slug is not None:
        sub = next((s for s in subpages if s.get("slug") == sub_slug), None)
        if sub is None:
            raise Http404("No such subpage.")
        if child_slug is not None:
            kids = [
                k for k in (sub.get("children") or []) if isinstance(k, dict) and k.get("slug")
            ]
            if not any(k.get("slug") == child_slug for k in kids):
                raise Http404("No such child page.")

    ctx = _shell_ctx(request, site, page.get("title") or "Page")
    ctx.update({"core_page": page, "subpages": subpages})
    return render(request, "storefront/referral/pages/core_page.html", ctx)
