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
are plain data arrays inside ``site.content`` and need no adapter — they render as
dicts exactly as the reference stored them.
"""

from __future__ import annotations

from types import SimpleNamespace

from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from commerce.models import Category, Product
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


def _template_shim(site: Site) -> SimpleNamespace:
    return SimpleNamespace(
        is_D2C=site.is_d2c,
        is_B2B=site.is_b2b,
        template_path=f"storefront/{site.template_dir}",
        name=site.get_theme_display(),
    )


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
    def image1(self):
        imgs = self.image_objects
        return imgs[0].image if imgs else None

    @property
    def image2(self):
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
        """All gallery images, for the PDP thumbnail strip."""
        return [img.image for img in self.image_objects]

    @property
    def in_stock(self):
        return not self._p.is_out_of_stock

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


def home_extra(request, site: Site) -> dict:
    """The real-data context the homepage design (``home.html``) reads.

    ``home.html`` shows best-sellers / latest products and homepage categories
    from the live catalogue, alongside the content blob. Blogs and videos are
    later phases, passed empty so their sections render nothing.
    """
    active = (
        Product.objects.for_site(site)
        .filter(is_active=True, is_deleted=False)
        .order_by("-created_at")
    )
    best = list(active.filter(is_best_seller=True)[:8])
    latest = best or list(active[:12])
    homepage_categories = list(
        Category.objects.for_site(site)
        .filter(is_active=True, is_deleted=False)
        .order_by("order", "name")[:12]
    )
    return {
        "latest_products": proxy_products(latest, request, site),
        "homepage_categories": homepage_categories,
        "total_active_products": active.count(),
        "is_d2c": site.is_d2c,
        # D2C storefronts show prices and buy actions; B2B leads with enquiry.
        "show_price_in_lists": site.is_d2c,
        "blogs": [],
        "videos": [],
    }


def build_context(request, site: Site, *, page=None, **extra) -> dict:
    """Assemble the shared context. ``extra`` overrides anything by name."""
    # The ported templates read keys off ``website.content`` inside
    # ``|default:website.content.X`` chains; resolving that argument against a
    # dict that lacks the key raises VariableDoesNotExist before the literal
    # fallback can apply. So the presentable defaults are merged *underneath*
    # whatever the store has saved — the owner's values always win, but every
    # key the templates reference is guaranteed present, even for a store
    # created before seeding or one with partial content. In-memory only:
    # nothing is persisted from a page render.
    from .content_seed import default_content
    from .safe import MISSING, wrap

    merged = default_content(site)
    if site.content:
        merged.update(site.content)
    # Wrap so a missing key at any depth renders blank instead of raising. The
    # demo defaults above fill the known keys; wrapping catches everything else.
    site.content = wrap(merged)
    # Give the ported branches their ``website.template`` object.
    site.template = _template_shim(site)

    content = site.content
    seo = content.get("seo") or wrap({})

    categories = list(
        Category.objects.for_site(site)
        .filter(is_active=True, is_deleted=False)
        .order_by("order", "name")
    )
    products = proxy_products(
        Product.objects.for_site(site)
        .filter(is_active=True, is_deleted=False)
        .order_by("-created_at")[:STOREFRONT_PRODUCT_LIMIT],
        request,
        site,
    )

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
        "products": products,
        "default_banners": wrap(DEFAULT_BANNERS),
        "canonical_url": request.build_absolute_uri(request.path),
        "is_public_view": True,
        "embedded": False,
        "now": timezone.now(),
    }
    ctx.update(extra)
    return ctx
