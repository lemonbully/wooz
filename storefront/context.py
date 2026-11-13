"""Build the template context every storefront page shares.

The ported designs were written against the reference ``Website``/``WebsiteProduct``
shape. Two thin adapters bridge that shape to OneWooz's models without editing the
templates:

* ``TemplateShim`` gives ``website.template.is_D2C`` / ``is_B2B`` /
  ``template_path``, which dozens of template branches read.
* ``ProductProxy`` exposes a live :class:`commerce.Product` under the attribute
  names the product pages use (``product_id``, ``image1``, ``in_stock`` …) while
  passing everything else straight through.

The homepage sections (top sellers, categories, testimonials, FAQs, hero banners)
are plain data arrays inside ``site.content`` and need no adapter  they render as
dicts exactly as the reference stored them.
"""

from __future__ import annotations

from types import SimpleNamespace

from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from commerce.models import Category, Product, display_ordered_categories
from tenancy.models import Site

# The storefront navigation and several pages iterate the live catalogue. Bound
# how much is proxied per request so a store with thousands of products does not
# build thousands of proxies for a menu.
STOREFRONT_PRODUCT_LIMIT = 60

# A neutral fallback so a brand-new store's hero is never empty and never points
# at the reference's dead ``via.placeholder.com`` host.
DEFAULT_BANNERS = [
    {
        "image": "",
        "title": "Welcome to our store",
        "description": "Discover quality products, thoughtfully made.",
        "button_text": "Shop now",
        "button_url": "/shop/",
        "alt_text": "Featured collection",
    }
]


class _SiteContentPage:
    """A stand-in ``page`` whose ``.content`` is the store's own content blob.

    The ported About page reads every field off ``page.content.X``  the
    reference passed the Website itself as the "page". OneWooz serves About with
    no CMS page object, so without this the template only ever saw the blank null
    page and fell back to its hard-coded placeholder copy, and the owner's saved
    About fields (``aus_brand_*``, ``cowork_*``, ``editorial_*``) never showed.

    ``content`` is read lazily off the site, so it reflects the wrapped content
    :func:`build_context` installs on ``site.content`` before the template renders.
    Every other attribute resolves to the null object, exactly like the default
    ``MISSING`` page, so ``page.title`` etc. stay blank as before.
    """

    def __init__(self, site: Site):
        self._site = site

    @property
    def content(self):
        return self._site.content

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        from .safe import MISSING

        return MISSING


def _template_shim(site: Site) -> SimpleNamespace:
    return SimpleNamespace(
        is_D2C=site.is_d2c,
        is_B2B=site.is_b2b,
        template_path=f"storefront/{site.template_dir}",
        name=site.get_theme_display(),
    )


def normalize_variant_types(raw):
    """Present variant types in the shape the ported PDP expects.

    OneWooz stores a type as ``{"name": "Size", "values": ["S", "M"]}`` (plain
    string values), but the storefront swatch markup reads ``value.value``  so
    a raw string renders as an empty box. Coerce every value to ``{"value": …}``
    (and tolerate values already stored as dicts).
    """
    out = []
    for vt in raw or []:
        if not isinstance(vt, dict):
            continue
        values = []
        for v in vt.get("values") or []:
            if isinstance(v, dict):
                s = str(v.get("value") or v.get("name") or "").strip()
            else:
                s = str(v).strip()
            if s:
                values.append({"value": s})
        out.append({"name": str(vt.get("name") or "").strip(), "values": values})
    return out


