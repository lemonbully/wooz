"""Readable colours for the homepage hero, whichever hero style is chosen.

Each banner can set its own title / description / accent / button colours.
Left blank, a colour is **auto**: worked out from what the text actually sits
on in the chosen style, so a store always shows legible copy:

* ``style3`` (split hero): the text sits on the solid panel colour
  (``hero_style3_left_bg``), so dark text on a light panel and light text on a
  dark one.
* ``style1`` / ``style2``: the text sits on the banner image or video, behind
  the banner's overlay tint. A photo's brightness is unknown, so the estimate
  is a mid-tone photo with the overlay blended over it: a dark (default black)
  overlay gives light text, a strong light overlay gives dark text.

A colour the owner *did* set is kept unless it is unreadable on a background
we know for sure (the split hero's solid panel), e.g. white text carried over
from a full-image style onto a cream panel. Then the auto colour is used.

The editor's live banner preview mirrors these rules (``heroAuto`` in
``templates/store/editor.html``); keep the two in step.
"""

from __future__ import annotations

import re

#: The split hero's panel colour when the owner has not picked one.
DEFAULT_PANEL = "#F5F1EA"
#: What a photo behind the text is assumed to look like (mid grey).
_ASSUMED_PHOTO = (128, 128, 128)
#: Overlay slider default for a banner saved before the slider existed.
_DEFAULT_OVERLAY = 5
#: The slider's 0–10 maps onto 0–0.8 opacity (see banner_overlay_opacity).
_MAX_OVERLAY = 0.8

# Light text set (for dark backgrounds) and dark text set (for light ones).
_ON_DARK = {
    "title": "#ffffff",
    "description": "#e5e7eb",
    "accent": "#d1d5db",
    "button_bg": "#ffffff",
    "button_text": "#111827",
}
_ON_LIGHT = {
    "title": "#111827",
    "description": "#4b5563",
    "accent": "#6b7280",
    "button_bg": "#111827",
    "button_text": "#ffffff",
}
#: Banner field → palette key.
FIELD_ROLES = {
    "title_color": "title",
    "description_color": "description",
    "button_bg_color": "button_bg",
    "button_text_color": "button_text",
}
#: Text below this contrast ratio on a known solid background is replaced.
_MIN_CONTRAST = 3.0

_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def parse_hex(value):
    """``"#abc"`` / ``"#aabbcc"`` → ``(r, g, b)``, or ``None``."""
    m = _HEX.match(str(value or "").strip())
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _channel(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb) -> float:
    """WCAG relative luminance (0 black … 1 white)."""
    r, g, b = rgb
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(a, b) -> float:
    """WCAG contrast ratio between two ``(r, g, b)`` colours (1 … 21)."""
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def overlay_alpha(value) -> float:
    """The banner overlay slider (0–10, blank = default) as a 0–0.8 opacity."""
    if value is None or str(value).strip() == "":
        value = _DEFAULT_OVERLAY
    try:
        v = float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0
    if v <= 0:
        return 0.0
    level = min(v, 100.0) / 10.0 if v > 10 else v
    return max(0.0, min(level, 10.0)) / 10.0 * _MAX_OVERLAY


def _mix(base, top, alpha):
    return tuple(round(b * (1 - alpha) + t * alpha) for b, t in zip(base, top))


def text_background(banner, content):
    """``(rgb, certain)``: the colour the hero copy sits on, and whether it is
    a known solid colour (the split panel) rather than an estimate."""
    content = content if isinstance(content, dict) else {}
    banner = banner if isinstance(banner, dict) else {}
    style = str(content.get("hero_banner_style") or "style1")
    if style == "style3":
        return parse_hex(content.get("hero_style3_left_bg")) or parse_hex(DEFAULT_PANEL), True
    overlay = parse_hex(banner.get("overlay_color")) or (0, 0, 0)
    return _mix(_ASSUMED_PHOTO, overlay, overlay_alpha(banner.get("overlay_opacity"))), False


def auto_palette(banner, content) -> dict:
    """The auto colour set for this banner in the chosen style."""
    bg, _certain = text_background(banner, content)
    on_dark = contrast((255, 255, 255), bg) >= contrast((17, 24, 39), bg)
    # Text over a photo stays light unless a strong light overlay makes the
    # backdrop clearly pale: a photo's own brightness is never known.
    style = str((content or {}).get("hero_banner_style") or "style1")
    if style != "style3" and luminance(bg) < 0.45:
        on_dark = True
    return dict(_ON_DARK if on_dark else _ON_LIGHT)


def resolve(banner, content, role: str) -> str:
    """The colour to render for ``role`` (title / description / accent /
    button_bg / button_text) of ``banner``: the owner's colour (or the theme's),
    else the auto one. An owner/theme text colour unreadable on a known solid
    background falls back to auto too."""
    banner = banner if isinstance(banner, dict) else {}
    content = content if isinstance(content, dict) else {}
    auto = auto_palette(banner, content)
    field = {v: k for k, v in FIELD_ROLES.items()}.get(role)
    theme = ((content.get("theme") or {}).get("colors") or {}) if isinstance(content.get("theme"), dict) else {}
    theme_key = {
        "title": "bannerTitle", "description": "bannerDescription",
        "button_bg": "bannerButtonBg", "button_text": "bannerButtonText",
    }.get(role)
    chosen = ""
    if field:
        chosen = str(banner.get(field) or "").strip()
    if role == "accent":
        # The accent line follows the title colour when one is set.
        chosen = str(banner.get("title_color") or "").strip()
    if not chosen and theme_key:
        chosen = str(theme.get(theme_key) or "").strip()
    rgb = parse_hex(chosen)
    if rgb is None:
        return auto[role]

    if role == "button_text":
        # Button label must read on the button itself.
        btn = parse_hex(resolve(banner, content, "button_bg"))
        if btn and contrast(rgb, btn) < _MIN_CONTRAST:
            return _best_on(btn)
        return chosen
    if role in ("title", "description", "accent"):
        bg, certain = text_background(banner, content)
        if certain and contrast(rgb, bg) < _MIN_CONTRAST:
            return auto[role]
    return chosen


def _best_on(bg) -> str:
    return "#ffffff" if contrast((255, 255, 255), bg) >= contrast((17, 24, 39), bg) else "#111827"
