"""Startup checks that turn the isolation rules into enforced ones.

``marketplace.E001``: no module under ``storefront/`` may import the
marketplace. A storefront reads the compiled ``SiteAppRender`` row through the
``marketplace_slots`` template tags only, so a marketplace outage, bad catalogue
edit or dropped table can never take a store down.

``marketplace.E002``: every theme base template renders the page slots, so an
app placed in a slot shows on every design (theme parity).

``marketplace.E003``: every storefront design follows the **theme contract**, so
any app block fits any design (present or future) with no per-theme work:

* style tokens: the ``--ow-*`` values app blocks are drawn with
  (``static/css/ow-apps.css``), mapped from the design's own look;
* placement markers: ``{% app_slot "<slot>" %}`` at the right spot in the
  product page (blocks render in place, in the design's flow);
* behaviour hooks: ``data-ow-qty`` on the quantity box, ``data-ow-price`` on
  the price per piece, and an ``ow:variant`` event after the design prints a
  new unit price (apps such as Quantity Discounts rewrite the price in place).

A design missing any of it fails startup with a list of what to add.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.core.checks import Error, Warning, register

_IMPORT = re.compile(r"^\s*(from\s+marketplace[\s.]|import\s+marketplace\b)", re.M)

#: Base templates that must carry the slot tags.
THEME_BASES = (
    "storefront/theme_base.html",
    "storefront/template3/_base.html",
    "storefront/template4/_base.html",
)


@register()
def storefront_does_not_import_marketplace(app_configs, **kwargs):
    errors = []
    root = Path(settings.BASE_DIR) / "storefront"
    if not root.exists():
        return errors
    for path in root.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if _IMPORT.search(text):
            errors.append(
                Error(
                    f"{path.relative_to(settings.BASE_DIR)} imports the marketplace.",
                    hint="Storefronts must only read the compiled SiteAppRender through "
                         "{% load marketplace_slots %}. Move the logic to marketplace/compiler.py.",
                    id="marketplace.E001",
                )
            )
    return errors


@register()
def theme_bases_render_app_slots(app_configs, **kwargs):
    warnings = []
    tdir = Path(settings.BASE_DIR) / "templates"
    for rel in THEME_BASES:
        path = tdir / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "app_tail" not in text or "app_head" not in text:
            warnings.append(
                Warning(
                    f"templates/{rel} does not render marketplace app slots.",
                    hint="Add {% load marketplace_slots %}, {% app_head %} in <head> and the page slots + "
                         "{% app_tail %} before </body>.",
                    id="marketplace.E002",
                )
            )
    return warnings


#: Style tokens every design must declare (see static/css/ow-apps.css).
REQUIRED_TOKENS = (
    "--ow-font-body", "--ow-text", "--ow-muted", "--ow-line", "--ow-surface", "--ow-surface-alt",
    "--ow-accent", "--ow-on-accent", "--ow-success", "--ow-radius-card", "--ow-radius-control",
    "--ow-choice-radius", "--ow-choice-bg", "--ow-choice-border", "--ow-choice-active-bg",
    "--ow-choice-active-fg", "--ow-choice-active-border",
)
#: Product-page markers and hooks every design must carry.
REQUIRED_PDP = (
    ('app_slot "pdp.after_variations"', 'the {% app_slot "pdp.after_variations" %} marker right below the variations'),
    ("data-ow-qty", "data-ow-qty on the quantity box(es)"),
    ("data-ow-price", "data-ow-price on each element whose whole text is the price per piece"),
    ("ow:variant", 'an "ow:variant" event (detail.price), fired after the design prints its price'),
)
_EXTENDS = re.compile(r"""{%\s*extends\s+["']([^"']+)["']\s*%}""")


def _chain_text(tdir: Path, rel: str, seen=None) -> str:
    """A template's text plus every template it extends (within templates/)."""
    seen = seen or set()
    if rel in seen:
        return ""
    seen.add(rel)
    path = tdir / rel
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore")
    m = _EXTENDS.search(text)
    if m:
        text += "\n" + _chain_text(tdir, m.group(1), seen)
    return text


@register()
def themes_follow_contract(app_configs, **kwargs):
    errors = []
    try:
        from tenancy.models import Site

        theme_dirs = [value for value, _label in Site.Theme.choices]
    except Exception:
        return errors
    tdir = Path(settings.BASE_DIR) / "templates"
    for d in theme_dirs:
        folder = tdir / "storefront" / d
        if not folder.exists():
            continue
        pdp_rel = next((f"storefront/{d}/{n}" for n in ("product.html", "product_detail.html")
                        if (folder / n).exists()), None)
        if pdp_rel is None:
            continue
        text = _chain_text(tdir, pdp_rel)
        css = ""
        assets = folder / "assets"
        if assets.exists():
            for path in assets.rglob("*.css"):
                css += path.read_text(encoding="utf-8", errors="ignore")
        missing = [label for needle, label in REQUIRED_PDP if needle not in text]
        tokens = [t for t in REQUIRED_TOKENS if t + ":" not in (text + css).replace(" :", ":")]
        if tokens:
            missing.append("style tokens " + ", ".join(tokens))
        if missing:
            errors.append(
                Error(
                    f"Storefront design '{d}' does not follow the theme contract: missing " + "; ".join(missing) + ".",
                    hint="See MARKETPLACE_PLAN.md, 'Theme contract'. Declare the --ow-* tokens (in the base "
                         "template's :root or the design's CSS), place the slot markers, and add the hooks.",
                    id="marketplace.E003",
                )
            )
    return errors
