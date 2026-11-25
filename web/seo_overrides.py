"""onewooz.com SEO set in the Django admin, applied over the built-in registry.

The marketing site's built-in, keyword-tuned SEO lives in :mod:`web.schema`
(``PAGE_SEO`` / ``LEGAL_SEO`` plus whatever a view attaches as ``seo_extra``).
This module lays the values OneWooz staff enter in the admin
(:class:`~web.models.MarketingSeoSettings` and its per-page
:class:`~web.models.MarketingPageSeo` rows) on top of it, mirroring a store's
own "different for every page" SEO (:mod:`storefront.seo_pages`). There is
no "same for every page" mode on onewooz.com: the site-wide set *is* the home
page's SEO and fills what another page lacks (keywords, share image), and each
page's own row wins field by field; a blank field keeps that page's built-in
value. (``MarketingSeoSettings.mode`` is kept but no longer read.)

Page keys are stable identifiers, never paths: the fixed pages by URL name
(``home``, ``pricing``…), ``page:privacy`` / ``page:terms``, ``policy:<slug>``,
``blog:<slug>`` and ``core:<page>[/<sub>[/<child>]]``.

Everything is read through one cached snapshot (invalidated whenever a settings
or page row is saved or deleted) and fails open: any error leaves the built-in
SEO untouched.
"""

from __future__ import annotations

import json
import re
from typing import Any

from django.core.cache import cache

_CACHE_KEY = "web:marketing-seo:v2"
_CACHE_TTL = 600

#: Text fields a settings object / page row can override.
TEXT_FIELDS = (
    "meta_title",
    "meta_description",
    "keywords",
    "og_title",
    "og_description",
    "twitter_title",
    "twitter_description",
)
IMAGE_FIELDS = ("og_image", "twitter_image")

#: Fixed marketing pages: (page key = URL name, label).
FIXED_PAGES = (
    ("home", "Home"),
    ("platform", "Platform"),
    ("pricing", "Pricing"),
    ("demo", "Demo stores"),
    ("solutions", "Solutions"),
    ("customers", "Customers"),
    ("about", "About"),
    ("security", "Security"),
    ("contact", "Contact"),
    ("support", "Support"),
    ("ask_home", "Ask OneWooz"),
    ("topics", "Ask OneWooz topics"),
    ("live", "Ask OneWooz live"),
    ("weekly", "Ask OneWooz weekly"),
    ("blog_list", "Blog"),
    ("core_pages", "Pages index"),
    ("page:privacy", "Privacy policy"),
    ("page:terms", "Terms of service"),
)
_FIXED_LABELS = dict(FIXED_PAGES)


# --------------------------------------------------------------------------
# Page catalogue (admin dropdown) and labels
# --------------------------------------------------------------------------


def page_catalog() -> list[tuple[str, list[tuple[str, str]]]]:
    """Every onewooz.com page that can carry its own SEO, grouped for the admin
    dropdown: ``[(group, [(key, label), ...]), ...]``."""
    from django.db import DatabaseError

    from .models import (
        LegalPolicy,
        MarketingBlog,
        MarketingCoreChildPage,
        MarketingCorePage,
        MarketingCoreSubpage,
    )

    groups: list[tuple[str, list[tuple[str, str]]]] = [("Main pages", list(FIXED_PAGES))]
    try:
        policies = [
            (f"policy:{p.slug}", p.title)
            for p in LegalPolicy.objects.exclude(slug__in=LegalPolicy.BUILT_IN_SLUGS)
            .only("title", "slug")
            .order_by("order", "title")
        ]
        if policies:
            groups.append(("Legal policies", policies))

        core: list[tuple[str, str]] = []
        for page in MarketingCorePage.objects.only("title", "slug").order_by("order", "title"):
            core.append((f"core:{page.slug}", page.title))
            for sub in MarketingCoreSubpage.objects.filter(core_page=page).only("title", "slug").order_by("order", "title"):
                core.append((f"core:{page.slug}/{sub.slug}", f"{page.title} › {sub.title}"))
                for child in (
                    MarketingCoreChildPage.objects.filter(subpage=sub).only("title", "slug").order_by("order", "title")
                ):
                    core.append(
                        (f"core:{page.slug}/{sub.slug}/{child.slug}", f"{page.title} › {sub.title} › {child.title}")
                    )
        if core:
            groups.append(("Content pages", core))

        from core.queries import sorted_rows

        # Posts carry their article body; sort keys only (MySQL error 1038).
        posts = sorted_rows(MarketingBlog.objects.order_by("-published_at"))
        blogs = [(f"blog:{p.slug}", p.title) for p in posts if p.slug]
        if blogs:
            groups.append(("Blog posts", blogs))
    except DatabaseError:
        pass
    return groups


