"""Template filters and tags the ported storefront designs rely on.

Faithful to the reference ``website_filters`` library so the copied templates
work unchanged, with three OneWooz adaptations:

* media URLs stay on this platform's own ``MEDIA_URL``  the reference rewrote
  them onto a hardcoded ``media.1matrix.io`` CDN host;
* ``website_base_path`` returns the storefront root via ``reverse`` so links are
  correct on ``<slug>.onewooz.site`` and under the ``/s/<slug>/`` dev prefix
  alike, instead of the reference's literal ``/website/s/<slug>/``;
* ``media_attrs``/``media_meta`` no longer read a per-asset model that does not
  exist here  they emit a safe ``alt`` and nothing more.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from django import template
from django.conf import settings
from django.template.defaultfilters import stringfilter
from django.urls import NoReverseMatch, reverse
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

register = template.Library()


def _media_url() -> str:
    media_url = getattr(settings, "MEDIA_URL", "/media/") or "/media/"
    if not media_url.startswith("/"):
        media_url = "/" + media_url
    if not media_url.endswith("/"):
        media_url = media_url + "/"
    return media_url


@register.filter(name="schedule_groups")
def schedule_groups(value) -> list:
    """A weekly availability (``[{"day", "start", "end"}]``, as the editor's
    schedule field stores it) as display lines, consecutive days with the same
    hours merged: ``[{"days": "Mon–Fri", "hours": "10:00 AM – 6:00 PM"}, …]``."""
    week = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    def fmt(t: str) -> str:
        try:
            h, m = (int(x) for x in str(t).split(":")[:2])
        except (TypeError, ValueError):
            return str(t or "")
        return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"

    items = []
    for s in value if isinstance(value, (list, tuple)) else []:
        if isinstance(s, dict) and s.get("day") in week and s.get("start") and s.get("end"):
            items.append(s)
    items.sort(key=lambda s: week.index(s["day"]))

    groups: list[dict] = []
    for s in items:
        g = groups[-1] if groups else None
        if (g and g["start"] == s["start"] and g["end"] == s["end"]
                and week.index(g["last"]) == week.index(s["day"]) - 1):
            g["last"] = s["day"]
        else:
            groups.append({"first": s["day"], "last": s["day"], "start": s["start"], "end": s["end"]})
    return [
        {
            "days": g["first"][:3] if g["first"] == g["last"] else f"{g['first'][:3]}–{g['last'][:3]}",
            "hours": f"{fmt(g['start'])} – {fmt(g['end'])}",
        }
        for g in groups
    ]


@register.filter(name="js_json")
def js_json(value):
    """``value`` as a JSON literal safe to drop inside an inline ``<script>``
    (``null`` when absent). ``<``, ``>`` and ``&`` are \\u-escaped, as Django's
    ``json_script`` does, so content can never close the script tag. Use this
    instead of ``|safe`` on a Python value, whose repr is not valid JS for
    ``None``/``True``/``False``."""
    import json

    from storefront.safe import MISSING

    if value is None or value is MISSING:
        return mark_safe("null")
    try:
        out = json.dumps(value, default=str)
    except (TypeError, ValueError):
        return mark_safe("null")
    out = out.replace("<", "\\u003C").replace(">", "\\u003E").replace("&", "\\u0026")
    return mark_safe(out)


@register.filter(name="contact_map")
def contact_map(contact_info) -> dict:
    """``{"embed", "link"}`` for a store's ``contact_info``: the embeddable
    Google Maps URL for the contact page's map frame (whatever form of link the
    owner pasted) and a link that opens it in Google Maps. Empty strings when no
    map is set. Never makes a network request (see ``storefront.maps``)."""
    from storefront.maps import contact_map as _contact_map

    return _contact_map(contact_info)


@register.filter(name="currency_symbol")
def currency_symbol(code) -> str:
    """Map a currency code (INR/USD/EUR) to a display symbol."""
    try:
        c = str(code or "INR").strip().upper()
    except Exception:
        c = "INR"
    return {"INR": "₹", "USD": "$", "EURO": "€", "EUR": "€"}.get(c, "₹")


@register.filter
def getattribute(obj, attr):
    """Dynamic attribute / key access: ``{{ obj|getattribute:"name" }}``."""
    if obj is None or attr is None:
        return None
    if isinstance(obj, dict):
        return obj.get(attr)
    if hasattr(obj, str(attr)):
        return getattr(obj, str(attr))
    return None


@register.filter(name="dict_get")
def dict_get(value, key):
    """Safe, dotted-path lookup over dicts, objects and sequences."""
    try:
        if value is None or key is None:
            return None
        path = str(key).strip()
        if not path:
            return None
        cur = value
        for part in path.split("."):
            if cur is None:
                return None
            if isinstance(cur, dict):
                cur = cur.get(part)
                continue
            if isinstance(cur, (list, tuple)) and part.isdigit():
                idx = int(part)
                cur = cur[idx] if 0 <= idx < len(cur) else None
                continue
            if hasattr(cur, "get") and callable(cur.get):
                try:
                    cur = cur.get(part)
                    continue
                except Exception:
                    pass
            cur = getattr(cur, part, None)
        return cur
    except Exception:
        return None


@register.filter(name="get_item")
def get_item(value, key):
    return dict_get(value, key)


@register.filter(name="index")
def index(sequence, position):
    """``sequence[position]`` or None if out of range/invalid."""
    try:
        return sequence[int(position)]
    except Exception:
        return None


@register.filter(name="split")
@stringfilter
def split(value: str, separator: str = ","):
    try:
        return [part.strip() for part in value.split(separator)]
    except Exception:
        return []


@register.filter(name="split_clean")
def split_clean(value, separator: str = ","):
    """Normalise a comma-separated value into a clean list of items.

    A list/tuple passes through (items stringified + stripped, blanks dropped);
    a string is split on ``separator``. Used for fields the editor stores as a
    comma-joined string (e.g. office_holidays) so the storefront iterates whole
    items instead of individual characters. Never raises  returns ``[]`` on any
    odd input.
    """
    try:
        if isinstance(value, (list, tuple)):
            items = [str(x).strip() for x in value]
        else:
            items = [part.strip() for part in str(value or "").split(separator)]
        return [x for x in items if x]
    except Exception:
        return []


@register.filter(name="is_image_url")
@stringfilter
def is_image_url(value: str) -> bool:
    try:
        return value.lower().strip().endswith(
            (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg")
        )
    except Exception:
        return False


@register.filter(name="contains")
def contains(value, needle) -> bool:
    try:
        if value is None or needle is None:
            return False
        return str(needle) in str(value)
    except Exception:
        return False


@register.filter(name="absolute_media_url")
@stringfilter
def absolute_media_url(value: str) -> str:
    """Resolve a stored media path to a servable URL on this platform."""
    try:
        v = (value or "").strip()
        if not v:
            return v
        lv = v.lower()
        if lv.startswith(("data:", "http://", "https://", "//")):
            return v
        if v.startswith("/"):
            return v
        return _media_url() + v.lstrip("/")
    except Exception:
        return value


@register.filter(name="media_cdn_url")
@stringfilter
def media_cdn_url(value: str) -> str:
    """Normalise a media URL/path. Leaves external video links untouched; keeps
    everything else on this platform's own media URL (no external CDN)."""
    try:
        raw = (value or "").strip()
        if not raw:
            return raw
        lower = raw.lower()
        if lower.startswith("data:"):
            return raw
        if any(m in lower for m in ("youtube.com", "youtu.be", "vimeo.com", "dailymotion.com")):
            return raw
        return absolute_media_url(raw)
    except Exception:
        return value


