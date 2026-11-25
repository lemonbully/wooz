"""Allowlist HTML cleaning for anything that reaches a page.

Staff-authored descriptions and owner-entered rich text both pass through here
on save and again on render (staff accounts get phished too). No scripts, no
event handlers, no ``javascript:`` URLs, no inline styles, links forced to
``rel="noopener nofollow"``.
"""

from __future__ import annotations

import re

import bleach

TAGS = [
    "p", "br", "strong", "b", "em", "i", "u", "s", "small", "mark",
    "ul", "ol", "li", "blockquote", "h2", "h3", "h4", "a", "span", "div",
    "table", "thead", "tbody", "tr", "th", "td", "hr", "img", "figure", "figcaption",
]


def _allowed_attr(tag, name, value):
    if name == "class":
        return bool(_CLASS_OK.match(value or ""))
    if tag == "a":
        if name in ("href", "title"):
            return True
        if name == "target":
            return value == "_blank"
        return False
    if tag == "img":
        return name in ("src", "alt", "width", "height", "loading")
    if tag in ("td", "th"):
        return name in ("colspan", "rowspan", "scope")
    return False


PROTOCOLS = ["https", "http", "mailto", "tel"]

_CLASS_OK = re.compile(r"^[a-zA-Z0-9 _-]{0,120}$")
_A_OPEN = re.compile(r"<a(\s|>)")


def clean_html(raw: str | None, *, max_length: int = 20000) -> str:
    text = (raw or "")[: max_length * 2]
    cleaned = bleach.clean(
        text,
        tags=TAGS,
        attributes=_allowed_attr,
        protocols=PROTOCOLS,
        strip=True,
        strip_comments=True,
    )
    # Every link gets rel="noopener nofollow" (rel itself is never accepted).
    cleaned = _A_OPEN.sub(lambda m: '<a rel="noopener nofollow"' + m.group(1), cleaned)
    return cleaned[:max_length]


def clean_text(raw, max_length: int = 255) -> str:
    return str(raw or "").replace("\x00", "").strip()[:max_length]


_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_IDENT = re.compile(r"^[A-Za-z0-9_.\-]{1,80}$")


def clean_color(raw, default: str = "") -> str:
    value = str(raw or "").strip()
    return value if _HEX.match(value) else default


def clean_identifier(raw) -> str:
    value = str(raw or "").strip()
    return value if _IDENT.match(value) else ""


def clean_https_url(raw, *, allow_http: bool = False) -> str:
    value = str(raw or "").strip()
    if len(value) > 500 or any(c in value for c in "\"'<> \n\r\t"):
        return ""
    if value.startswith("https://"):
        return value
    if allow_http and value.startswith("http://"):
        return value
    if value.startswith("/") and not value.startswith("//"):
        return value  # a path on the store itself
    return ""
