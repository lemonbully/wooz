"""Template context for the marketing site."""

from __future__ import annotations


def footer_pages(request):
    """Kept for template compatibility; the footer now links to the /pages/
    index (``web:core_pages``) instead of listing individual core pages, so no
    per-request query is run here."""
    return {"footer_core_pages": []}


def seo(request):
    """Per-page SEO/structured-data context for the marketing surface only.

    Provides ``seo`` (canonical, robots, keywords and Open-Graph/Twitter values)
    and ``schema_jsonld`` (the JSON-LD ``@graph`` string) to the marketing shell
    head. Runs on every template render, so it is scoped tightly to the
    marketing host and wrapped so a failure degrades to "no structured data"
    rather than breaking the page. Other surfaces (app, admin, storefront) get
    an empty result and are untouched.
    """
    if getattr(request, "surface", "") != "marketing":
        return {}
    try:
        from . import schema

        return {"seo": schema.head_meta(request), "schema_jsonld": schema.json_ld(request)}
    except Exception:
        return {}
