"""The storefront announcement bar.

Edited on the content editor's Brand tab and stored in ``Site.content``:

* ``announcement_mode``  ``"static"`` (one statement, no motion) or
  ``"dynamic"`` (a marquee that loops the statements left to right);
* ``announcement_items``  up to ``MAX_ITEMS`` statements, each
  ``{"text", "link_word", "link_url"}``. ``link_word`` is a word or phrase of
  the statement rendered as a link to ``link_url``, opened in a new tab;
* ``announcement_text``  the original single-message key. Kept as a mirror of
  the first statement so older readers still see the right message, and read
  as the fallback for stores saved before statements existed.

With no statement at all the bar shows ``DEFAULT_TEXT``, exactly as the
storefront always has.
"""

from __future__ import annotations

import math
import re

MAX_ITEMS = 4
MAX_TEXT = 160
MAX_WORD = 60
MODES = ("static", "dynamic")
DEFAULT_TEXT = "FREE SHIPPING ON ORDERS OVER ₹999"

# A bare domain the owner typed without a scheme ("example.com/sale").
_BARE_DOMAIN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+(:\d+)?([/?#]\S*)?$", re.IGNORECASE)
# A store page typed without its leading slash ("products/").
_BARE_PATH = re.compile(r"^[a-z0-9][a-z0-9_\-./]*([?#]\S*)?$", re.IGNORECASE)


def clean_link_url(raw) -> str:
    """A link the bar may render as an ``href``.

    Keeps http(s), mailto: and tel: links and store paths. A bare domain gets
    ``https://``; a bare path gets a leading ``/``. Anything else (another
    scheme such as ``javascript:``, whitespace inside) is dropped.
    """
    url = str(raw or "").strip()[:2000]
    if not url or any(c.isspace() for c in url):
        return ""
    low = url.lower()
    if low.startswith(("https://", "http://", "mailto:", "tel:")):
        return url
    if url.startswith("#"):
        return url
    if url.startswith("/"):
        return "" if url.startswith("//") else url
    if ":" in url.split("/", 1)[0] and not _BARE_DOMAIN.match(url):
        return ""  # some other scheme
    if _BARE_DOMAIN.match(url):
        return "https://" + url
    if _BARE_PATH.match(url):
        return "/" + url
    return ""


def clean_mode(raw) -> str:
    mode = str(raw or "").strip().lower()
    return mode if mode in MODES else "static"


def clean_items(items) -> list[dict]:
    """The editor's statements, trimmed and capped. A statement with neither
    text nor a linked word is dropped; at most ``MAX_ITEMS`` are kept."""
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text") or "").split())[:MAX_TEXT]
        word = " ".join(str(item.get("link_word") or "").split())[:MAX_WORD]
        url = clean_link_url(item.get("link_url"))
        if not text and not word:
            continue
        out.append({"text": text, "link_word": word, "link_url": url})
        if len(out) >= MAX_ITEMS:
            break
    return out


def _resolve_href(url: str, base: str) -> str:
    """Store paths resolve against the storefront root, so ``/products/``
    works on a real storefront host and under the ``/s/<slug>/`` dev prefix."""
    if url.startswith("/") and not url.startswith("//"):
        base = base if base.endswith("/") else base + "/"
        return base + url.lstrip("/")
    return url


def _segments(item: dict, base: str) -> dict:
    """Split a statement around its linked word: ``before`` + ``word`` (the
    link) + ``after``. The match is case-insensitive and keeps the statement's
    own casing. A linked word missing from the text is appended after it."""
    text, word, url = item["text"], item["link_word"], item["link_url"]
    if not (word and url):
        return {"before": text or word, "word": "", "after": "", "href": ""}
    href = _resolve_href(url, base)
    if not text:
        return {"before": "", "word": word, "after": "", "href": href}
    match = re.search(re.escape(word), text, re.IGNORECASE)
    if not match:
        return {"before": text + " ", "word": word, "after": "", "href": href}
    return {
        "before": text[: match.start()],
        "word": text[match.start(): match.end()],
        "after": text[match.end():],
        "href": href,
    }


def bar_context(content, base: str = "/") -> dict:
    """Everything the navigation needs to draw the bar.

    ``{"mode", "items", "groups", "duration"}``: ``items`` are the statements as
    link segments; for the marquee, ``groups`` holds two identical runs of them
    (repeated so one run is wider than most screens) and ``duration`` is the
    loop length in seconds, scaled to the run's length so the scroll speed
    stays even whatever the owner writes.
    """
    content = content if isinstance(content, dict) else {}
    items = clean_items(content.get("announcement_items"))
    if not items:
        legacy = " ".join(str(content.get("announcement_text") or "").split())
        items = [{"text": legacy or DEFAULT_TEXT, "link_word": "", "link_url": ""}]
    mode = clean_mode(content.get("announcement_mode"))
    segments = [_segments(i, base) for i in items]
    ctx = {"mode": mode, "items": segments, "groups": [], "duration": 0}
    if mode != "dynamic":
        ctx["items"] = segments[:1]
        return ctx

    # One run holds at least 8 entries, so even a single short statement fills
    # a wide screen before the loop's seam comes round.
    repeat = max(1, math.ceil(8 / len(segments)))
    run = segments * repeat
    groups = []
    for g in range(2):
        groups.append([
            # Only the first copy of each statement is announced and focusable;
            # every repeat (and the whole second run) is decorative.
            {**s, "primary": g == 0 and n < len(segments)}
            for n, s in enumerate(run)
        ])
    # ~7.5 px per character at the bar's 13 px type, plus the 3.5 rem gap
    # (56 px) per entry, crossed at ~45 px/s.
    chars = sum(len(s["before"]) + len(s["word"]) + len(s["after"]) for s in run)
    width = chars * 7.5 + len(run) * 56
    ctx["groups"] = groups
    ctx["duration"] = max(12, round(width / 45))
    return ctx