class ProductProxy:
    """Adapt a :class:`commerce.Product` to the reference product shape."""

    def __init__(self, product, request=None, site=None):
        self._p = product
        self._request = request
        self._site = site
        self._images = None

    @property
    def seller_name(self):
        return self._site.name if self._site is not None else ""

    # Anything not overridden below (title, slug, description, price, mrp,
    # currency, specifications, variants, faqs, trusted_icons, the physical and
    # compliance fields, and every get_*_display) comes straight off the model.
    # An attribute the model doesn't have resolves to the blank null object
    # rather than raising, so a template reading an unexpected field can't take
    # the storefront down.
    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        # image3, image4, … imageN. ``image1``/``image2`` are explicit properties
        # above (image1 carries a demo fallback); every further ``imageN`` the
        # PDP thumbnail strip asks for maps to the Nth uploaded gallery image, so
        # a product with 5–7 photos shows all of them instead of only the first
        # two. Returns None past the end so empty slots simply don't render.
        if name.startswith("image") and name[5:].isdigit():
            idx = int(name[5:]) - 1
            imgs = self.image_objects
            return imgs[idx].image if 0 <= idx < len(imgs) else None
        try:
            return getattr(self._p, name)
        except AttributeError:
            from .safe import MISSING

            return MISSING

    @property
    def image_objects(self):
        if self._images is None:
            self._images = list(self._p.images.all())
        return self._images

    @property
    def product_id(self):
        return self._p.id

    @property
    def _demo_idx(self) -> int:
        """A stable index into the demo product photo set for this product, so a
        product with no uploaded image always shows the *same* reference photo,
        and different products show different ones."""
        key = str(getattr(self._p, "slug", "") or self._p.id or "")
        return sum(ord(c) for c in key)

    @property
    def image1(self):
        """The primary image. Falls back to a reference photo when the product
        has none, so every listing, the PDP and the cart show a real picture
        instead of an empty box (only the homepage had this fallback before)."""
        imgs = self.image_objects
        if imgs:
            return imgs[0].image
        from .demo_media import demo_image

        return _Img(demo_image("product", self._demo_idx))

    @property
    def image2(self):
        # No demo fallback here on purpose: a blank second image keeps the
        # hover-swap off rather than swapping between two unrelated stock photos.
        imgs = self.image_objects
        return imgs[1].image if len(imgs) > 1 else None

    @property
    def image(self):
        return self.image1

    @property
    def alt_text(self):
        imgs = self.image_objects
        return imgs[0].alt_text if imgs else self._p.title

    @property
    def gallery(self):
        """All gallery images, for the PDP thumbnail strip. Falls back to a
        single reference photo so the PDP is never imageless."""
        imgs = [img.image for img in self.image_objects]
        if imgs:
            return imgs
        from .demo_media import demo_image

        return [_Img(demo_image("product", self._demo_idx))]

    @property
    def in_stock(self):
        if self._p.variant_combinations:
            # Only variations still for sale count (one the owner switched
            # off can't be bought, whatever its stock).
            from commerce.variant_profiles import sellable_stock

            return sellable_stock(self._p) > 0
        return not self._p.is_out_of_stock

    @property
    def variant_combinations(self):
        """The combinations as the product page's scripts read them: a
        switched-off variation shows 0 stock (so it reads as sold out and can't
        be added), and each variation's own details (``profile``) are left out
        of the page data (they are rendered separately)."""
        raw = self._p.variant_combinations
        if not raw:
            return raw
        from commerce.cart import combos_of
        from commerce.variant_profiles import combo_units

        out = []
        for combo in combos_of(self._p):
            c = {k: v for k, v in combo.items() if k != "profile"}
            c["inventory"] = combo_units(combo)
            out.append(c)
        return out

    @property
    def dangerous_good(self):
        # The ported PDP reads the singular name; map it to the model's field.
        return self._p.get_dangerous_goods_display() if self._p.dangerous_goods else ""

    # JSON blobs the ported PDP reads with null-safe lookups (and eager
    # ``|default:x.key`` filter arguments, which RAISE on a missing key on a
    # plain dict). Wrapping makes a missing key resolve to blank instead of
    # 500ing the page.
    @property
    def specifications(self):
        from .safe import wrap

        return wrap(self._p.specifications or [])

    @property
    def variants(self):
        from .safe import wrap

        return wrap(normalize_variant_types(self._p.variants or []))

    @property
    def faqs(self):
        from .safe import wrap

        return wrap(self._p.faqs or [])

    @property
    def trusted_icons(self):
        from commerce.trust_badges import is_removed

        from .safe import wrap

        # Retired badges (6-month warranty, easy/free returns, COD) are hidden
        # even on products saved before they were removed.
        return wrap([n for n in (self._p.trusted_icons or []) if not is_removed(n)])

    @property
    def original_price(self):
        return self._p.mrp

    @property
    def on_sale(self):
        return bool(self._p.mrp and self._p.price and self._p.mrp > self._p.price)

    @property
    def is_new(self):
        return self._p.is_recently_launched

    @property
    def moq(self):
        details = self._p.d2c_details or {}
        try:
            return int(details.get("moq") or 1)
        except (TypeError, ValueError):
            return 1

    @property
    def url(self):
        try:
            return reverse("public_product_detail", kwargs={"product_slug": self._p.slug})
        except NoReverseMatch:
            return f"/products/{self._p.slug}/"


def proxy_products(queryset_or_list, request=None, site=None):
    return [ProductProxy(p, request, site) for p in queryset_or_list]