@register.filter(name="opacity_from_percent")
def opacity_from_percent(value) -> str:
    """Convert a 0–100 percentage to a CSS opacity (0.0–1.0)."""
    try:
        pct = 50.0 if value is None else float(str(value).strip() or "50")
        pct = min(100.0, max(0.0, pct))
        return str(pct / 100.0)
    except Exception:
        return "0.5"


#: The overlay is a *readability tint* over the banner media, never a curtain
#: that hides it. Capping the effective opacity below 1.0 guarantees that an
#: uploaded banner image/video always shows through, even at the maximum slider
#: value  otherwise a darkness of 10 rendered a solid ``opacity: 1`` black box
#: over the media, which read as "my uploaded banner doesn't render at all".
#: The whole 0–10 slider is spread evenly up to this ceiling (each step adds
#: 0.08), so every step from 0 to 10 visibly darkens the tint. (It used to clip
#: at 0.6, which made 6–10 all look identical.)
_MAX_OVERLAY = 0.8

#: The editor slider's default (0–10) for a banner that never set one.
_DEFAULT_OVERLAY = 5


@register.simple_tag(takes_context=True)
def hero_color(context, banner, role):
    """``{% hero_color banner 'title' %}``: the hero colour to render for a
    banner in the chosen hero style. The owner's colour if readable, else an
    automatic one worked out from the background the copy sits on (see
    storefront.hero_colors). Roles: title, description, accent, button_bg,
    button_text."""
    from storefront.hero_colors import resolve

    website = context.get("website")
    content = getattr(website, "content", None) or {}
    try:
        return resolve(banner, content, str(role))
    except Exception:
        return "#111827" if role in ("title", "button_bg") else "#ffffff" if role == "button_text" else "#4b5563"


