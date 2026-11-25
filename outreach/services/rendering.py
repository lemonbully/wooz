"""Render one delivery's email  subject, plain-text and tracked HTML.

Tracking is what turns a plain email into a measurable one, and it is injected
here so no call site has to remember it:

* a 1×1 open pixel just before ``</body>``,
* the call-to-action routed through a signed click endpoint,
* an unsubscribe link (always present  appended if the author left it out).

Each link carries a purpose-salted signed token (:mod:`core.tokens`) bound to
the delivery, so a token minted for the open pixel cannot be replayed against
the unsubscribe endpoint. URLs are absolute on the app surface and built without
a request, because rendering happens in a Celery worker.
"""

from __future__ import annotations

import re

from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import strip_tags

from core import tokens
from core.hosts import Surface
from core.links import surface_url

_APP_URLCONF = "onewooz.urls.app"

OPEN_PURPOSE = "outreach.open"
CLICK_PURPOSE = "outreach.click"
UNSUB_PURPOSE = "outreach.unsubscribe"


def _abs_url(name: str, token: str) -> str:
    path = reverse(f"outreach:{name}", args=[token], urlconf=_APP_URLCONF)
    return surface_url(Surface.APP, path)


def tracking_urls(delivery) -> dict:
    """Absolute open / click / unsubscribe URLs bound to ``delivery``."""
    did = str(delivery.id)
    return {
        "open_pixel_url": _abs_url("open", tokens.sign(OPEN_PURPOSE, {"d": did})),
        "click_url": _abs_url("click", tokens.sign(CLICK_PURPOSE, {"d": did})),
        "unsubscribe_url": _abs_url("unsub", tokens.sign(UNSUB_PURPOSE, {"d": did})),
    }


def _pixel_tag(url: str) -> str:
    return (
        f'<img src="{url}" width="1" height="1" alt="" '
        f'style="display:block;width:1px;height:1px;border:0;outline:none;'
        f'text-decoration:none;" />'
    )


def _substitute(html: str, *, values: dict) -> str:
    """Replace ``{{ key }}`` placeholders (any inner whitespace) with values."""
    out = html or ""
    for key, value in values.items():
        pattern = re.compile(r"\{\{\s*" + re.escape(key) + r"\s*\}\}", flags=re.IGNORECASE)
        out = pattern.sub(lambda _m: value, out)
    return out


def _finalise_owner_html(body: str, *, unsubscribe_url: str, pixel_url: str, brand: str) -> str:
    """Guarantee the author's HTML carries a tracking pixel and an unsubscribe
    link, appending an email-safe footer / pixel when they are missing."""
    html = body or ""

    if "unsubscribe" not in html.lower():
        footer = (
            '<div style="margin-top:28px;padding-top:16px;border-top:1px solid #d7d7d3;'
            "font-family:'SFMono-Regular',ui-monospace,Consolas,Menlo,monospace;"
            "font-size:11px;letter-spacing:0.06em;color:#9a9aa0;text-align:center;\">"
            f"{brand} &middot; "
            f'<a href="{unsubscribe_url}" style="color:#9a9aa0;">Unsubscribe</a>'
            "</div>"
        )
        if re.search(r"</body>", html, flags=re.IGNORECASE):
            html = re.sub(r"</body>", footer + "</body>", html, count=1, flags=re.IGNORECASE)
        else:
            html = html + footer

    if pixel_url not in html:
        pixel = _pixel_tag(pixel_url)
        if re.search(r"</body>", html, flags=re.IGNORECASE):
            html = re.sub(r"</body>", pixel + "</body>", html, count=1, flags=re.IGNORECASE)
        else:
            html = html + pixel
    return html


def preview_html(raw_html: str) -> str:
    """Substitute the pre-defined variables with *demo* values for a preview.

    Used by the admin template builder's live preview and the store composer's
    eye-preview, so an author sees a finished-looking email with demo content,
    structure and images before any real store data exists.
    """
    from outreach import variables

    return _substitute(raw_html or "", values=variables.demo_context())


def _inject_preheader(html: str, text: str) -> str:
    from django.utils.html import escape

    block = (
        '<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;'
        f'line-height:1px;color:#ffffff;opacity:0;">{escape(text)}&#8203;&#847;&#8203;&#847;</div>'
    )
    match = re.search(r"<body[^>]*>", html, flags=re.IGNORECASE)
    if match:
        return html[: match.end()] + block + html[match.end():]
    return block + html