def page_choices(current: str = "") -> list:
    """Grouped Django choices for the admin's page dropdown. A saved key whose
    page no longer exists is kept (labelled as such) so the row still opens."""
    groups = page_catalog()
    known = {k for _g, pages in groups for k, _l in pages}
    choices: list = [("", "Choose a page…")]
    choices += [(group, pages) for group, pages in groups]
    if current and current not in known:
        choices.append(("Missing", [(current, f"{current} (page no longer exists)")]))
    return choices


_KIND_LABELS = {"policy": "Legal policy", "blog": "Blog post", "core": "Content page"}


def label_for(key: str) -> str:
    """A readable name for a page key, without touching the database (used for
    every page row's title in the admin)."""
    key = str(key or "")
    if key in _FIXED_LABELS:
        return _FIXED_LABELS[key]
    kind, _sep, rest = key.partition(":")
    if kind in _KIND_LABELS and rest:
        return f"{_KIND_LABELS[kind]} · {rest.replace('/', ' › ')}"
    return key or "Page"


# --------------------------------------------------------------------------
# Which page is this request?
# --------------------------------------------------------------------------


def page_key_for_request(request) -> str:
    """The SEO page key for the marketing page being rendered, or ``""``."""
    match = getattr(request, "resolver_match", None)
    if match is None:
        return ""
    name = match.url_name or ""
    kw = match.kwargs or {}
    if name == "page":
        return f"page:{kw.get('slug', '')}"
    if name == "policy":
        return f"policy:{kw.get('slug', '')}"
    if name == "blog_detail":
        return f"blog:{kw.get('slug', '')}"
    if name in ("core_page", "core_subpage", "core_child"):
        parts = [kw.get("slug"), kw.get("sub_slug"), kw.get("child_slug")]
        return "core:" + "/".join(p for p in parts if p)
    if name in _FIXED_LABELS:
        return name
    return ""


# --------------------------------------------------------------------------
# Pasted structured data (JSON-LD)
# --------------------------------------------------------------------------

_SCRIPT_RE = re.compile(r"<script\b[^>]*>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def parse_schema(text: str) -> list[str]:
    """The JSON-LD blocks in what an admin pasted, each as compact JSON ready to
    sit inside its own ``<script type="application/ld+json">``.

    Accepts bare JSON (an object or an array) or one or more complete
    ``<script>`` blocks as schema generators produce them. Raises
    :class:`ValueError` with a readable message when a block is not valid JSON
    or is not an object/array, so the admin form can show it on save.
    "<" is escaped as ``\\u003c`` so the output cannot close its script tag.
    """
    text = _COMMENT_RE.sub("", str(text or "")).strip()
    if not text:
        return []
    found = _SCRIPT_RE.findall(text)
    if found:
        leftover = _SCRIPT_RE.sub("", text).strip()
        if leftover:
            raise ValueError(
                "There is text outside the <script> blocks. Paste only the script "
                "block(s), or only the JSON."
            )
        chunks = [c.strip() for c in found]
    elif text.lstrip().lower().startswith("<"):
        raise ValueError(
            "This looks like HTML but has no complete <script>...</script> block. "
            "Paste the full script block(s), or only the JSON."
        )
    else:
        chunks = [text]

    blocks: list[str] = []
    many = len(chunks) > 1
    for n, chunk in enumerate(chunks, start=1):
        where = f"Block {n}: " if many else ""
        if not chunk:
            raise ValueError(f"{where}the script block is empty.")
        try:
            data = json.loads(chunk)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{where}not valid JSON ({exc.msg} at line {exc.lineno}, column {exc.colno})."
            ) from None
        if not isinstance(data, (dict, list)) or not data:
            raise ValueError(f"{where}schema must be a JSON object {{...}} or a list [...].")
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        blocks.append(payload.replace("<", "\\u003c"))
    return blocks


def _schema_blocks(text: str) -> list[str]:
    """parse_schema for rendering: anything invalid renders nothing."""
    try:
        return parse_schema(text)
    except ValueError:
        return []


# --------------------------------------------------------------------------
# Snapshot (cached) and resolution
# --------------------------------------------------------------------------


def _image_url(field) -> str:
    try:
        return field.url if field else ""
    except Exception:
        return ""


