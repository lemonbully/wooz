"""Fill a product's variation combinations with distinct test data.

Every combination gets data no other combination has: its own SKU, MRP,
selling price, stock, three generated images (each labelled with the
variation's name and number, in its own colour) and a value of its own for
every field of the product form: title, brand, bullets, description,
keywords, features, recommended use, material, colour / texture, handling
time, accessories, what's in the box, FAQs, specifications, extras, category
and extra categories, trust badges, GST % / HSN / GST included, low-stock
level, return policy and window, dangerous goods, best seller, video and video
format, origin, manufacturer, packer, importer, warranty, warning, support
phone and email, dimensions and weight. Each text
value names its variation ("... for M / Black"), so on the product page (your
store and myugcstore) you can see at a glance that picking a variation shows
that variation's data and nothing from another one.

Shared on purpose: the colour dot of a Color value (the page shows one dot per
colour, so every "Black" variation has the same one), the length / weight
units (cm, g) and the currency (a mixed-currency product can't be checked out).
Values picked from a short list (categories, dangerous-goods types, return
policy, true/false switches) repeat once the list runs out; trust badges use a
different combination per variation, and each video link is tagged with its
variation number.

The stock pattern covers every state the page shows:

    variation 1   25 units   In stock          (default selected)
    variation 2    3 units   Only 3 left       (low stock)
    variation 3    0 units   Out of stock
    others        30+ units  In stock          (each a different number)

Usage:

    python manage.py seed_variation_test --store <store-slug>
        Creates (or refreshes) the product "Variation Test Tee" with
        Size S/M/L x Color Black/White.

    python manage.py seed_variation_test --store <slug> --product <product-slug>
        Fills an existing product's combinations instead (its variation types
        are kept; it gets the default ones if it has none). Its own title,
        description etc. are not touched.

    --axes "Size=S,M,L;Color=Black,White,Blue"   variation types for a new product
    --collab <handle>   also add the product to that collaborator's catalogue
                        (needed when the collaborator only sells specific products)
    --inherit-last      leave the last combination without its own details, to
                        check the fallback to the main product's details
    --all-inherit       give no combination its own details (price/stock/images only)
    --remove            delete the "Variation Test Tee" product again
    --force             allow running with DEBUG off (writes to that database!)
"""

from __future__ import annotations

import colorsys
import re
from xml.sax.saxutils import escape

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

TEST_SKU = "VARTEST"
TEST_TITLE = "Variation Test Tee"
DEFAULT_AXES = "Size=S,M,L;Color=Black,White"
IMAGES_PER_VARIATION = 3

_COLOURS = {
    "black": "#111111", "white": "#f4f4f4", "blue": "#1f4fd8", "red": "#c62828", "green": "#2e7d32",
    "navy": "#1b2a4a", "grey": "#8a8a8a", "gray": "#8a8a8a", "yellow": "#f2c94c", "pink": "#e88fb4",
}
_ORIGINS = ["India", "Vietnam", "Bangladesh", "Portugal", "Turkey", "Sri Lanka", "Italy", "Peru",
            "Egypt", "Mexico", "Morocco", "Cambodia"]
_MATERIALS = ["100% cotton", "Cotton-poly blend", "Organic cotton", "Linen blend", "Bamboo cotton",
              "Modal blend", "Pima cotton", "Hemp blend", "Supima cotton", "Tencel blend"]
_FITS = ["Regular", "Slim", "Relaxed", "Oversized", "Athletic", "Boxy", "Tapered", "Classic"]
_USES = ["casual outings", "travel", "gym sessions", "office Fridays", "lounging", "summer evenings",
         "weekend hikes", "festivals"]


def _parse_axes(raw: str) -> list:
    axes = []
    for part in (raw or "").split(";"):
        if "=" not in part:
            continue
        name, vals = part.split("=", 1)
        values = [v.strip() for v in vals.split(",") if v.strip()]
        if name.strip() and values:
            axes.append({"name": name.strip(), "values": values})
    return axes


