"""Background jobs for platform billing."""

from __future__ import annotations

from celery import shared_task


@shared_task(name="billing.tasks.expire_template_licences")
def expire_template_licences() -> int:
    """Daily: move stores off website templates whose yearly licence lapsed."""
    from . import website_templates

    return website_templates.enforce_all_expired()
