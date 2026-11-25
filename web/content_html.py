"""Clean pasted HTML for onewooz.com core pages and blog posts.

Content is written outside OneWooz (ChatGPT, Claude, or by hand) and pasted
into the admin as plain HTML. The site's stylesheet styles plain semantic tags
(``.cpa-body`` in css/onewooz-core-pages.css), so the only job here is to keep
what the theme styles and drop everything that would fight it or be unsafe:

* **Kept:** headings, paragraphs, lists, tables, quotes, links, images and
  figures, code, and inline emphasis, with a short allowlist of attributes.
* **Dropped with their contents:** ``script``, ``style``, ``head``, forms,
  embeds, ``svg`` and similar.
* **Unwrapped (tag removed, text kept):** ``html``, ``body``, ``div``,
  ``span``, ``section`` and other layout wrappers AI tools like to add.
* **Rewritten:** ``h1`` becomes ``h2`` (the page title is the page's only
  ``h1``); every ``style`` / ``class`` / event attribute is removed; links
  opening a new tab get ``rel="noopener noreferrer"``; images get lazy loading;
  ``javascript:`` and ``data:`` URLs are refused; Markdown code fences around a
  pasted block are removed; empty paragraphs are dropped.

:func:`clean_html` is idempotent (cleaning clean HTML returns it unchanged), so
it runs on every save of the four content models and on the admin preview.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

__all__ = ["CleanResult", "clean_html", "clean_report"]

# Tags kept as-is, with the attributes each may carry.
_GLOBAL_ATTRS: frozenset[str] = frozenset({"title", "lang", "dir"})
_ALLOWED: dict[str, frozenset[str]] = {
    "p": frozenset(), "br": frozenset(), "hr": frozenset(),
    "h2": frozenset({"id"}), "h3": frozenset({"id"}), "h4": frozenset({"id"}),
    "h5": frozenset({"id"}), "h6": frozenset({"id"}),
    "ul": frozenset(), "ol": frozenset({"start", "reversed"}), "li": frozenset({"value"}),
    "dl": frozenset(), "dt": frozenset(), "dd": frozenset(),
    "strong": frozenset(), "b": frozenset(), "em": frozenset(), "i": frozenset(),
    "u": frozenset(), "s": frozenset(), "del": frozenset(), "ins": frozenset(),
    "mark": frozenset(), "small": frozenset(), "sub": frozenset(), "sup": frozenset(),
    "abbr": frozenset(), "cite": frozenset(), "q": frozenset(), "time": frozenset({"datetime"}),
    "code": frozenset(), "pre": frozenset(), "kbd": frozenset(),
    "blockquote": frozenset({"cite"}),
    "a": frozenset({"href", "target", "rel", "hreflang"}),
    "img": frozenset({"src", "alt", "width", "height", "loading", "decoding"}),
    "figure": frozenset(), "figcaption": frozenset(),
    "table": frozenset(), "caption": frozenset(), "thead": frozenset(), "tbody": frozenset(),
    "tfoot": frozenset(), "tr": frozenset(),
    "th": frozenset({"colspan", "rowspan", "scope"}), "td": frozenset({"colspan", "rowspan"}),
    "details": frozenset({"open"}), "summary": frozenset(),
}
_VOID = frozenset({"br", "hr", "img"})

# Removed together with everything inside them.
_DROP = frozenset({
    "script", "style", "head", "title", "meta", "link", "base", "noscript", "template",
    "iframe", "frame", "frameset", "object", "embed", "applet", "param", "source", "track",
    "video", "audio", "canvas", "svg", "math", "form", "input", "button", "select",
    "option", "optgroup", "textarea", "label", "fieldset", "legend", "dialog", "nav",
})
# Elements whose tag is removed but whose content is kept.
_UNWRAP_REPORTED = frozenset({"font", "center"})
_RENAME = {"h1": "h2"}

# Blocks after which a newline keeps the stored HTML readable in the textarea.
_BLOCK = frozenset({
    "p", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "dl", "dt", "dd", "blockquote",
    "figure", "figcaption", "table", "caption", "thead", "tbody", "tfoot", "tr", "pre",
    "details", "summary", "hr",
})

# Implied end tags: opening KEY closes the nearest open tag in the first set,
# unless a tag in the second set (its container) is reached first.
_TABLE_SECTIONS = {"thead", "tbody", "tfoot"}
_IMPLIED: dict[str, tuple[set[str], set[str]]] = {
    "li": ({"li"}, {"ul", "ol"}),
    "dt": ({"dt", "dd"}, {"dl"}),
    "dd": ({"dt", "dd"}, {"dl"}),
    "td": ({"td", "th"}, {"tr", "table"}),
    "th": ({"td", "th"}, {"tr", "table"}),
    "tr": ({"tr"}, {"table", *_TABLE_SECTIONS}),
    "thead": (set(_TABLE_SECTIONS), {"table"}),
    "tbody": (set(_TABLE_SECTIONS), {"table"}),
    "tfoot": (set(_TABLE_SECTIONS), {"table"}),
}
# A block element opening inside an unclosed <p> ends that paragraph.
_BLOCK_OPENERS = frozenset({
    "p", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "dl", "blockquote", "figure",
    "table", "pre", "hr", "details",
})
_P_BOUNDARY = {"li", "td", "th", "blockquote", "figure", "dd", "details", "summary", "figcaption"}

_SAFE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,80}$")
_NUM = re.compile(r"^\d{1,5}$")
_FENCE_OPEN = re.compile(r"^\s*```[a-zA-Z]*\s*\n")
_FENCE_CLOSE = re.compile(r"\n\s*```\s*$")
_EMPTY_P = re.compile(r"<p>(?:\s|&nbsp;|&#160;|<br>)*</p>\n?", re.I)
_BLANK_RUN = re.compile(r"\n{3,}")


@dataclass
class CleanResult:
    html: str
    #: Human-readable notes on what was changed, for the admin preview.
    notes: list[str] = field(default_factory=list)


def _safe_url(value: str, *, image: bool) -> str | None:
    """The URL when it is safe to keep, else None. Relative URLs are allowed."""
    url = (value or "").strip()
    if not url:
        return None
    lowered = re.sub(r"[\s\x00-\x1f]", "", url).lower()
    if lowered.startswith(("javascript:", "vbscript:", "data:", "file:")):
        return None
    if image:
        if lowered.startswith(("https://", "http://", "/", "//")):
            return url
        return None
    if lowered.startswith(("https://", "http://", "mailto:", "tel:", "/", "#", "?", "//")):
        return url
    # Bare relative paths such as "pricing/" are fine; anything with an
    # unknown scheme ("foo:bar") is not.
    if ":" in lowered.split("/", 1)[0]:
        return None
    return url


class _Cleaner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []  # open allowed tags, in output order
        self.drop_depth = 0
        self.drop_tag: list[str] = []
        self.counts: dict[str, int] = {}

    # -- bookkeeping ----------------------------------------------------
    def _note(self, key: str) -> None:
        self.counts[key] = self.counts.get(key, 0) + 1

    def _attrs(self, tag: str, attrs) -> str:
        allowed = _ALLOWED[tag] | _GLOBAL_ATTRS
        kept: dict[str, str] = {}
        for name, value in attrs:
            name = (name or "").lower()
            value = "" if value is None else value
            if name in ("style", "class") or name.startswith("on"):
                self._note("styling" if name in ("style", "class") else "script")
                continue
            if name not in allowed:
                continue
            if name == "id" and not _SAFE_ID.match(value):
                continue
            if name in ("width", "height", "colspan", "rowspan", "start", "value") and not _NUM.match(value.strip()):
                continue
            if name == "href":
                safe = _safe_url(value, image=False)
                if safe is None:
                    self._note("unsafe_link")
                    continue
                value = safe
            if name == "src":
                safe = _safe_url(value, image=True)
                if safe is None:
                    self._note("bad_image")
                    return ""  # signal: drop the whole image
                value = safe
            if name == "target" and value not in ("_blank", "_self"):
                continue
            kept[name] = value

        if tag == "img":
            if "src" not in kept:
                return ""
            kept.setdefault("alt", "")
            kept.setdefault("loading", "lazy")
            kept.setdefault("decoding", "async")
        if tag == "a" and kept.get("target") == "_blank":
            rel = set((kept.get("rel") or "").split()) | {"noopener", "noreferrer"}
            kept["rel"] = " ".join(sorted(rel))
        return "".join(
            f' {k}="{html.escape(v, quote=True)}"' if v != "" or k == "alt" else f" {k}"
            for k, v in kept.items()
        )

    def _close_to(self, index: int) -> None:
        """Close every open tag from the top of the stack down to ``index``."""
        while len(self.stack) > index:
            open_tag = self.stack.pop()
            self.out.append(f"</{open_tag}>")
            if open_tag in _BLOCK:
                self.out.append("\n")

    def _implied_close(self, tag: str) -> None:
        """HTML's implied end tags: ``<li>a<li>b``, ``<td>a<td>b`` and a block
        opening inside an unclosed ``<p>`` close the earlier element first."""
        rule = _IMPLIED.get(tag)
        if rule is None and tag in _BLOCK_OPENERS:
            rule = ({"p"}, _P_BOUNDARY)
        if rule is None:
            return
        closable, boundary = rule
        for index in range(len(self.stack) - 1, -1, -1):
            open_tag = self.stack[index]
            if open_tag in closable:
                self._close_to(index)
                return
            if open_tag in boundary:
                return

    # -- parser hooks ---------------------------------------------------
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self.drop_depth:
            if tag == self.drop_tag[-1] and tag not in _VOID:
                self.drop_depth += 1
            return
        if tag in _DROP:
            self._note("removed")
            if tag not in ("meta", "link", "base", "param", "source", "track", "input"):
                self.drop_depth = 1
                self.drop_tag.append(tag)
            return
        if tag in _RENAME:
            self._note("h1")
            tag = _RENAME[tag]
        if tag not in _ALLOWED:
            if tag in _UNWRAP_REPORTED:
                self._note("styling")
            return  # unwrap: keep the content, lose the tag
        attr_text = self._attrs(tag, attrs)
        if tag == "img" and not attr_text:
            return
        self._implied_close(tag)
        self.out.append(f"<{tag}{attr_text}>")
        if tag in _VOID:
            if tag in _BLOCK:
                self.out.append("\n")
            return
        self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        tag = tag.lower()
        if tag not in _VOID and not self.drop_depth and self.stack and self.stack[-1] == _RENAME.get(tag, tag):
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.drop_depth:
            if tag == self.drop_tag[-1]:
                self.drop_depth -= 1
                if not self.drop_depth:
                    self.drop_tag.pop()
            return
        tag = _RENAME.get(tag, tag)
        if tag not in _ALLOWED or tag in _VOID or tag not in self.stack:
            return
        while self.stack:
            open_tag = self.stack.pop()
            self.out.append(f"</{open_tag}>")
            if open_tag in _BLOCK:
                self.out.append("\n")
            if open_tag == tag:
                break

    def handle_data(self, data):
        if self.drop_depth:
            return
        # Whitespace between blocks: the newline written after each block
        # close already separates them, so a source newline is not doubled
        # (this keeps cleaning idempotent).
        if not data.strip() and (not self.out or self.out[-1] == "\n"):
            return
        self.out.append(html.escape(data, quote=False))

    def handle_comment(self, data):
        return

    def handle_decl(self, decl):
        return

    def handle_pi(self, data):
        return

    def unknown_decl(self, data):
        return

    def finish(self) -> str:
        self.close()
        while self.stack:
            open_tag = self.stack.pop()
            self.out.append(f"</{open_tag}>")
            if open_tag in _BLOCK:
                self.out.append("\n")
        return "".join(self.out)


_NOTE_TEXT = {
    "styling": "Removed {n} inline style/class attribute(s): the theme styles the content.",
    "removed": "Removed {n} unsupported element(s) (script, style, form, embed or similar).",
    "script": "Removed {n} event handler attribute(s).",
    "h1": "Changed {n} h1 heading(s) to h2: the page title is already the h1.",
    "unsafe_link": "Removed {n} unsafe link address(es).",
    "bad_image": "Removed {n} image(s) whose address was not a web URL. Upload images and paste their link.",
    "fence": "Removed the Markdown code fence around the pasted HTML.",
    "empty": "Removed {n} empty paragraph(s).",
}


def clean_report(raw: str | None) -> CleanResult:
    """Clean ``raw`` and explain what changed."""
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    counts: dict[str, int] = {}
    if _FENCE_OPEN.match(text) or text.startswith("```"):
        text = _FENCE_OPEN.sub("", text, count=1)
        text = _FENCE_CLOSE.sub("", text, count=1)
        counts["fence"] = 1

    cleaner = _Cleaner()
    cleaner.feed(text)
    out = cleaner.finish()
    for key, n in cleaner.counts.items():
        counts[key] = counts.get(key, 0) + n

    out, empties = _EMPTY_P.subn("", out)
    if empties:
        counts["empty"] = empties
    # Tidy whitespace between blocks; never inside <pre>.
    if "<pre" not in out:
        out = "\n".join(line.strip() for line in out.split("\n"))
    out = _BLANK_RUN.sub("\n\n", out).strip()

    notes = [
        _NOTE_TEXT[key].format(n=n)
        for key, n in counts.items()
        if key in _NOTE_TEXT
    ]
    return CleanResult(html=out, notes=notes)


def clean_html(raw: str | None) -> str:
    """Pasted HTML reduced to the semantic tags the theme styles."""
    return clean_report(raw).html