def _axes_of(product) -> list:
    from storefront.context import normalize_variant_types

    out = []
    for v in normalize_variant_types(product.variants or []):
        name = v.get("name") if isinstance(v, dict) else None
        values = []
        for x in (v.get("values") or []) if isinstance(v, dict) else []:
            if isinstance(x, dict):
                x = x.get("value") or x.get("name") or ""
            x = str(x).strip()
            if x:
                values.append(x)
        if name and values:
            out.append({"name": str(name), "values": values})
    return out


def _cartesian(axes: list) -> list:
    combos = [[]]
    for axis in axes:
        combos = [c + [{"name": axis["name"], "value": v}] for c in combos for v in axis["values"]]
    return combos if axes else []


def _sku_part(value) -> str:
    return "".join(ch for ch in str(value).upper() if ch.isalnum())[:12]


def _pick(items: list, i: int) -> str:
    """The i-th item; past the end of the list, numbered so it never repeats."""
    base = items[i % len(items)]
    return base if i < len(items) else f"{base} {i // len(items) + 1}"


def _hex(h: float, l: float, s: float) -> str:
    r, g, b = colorsys.hls_to_rgb(h % 1.0, l, s)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


def _svg(label: str, n: int, k: int, hue: float) -> bytes:
    """A labelled square image: the variation's name, its number and which of
    its images this is, on a colour no other variation uses."""
    bg = _hex(hue + k * 0.04, 0.42 - k * 0.06, 0.55)
    fg = "#ffffff"
    text = escape(label)
    size = 64 if len(label) <= 10 else (48 if len(label) <= 16 else 34)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="800" viewBox="0 0 800 800">'
        f'<rect width="800" height="800" fill="{bg}"/>'
        f'<circle cx="400" cy="330" r="{150 - 25 * k}" fill="none" stroke="{fg}" stroke-opacity="0.35" stroke-width="10"/>'
        f'<text x="400" y="352" text-anchor="middle" font-family="Arial, sans-serif" font-size="96" font-weight="700" fill="{fg}">V{n}</text>'
        f'<text x="400" y="560" text-anchor="middle" font-family="Arial, sans-serif" font-size="{size}" font-weight="600" fill="{fg}">{text}</text>'
        f'<text x="400" y="640" text-anchor="middle" font-family="Arial, sans-serif" font-size="34" fill="{fg}" fill-opacity="0.85">Variation {n} · image {k + 1} of {IMAGES_PER_VARIATION}</text>'
        '</svg>'
    ).encode("utf-8")


_DANGEROUS = ["not_applicable", "ghs", "storage", "transportation", "waste", "other", "unknown"]
_RETURNS = ["returnable", "replacement", "non_returnable"]


def _badge_set(pool: list, i: int) -> list:
    """The i-th distinct set of badges from ``pool`` (sets of 3, then 2, then
    4, then 1), so no two variations show the same badges until every
    combination of the available badges is used."""
    from itertools import combinations

    if not pool:
        return []
    sets = []
    for size in (3, 2, 4, 1):
        if size <= len(pool):
            sets.extend(list(c) for c in combinations(pool, size))
    return sets[i % len(sets)]