class _Img:
    """Wraps an image URL string so templates can read ``.url`` and truthiness."""

    def __init__(self, url):
        self.url = url or ""

    def __bool__(self):
        return bool(self.url)

    def __str__(self):
        return self.url


class BlogProxy:
    """Adapt a content-editor blog dict to the reference blog object shape."""

    def __init__(self, data):
        self._d = data if isinstance(data, dict) else {}

    def __getattr__(self, name):
        return self._d.get(name, "")

    @property
    def image(self):
        url = self._d.get("image") or ""
        return _Img(url) if url else ""

    @property
    def created_at(self):
        import datetime as _dt

        raw = str(self._d.get("date") or "").strip()
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%b %d, %Y", "%B %d, %Y"):
            try:
                return _dt.datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        return None


def proxy_blogs(items):
    return [BlogProxy(d) for d in (items or []) if isinstance(d, dict)]


def _seo_page_key(path: str) -> str:
    """Map a storefront path to its per-page SEO key (see the editor's SEO tab)."""
    p = (path or "/").rstrip("/") or "/"
    if p == "/":
        return "seo"
    if "/products/" in p or "/product/" in p or "/shop" in p or p.endswith("/products") or "/category" in p:
        return "seo_products"
    if "/about" in p:
        return "seo_about"
    if "/blog" in p:
        return "seo_blogs"
    if "/page/" in p:
        return "seo_core"
    return "seo"


def _effective_seo(merged: dict, request) -> dict:
    """The SEO dict for this page. In "same" mode every page uses the default
    set; in "custom" mode the page's own non-empty fields override the default."""
    base = merged.get("seo") if isinstance(merged.get("seo"), dict) else {}
    result = dict(base)
    if str(merged.get("seo_mode") or "same") == "custom":
        key = _seo_page_key(getattr(request, "path", "/"))
        page = merged.get(key) if (key != "seo" and isinstance(merged.get(key), dict)) else {}
        for k, v in page.items():
            if v not in (None, ""):
                result[k] = v
    # The ported <title> reads seo.page_meta_title; mirror the meta title so the
    # tab title follows whichever SEO set applies to this page.
    if result.get("meta_title") and not result.get("page_meta_title"):
        result["page_meta_title"] = result["meta_title"]
    return result


def home_extra(request, site: Site) -> dict:
    """The real-data context the homepage design (``home.html``) reads.

    ``home.html`` shows best-sellers / latest products and homepage categories
    from the live catalogue, alongside the content blob. It also carries the
    store's blog posts, so the homepage "Blog" strip renders the same cards the
    /blog listing does. Videos are handled by the reels/video sections.
    """
    from .scope import scope_categories, scope_products

    active = scope_products(
        Product.objects.for_site(site)
        .filter(is_active=True, is_deleted=False)
        .order_by("-created_at"),
        request,
    )
    content = site.content or {}
    # Best sellers, in the exact order the owner set on the Products page
    # (``best_sellers_order``); anything not in that list trails, newest first.
    # Capped at 8, like the dashboard.
    saved_order = content.get("best_sellers_order") or []
    order_index = {str(pid): i for i, pid in enumerate(saved_order)}
    best = list(active.filter(is_best_seller=True))
    best.sort(key=lambda p: order_index.get(str(p.id), len(order_index)))
    best = best[:8]
    latest = best or list(active[:12])
    homepage_categories = list(
        display_ordered_categories(
            scope_categories(
                Category.objects.for_site(site).filter(is_active=True, is_deleted=False),
                request,
            )
        )[:12]
    )
    return {
        "latest_products": proxy_products(latest, request, site),
        "homepage_categories": homepage_categories,
        "total_active_products": active.count(),
        "is_d2c": site.is_d2c,
        # Prices on product cards follow the "Show price in lists" toggle on the
        # Products page; unset, it defaults to D2C-shows / B2B-hides.
        "show_price_in_lists": bool(content.get("show_price_in_lists", site.is_d2c)),
        # The store's blog posts (added in the editor's Blogs tab), for the
        # homepage "Blog" strip  same cards as the /blog listing page. Draft
        # posts are excluded (a post is public unless status is "draft"); empty
        # when the owner has added none, so the section simply doesn't render.
        "blogs": proxy_blogs([
            p for p in ((site.content or {}).get("blog_posts") or [])
            if isinstance(p, dict) and (p.get("status") or "published") != "draft"
        ]),
        "videos": [],
    }


