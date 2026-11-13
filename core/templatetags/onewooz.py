"""Template helpers for OneWooz."""

from __future__ import annotations

from django import template

from core import links

register = template.Library()


@register.simple_tag(takes_context=True)
def surface_url(context, surface, path="/"):
    """Absolute URL to a path on another surface.

    ``{% surface_url 'app' '/sign-up/' %}`` renders
    ``https://app.onewooz.com/sign-up/`` in production and
    ``http://app.onewooz.localhost:8000/sign-up/`` locally.

    Only for links that genuinely cross surfaces. A link within the current
    surface should be a plain path or ``{% url %}``, so it stays on whichever
    host the visitor is already on.
    """
    return links.surface_url(surface, path, request=context.get("request"))


def _brand() -> dict:
    try:
        from web.seo_overrides import brand_assets

        return brand_assets()
    except Exception:
        return {}


@register.simple_tag
def brand_logo_url():
    """The OneWooz logo: the one uploaded in admin → onewooz.com SEO, else the
    built-in file. ``<img src="{% brand_logo_url %}">``"""
    from django.templatetags.static import static

    return _brand().get("logo") or static("img/final_onewooz.png")


@register.simple_tag
def brand_favicon_links(apple_touch=True):
    """The favicon ``<link>`` tags for every OneWooz page.

    Built-in: the light/dark pair. Uploaded: the favicon (plus the dark-mode
    one when set; without it the favicon serves both modes)."""
    from django.templatetags.static import static
    from django.utils.html import format_html_join
    from django.utils.safestring import mark_safe

    brand = _brand()
    light = brand.get("favicon")
    dark = brand.get("favicon_dark")
    if not light and not dark:
        light = static("img/OneWooz_favicon_white_theme.png")
        dark = static("img/OneWooz_favicon_dark_theme.png")
    elif not light:
        light = static("img/OneWooz_favicon_white_theme.png")
    rows = []
    if dark:
        rows.append(('icon', ' media="(prefers-color-scheme: dark)"', dark))
        rows.append(('icon', ' media="(prefers-color-scheme: light)"', light))
    rows.append(("icon", "", light))
    if apple_touch:
        rows.append(("apple-touch-icon", "", light))
    # The media fragments are fixed literals above; hrefs are escaped.
    return format_html_join(
        "\n", '<link rel="{}"{} href="{}">', ((rel, mark_safe(media), href) for rel, media, href in rows)
    )


@register.simple_tag(takes_context=True)
def storefront_url(context, slug, path="/"):
    """Absolute URL to a generated storefront on the platform apex."""
    origin = links.storefront_origin(slug, request=context.get("request"))
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{origin}{path}"