def _profile(i: int, label: str, attrs: list, base_title: str, ctx: dict) -> dict:
    """Values for variation ``i`` that no other variation shares (where the
    choices allow it)."""
    colour = next((a["value"] for a in attrs if a["name"].lower() in ("color", "colour")), "")
    n = i + 1
    rtype = _RETURNS[i % len(_RETURNS)]
    cats = ctx.get("categories") or []
    badges = ctx.get("badges") or []
    videos = ctx.get("videos") or []
    extra = {
        "brand": f"Brand V{n} ({label})",
        "keywords": [f"{label} tee", f"variation {n}", f"vt-{1000 + n}"],
        "hsn_code": f"6109{10 + n:02d}",
        "gst_percentage": f"{min(5 + i, 28)}",
        "gst_inclusive": i % 2 == 0,
        "low_stock_threshold": 2 + n,
        "return_type": rtype,
        "return_days": 0 if rtype == "non_returnable" else 5 + n,
        "replacement_days": n + 2 if rtype == "non_returnable" else None,
        "dangerous_goods": _DANGEROUS[i % len(_DANGEROUS)],
        "is_active": True,
        "is_best_seller": i % 2 == 1,
        "trusted_icons": _badge_set(badges, i),
    }
    if cats:
        extra["category"] = cats[i % len(cats)]
        if len(cats) > 1:
            extra["categories"] = [cats[(i + 1) % len(cats)]]
    if videos:
        # Seven bundled demo videos; the ?variation=N tag keeps every
        # variation's link its own (and shows which one is playing).
        url = videos[i % len(videos)]
        extra["video_link"] = f"{url}{'&' if '?' in url else '?'}variation={n}"
        extra["video_orientation"] = "portrait" if i % 2 else "landscape"
    return {
        **extra,
        "title": f"{base_title} ({label})",
        "description": (
            f"This is the description written only for variation {n}, {label}.\n\n"
            f"If you can read \"{label}\" here, the product page switched the Description tab correctly."
        ),
        "bullets": [
            f"Bullet 1 for {label}",
            f"Bullet 2 for {label}: tailored for variation {n}",
            f"Bullet 3 for {label}: {170 + 10 * i} GSM fabric",
            f"Bullet 4 for {label}: shown only after Read more on phones",
        ],
        "features": [f"{label} feature A", f"{label} feature B", f"Variation {n} only"],
        "recommended_use": f"Best for {_pick(_USES, i)} ({label})",
        "material": f"{_pick(_MATERIALS, i)} ({label})",
        "colour": f"{colour or 'Shade'}, finish {n} ({label})",
        "handling_time": f"Ships in {n} day{'s' if n > 1 else ''}",
        "accessories": [f"Spare button ({label})", f"Gift pouch no. {n}"],
        "includes": [f"1 x tee ({label})", f"Care card no. {n}"],
        "faqs": [
            {"question": f"Is {label} true to size?", "answer": f"Yes, variation {n} ({label}) follows the standard size chart."},
            {"question": f"How do I wash {label}?", "answer": f"Machine wash at {20 + i} degrees. This answer belongs to {label}."},
        ],
        "specifications": [
            {"name": "Variation", "value": f"{n}: {label}"},
            {"name": "GSM", "value": str(170 + 10 * i)},
            {"name": "Fit", "value": f"{_pick(_FITS, i)} ({label})"},
            {"name": "Batch", "value": f"VT-{1000 + n}"},
        ],
        "extras": [
            {"name": "Care", "value": f"Care instructions for {label}"},
            {"name": "Story", "value": f"How variation {n} ({label}) is made"},
        ],
        "country_of_origin": _pick(_ORIGINS, i),
        "manufacturer_contact": f"Maker {n} Textiles Pvt Ltd, Plot {n}, Surat 395003, +91 90000 {10000 + n}",
        "packer_contact": f"Packer {n} Logistics, Unit {n}, Bhiwandi 421302",
        "importer_contact": f"Importer {n} Trading Co, Shop {n}, Mumbai 400001",
        "guarantee_warranty": f"{n} month warranty on {label}",
        "warning": f"Test warning {n} for {label}: keep away from open flame",
        "support_phone": f"+91 80000 {20000 + n}",
        "support_email": f"support+v{n}@example.com",
        "product_length": f"{60 + 2 * i}",
        "product_breadth": f"{45 + i}",
        "product_height": f"{1 + 0.1 * i:.2f}",
        "product_unit": "cm",
        "weight_value": f"{180 + 15 * i}",
        "weight_unit": "g",
        "package_length": f"{30 + i}",
        "package_breadth": f"{25 + i}",
        "package_height": f"{3 + 0.5 * i:.2f}",
        "package_unit": "cm",
        "package_weight_value": f"{220 + 15 * i}",
        "package_weight_unit": "g",
    }


def _stock_for(i: int) -> int:
    return {0: 25, 1: 3, 2: 0}.get(i, 30 + i)


