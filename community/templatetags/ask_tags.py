"""Small display helpers for the Ask OneWooz templates."""

from __future__ import annotations

from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def ago(value) -> str:
    """Compact age: "just now", "12m", "5h", "3d", "4w", or a date."""
    if not value:
        return ""
    seconds = int((timezone.now() - value).total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    if seconds < 7 * 86400:
        return f"{seconds // 86400}d ago"
    if seconds < 30 * 86400:
        return f"{seconds // (7 * 86400)}w ago"
    local = timezone.localtime(value)
    return f"{local.day} {local.strftime('%b %Y')}"  # no "%-d": it fails on Windows


@register.filter
def compact(n) -> str:
    """1240 → "1.2k"."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    if n < 1000:
        return str(n)
    if n < 100000:
        return f"{n / 1000:.1f}".rstrip("0").rstrip(".") + "k"
    return f"{n // 1000}k"


@register.filter
def get_item(mapping, key):
    """``{{ dict|get_item:key }}`` for a key held in a variable."""
    try:
        return mapping.get(key, "")
    except AttributeError:
        return ""


@register.simple_tag(takes_context=True)
def is_voted(context, obj) -> bool:
    return str(obj.pk) in (context.get("voted") or set())