def build_context(request, site: Site, *, page=None, **extra) -> dict:
    """Assemble the shared context. ``extra`` overrides anything by name."""
    # The ported templates read keys off ``website.content`` inside
    # ``|default:website.content.X`` chains; resolving that argument against a
    # dict that lacks the key raises VariableDoesNotExist before the literal
    # fallback can apply. So the presentable defaults are merged *underneath*
    # whatever the store has saved  the owner's values always win, but every
    # key the templates reference is guaranteed present, even for a store
    # created before seeding or one with partial content. In-memory only:
    # nothing is persisted from a page render.
    from .content_seed import default_content
    from .safe import MISSING, wrap

    merged = default_content(site)
    if site.content:
        merged.update(site.content)
    # Resolve the SEO for this specific page (same-for-all vs per-page) from the
    # raw dict before wrapping.
    # Per-page SEO (storefront.seo_pages): the page is identified by its route,
    # and gets either its generated "smart" set (same mode) or the owner's own
    # tab (custom mode). Falls back to the old path heuristic if anything goes
    # wrong, so a render never fails over SEO.
    try:
        from .seo_pages import resolve as _resolve_seo

        effective_seo = _resolve_seo(site, merged, request)
    except Exception:
        effective_seo = _effective_seo(merged, request)
    # Wrap so a missing key at any depth renders blank instead of raising. The
    # demo defaults above fill the known keys; wrapping catches everything else.
    site.content = wrap(merged)
    # Give the ported branches their ``website.template`` object.
    site.template = _template_shim(site)

    content = site.content
    seo = wrap(effective_seo) if effective_seo else wrap({})

    # Top-level core pages, for the storefront nav (so pages the owner adds in
    # the editor are reachable). Detail pages render at /page/<slug>/.
    _cps = merged.get("core_pages")
    core_nav_pages = [
        {"title": p.get("title"), "slug": p.get("slug")}
        for p in (_cps if isinstance(_cps, list) else [])
        if isinstance(p, dict) and p.get("slug") and p.get("title")
    ]

    from .certificates import public_certificates as _public_certificates
    from .scope import scope_categories, scope_products

    categories = list(
        display_ordered_categories(
            scope_categories(
                Category.objects.for_site(site).filter(is_active=True, is_deleted=False),
                request,
            )
        )
    )
    products = proxy_products(
        scope_products(
            Product.objects.for_site(site).filter(is_active=True, is_deleted=False),
            request,
        ).order_by("-created_at")[:STOREFRONT_PRODUCT_LIMIT],
        request,
        site,
    )

    # The category the owner starred (Categories page ★) as the mega menu's
    # special card. Stored as a string id; resolved here because the template
    # can't compare it with a Category's UUID id (they never matched, so the
    # card never showed). Only an active, in-scope category qualifies.
    _cfg = merged.get("d2c_config")
    _highlight_id = str((_cfg or {}).get("mega_highlight_category_id") or "").strip() if isinstance(_cfg, dict) else ""
    mega_highlight_category = (
        next((c for c in categories if str(c.id) == _highlight_id), None) if _highlight_id else None
    )
    # The mega menu's left grid: every category except the featured one (it
    # already has the special card on the right, so it isn't listed twice).
    # Capped after excluding it, so the grid still shows up to 24 others.
    mega_menu_categories = [
        c for c in categories
        if mega_highlight_category is None or c.id != mega_highlight_category.id
    ][:24]

    ctx = {
        "website": site,
        "content": content,
        # The ported policy/footer templates read ``global_content`` (the site's
        # whole content blob); it is the same wrapped dict as ``content``.
        "global_content": content,
        "seo": seo,
        # A null object rather than None, so ``page.content.x`` chains in the
        # templates resolve blank instead of raising if used as a default-arg.
        "page": page if page is not None else MISSING,
        "categories": categories,
        "mega_highlight_category": mega_highlight_category,
        "mega_menu_categories": mega_menu_categories,
        "products": products,
        "core_nav_pages": core_nav_pages,
        # Google Site Verification token from the Google Merchant connection
        # (lets Merchant Center verify + claim this store's website).
        "google_site_verification": _google_site_verification(site),
        # A B2B store with certificates links its /certificates/ page from the
        # footer (an unlinked page is never crawled).
        "has_certificates": bool(site.is_b2b and _public_certificates(merged)),
        # Storefront presentation toggles (Products page), available on every
        # page so product cards and the PDP honour them consistently. A view may
        # still override any of these via ``extra``.
        "show_price_in_lists": bool(merged.get("show_price_in_lists", site.is_d2c)),
        "enable_product_detail_zoom": bool(merged.get("enable_product_detail_zoom", False)),
        "enable_product_hover_swap": bool(merged.get("enable_product_hover_swap", False)),
        # The store's own base template, so shared pages (cart, checkout, order
        # confirmation) inherit the storefront's real nav + footer + cart drawer
        # rather than the generic chrome. On a UGC collaborator storefront these
        # shared pages instead inherit the referral card chrome, so checkout, cart
        # and account render *inside the collaborator's store*  never the owner's
        # themed site.
        "store_base": (
            "storefront/referral/_base.html"
            if getattr(request, "collaboration", None) is not None
            else f"storefront/{site.template_dir}/base.html"
        ),
        "default_banners": wrap(DEFAULT_BANNERS),
        "canonical_url": request.build_absolute_uri(request.path),
        # A store is only search-indexable on its own verified custom domain.
        # While it is served on the <slug>.onewooz.site subdomain (no custom
        # domain yet), the head carries noindex,nofollow so the subdomain never
        # competes with  or pre-empts  the owner's real domain in search.
        "noindex": not getattr(request, "is_custom_domain", False),
        "is_public_view": True,
        "embedded": False,
        "now": timezone.now(),
        # The signed-in storefront shopper, if any (email-OTP customer session).
        # The nav (account icon) and the account page branch on this.
        "customer_email": (request.session.get("ow_customer_email") or ""),
        # Ids (as strings) of the products in the signed-in shopper's wishlist,
        # so product-card hearts render filled for products already saved.
        # Lazy: the query only runs on a page whose template asks for it.
        "wishlisted_product_ids": _wishlisted_ids_lazy(request, site),
        # Marketing tags (analytics + ad pixels) the owner configured under
        # Tracking & Ads. Placed into <head>/<body> by the tracking partials.
        "tracking": _tracking_context(site),
    }
    ctx.update(extra)
    # Schema.org JSON-LD for this page (storefront.structured_data), printed by
    # theme_base.html. Built after ``extra`` so it reads the page's own objects
    # (the product and its reviews, the listed products, the blog post …).
    # Returns "" on any failure, so it can never break a render.
    if "page_schema_json" not in ctx:
        from .structured_data import page_schema_json

        ctx["page_schema_json"] = page_schema_json(request, site, merged, effective_seo, ctx)
    return ctx


