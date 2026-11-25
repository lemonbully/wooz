"""The onewooz.com core page tree (page → subpage → child), loaded and analysed.

Two jobs, shared by the views, the sitemap and ``llms.txt``:

* **Loading.** Three flat queries (pages, subpages, child pages), sorted in
  Python. The rows carry long HTML bodies, so letting MySQL ORDER BY them is
  exactly the wide-row sort that fails with error 1038; the tree is small, so
  sorting it here costs nothing. Parents are attached to their children, so
  ``get_absolute_url()`` never issues a query per node.

* **Analysis.** From a node's authored HTML: stable anchor ids on every
  ``<h2>`` (the in-page table of contents, and "jump to" links in search
  results), the answer-first summary (the first paragraph), reading time,
  question-style headings with their answers (FAQ structured data), and the
  first ordered list as how-to steps. Everything is derived from content the
  admin already writes, so no field or migration is needed.
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass, field

from django.utils.html import strip_tags
from django.utils.text import slugify

from .models import MarketingCoreChildPage, MarketingCorePage, MarketingCoreSubpage

WORDS_PER_MINUTE = 200


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def _sort(rows):
    return sorted(rows, key=lambda r: (r.order, (r.title or "").lower()))


def load_tree(root_slug: str | None = None) -> list[MarketingCorePage]:
    """Published core pages, each with ``.subs`` (published subpages) and each
    subpage with ``.kids`` (published child pages), in display order.

    With ``root_slug`` only that page is loaded (an empty list when it does not
    exist or is unpublished).
    """
    roots_qs = MarketingCorePage.objects.filter(is_published=True).order_by()
    if root_slug is not None:
        roots_qs = roots_qs.filter(slug=root_slug)
    roots = _sort(roots_qs)
    if not roots:
        return []

    subs = _sort(
        MarketingCoreSubpage.objects.filter(
            is_published=True, core_page_id__in=[r.pk for r in roots]
        ).order_by()
    )
    kids = _sort(
        MarketingCoreChildPage.objects.filter(
            is_published=True, subpage_id__in=[s.pk for s in subs]
        ).order_by()
    ) if subs else []

    by_root = {r.pk: r for r in roots}
    for r in roots:
        r.subs = []
        r.level = "page"
    by_sub = {}
    for s in subs:
        root = by_root[s.core_page_id]
        s.core_page = root  # cache the FK: no query in get_absolute_url
        s.kids = []
        s.level = "subpage"
        root.subs.append(s)
        by_sub[s.pk] = s
    for k in kids:
        sub = by_sub[k.subpage_id]
        k.subpage = sub
        k.level = "child"
        sub.kids.append(k)
    return roots


def flatten(root) -> list:
    """Reading order of one tree: overview, then each subpage and its children."""
    nodes = [root]
    for sub in root.subs:
        nodes.append(sub)
        nodes.extend(sub.kids)
    return nodes


def article_count(roots) -> int:
    return sum(len(s.kids) or 1 for r in roots for s in r.subs)


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
_H2 = re.compile(r"<h2\b([^>]*)>(.*?)</h2>", re.I | re.S)
_ID_ATTR = re.compile(r"""\bid\s*=\s*["']([^"']+)["']""", re.I)
_FIRST_P = re.compile(r"<p\b[^>]*>(.*?)</p>", re.I | re.S)
_SECTION = re.compile(r"<h([23])\b[^>]*>(.*?)</h\1>(.*?)(?=<h[1-6]\b|\Z)", re.I | re.S)
_FIRST_OL = re.compile(r"<ol\b[^>]*>(.*?)</ol>", re.I | re.S)
_LI = re.compile(r"<li\b[^>]*>(.*?)</li>", re.I | re.S)


def text_of(fragment: str) -> str:
    """Plain, single-spaced text of an HTML fragment, entities decoded."""
    return " ".join(html_lib.unescape(strip_tags(fragment or "")).split())


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:.")
    return f"{cut}…"


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def meta_description(text: str, limit: int = 158) -> str:
    """A meta description that ends on a full sentence.

    Takes as many whole sentences as fit in ``limit`` characters, so the snippet
    never stops mid-thought. Only when the first sentence alone is longer than
    the limit is it clipped at a word boundary (with an ellipsis).
    """
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    out = ""
    for sentence in _SENTENCE_END.split(text):
        candidate = f"{out} {sentence}".strip()
        if len(candidate) > limit:
            break
        out = candidate
    return out if len(out) >= 50 else clip(text, limit)


@dataclass
class Analysis:
    body: str = ""
    toc: list[dict] = field(default_factory=list)
    summary: str = ""
    words: int = 0
    minutes: int = 1
    faqs: list[dict] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)


def summary_of(content: str) -> str:
    """The node's answer-first summary: its first paragraph (or opening text)."""
    match = _FIRST_P.search(content or "")
    return text_of(match.group(1)) if match else clip(text_of(content), 240)


def analyse(content: str) -> Analysis:
    content = content or ""
    toc: list[dict] = []
    used: set[str] = set(_ID_ATTR.findall(content))

    def anchor(match: re.Match) -> str:
        attrs, inner = match.group(1), match.group(2)
        title = text_of(inner)
        existing = _ID_ATTR.search(attrs)
        if existing:
            ident = existing.group(1)
        else:
            base = slugify(title)[:60] or "section"
            ident, n = base, 2
            while ident in used:
                ident, n = f"{base}-{n}", n + 1
            used.add(ident)
            attrs = f' id="{ident}"{attrs}'
        if title:
            toc.append({"id": ident, "title": title})
        return f"<h2{attrs}>{inner}</h2>"

    body = _H2.sub(anchor, content)
    words = len(text_of(content).split())

    faqs = []
    for _level, question, answer in _SECTION.findall(content):
        q, a = text_of(question), text_of(answer)
        if q.endswith("?") and a:
            faqs.append({"q": q, "a": clip(a, 600)})

    steps = []
    ol = _FIRST_OL.search(content)
    if ol:
        steps = [s for s in (text_of(li) for li in _LI.findall(ol.group(1))) if s]
        if len(steps) < 3:
            steps = []

    return Analysis(
        body=body,
        toc=toc,
        summary=summary_of(content),
        words=words,
        minutes=max(1, round(words / WORDS_PER_MINUTE)),
        faqs=faqs,
        steps=steps,
    )