@register.filter(name="banner_overlay_opacity")
def banner_overlay_opacity(value) -> str:
    """Convert a banner's overlay setting to a CSS opacity (0.0–``_MAX_OVERLAY``).

    The editor's overlay control is a 0–10 slider, mapped linearly onto
    0–``_MAX_OVERLAY`` (0 → none, 5 → 0.4, 10 → 0.8), so every step is visible
    and the darkest setting still leaves the banner showing through the tint.
    Values above 10 are read as legacy 0–100 percentages (50 → slider 5).
    Anything unparseable falls back to no overlay.

    An *unset* value (``None`` / blank: a banner saved before the slider existed)
    uses the slider's default of 5. A real ``0`` is the owner sliding the tint
    all the way off and must render no overlay: templates must therefore NOT
    pipe through ``|default:5`` first, because Django's ``default`` treats 0 as
    empty and turned "0" into the darker 5 (the slider then felt reversed:
    2 looked lighter than 0).
    """
    if value is None or str(value).strip() == "":
        value = _DEFAULT_OVERLAY
    try:
        v = float(str(value).strip())
    except (TypeError, ValueError):
        return "0"
    if v <= 0:
        return "0"
    level = min(v, 100.0) / 10.0 if v > 10 else v  # legacy percent → 0–10
    frac = max(0.0, min(level, 10.0)) / 10.0 * _MAX_OVERLAY
    return ("%.3f" % frac).rstrip("0").rstrip(".") or "0"


#: A transparent 1×1 GIF: what a banner size with no image of its own shows
#: (``img-src`` admits ``data:``), so it never borrows the other size's photo.
_BLANK_PIXEL = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"


def _own(banner, key: str) -> str:
    return str(dict_get(banner, key) or "").strip()


def _mobile_src(banner, mobile_key: str, desktop_key: str) -> str:
    """The phone media: its own; else the desktop one, unless the owner removed
    the phone media in the editor (``<mobile_key>_removed``)."""
    own = _own(banner, mobile_key)
    if own:
        return own
    if dict_get(banner, f"{mobile_key}_removed"):
        return ""
    return _own(banner, desktop_key)


# Hero banners: each screen size shows ITS OWN media. Desktop never borrows the
# phone image/video (a 9:16 picture stretched across a desktop hero, and the
# reason a deleted desktop image appeared to "come back"); a phone may reuse the
# desktop media only while its own slot was never removed. These rules do not
# depend on a "removed" flag for the desktop side, so a slot emptied by an
# older editor (before the flag existed) is honoured too.
@register.filter(name="banner_desktop_src")
def banner_desktop_src(banner) -> str:
    return _own(banner, "image") or _BLANK_PIXEL


