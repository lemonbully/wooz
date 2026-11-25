"""Storefront slot tags. The only marketplace code a storefront runs.

They read one compiled row (``SiteAppRender``, cached) and the paused set, and
nothing else: no catalogue, no licences, no installations. Every fragment is
rendered inside its own guard; a failure renders nothing for that fragment and
counts an error for its app. With no apps on a store the tags cost one cache
read and print nothing at all.

Usage in a theme base template::

    {% load marketplace_slots %}
    <head> ... {% app_head %} </head>
    <body> {% app_slot "body.start" %} {% app_slot "header.above" %} <header>..</header>
           {% app_slot "header.below" %} ... {% app_slot "footer.above" %} <footer>..</footer>
           {% app_tail %} </body>
"""

from __future__ import annotations

import logging

from django import template
from django.templatetags.static import static
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()
logger = logging.getLogger("onewooz.marketplace")

_DEVICE_CLASS = {"mobile": "ow-only-mobile", "desktop": "ow-only-desktop"}


def _site(context):
    request = context.get("request")
    site = getattr(request, "site", None) if request is not None else None
    if site is not None and getattr(site, "pk", None):
        return site
    for name in ("site", "store"):
        cand = context.get(name)
        if cand is not None and getattr(cand, "_meta", None) is not None and getattr(cand, "pk", None):
            if cand._meta.model_name == "site":
                return cand
    return None


def _state(context) -> dict:
    """Per-request: bundle, paused set and page kind (computed once)."""
    request = context.get("request")
    cached = getattr(request, "_ow_app_slots", None) if request is not None else None
    if cached is not None:
        return cached
    state = {"bundle": {}, "paused": set(), "page": "content", "used": False,
             "site": None, "live": False, "live_visual": False, "used_visual": False,
             "placed": set(), "assets": []}
    try:
        from marketplace import compiler, health
        from marketplace.blocks import URLNAME_TO_PAGE

        site = _site(context)
        state["site"] = site
        if site is not None:
            state["bundle"] = compiler.load_bundle(site) or {}
            match = getattr(request, "resolver_match", None)
            state["page"] = URLNAME_TO_PAGE.get(getattr(match, "url_name", "") or "", "content")
            state["theme"] = getattr(site, "template_dir", "") or ""
            # First-party live blocks (product-aware, e.g. Quantity Discounts):
            # rendered from commerce data at the theme's own markers.
            from commerce import storefront_blocks

            state["live"] = storefront_blocks.wanted(site)
            if state["bundle"] or state["live"]:
                state["paused"] = set(health.paused_slugs())
                if state["live"] and state["paused"]:
                    state["live"] = storefront_blocks.wanted(site, state["paused"])
            if state["live"]:
                # Only blocks that print UI need the app stylesheet.
                state["live_visual"] = storefront_blocks.wanted(site, state["paused"], visual_only=True)
    except Exception:
        logger.warning("app slots unavailable for this request", exc_info=True)
    if request is not None:
        request._ow_app_slots = state
    return state


def _fragments(state, slot: str) -> list[dict]:
    out = []
    for frag in state["bundle"].get(slot) or []:
        if frag.get("app") in state["paused"]:
            continue
        pages = frag.get("pages") or ["all"]
        if "all" not in pages and state["page"] not in pages:
            continue
        out.append(frag)
    return out


def _wrap(frag: dict, slot: str) -> str:
    cls = _DEVICE_CLASS.get(frag.get("devices") or "all", "")
    return format_html(
        '<div class="ow-app {}" data-ow-app="{}" data-ow-kind="{}">{}</div>',
        cls, frag.get("app", ""), frag.get("kind", ""), mark_safe(frag.get("html", "")),
    )


def _render(state, slot: str) -> str:
    parts = []
    for frag in _fragments(state, slot):
        try:
            parts.append(_wrap(frag, slot) if slot != "head.end" else frag.get("html", ""))
        except Exception as exc:
            try:
                from marketplace import health

                health.record_error(frag.get("app", ""), "slot", exc, {"slot": slot})
            except Exception:
                pass
    if parts:
        state["used"] = True
    return "".join(parts)


def _any_for_page(state) -> bool:
    return any(_fragments(state, slot) for slot in state["bundle"])


