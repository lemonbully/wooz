"""The public catalogue, compiled to plain dicts and cached.

The catalogue is the same for every visitor, so browse pages render from one
cached list keyed by the catalogue version (bumped on every admin save). A warm
browse page costs zero database queries; personal state (installed, owned) is
a second small lookup for signed-in owners only.
"""

from __future__ import annotations

from django.core.cache import cache

from . import api, registry
from .sanitize import clean_html

_TTL = 60 * 30


def _img(field) -> str:
    try:
        return field.url if field else ""
    except Exception:
        return ""


def _price_num(app) -> float:
    """A sortable price: the per-month rate for apps sold on billing cycles
    (their cheapest), else the list price. 0 for included apps."""
    if not app.is_paid:
        return 0.0
    if app.pricing == app.Pricing.CYCLES:
        return float(min(c["per_month"] for c in app.cycle_options()))
    return float(app.price or 0)


def _app_dict(app, *, items: bool = False) -> dict:
    d = {
        "id": str(app.pk),
        "slug": app.slug,
        "name": app.name,
        "tagline": app.tagline,
        "description": clean_html(app.description),
        "icon": _img(app.icon),
        "icon_key": app.icon_key,
        "accent": app.accent or "#4b2bf5",
        "category": app.category,
        "category_label": app.get_category_display(),
        "developer": app.developer,
        "kind": app.kind,
        "is_system": app.is_system,
        "is_default": app.is_default,
        "pricing": app.pricing,
        "pricing_label": app.pricing_label(),
        "is_paid": app.is_paid,
        "price": str(app.price),
        "trial_days": app.trial_days,
        "fits": app.fits,
        "status": app.status,
        "featured": app.featured,
        "support_url": app.support_url,
        "privacy_url": app.privacy_url,
        "has_items": False,
        "module": app.module,
        "is_designs": app.module == "storefront_designs",
        "period_days": app.period_days,
        "price_num": _price_num(app),
        # Billing cycles (pricing "cycles"): what each cycle costs, for the app
        # page's Pricing tab. Plain numbers/strings only (this dict is cached).
        "cycles": [
            {"key": c["key"], "label": c["label"], "months": c["months"], "billed": c["billed"],
             "per_month": f"{c['per_month']:,.0f}" if c["per_month"] == c["per_month"].to_integral_value()
             else f"{c['per_month']:,.2f}",
             "total": f"{c['total']:,.0f}" if c["total"] == c["total"].to_integral_value() else f"{c['total']:,.2f}",
             "save_pct": c["save_pct"]}
            for c in app.cycle_options()
        ],
        "published": app.published_at.date().isoformat() if app.published_at else "",
    }
    if items:
        d["screenshots"] = [{"url": _img(s.image), "caption": s.caption} for s in app.screenshots.all()]
        d["dashboard"] = [
            {"point": p.get_point_display(), "label": p.label, "key": p.point}
            for p in app.dashboard_placements.filter(is_active=True)
        ]
        d["storefront"] = [
            {"label": p.label, "widget": p.get_widget_type_display(), "slot": p.default_slot}
            for p in app.storefront_placements.filter(is_active=True)
        ]
        rows = []
        for it in app.items.all().order_by("order", "name"):
            if not it.is_listed:
                continue
            rows.append(
                {
                    "key": it.key,
                    "name": it.name,
                    "tagline": it.tagline,
                    "description": clean_html(it.description),
                    "thumbnail": _img(it.thumbnail),
                    "preview_url": it.preview_url,
                    "fits": it.fits,
                    "fits_label": it.get_fits_display(),
                    "tags": it.tags(),
                    "is_priced": it.is_priced,
                    "is_yearly": it.is_yearly,
                    "price": str(it.price),
                    "pricing_label": it.pricing_label(),
                    "is_system_default": it.is_system_default,
                }
            )
        d["items"] = rows
        d["has_items"] = bool(rows)
    return d


def app_card(app) -> dict:
    """Plain dict for one App (no items), for pages that already hold the row."""
    return _app_dict(app)


def listing() -> list[dict]:
    """Every published app (cards on the home page)."""
    registry.sync()
    key = f"mkt:catalog:{api.catalogue_version()}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    from .models import App

    rows = [_app_dict(a) for a in App.objects.filter(status=App.Status.PUBLISHED).order_by("order", "name")]
    cache.set(key, rows, _TTL)
    return rows


def detail(slug: str) -> dict | None:
    """One app page (published or unlisted), with items and placements."""
    registry.sync()
    key = f"mkt:app:{slug}:{api.catalogue_version()}"
    cached = cache.get(key)
    if cached is not None:
        return cached or None
    from .models import App

    app = (
        App.objects.filter(slug=slug, status__in=(App.Status.PUBLISHED, App.Status.UNLISTED))
        .prefetch_related("screenshots", "items")
        .first()
    )
    value = _app_dict(app, items=True) if app is not None else {}
    cache.set(key, value, _TTL)
    return value or None


def categories(rows: list[dict]) -> list[tuple[str, str]]:
    seen: dict[str, str] = {}
    for r in rows:
        seen.setdefault(r["category"], r["category_label"])
    return sorted(seen.items(), key=lambda kv: kv[1])