@register.filter(name="banner_mobile_src")
def banner_mobile_src(banner) -> str:
    return _mobile_src(banner, "mobile_image", "image") or _BLANK_PIXEL


@register.filter(name="banner_video_desktop_src")
def banner_video_desktop_src(banner) -> str:
    return _own(banner, "video")


@register.filter(name="banner_video_mobile_src")
def banner_video_mobile_src(banner) -> str:
    return _mobile_src(banner, "mobile_video", "video")


@register.filter(name="about_video_poster")
def about_video_poster(content) -> str:
    """The About video's cover: its own thumbnail, else the About image, unless
    the owner removed the thumbnail in the editor (then no cover)."""
    thumb = _own(content, "about_video_thumbnail")
    if thumb:
        return thumb
    if dict_get(content, "about_video_thumbnail_removed"):
        return ""
    return _own(content, "about_image")


@register.simple_tag(takes_context=True, name="newsletter_popup_config")
def newsletter_popup_config(context, website=None):
    """The storefront newsletter popup's settings, or ``None`` when it's off
    (see :mod:`storefront.newsletter_popup`)."""
    site = website or context.get("website")
    if site is None:
        return None
    try:
        from storefront.newsletter_popup import config

        return config(site, context.get("request"))
    except Exception:
        return None


@register.filter(name="trust_badge_label")
def trust_badge_label(name):
    """A trust badge's name as shoppers see it ("1-Year Warranty",
    "Non-Returnable"), whatever form it was stored in."""
    try:
        from commerce.trust_badges import display_name

        return display_name(name)
    except Exception:
        return name


@register.filter(name="return_policy_badge")
def return_policy_badge(product):
    """The product page's return-policy trust badge, from the product's own
    return policy (or a picked variation's): ``{"kind", "label", "title"}``
    for a returnable or replacement-only product, else ``None``. The badge
    links to the store's refund / return policy page."""
    try:
        rt = str(getattr(product, "return_type", "") or "")
        if rt not in ("returnable", "replacement"):
            return None
        # The owner already shows a returns / replacement badge of their own:
        # that one becomes the link, so the row never says it twice.
        if any(is_return_badge(b) for b in (getattr(product, "trusted_icons", None) or [])):
            return None
        try:
            days = int(getattr(product, "return_days", None) or 0)
        except (TypeError, ValueError):
            days = 0
        noun = "Replacement" if rt == "replacement" else "Return"
        label = f"{days}-Day {noun}" if days > 0 else ("Replacement Only" if rt == "replacement" else "Returnable")
        if days > 0:
            unit = "day" if days == 1 else "days"
            title = (
                f"Replacement within {days} {unit}" if rt == "replacement" else f"Returnable within {days} {unit}"
            ) + ". Read the return and refund policy."
        else:
            title = "Read the return and refund policy."
        return {"kind": rt, "label": label, "title": title}
    except Exception:
        return None


@register.filter(name="is_return_badge")
def is_return_badge(name) -> bool:
    """A store-chosen badge that talks about returns or replacements (not a
    "Non returnable" one): the product page links it to the refund policy."""
    try:
        from commerce.trust_badges import is_non_returnable, norm

        n = norm(name)
        if not n or is_non_returnable(n):
            return False
        return "return" in n or "replace" in n or "refund" in n
    except Exception:
        return False


@register.simple_tag(name="trust_badges_map")
def trust_badges_map():
    """A ``{badge name: image URL}`` map of the active, admin-managed trust
    badges, so the storefront can render a badge's uploaded icon (image or SVG)
    beside its name. One query; badges without an image are omitted (the product
    page then falls back to its built-in icon for that name)."""
    try:
        from commerce.models import TrustBadge

        out = {}
        for badge in TrustBadge.objects.filter(is_active=True):
            if not badge.image:
                continue
            try:
                out[badge.name] = badge.image.url
            except Exception:
                continue
        return out
    except Exception:
        return {}


