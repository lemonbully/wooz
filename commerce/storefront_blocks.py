"""First-party *live* storefront blocks: per-request, product-aware app output.

Compiled marketplace fragments (``SiteAppRender``) are the same for every page
of a store. Some apps need the page's own data, like Quantity Discounts on a
product page. Those render here, from ``commerce`` data only, at request time,
through the same theme-contract markers (``{% app_slot "<slot>" %}``) as every
other app, so they land in the right place and wear the theme's tokens.

The marketplace slot tags call :func:`render` and :func:`wanted`; each provider
is guarded there, so a failing block renders nothing and never breaks a page.

A provider is ``(app_slug, slot, active(site) -> bool, render(context, site) -> str | None,
assets: tuple[str, ...])``. ``assets`` are static JS paths printed once at the
end of the page when the block rendered.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger("onewooz.storefront")


@dataclass(frozen=True)
class LiveBlock:
    app_slug: str
    slot: str
    active: Callable
    render: Callable
    assets: tuple = ()
    #: False for blocks that print no UI (config / behaviour only): no
    #: wrapper, no app stylesheet.
    visual: bool = True


# --------------------------------------------------------------------------
# Quantity Discounts (non-visual: the design's own price changes in place)
# --------------------------------------------------------------------------
def _qd_active(site) -> bool:
    from commerce import quantity_pricing as qp

    return bool(qp.is_live(site) and qp._rules(site))


def _unwrap(product):
    """The storefront hands templates a ``ProductProxy``; pricing wants the model."""
    inner = getattr(product, "_p", None)
    return inner if inner is not None and getattr(inner, "_meta", None) is not None else product


def _qd_render(context, site) -> str | None:
    """A hidden config only. static/js/marketplace/ow-qty-pricing.js reads it
    and writes the tier price into the design's own price elements
    ([data-ow-price]) whenever the shopper changes the quantity."""
    from django.utils.safestring import mark_safe

    from commerce import quantity_pricing as qp

    product = _unwrap(context.get("product"))
    if product is None or getattr(product, "_meta", None) is None:
        return None
    cfg = qp.pdp_config(site, product, base_price=product.price)
    if cfg is None:
        return None
    # Escaped like Django's json_script (no <, > or & left, so nothing in it
    # can close the tag); not HTML-escaped, since script text is not decoded.
    raw = json.dumps(cfg).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return mark_safe('<script type="application/json" data-ow-qd-config>' + raw + "</script>")


REGISTRY: tuple[LiveBlock, ...] = (
    LiveBlock(
        app_slug="quantity-discounts",
        slot="pdp.after_variations",
        active=_qd_active,
        render=_qd_render,
        assets=("js/marketplace/ow-qty-pricing.js",),
        visual=False,
    ),
)


# --------------------------------------------------------------------------
# Called by the marketplace slot tags
# --------------------------------------------------------------------------
def wanted(site, paused=frozenset(), *, visual_only: bool = False) -> bool:
    """Whether any live block may render on this store (``visual_only``: one
    that prints UI, so the page needs the app stylesheet)."""
    if site is None:
        return False
    for b in REGISTRY:
        if b.app_slug in paused or (visual_only and not b.visual):
            continue
        try:
            if b.active(site):
                return True
        except Exception:
            logger.warning("live block %s: active() failed", b.app_slug, exc_info=True)
    return False


def render(slot: str, context, site, paused=frozenset()) -> list[tuple[str, str, tuple, bool]]:
    """``[(app_slug, html, assets, visual)]`` for ``slot`` on this request."""
    out = []
    if site is None:
        return out
    for b in REGISTRY:
        if b.slot != slot or b.app_slug in paused:
            continue
        try:
            if not b.active(site):
                continue
            html = b.render(context, site)
        except Exception:
            logger.warning("live block %s failed in %s", b.app_slug, slot, exc_info=True)
            continue
        if html:
            out.append((b.app_slug, str(html), b.assets, b.visual))
    return out