def _wishlisted_ids_lazy(request, site: Site):
    """A lazily-built frozenset of ``str(product_id)`` in the signed-in
    shopper's wishlist (empty when signed out). Templates test membership
    with ``product.product_id|stringformat:"s" in wishlisted_product_ids``."""
    from django.utils.functional import SimpleLazyObject

    def _load():
        from commerce.models import WishlistItem

        from .customer_accounts import current_customer_email

        email = current_customer_email(request)
        if not email:
            return frozenset()
        try:
            return frozenset(
                str(pid)
                for pid in WishlistItem.objects.for_site(site)
                .filter(customer_email=email)
                .order_by()
                .values_list("product_id", flat=True)
            )
        except Exception:
            return frozenset()

    return SimpleLazyObject(_load)


def _tracking_context(site: Site):
    """The public tracking IDs + custom head HTML for this store, or ``None``.

    Only browser-safe values are exposed: the Meta Conversions-API token is a
    server-side secret and is deliberately never handed to the template.
    """
    from commerce.models import TrackingConfig

    cfg = TrackingConfig.objects.for_site(site).first()
    if cfg is None:
        return None
    return SimpleNamespace(
        google_analytics_id=cfg.google_analytics_id,
        google_tag_manager_id=cfg.google_tag_manager_id,
        google_ads_id=cfg.google_ads_id,
        meta_pixel_id=cfg.meta_pixel_id,
        tiktok_pixel_id=cfg.tiktok_pixel_id,
        pinterest_tag_id=cfg.pinterest_tag_id,
        microsoft_uet_id=cfg.microsoft_uet_id,
        custom_head_html=cfg.custom_head_html,
    )


def _google_site_verification(site) -> str:
    """The store's Google Site Verification meta-tag token, or ""."""
    try:
        from commerce.models import GoogleMerchantConfig

        return (
            GoogleMerchantConfig.all_objects.filter(site_id=site.pk)
            .values_list("site_verification_token", flat=True)
            .first()
            or ""
        )
    except Exception:
        return ""
