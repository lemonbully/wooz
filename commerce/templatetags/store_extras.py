"""Small template helpers for the store surface."""

from __future__ import annotations

from django import template

register = template.Library()


@register.filter
def dictkey(mapping, key):
    """Look up ``mapping[key]`` from a template. Returns 0 when absent so count
    badges render cleanly."""
    if hasattr(mapping, "get"):
        return mapping.get(key, 0)
    return 0


@register.filter
def split(value, separator=","):
    """Split a string into a list — used to build step labels inline."""
    return (value or "").split(separator)


@register.filter
def widget_type(bound_field):
    """Return a bound field's widget class name, e.g. 'Textarea', so templates
    can lay a field out by its input kind."""
    try:
        return bound_field.field.widget.__class__.__name__
    except AttributeError:
        return ""


@register.filter
def money(value, currency="INR"):
    """Format a Decimal as ``INR 1,234`` with thousands separators."""
    try:
        return f"{currency} {value:,.2f}"
    except (TypeError, ValueError):
        return f"{currency} 0.00"