def _row_values(obj, *, page: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in TEXT_FIELDS:
        value = " ".join(str(getattr(obj, name, "") or "").split())
        if value:
            out[name] = value
    for name in IMAGE_FIELDS:
        url = _image_url(getattr(obj, name, None))
        if url:
            out[name] = url
    schema = _schema_blocks(getattr(obj, "schema_markup", ""))
    if schema:
        # Always set together, so a page's own schema also decides "replace"
        # rather than inheriting the home set's choice.
        out["schema"] = schema
        out["replace_schema"] = bool(getattr(obj, "replace_built_in_schema", False))
    if page:
        canonical = str(getattr(obj, "canonical_url", "") or "").strip()
        if canonical:
            out["canonical_url"] = canonical
        if getattr(obj, "noindex", False):
            out["noindex"] = True
    return out


def snapshot() -> dict[str, Any]:
    """``{"mode", "site": {...}, "pages": {key: {...}}}``  cached; ``{}`` when
    nothing is configured or the tables don't exist yet."""
    data = cache.get(_CACHE_KEY)
    if data is not None:
        return data
    data = {}
    try:
        from .models import MarketingSeoSettings

        settings_obj = MarketingSeoSettings.objects.order_by("created_at").first()
        if settings_obj is not None:
            data = {
                "mode": settings_obj.mode or "same",
                "site": _row_values(settings_obj, page=False),
                "pages": {
                    row.page_key: _row_values(row, page=True)
                    for row in settings_obj.pages.all()
                    if row.page_key
                },
            }
    except Exception:
        data = {}
    try:
        cache.set(_CACHE_KEY, data, _CACHE_TTL)
    except Exception:
        pass
    return data


_BRAND_CACHE_KEY = "web:brand-assets:v1"


def brand_assets() -> dict[str, str]:
    """URLs of the logo / favicon / dark favicon uploaded on onewooz.com SEO
    (only the ones set), cached. Read on every OneWooz page (marketing, sign-in,
    dashboard, admin), so it is a separate, three-column lookup and fails open
    to ``{}`` (the built-in files)."""
    data = cache.get(_BRAND_CACHE_KEY)
    if data is not None:
        return data
    data = {}
    try:
        from .models import MarketingSeoSettings

        obj = (
            MarketingSeoSettings.objects.order_by("created_at")
            .only("site_logo", "favicon", "favicon_dark")
            .first()
        )
        if obj is not None:
            for key, field in (("logo", "site_logo"), ("favicon", "favicon"), ("favicon_dark", "favicon_dark")):
                url = _image_url(getattr(obj, field, None))
                if url:
                    data[key] = url
    except Exception:
        data = {}
    try:
        cache.set(_BRAND_CACHE_KEY, data, _CACHE_TTL)
    except Exception:
        pass
    return data


def invalidate() -> None:
    try:
        cache.delete_many([_CACHE_KEY, _BRAND_CACHE_KEY])
    except Exception:
        pass


def noindex_keys() -> set[str]:
    """Page keys hidden from search, for the sitemap."""
    snap = snapshot()
    return {k for k, v in (snap.get("pages") or {}).items() if v.get("noindex")}


def _abs(request, url: str) -> str:
    url = str(url or "").strip()
    if url.startswith("/") and not url.startswith("//"):
        try:
            return request.build_absolute_uri(url)
        except Exception:
            return url
    return url


def apply(request, rec: dict[str, Any]) -> dict[str, Any]:
    """``rec`` (the built-in record for this page) with the admin's SEO laid
    over it. Adds ``og_title`` / ``og_description`` / ``twitter_*`` /
    ``canonical_url`` / ``noindex`` / ``custom_title`` / ``custom_schema`` /
    ``replace_schema`` keys when set."""
    snap = snapshot()
    if not snap:
        return rec
    rec = dict(rec)
    key = page_key_for_request(request)
    site = snap.get("site") or {}
    own: dict[str, Any] = {}
    if key == "home":
        own.update(site)
    # SEO is always per page: a page's own row wins field by field.
    if key:
        own.update((snap.get("pages") or {}).get(key) or {})

    if own.get("meta_title"):
        rec["title"] = own["meta_title"]
        rec["custom_title"] = True
    if own.get("meta_description"):
        rec["description"] = own["meta_description"]
    if own.get("keywords"):
        rec["keywords"] = [k.strip() for k in own["keywords"].split(",") if k.strip()]
    elif not rec.get("keywords") and site.get("keywords"):
        rec["keywords"] = [k.strip() for k in site["keywords"].split(",") if k.strip()]
    for name in ("og_title", "og_description", "twitter_title", "twitter_description"):
        if own.get(name):
            rec[name] = own[name]
    # Share images: the page's own, else the site-wide one (in place of the
    # static logo the registry falls back to).
    og_image = own.get("og_image") or (site.get("og_image") if not rec.get("og_image") else "")
    if og_image:
        rec["og_image"] = _abs(request, og_image)
    if own.get("twitter_image"):
        rec["twitter_image"] = _abs(request, own["twitter_image"])
    if own.get("canonical_url"):
        rec["canonical_url"] = own["canonical_url"]
    if own.get("noindex"):
        rec["noindex"] = True
    # Pasted JSON-LD: the home page gets the site-wide block's, any page its
    # own row's (which wins on the home page too).
    if own.get("schema"):
        rec["custom_schema"] = list(own["schema"])
        rec["replace_schema"] = bool(own.get("replace_schema"))
    return rec