class Command(BaseCommand):
    help = "Fill a product's variation combinations with distinct test data (price, stock, images, details)."

    def add_arguments(self, parser):
        parser.add_argument("--store", help="Store slug (optional when there is only one store).")
        parser.add_argument("--product", help="Slug of an existing product to fill instead of the test product.")
        parser.add_argument("--axes", default=DEFAULT_AXES, help=f'Variation types for a new product, e.g. "{DEFAULT_AXES}".')
        parser.add_argument("--collab", help="Collaborator handle/subdomain whose catalogue should include the product.")
        parser.add_argument("--inherit-last", action="store_true", help="Leave the last combination without its own details.")
        parser.add_argument("--all-inherit", action="store_true", help="Give no combination its own details.")
        parser.add_argument("--remove", action="store_true", help="Delete the test product again.")
        parser.add_argument("--force", action="store_true", help="Allow running with DEBUG off.")

    # ------------------------------------------------------------------
    def handle(self, *args, **opts):
        if not settings.DEBUG and not opts["force"]:
            raise CommandError("DEBUG is off: refusing to write test data. Re-run with --force if this database is meant for testing.")

        from commerce.models import Product

        site = self._site(opts.get("store"))
        if opts["remove"]:
            self._remove(site)
            return

        with transaction.atomic():
            if opts.get("product"):
                product = Product.objects.for_site(site).filter(slug=opts["product"], is_deleted=False).first()
                if product is None:
                    raise CommandError(f"No product with slug '{opts['product']}' in store '{site.slug}'.")
                axes = _axes_of(product) or _parse_axes(DEFAULT_AXES)
                fill_base = False
            else:
                axes = _parse_axes(opts["axes"])
                if not axes:
                    raise CommandError('Give --axes like "Size=S,M,L;Color=Black,White".')
                product = self._test_product(site)
                fill_base = True

            if fill_base:
                self._fill_base(product, site)
            combos = self._combos(site, product, axes, inherit_last=opts["inherit_last"], all_inherit=opts["all_inherit"])
            product.variants = axes
            product.variant_combinations = combos
            product.inventory = None
            product.is_active = True
            # Same store-aware checks the product form applies on save
            # (categories in this store, badge locks, return windows).
            from commerce.variant_profiles import finalise_profiles

            finalise_profiles(product, site)
            self._save_without_merchant_sync(product)
            collab_note = self._collab(site, product, opts.get("collab"))

        self._report(site, product, combos, collab_note)

    @staticmethod
    def _save_without_merchant_sync(product):
        """Save, but keep test data out of Google Merchant: the auto-sync hook
        (commerce.apps) is muted for this one save, then put back."""
        from django.db.models.signals import post_save

        from commerce.apps import _merchant_product_saved
        from commerce.models import Product

        uid = "commerce.google_merchant.product_saved"
        muted = post_save.disconnect(sender=Product, dispatch_uid=uid)
        try:
            product.save()
        finally:
            if muted:
                post_save.connect(_merchant_product_saved, sender=Product, dispatch_uid=uid)

    # ------------------------------------------------------------------
    def _site(self, slug):
        from tenancy.models import Site

        stores = Site.objects.filter(is_deleted=False)
        if slug:
            site = stores.filter(slug=slug).first()
            if site is None:
                raise CommandError(f"No store with slug '{slug}'.")
            return site
        found = list(stores.values_list("slug", flat=True)[:21])
        if len(found) == 1:
            return stores.get(slug=found[0])
        raise CommandError("Pick a store with --store <slug>. Stores: " + (", ".join(found) or "(none)"))

    def _test_product(self, site):
        from commerce.models import Product

        product = Product.objects.for_site(site).filter(sku=TEST_SKU, is_deleted=False).first()
        if product is None:
            product = Product(site=site, title=TEST_TITLE, sku=TEST_SKU)
        return product

    def _fill_base(self, product, site):
        """The main product's own details, all starting with "MAIN", so a
        variation showing its own data is easy to tell apart from them."""
        from decimal import Decimal

        from commerce.models import Category, Product

        product.title = TEST_TITLE
        product.brand = product.brand or site.name or "Test brand"
        product.description = "MAIN PRODUCT description. A variation without its own details shows this text."
        product.price = Decimal("999.00")
        product.mrp = Decimal("1299.00")
        product.gst_percentage = product.gst_percentage or Decimal("5")
        product.hsn_code = product.hsn_code or "6109"
        product.low_stock_threshold = 5
        product.country_of_origin = "MAIN: India"
        product.manufacturer_contact = "MAIN: Product-level manufacturer"
        product.packer_contact = "MAIN: Product-level packer"
        product.guarantee_warranty = "MAIN: 6 month warranty"
        product.return_type = product.return_type or Product.ReturnType.RETURNABLE
        product.return_days = product.return_days or 7
        product.specifications = [{"name": "Variation", "value": "MAIN product"}, {"name": "GSM", "value": "150"}]
        product.faqs = [{"question": "MAIN: What is this?", "answer": "The main product's FAQ (shown when a variation has none)."}]
        product.d2c_details = {
            "bullets": ["MAIN bullet 1", "MAIN bullet 2"],
            "features": ["MAIN feature"],
            "material": "MAIN material",
            "colour": "MAIN colour",
            "recommended_use": "MAIN recommended use",
            "handling_time": "MAIN: Ships in 2 days",
            "accessories": [],
            "includes": ["MAIN: 1 x tee"],
            "extras": [{"name": "Care", "value": "MAIN care instructions"}],
            "warning": "",
            "support_phone": "",
            "support_email": "",
        }
        if not product.category_id:
            product.category = Category.objects.for_site(site).filter(is_deleted=False).first()

    def _images(self, site, key: str, label: str, i: int, total: int) -> list:
        """Write this variation's own labelled images to media storage and
        return their URLs. Re-running overwrites them (same paths)."""
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        slug = re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")[:80] or f"v{i + 1}"
        hue = i / max(total, 1)
        urls = []
        for k in range(IMAGES_PER_VARIATION):
            path = f"sites/{site.id}/variation-test/{slug}-{k + 1}.svg"
            if default_storage.exists(path):
                default_storage.delete(path)
            saved = default_storage.save(path, ContentFile(_svg(label, i + 1, k, hue)))
            urls.append(default_storage.url(saved))
        return urls

    def _context(self, site) -> dict:
        """What the per-variation choices are picked from: this store's
        categories, the badges the product form offers (minus the ones that
        lock the return policy / warranty, which would override the test
        values) and the bundled demo videos."""
        ctx = {"categories": [], "badges": [], "videos": []}
        try:
            from commerce.models import Category

            ctx["categories"] = [
                str(pk) for pk in Category.objects.for_site(site).filter(is_deleted=False).order_by("name").values_list("pk", flat=True)
            ]
        except Exception:
            pass
        try:
            from commerce import trust_badges

            ctx["badges"] = [
                b["name"] for b in trust_badges.options(site)
                if not trust_badges.is_non_returnable(b["name"]) and not trust_badges.is_warranty(b["name"])
            ]
        except Exception:
            pass
        try:
            from storefront.demo_media import demo_hero_video, demo_reel_videos

            ctx["videos"] = list(demo_reel_videos()) + [demo_hero_video()]
        except Exception:
            pass
        return ctx

    def _combos(self, site, product, axes, *, inherit_last: bool, all_inherit: bool) -> list:
        from commerce.variant_profiles import clean_profile

        ctx = self._context(site)

        existing = {}
        for c in product.variant_combinations or []:
            if isinstance(c, dict):
                existing[str(c.get("key") or "")] = c
        rows = _cartesian(axes)
        base_sku = (product.sku or TEST_SKU).strip() or TEST_SKU
        base_title = product.title or TEST_TITLE
        out = []
        for i, attrs in enumerate(rows):
            key = "__".join(f"{a['name']}-{a['value']}" for a in attrs)
            label = " / ".join(a["value"] for a in attrs)
            old = existing.get(key, {})
            price = 499 + 100 * i
            colour = next((a["value"] for a in attrs if a["name"].lower() in ("color", "colour")), "")
            row = {
                "key": key,
                "attributes": attrs,
                "sku": str(old.get("sku") or "").strip() or "-".join([base_sku] + [_sku_part(a["value"]) for a in attrs]),
                "mrp": float(price + 300 + 7 * i),
                "price": float(price),
                "inventory": _stock_for(i),
                "default_selected": i == 0,
                "images": self._images(site, key, label, i, len(rows)),
                # One dot per colour value on the page, so variations sharing
                # a colour share it (the product form keeps them in step too).
                "swatch": _COLOURS.get(colour.strip().lower(), str(old.get("swatch") or "")),
            }
            last = i == len(rows) - 1
            if not all_inherit and not (inherit_last and last and len(rows) > 1):
                row["profile"] = clean_profile(_profile(i, label, attrs, base_title, ctx))
            out.append(row)
        return out

    def _collab(self, site, product, handle):
        from commerce.models import Collaboration

        collabs = list(Collaboration.objects.for_site(site))
        if handle:
            match = [c for c in collabs if handle in (c.handle, c.subdomain)]
            if not match:
                raise CommandError(f"No collaborator '{handle}' in this store.")
            for c in match:
                c.products.add(product)
            return f"Added to {handle}'s catalogue."
        specific = [c.handle for c in collabs if c.product_scope == Collaboration.Scope.SPECIFIC]
        if specific:
            return ("These collaborators sell specific products only and won't show it until added "
                    f"(re-run with --collab <handle>): {', '.join(specific)}")
        return ""

    def _remove(self, site):
        from commerce.models import Product

        qs = Product.objects.for_site(site).filter(sku=TEST_SKU, is_deleted=False)
        n = 0
        for p in qs:
            p.soft_delete()  # same as deleting it from the dashboard
            n += 1
        self.stdout.write(self.style.SUCCESS(f"Removed {n} test product(s) from '{site.slug}'."))

    def _report(self, site, product, combos, collab_note):
        from core import links

        w = self.stdout.write
        w(self.style.SUCCESS(f"\n'{product.title}' in store '{site.slug}' now has {len(combos)} test variations:\n"))
        for i, c in enumerate(combos, 1):
            label = " / ".join(a["value"] for a in c["attributes"])
            state = "OUT OF STOCK" if c["inventory"] <= 0 else (
                f"only {c['inventory']} left" if c["inventory"] <= (product.low_stock_threshold or 0) else f"{c['inventory']} in stock")
            own = "own details" if c.get("profile") else "inherits MAIN details"
            dflt = "  [default]" if c.get("default_selected") else ""
            w(f"  {i:>2}. {label:<22} SKU {c['sku']:<24} Rs {c['price']:.2f} (MRP {c['mrp']:.2f})  {state:<14} {len(c['images'])} img  {own}{dflt}")
        path = f"products/{product.slug}/"
        w("")
        try:
            w(f"Store product page:  {site.storefront_url()}{path}")
        except Exception:
            w(f"Store product page:  /{path} on the store")
        if settings.DEBUG:
            try:
                from core.hosts import Surface, dev_prefix_for

                prefix = dev_prefix_for(Surface.STOREFRONT)
                if prefix:
                    w(f"Local dev (one host): http://127.0.0.1:8000{prefix}/{site.slug}/{path}")
            except Exception:
                pass
        try:
            from commerce.models import Collaboration

            for c in Collaboration.objects.for_site(site):
                sub = c.subdomain or c.handle
                if sub:
                    w(f"myugcstore ({c.handle}):  {links.referral_store_origin(sub)}/{path}")
        except Exception:
            pass
        if collab_note:
            w(self.style.WARNING(collab_note))
        w("Dashboard: Products > edit this product to see the numbered variation tabs.")
        w("Remove it later with:  python manage.py seed_variation_test --store " + site.slug + " --remove")
