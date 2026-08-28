"""Template filters and tags the ported storefront designs rely on.

Faithful to the reference ``website_filters`` library so the copied templates
work unchanged, with three OneWooz adaptations:

* media URLs stay on this platform's own ``MEDIA_URL`` — the reference rewrote
  them onto a hardcoded ``media.1matrix.io`` CDN host;
* ``website_base_path`` returns the storefront root via ``reverse`` so links are
  correct on ``<slug>.onewooz.site`` and under the ``/s/<slug>/`` dev prefix
  alike, instead of the reference's literal ``/website/s/<slug>/``;
* ``media_attrs``/``media_meta`` no longer read a per-asset model that does not
  exist here — they emit a safe ``alt`` and nothing more.
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