def tracked_link(delivery, url: str) -> str:
    """A signed click URL that records the click and then lands on ``url``.

    Built-in designs carry several buttons, each with its own destination, so
    the destination travels inside the signed token (never as a free query
    parameter, which would make the endpoint an open redirect).
    """
    token = tokens.sign(CLICK_PURPOSE, {"d": str(delivery.id), "u": url})
    return _abs_url("click", token)


def _render_builtin_design(campaign, delivery) -> str:
    """HTML for a campaign that uses a built-in field-driven design, else ""."""
    if getattr(campaign, "design", "") != "promo":
        return ""
    from outreach import promo_design

    single = getattr(campaign, "product_mode", "") == "single"
    products = promo_design.load_products(
        campaign.site,
        ids=list(getattr(campaign, "product_ids", None) or []),
        limit=1 if single else promo_design.MAX_PRODUCTS,
        show_price=bool(getattr(campaign, "show_product_price", True)),
    )
    return promo_design.render(
        campaign.site,
        campaign.design_data or {},
        products=products,
        preheader=(campaign.preheader or "").strip(),
        link=lambda url: tracked_link(delivery, url),
        absolute=True,
    )


def render_delivery(campaign, delivery, step: dict | None = None) -> tuple[str, str, str]:
    """Return ``(subject, text, html)`` for one delivery.

    ``step`` is a drip step dict when the campaign is a drip; ``None`` for a
    broadcast (content is read off the campaign itself).
    """
    site = campaign.site
    track = tracking_urls(delivery)
    brand = getattr(site, "name", "") or "Our store"
    name = (delivery.recipient_name or "").strip() or "there"

    if step is not None:
        subject = (step.get("subject") or campaign.name or "").strip()
        raw_html = step.get("html") or ""
        plain_message = step.get("message") or ""
    else:
        subject = (campaign.subject or campaign.name or "").strip()
        raw_html = campaign.html or ""
        plain_message = ""
        design_html = _render_builtin_design(campaign, delivery)
        if design_html:
            raw_html = design_html

    from outreach import variables

    # Campaign-chosen products fill the {{products}} grid: one for a
    # single-product campaign, up to four for multi. Empty falls back to latest.
    product_ids = list(getattr(campaign, "product_ids", None) or [])
    product_limit = 1 if getattr(campaign, "product_mode", "") == "single" else 4

    values = variables.live_context(
        site,
        recipient_name=name,
        cta_url=track["click_url"],
        unsubscribe_url=track["unsubscribe_url"],
        tracking_pixel=_pixel_tag(track["open_pixel_url"]),
        product_ids=product_ids,
        product_limit=product_limit,
        show_price=bool(getattr(campaign, "show_product_price", True)),
    )

    raw_html = _substitute(raw_html, values=values)
    plain_message = _substitute(plain_message, values=values)

    # Published designs have no slot for the campaign's preview text: add it as
    # the standard hidden preheader so inboxes show it after the subject. (The
    # built-in design renders its own.)
    preheader = (campaign.preheader or "").strip() if step is None else ""
    if preheader and raw_html.strip() and getattr(campaign, "design", "") != "promo":
        raw_html = _inject_preheader(raw_html, preheader)

    if raw_html.strip():
        html = _finalise_owner_html(
            raw_html,
            unsubscribe_url=track["unsubscribe_url"],
            pixel_url=track["open_pixel_url"],
            brand=brand,
        )
    else:
        # No authored HTML  wrap the plain message (or the subject) in the
        # shared branded shell, with a tracked CTA when the campaign set one.
        html = render_to_string(
            "email/store/campaign.html",
            {
                "brand": brand,
                "title": subject,
                "preheader": (campaign.preheader or subject),
                "message": plain_message or campaign.preheader or "",
                "cta_label": (campaign.cta_label or "").strip(),
                "cta_url": track["click_url"] if (campaign.cta_label or campaign.cta_url) else "",
                "unsubscribe_url": track["unsubscribe_url"],
                "tracking_pixel_url": track["open_pixel_url"],
                "powered_by": True,
            },
        )

    # Drop the <head> (title, <style> media queries) so the plain-text part is
    # only the readable copy, then collapse the blank runs table markup leaves.
    body_only = re.sub(r"(?is)<head\b.*?</head>", "", html)
    text = strip_tags(body_only)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return subject[:255], text, html
