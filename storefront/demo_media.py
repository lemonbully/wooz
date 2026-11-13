"""Reference/demo media for storefront previews and the content editor.

A brand-new store  or any section an owner has not filled in yet  should still
*look* like a finished storefront, so the owner can see what belongs where and
what kind of imagery each slot expects. These are neutral stock photographs used
purely as a visual fallback:

* they are never written into ``Site.content`` (a blank field stays blank, so the
  owner's real content is never polluted);
* on the storefront they only stand in where a real image is absent;
* in the editor the same picture appears, dimmed and badged "Example", under each
  image field  so the owner sees exactly where that image uploads from.

Most images are served from ``picsum.photos`` (real photographs, deterministic
by seed) over HTTPS, which every surface's CSP admits (``CSP_IMG_SRC`` includes
``https:``). Several slots ship their own bundled assets instead  served
same-origin (``/static/…`` via WhiteNoise), so ``'self'`` in ``img-src`` /
``media-src`` admits them:

* the **product** cards fall back to the curated ``p1``–``p4`` photos, so a
  brand-new store's product rows read as a real catalogue;
* the **category** tiles fall back to ``c1``–``c4`` (six entries, two repeated)
  so the home page's category row is full on a fresh store;
* the **about** section falls back to ``about_img.webp``;
* the **hero banner** falls back to a muted, looping demo *video* (``v.mp4``) 
  possible precisely because it is same-origin (an external ``<video>`` would be
  blocked by the strict ``media-src``).

Change a URL/path here to change that demo media everywhere at once.
"""

from __future__ import annotations


def _pic(seed: str, w: int, h: int) -> str:
    # Deterministic real photo for a given seed; stable across renders so the
    # storefront preview and the editor show the exact same picture per slot.
    return f"https://picsum.photos/seed/{seed}/{w}/{h}"


def _static(path: str) -> str:
    # Resolve a bundled asset to its served URL (hashed name under
    # ManifestStaticFilesStorage in prod, plain path in dev). Imported lazily so
    # this module is import-safe before the staticfiles machinery is ready.
    from django.templatetags.static import static

    return static(path)


# Bundled demo assets under ``static/storefront/`` (served same-origin).
_PRODUCT_DEMO_FILES = [
    "storefront/p1.webp",
    "storefront/p2.webp",
    "storefront/p3.webp",
    "storefront/p4.webp",
]
_HERO_VIDEO_FILE = "storefront/v.mp4"
_REEL_VIDEO_FILES = [f"storefront/v{i}.mp4" for i in range(1, 7)]

# Bundled demo assets under ``static/img/`` (served same-origin).
_ABOUT_DEMO_FILE = "img/about_img.webp"

# Four curated category photos (1000×1000, product on a light ground). Two are
# repeated so a fresh store's category row always fills at least six tiles;
# ``demo_image`` wraps with modulo, so extra tiles keep cycling this list.
_CATEGORY_DEMO_FILES = [
    "img/c1.webp",
    "img/c2.webp",
    "img/c3.webp",
    "img/c4.webp",
    "img/c1.webp",
    "img/c2.webp",
]


def demo_products() -> list[str]:
    """The bundled p1–p4 demo product photos, as served URLs."""
    return [_static(p) for p in _PRODUCT_DEMO_FILES]


def demo_about() -> str:
    """The bundled about-section reference photo, as a served URL."""
    return _static(_ABOUT_DEMO_FILE)


def demo_categories() -> list[str]:
    """The bundled c1–c4 category reference photos (six entries), as URLs."""
    return [_static(p) for p in _CATEGORY_DEMO_FILES]


def demo_hero_video() -> str:
    """The bundled looping demo hero video (``v.mp4``), as a served URL."""
    return _static(_HERO_VIDEO_FILE)


def demo_reel_videos() -> list[str]:
    """The bundled v1–v6 demo reel videos, as served URLs.

    Used by the reels strip's no-media fallback so a fresh store shows real,
    autoplaying short videos (same-origin, so ``media-src 'self'`` admits them).
    """
    return [_static(p) for p in _REEL_VIDEO_FILES]


# Single curated image per purpose (landscape/portrait sized to how it renders).
DEMO_IMAGES: dict[str, str] = {
    "hero": _pic("onewooz-hero", 1600, 700),
    "hero_mobile": _pic("onewooz-hero-mobile", 800, 1200),
    # "about" is served from a bundled static asset, not picsum  see
    # ``demo_about()`` and the special case in ``demo_image``.
    "about_video": _pic("onewooz-about-video", 1000, 720),
    "editorial": _pic("onewooz-editorial", 1600, 640),
    "cowork1": _pic("onewooz-cowork1", 900, 680),
    "cowork2": _pic("onewooz-cowork2", 900, 680),
    "avatar": _pic("onewooz-avatar", 240, 240),
    "certificate": _pic("onewooz-certificate", 480, 600),
    "blog": _pic("onewooz-blog", 900, 560),
    "logo": _pic("onewooz-logo", 320, 120),
    "favicon": _pic("onewooz-favicon", 128, 128),
    "og": _pic("onewooz-og", 1200, 630),
    "page": _pic("onewooz-page", 1400, 700),
}

# Several distinct images per purpose, so a grid/row of demo cards isn't one
# picture repeated. The template tag cycles these by index.
DEMO_SETS: dict[str, list[str]] = {
    # "product" (p1–p4) and "category" (c1–c4) are served from bundled static
    # assets, not picsum  see ``demo_products()`` / ``demo_categories()`` and
    # the special cases in ``demo_image``.
    # Vertical stills for the reels / short-video strip (last home section).
    "reel": [_pic(f"onewooz-reel-{i}", 420, 720) for i in range(1, 7)],
    "avatar": [_pic(f"onewooz-avatar-{i}", 240, 240) for i in range(1, 7)],
}


def demo_image(key: str, index: int = 0) -> str:
    """Return a demo image URL for ``key``.

    ``key`` may name a single image (``DEMO_IMAGES``) or a set that varies by
    ``index`` (``DEMO_SETS``). Unknown keys fall back to the generic page image
    so a caller never gets an empty string.
    """
    if key == "about":
        return demo_about()
    bundled = {"product": demo_products, "category": demo_categories}.get(key)
    if bundled is not None:
        items = bundled()
        try:
            i = int(index)
        except (TypeError, ValueError):
            i = 0
        return items[i % len(items)] if items else ""
    if key in DEMO_SETS:
        items = DEMO_SETS[key]
        try:
            i = int(index)
        except (TypeError, ValueError):
            i = 0
        return items[i % len(items)] if items else ""
    return DEMO_IMAGES.get(key, DEMO_IMAGES["page"])


def demo_reels() -> list[str]:
    """The vertical stills for the reels/short-video strip."""
    return list(DEMO_SETS["reel"])


def editor_demo_map() -> dict[str, str]:
    """A flat ``{demo-key: url}`` map for the editor's ghost previews.

    One representative URL per key (the first of a set), so a blank image field
    in the editor can show the same picture the storefront would fall back to.
    """
    out = dict(DEMO_IMAGES)
    for key, items in DEMO_SETS.items():
        if items:
            out[key] = items[0]
    # Product / category ghosts and the about photo use bundled assets, not
    # picsum, so they must be filled in after the DEMO_SETS pass above.
    products = demo_products()
    if products:
        out["product"] = products[0]
    categories = demo_categories()
    if categories:
        out["category"] = categories[0]
    out["about"] = demo_about()
    return out
