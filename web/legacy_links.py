"""Repairs internal links that point at Resource-centre URLs which do not exist.

Core pages and blog posts are pasted HTML authored in the admin, so a link
typed by hand (an old hyphenated slug, a singular/plural typo, a guide that is
not published yet) ships as a real 404. Search engines count every one of
those against the site.

This module fixes them in two places, from one rule set:

* :func:`fix_links` rewrites ``href`` values in a rendered article body, so the
  page links straight to a live URL (no redirect hop).
* :func:`redirect_for` answers a request for a dead path with the live URL the
  view then 301s to, so links from outside the site recover too.

A path that resolves to a published page is never touched, so the moment a
missing guide is published its own URL wins over any fallback below.

Resolution order for a dead ``/pages/...`` path:

1. :data:`ALIASES`: explicit renames and typos (``/pages/pricing/`` is the
   pricing page, ``blog-and-corepages`` is ``blogs-and-corepages``).
2. A hyphenated ``/pages/<page>-<topic>/``: the live topic, else the live page.
3. A single-segment ``/pages/<slug>/`` whose slug belongs to exactly one live
   topic or article anywhere in the tree.
4. The nearest live ancestor (``/pages/features/grow/`` → ``/pages/features/``),
   finally the ``/pages/`` hub itself.

``/signup`` (a bare path, not an app URL) is sent to pricing: accounts are
created after a plan is chosen there, see :mod:`billing.onboarding`.
"""

from __future__ import annotations

import re

from django.urls import get_script_prefix, reverse

#: Explicit renames and typos. Keys are prefix-free paths; a value is either a
#: prefix-free path or ``@<url name>`` for a marketing route.
ALIASES: dict[str, str] = {
    "/pages/pricing/": "@web:pricing",
    "/pages/blog-and-corepages/": "/pages/features/build/blogs-and-corepages/",
    "/pages/features/build/blog-and-corepages/": "/pages/features/build/blogs-and-corepages/",
    "/pages/multiple-stores/": "/pages/features/build/multiple-stores/",
    "/signup/": "@web:pricing",
    "/sign-up/": "@web:pricing",
}

_HREF = re.compile(r"""(\bhref\s*=\s*)(["'])([^"']*)\2""", re.I)


def _strip_prefix(path: str) -> str:
    prefix = get_script_prefix()
    if prefix != "/" and path.startswith(prefix):
        return "/" + path[len(prefix):]
    return path


def _with_prefix(path: str) -> str:
    prefix = get_script_prefix()
    return prefix.rstrip("/") + path if prefix != "/" else path


def _norm(path: str) -> str:
    path = path.split("#", 1)[0].split("?", 1)[0]
    if not path.startswith("/"):
        return ""
    return path if path.endswith("/") else path + "/"


class LinkIndex:
    """Every published Resource-centre URL, plus slug lookups for repairs."""

    def __init__(self, roots=None):
        if roots is None:
            from .core_tree import load_tree

            roots = load_tree()
        self.live: set[str] = {"/pages/"}
        self.by_slug: dict[str, list[str]] = {}
        self.root_subs: dict[tuple[str, str], str] = {}
        self.root_urls: dict[str, str] = {}
        for root in roots:
            root_url = _strip_prefix(root.get_absolute_url())
            self.live.add(root_url)
            self.root_urls[root.slug] = root_url
            for sub in getattr(root, "subs", []):
                sub_url = _strip_prefix(sub.get_absolute_url())
                self.live.add(sub_url)
                self.by_slug.setdefault(sub.slug, []).append(sub_url)
                self.root_subs[(root.slug, sub.slug)] = sub_url
                for kid in getattr(sub, "kids", []):
                    kid_url = _strip_prefix(kid.get_absolute_url())
                    self.live.add(kid_url)
                    self.by_slug.setdefault(kid.slug, []).append(kid_url)

    def _alias(self, path: str) -> str | None:
        target = ALIASES.get(path)
        if target is None:
            return None
        if target.startswith("@"):
            return _strip_prefix(reverse(target[1:]))
        # An alias into the Resource centre must itself be live; otherwise fall
        # through to the ancestor rule for the corrected path.
        return target if target in self.live else self._ancestor(target)

    def _ancestor(self, path: str) -> str:
        parts = [p for p in path.split("/") if p]
        while len(parts) > 1:
            parts.pop()
            candidate = "/" + "/".join(parts) + "/"
            if candidate in self.live:
                return candidate
        return "/pages/"

    def resolve(self, path: str, *, allow_hub: bool = True) -> str | None:
        """The live replacement for a dead internal path, or None to leave it.

        ``allow_hub=False`` (used for redirects) refuses the last-resort
        fallback to the ``/pages/`` hub, so an unknown path stays an honest 404
        instead of becoming a soft one.
        """
        path = _norm(path)
        if not path:
            return None
        aliased = self._alias(path)
        if aliased:
            return aliased
        if not path.startswith("/pages/") or path in self.live:
            return None
        parts = [p for p in path.split("/") if p][1:]  # drop "pages"
        if len(parts) == 1:
            slug = parts[0]
            if "-" in slug:
                for (root_slug, sub_slug), url in self.root_subs.items():
                    if slug == f"{root_slug}-{sub_slug}":
                        return url
                # "<page>-<topic>" whose topic is not published yet: the page.
                for root_slug, url in self.root_urls.items():
                    if slug.startswith(f"{root_slug}-"):
                        return url
            matches = self.by_slug.get(slug, [])
            if len(matches) == 1:
                return matches[0]
        fallback = self._ancestor(path)
        if fallback == "/pages/" and not allow_hub:
            return None
        return fallback


def _site_relative(href: str, request) -> str | None:
    """The prefix-free path of an href that points at this site, else None."""
    if href.startswith("/") and not href.startswith("//"):
        return _strip_prefix(href)
    if request is not None:
        origin = request.build_absolute_uri("/").rstrip("/")
        for base in (origin, origin.replace("https://", "http://", 1)):
            if href.startswith(base + "/"):
                return _strip_prefix(href[len(base):])
    return None


def fix_links(html: str, index: LinkIndex | None = None, request=None) -> str:
    """Rewrite hrefs in ``html`` that point at dead internal paths."""
    if not html or "href" not in html:
        return html
    index = index or LinkIndex()

    def repl(match: re.Match) -> str:
        attr, quote, href = match.group(1), match.group(2), match.group(3)
        path = _site_relative(href.strip(), request)
        if path is None:
            return match.group(0)
        fixed = index.resolve(path)
        if not fixed:
            return match.group(0)
        anchor = ""
        if "#" in href:
            anchor = "#" + href.split("#", 1)[1]
        return f"{attr}{quote}{_with_prefix(fixed)}{anchor}{quote}"

    return _HREF.sub(repl, html)


def redirect_for(request, index: LinkIndex | None = None) -> str | None:
    """The live URL a dead request path should 301 to, or None."""
    fixed = (index or LinkIndex()).resolve(request.path_info, allow_hub=False)
    if not fixed or _norm(request.path_info) == fixed:
        return None
    return _with_prefix(fixed)
