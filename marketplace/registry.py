"""First-party apps that must exist, created automatically (no admin step).

Same pattern as ``billing.website_templates.sync``: rows are created when
missing and never edited afterwards (staff own them once they exist), except
for code-owned facts that keep the system safe (the system-default item stays
the free, published fallback).
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.core.cache import cache
from django.db import transaction

logger = logging.getLogger("onewooz.marketplace")

TEMPLATES_SLUG = "templates"

#: Apps the platform needs. Everything else is created by staff in the admin.
SYSTEM_APPS: tuple[dict, ...] = (
    {
        "slug": TEMPLATES_SLUG,
        "name": "OneWooz Templates",
        "tagline": "Storefront designs for your store. Add one to your library, switch any time.",
        "description": (
            "<p>Every OneWooz storefront design in one place. Add a design to your library, "
            "preview it with your own products and content, and switch your store to it from "
            "Store Settings whenever you like. Your products, pages, SEO and settings carry over "
            "as they are.</p>"
        ),
        "category": "design",
        "icon_key": "layout",
        "module": "storefront_designs",
        "kind": "system",
        "is_default": True,
        "pricing": "free",
        "status": "published",
        "order": 1,
        "featured": True,
        # No dashboard placement: an app without one gets its own sidebar link,
        # which opens the module's home page (the design library).
        "placements": (),
    },
    {
        "slug": "quantity-discounts",
        "name": "Quantity Discounts",
        "tagline": "The price per piece drops as shoppers pick a bigger quantity, the way Amazon does it.",
        "description": (
            "<p>Reward bigger orders the way the large marketplaces do: <strong>buy 2 or more, save 5%; "
            "buy 5 or more, save 10%</strong>. When a shopper changes the quantity on a product page, the "
            "price per piece changes to the tier price right where your design shows it. No extra boxes "
            "or banners on your store.</p>"
            "<ul><li>Percentage off, amount off, or a fixed price per piece, up to 5 tiers, up to 100 units.</li>"
            "<li>Rules for every product, for categories, or for single products; the most specific wins.</li>"
            "<li>You decide what happens above your last tier: keep the discount, stop at a limit, or tell "
            "big buyers to message you on WhatsApp or read your own note.</li>"
            "<li>The price drops on the line itself, so the cart, GST, invoices and shipping labels all show "
            "the price the shopper paid.</li>"
            "<li>Works with D2C and B2B stores and with every storefront design.</li></ul>"
        ),
        "category": "sales",
        "icon_key": "tag",
        "accent": "#2f9e44",
        "module": "quantity_discounts",
        "kind": "standard",
        "is_default": False,
        "pricing": "free",
        "status": "published",
        "order": 5,
        "featured": True,
        "placements": (
            {
                "point": "sidebar", "nav_group": "Grow", "order": 40, "label": "Quantity discounts",
                "summary": "Tiered prices that drop as shoppers buy more.",
                "icon_key": "tag", "page_kind": "module_page", "page_key": "rules",
            },
        ),
    },
    {
        "slug": "magic-seo",
        "name": "Magic SEO",
        "tagline": "One click writes the SEO for every page of your store: titles, descriptions, keywords and share cards.",
        "description": (
            "<p>Click the pencil in <strong>Edit content &gt; SEO</strong> and Magic SEO writes the search and "
            "social details of every page of your store, from your own products, categories, blog posts, "
            "policies and pages.</p>"
            "<ul><li>Every field on every page: meta title, meta description, keywords, Open Graph and "
            "Twitter titles, descriptions and images, and the canonical address.</li>"
            "<li>Written from what each page really says, so nothing is invented: no fake offers, prices "
            "or reviews.</li>"
            "<li>Tell it your focus keywords, who you sell to, where, and in what tone and language, "
            "or leave it to your store's content.</li>"
            "<li>Fill only the empty fields, or rewrite everything. One click undoes the last run.</li>"
            "<li>Titles sized for Google results, descriptions sized for search snippets and share "
            "previews on WhatsApp, Facebook and X.</li>"
            "<li>Works with D2C and B2B stores and every storefront design.</li></ul>"
        ),
        "category": "marketing",
        "icon_key": "pencil",
        "accent": "#4b2bf5",
        "module": "magic_seo",
        "kind": "standard",
        "is_default": False,
        # Sold on billing cycles (per-month prices). Created as a draft: set
        # the prices in the console or Django admin, then publish.
        "pricing": "cycles",
        "price_monthly": Decimal("299.00"),
        "price_quarterly": Decimal("269.00"),
        "price_half_yearly": Decimal("249.00"),
        "price_yearly": Decimal("199.00"),
        "status": "draft",
        "order": 6,
        "featured": True,
        # No dashboard placement: its own "Apps" link opens Edit content > SEO
        # (MagicSeo.home), where the pencil is.
        "placements": (),
    },
)

_SYNC_KEY = "mkt:sync:v6"


def sync(force: bool = False) -> None:
    """Create missing system apps and their items. Cheap after the first run."""
    if not force and cache.get(_SYNC_KEY):
        return
    try:
        from .models import App, AppDashboardPlacement
    except Exception:
        return
    try:
        for spec in SYSTEM_APPS:
            with transaction.atomic():
                app = App.objects.filter(slug=spec["slug"]).first()
                if app is None:
                    fields = {k: v for k, v in spec.items() if k not in ("placements",)}
                    app = App(**fields)
                    app.save()
                    for p in spec.get("placements", ()):
                        AppDashboardPlacement.objects.create(app=app, **p)
                elif spec["kind"] == App.Kind.SYSTEM and (app.kind != App.Kind.SYSTEM or not app.is_default):
                    # Pre-added for every owner and never removable, whatever
                    # an admin save did to the row.
                    app.kind, app.is_default = App.Kind.SYSTEM, True
                    app.save()
                _sync_items(app)
        cache.set(_SYNC_KEY, 1, 300)
    except Exception:
        # Tables not migrated yet, or a transient DB error: callers fall back.
        logger.warning("marketplace registry sync skipped", exc_info=True)


def _sync_items(app) -> None:
    from . import modules
    from .models import AppItem

    module = modules.get(app.module)
    if module is None:
        return
    existing = {i.key: i for i in AppItem.objects.filter(app=app)}
    legacy = _legacy_templates() if app.module == "storefront_designs" else {}
    for spec in module.items():
        row = existing.get(spec["key"])
        if row is not None:
            # Code-owned safety facts only.
            if spec.get("system_default") and not row.is_system_default:
                row.is_system_default = True
                row.save()
            continue
        item = AppItem(
            app=app,
            key=spec["key"],
            name=spec["name"],
            description=spec.get("description", ""),
            order=spec.get("order", 100),
            status=AppItem.Status.PUBLISHED if spec.get("published") else AppItem.Status.DRAFT,
            is_system_default=bool(spec.get("system_default")),
        )
        wt = legacy.get(spec["key"])
        if wt is not None:
            # First sync copies the admin's existing settings for this design.
            item.name = wt.name or item.name
            item.description = wt.description or item.description
            if wt.thumbnail:
                item.thumbnail = wt.thumbnail.name
            item.fits = wt.template_type or "both"
            if wt.is_priced:
                item.pricing = AppItem.Pricing.PRICED
                item.price = wt.price or Decimal("0")
            item.licence_type = "yearly" if wt.is_yearly else "permanent"
            if spec.get("published") and not wt.is_available:
                item.status = AppItem.Status.DRAFT
            item.order = wt.order or item.order
        item.save()


def _legacy_templates() -> dict:
    try:
        from billing.models import WebsiteTemplate

        return {t.key: t for t in WebsiteTemplate.objects.all()}
    except Exception:
        return {}


def templates_app():
    """The OneWooz Templates app row (synced), or ``None`` before migration."""
    sync()
    try:
        from .models import App

        return App.objects.filter(slug=TEMPLATES_SLUG).first()
    except Exception:
        return None
