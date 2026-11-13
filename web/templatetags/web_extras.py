"""Template helpers for the onewooz.com marketing site."""

from __future__ import annotations

from django import template
from django.db import DatabaseError

register = template.Library()


@register.simple_tag
def footer_policies():
    """Admin-authored legal policies for the footer's "Legal" column (after the
    built-in Privacy / Terms links). Empty if the table isn't migrated yet, so
    the footer never breaks."""
    from web.models import LegalPolicy

    try:
        return LegalPolicy.footer_links()
    except DatabaseError:
        return []
