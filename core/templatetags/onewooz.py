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


@register.simple_tag(takes_context=True)
def storefront_url(context, slug, path="/"):
    """Absolute URL to a generated storefront on the platform apex."""
    origin = links.storefront_origin(slug, request=context.get("request"))
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{origin}{path}"