@register.simple_tag(name="num_range")
def num_range(start, end=None, step=1):
    """Python ``range`` for template iteration; end exclusive."""
    try:
        s = int(start)
        if end is None:
            s, e = 0, s
        else:
            e = int(end)
        st = int(step) or 1
        return range(s, e, st)
    except Exception:
        return range(0)


@register.simple_tag(name="website_base_path")
def website_base_path(website=None, request=None):
    """The storefront root, correct on the apex host and the dev path prefix.

    ``reverse`` applies the request's script prefix, so this yields ``/`` on a
    real storefront host and ``/s/<slug>/`` in single-host development.
    """
    try:
        return reverse("public_website")
    except NoReverseMatch:
        return "/"


@register.simple_tag(name="announcement_bar")
def announcement_bar(website=None):
    """The announcement bar's statements, mode and marquee runs (see
    ``storefront.announcement.bar_context``), with store links resolved
    against the storefront root."""
    from storefront.announcement import bar_context

    content = getattr(website, "content", None) or {}
    try:
        base = reverse("public_website")
    except NoReverseMatch:
        base = "/"
    return bar_context(content, base)


@register.filter(name="public_href")
def public_href(value, base: str = "/") -> str:
    """Build a public href relative to ``base`` (from ``website_base_path``)."""
    try:
        v = (value or "").strip()
        b = (base or "/").strip() or "/"
        if not b.endswith("/"):
            b += "/"
        if not v:
            return "#"
        lv = v.lower()
        if lv.startswith(("http://", "https://", "mailto:", "tel:", "whatsapp:", "sms:", "data:", "//", "#")):
            return v
        if v == "/":
            return b
        if v.startswith("/"):
            return v
        return b + v.lstrip("/")
    except Exception:
        return value or "#"


@register.simple_tag(name="media_meta")
def media_meta(website, url):
    """No per-asset metadata store on this platform yet.

    Returns a safe mapping (not a bare ``{}``) so ``media_meta … as m`` followed
    by ``m.alt_text`` in a ``|default:`` argument resolves to blank instead of
    raising VariableDoesNotExist.
    """
    from ..safe import SafeDict

    return SafeDict()


@register.simple_tag(name="media_attrs")
def media_attrs(website, url, default_alt=""):
    """Emit a safe ``alt`` attribute for an image."""
    return format_html('alt="{}"', default_alt or "")


@register.simple_tag(name="demo_media")
def demo_media(key, index=0):
    """A reference/demo image URL for a storefront slot that has no real image.

    Used inside ``{% if real_image %}…{% else %}{% demo_media 'hero' %}{% endif %}``
    fallbacks so a brand-new store still looks full. ``index`` cycles the sets
    (categories, products, reels) so a row of demo cards isn't one repeated
    picture. See :mod:`storefront.demo_media`.
    """
    from ..demo_media import demo_image

    return demo_image(str(key or ""), index or 0)


@register.simple_tag(name="demo_reels")
def demo_reels():
    """The list of vertical stills for the reels/short-video strip."""
    from ..demo_media import demo_reels as _reels

    return _reels()


@register.simple_tag(name="demo_hero_video")
def demo_hero_video():
    """The bundled looping demo hero video URL, for the hero's no-media fallback.

    Same-origin (``/static/…``), so the storefront ``media-src 'self'`` admits it.
    """
    from ..demo_media import demo_hero_video as _video

    return _video()


@register.simple_tag(name="demo_reel_videos")
def demo_reel_videos():
    """The bundled v1–v6 demo reel video URLs, for the reels strip's fallback."""
    from ..demo_media import demo_reel_videos as _videos

    return _videos()