def _live(context, state, slot: str) -> tuple[str, str]:
    """Live blocks for ``slot`` (each guarded in commerce.storefront_blocks),
    as ``(visual_html, hidden_html)``: hidden ones (config / behaviour only,
    e.g. Quantity Discounts) print no UI and get no wrapper."""
    if not state.get("live"):
        return "", ""
    try:
        from commerce import storefront_blocks

        shown, hidden = [], []
        for _slug, html, assets, visual in storefront_blocks.render(slot, context, state["site"], state["paused"]):
            (shown if visual else hidden).append(html)
            for a in assets:
                if a not in state["assets"]:
                    state["assets"].append(a)
        if shown or hidden:
            state["used"] = True
        if shown:
            state["used_visual"] = True
        return "".join(shown), "".join(hidden)
    except Exception:
        logger.warning("live blocks failed in %s", slot, exc_info=True)
        return "", ""


@register.simple_tag(takes_context=True)
def app_head(context):
    """Stylesheet (only when this page has app content) + ``head.end`` fragments.
    Also admits approved pixel origins to this response's CSP."""
    try:
        state = _state(context)
        if not state["live_visual"] and (not state["bundle"] or not _any_for_page(state)):
            return ""
        request = context.get("request")
        origins = sorted({o for frags in state["bundle"].values() for f in frags
                          if f.get("app") not in state["paused"] for o in f.get("origins", [])})
        if origins and request is not None:
            extra = getattr(request, "csp_extra", None) or {}
            for directive in ("script-src", "connect-src"):
                extra.setdefault(directive, [])
                extra[directive].extend(o for o in origins if o not in extra[directive])
            request.csp_extra = extra
        head = _render(state, "head.end")
        return mark_safe(
            format_html('<link rel="stylesheet" href="{}">', static("css/ow-apps.css")) + head
        )
    except Exception:
        logger.warning("app_head failed", exc_info=True)
        return ""


@register.simple_tag(takes_context=True)
def app_slot(context, slot: str):
    """Render ``slot`` here. In a theme this is also the placement marker
    (theme contract): inline slots rendered in place are not mounted again by
    ``app_tail``/ow-slots.js, so nothing jumps after load and nothing needs JS."""
    try:
        from marketplace.blocks import INLINE_SLOTS

        state = _state(context)
        inline = slot in INLINE_SLOTS
        if inline:
            state["placed"].add(slot)
        if not state["bundle"] and not state["live"]:
            return ""
        shown, hidden = _live(context, state, slot)
        html = (_render(state, slot) if state["bundle"] else "") + shown
        if not html:
            return mark_safe(hidden)
        if inline:
            return format_html(
                '<div class="ow-slot ow-slot--{}" data-ow-slot-anchor="{}">{}</div>{}',
                slot.replace(".", "-"), slot, mark_safe(html), mark_safe(hidden),
            )
        return mark_safe(html + hidden)
    except Exception:
        logger.warning("app_slot %s failed", slot, exc_info=True)
        return ""


@register.simple_tag(takes_context=True)
def app_tail(context):
    """``body.end``, floating buttons, popup, inline-slot templates and the
    mounting script. Prints nothing when the page has no app content."""
    try:
        from marketplace.blocks import INLINE_SLOTS

        state = _state(context)
        has_bundle = bool(state["bundle"]) and _any_for_page(state)
        if not has_bundle and not state["used"]:
            return ""
        parts = []
        # Theme-contract safety net (fills --ow-* tokens a theme forgot) when
        # anything visible was printed, then the scripts of live blocks.
        if has_bundle or state["used_visual"]:
            parts.append(format_html('<script src="{}" defer></script>', static("js/marketplace/ow-theme.js")))
        for asset in state["assets"]:
            parts.append(format_html('<script src="{}" defer></script>', static(asset)))
        if not has_bundle:
            return mark_safe("".join(str(p) for p in parts))
        parts += [
            _render(state, "body.end"),
            format_html('<div class="ow-float ow-float--left">{}</div>', mark_safe(_render(state, "float.bottom_left"))),
            format_html('<div class="ow-float ow-float--right">{}</div>', mark_safe(_render(state, "float.bottom_right"))),
            _render(state, "popup"),
        ]
        for slot in INLINE_SLOTS:
            if slot in state["placed"]:
                continue  # rendered in place at the theme's marker
            html = _render(state, slot)
            if html:
                parts.append(format_html('<template data-ow-slot="{}">{}</template>', slot, mark_safe(html)))
        parts.append(
            format_html(
                '<script src="{}" defer data-ow-theme="{}"></script>',
                static("js/marketplace/ow-slots.js"), state.get("theme", ""),
            )
        )
        return mark_safe("".join(str(p) for p in parts))
    except Exception:
        logger.warning("app_tail failed", exc_info=True)
        return ""
