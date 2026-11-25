"""Ready-to-publish example content for onewooz.com.

Used by the ``generate_core_pages`` and ``generate_blogs`` management commands.
The content is written to be presentable as-is (real headings, lists, tables
and cross-links, never "lorem ipsum" or ``<Insert ...>`` placeholders), so a
fresh environment shows what a finished Help Centre and blog look like.

Internal links are written as tokens and resolved with ``reverse()`` when the
content is generated, so no path is hardcoded:

* ``[[core:help-centre]]``, ``[[core:help-centre/payments]]``,
  ``[[core:help-centre/payments/cod-and-part-payment]]``
* ``[[blog:<slug>]]``
* ``[[web:<url name>]]``, e.g. ``[[web:pricing]]``
"""

from __future__ import annotations

import re

from django.urls import NoReverseMatch, reverse

#: Marketing routes, whatever ROOT_URLCONF a caller has active.
_URLCONF = "onewooz.urls.marketing"

_TOKEN = re.compile(r"\[\[(core|blog|web):([a-z0-9_/\-]+)\]\]")


def _resolve(kind: str, value: str) -> str:
    if kind == "web":
        return reverse(f"web:{value}", urlconf=_URLCONF)
    if kind == "blog":
        return reverse("web:blog_detail", kwargs={"slug": value}, urlconf=_URLCONF)
    parts = value.strip("/").split("/")
    if len(parts) == 1:
        return reverse("web:core_page", kwargs={"slug": parts[0]}, urlconf=_URLCONF)
    if len(parts) == 2:
        return reverse("web:core_subpage", kwargs={"slug": parts[0], "sub_slug": parts[1]}, urlconf=_URLCONF)
    return reverse(
        "web:core_child",
        kwargs={"slug": parts[0], "sub_slug": parts[1], "child_slug": parts[2]},
        urlconf=_URLCONF,
    )


def resolve_links(html: str) -> str:
    """Replace every ``[[kind:value]]`` token in ``html`` with its URL."""

    def sub(match: re.Match) -> str:
        try:
            return _resolve(match.group(1), match.group(2))
        except NoReverseMatch:
            return "#"

    return _TOKEN.sub(sub, html)


def clean(html: str) -> str:
    """Resolve links, drop the source indentation and clean as a save would
    (so ``--update`` compares like with like)."""
    from web.content_html import clean_html

    lines = [line.strip() for line in html.strip().splitlines()]
    return clean_html(resolve_links("\n".join(line for line in lines if line)))