@register.simple_tag(name="padded_items")
def padded_items(items, kind, target):
    """Return ``items`` topped up to ``target`` length with demo defaults.

    Pre-render completeness for sections whose stored content may hold fewer
    entries than the design shows (e.g. a store seeded before the defaults grew):
    the store's own items are kept, and demo defaults for ``kind`` ("why" /
    "faq") that aren't already present are appended until ``target`` is reached.
    A store that already has enough items is returned unchanged.
    """
    from ..content_seed import DEFAULT_FAQS, DEFAULT_WHY_CHOOSE_US

    out = [x for x in (items or []) if isinstance(x, dict)]
    try:
        target = int(target)
    except (TypeError, ValueError):
        return out
    if len(out) >= target:
        return out

    if kind == "why":
        defaults, key = DEFAULT_WHY_CHOOSE_US, "title"
    elif kind == "faq":
        defaults, key = DEFAULT_FAQS, "question"
    else:
        return out

    present = {(d.get(key) or "").strip().lower() for d in out}
    for d in defaults:
        if len(out) >= target:
            break
        if (d.get(key) or "").strip().lower() not in present:
            out.append(dict(d))
            present.add((d.get(key) or "").strip().lower())
    return out


@register.filter(name="markdown_links")
@stringfilter
def markdown_links(value: str) -> str:
    """Render a tiny, safe subset of Markdown links and linkify bare URLs."""
    try:
        text = value or ""
        url_re = re.compile(r"(https?://[^\s<>()]+)", re.IGNORECASE)

        def linkify(seg: str) -> str:
            out, last = [], 0
            for m in url_re.finditer(seg):
                start, end = m.span(1)
                out.append(escape(seg[last:start]))
                safe_url = escape(m.group(1))
                out.append(
                    f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer" '
                    f'class="underline underline-offset-2 hover:opacity-90">{safe_url}</a>'
                )
                last = end
            out.append(escape(seg[last:]))
            return "".join(out)

        md_re = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
        parts, last_idx = [], 0
        for m in md_re.finditer(text):
            start, end = m.span(0)
            parts.append(linkify(text[last_idx:start]))
            label = escape((m.group(1) or "").strip()) or "Link"
            safe_url = escape((m.group(2) or "").strip())
            parts.append(
                f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer" '
                f'class="underline underline-offset-2 hover:opacity-90">{label}</a>'
            )
            last_idx = end
        parts.append(linkify(text[last_idx:]))
        return mark_safe("".join(parts))
    except Exception:
        return escape(value)


_SWATCH_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")
_COLOR_AXES = {"color", "colour"}


@register.filter(name="variant_swatch")
def variant_swatch(combinations, color_value) -> str:
    """The swatch colour for one Color/Colour value on the product page.

    The owner can pick the exact dot colour per variation on the Variations step
    (stored as ``swatch`` on each combination). Returns the first ``#rrggbb``
    stored for a combination whose Color attribute equals ``color_value``;
    otherwise the value itself, so a plain CSS name ("Black", "navy") still
    paints as before.
    """
    want = " ".join(str(color_value or "").split()).lower()
    rows = combinations or []
    if isinstance(rows, dict):
        rows = list(rows.values())
    for combo in rows if isinstance(rows, list) else []:
        if not isinstance(combo, dict):
            continue
        swatch = str(combo.get("swatch") or "").strip()
        if not _SWATCH_HEX_RE.fullmatch(swatch):
            continue
        attrs = combo.get("attributes") or []
        if isinstance(attrs, dict):
            attrs = [{"name": k, "value": v} for k, v in attrs.items()]
        for a in attrs:
            if not isinstance(a, dict):
                continue
            if str(a.get("name") or "").strip().lower() in _COLOR_AXES and (
                " ".join(str(a.get("value") or "").split()).lower() == want
            ):
                return swatch
    return str(color_value or "")


@register.simple_tag(name="trust_badge_icons")
def trust_badge_icons(website):
    """``{badge name: <svg>}`` for the store's own trust badges (made in the
    product form's "+" popup from the built-in icon set), so the product page
    can draw each one's chosen icon. Empty on any error."""
    try:
        from commerce import trust_badges

        return trust_badges.icon_svg_map(website)
    except Exception:
        return {}
