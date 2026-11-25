"""Staff alerts. Recipients are managed in the Django admin
(Marketplace -> Marketplace alert recipients).

Each alert is de-duplicated per (kind, app, recipient) for the recipient's
``min_interval_minutes``, logged as a ``MarketplaceAlert`` row with its delivery
result, and sent from the ``marketplace`` Celery queue. With no recipient
configured, alerts fall back to ``settings.ADMINS`` so nothing is lost.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger("onewooz.marketplace")


def notify(kind: str, app, *, subject: str, body: str) -> None:
    """Queue an alert. Never raises."""
    try:
        from . import tasks

        tasks.send_alert_task.delay(kind, str(app.pk) if app is not None else "", subject[:200], body[:5000])
    except Exception:
        try:
            deliver(kind, str(app.pk) if app is not None else "", subject, body)
        except Exception:
            logger.exception("marketplace alert could not be queued or sent")


def deliver(kind: str, app_id: str, subject: str, body: str) -> int:
    from django.core.mail import send_mail

    from .models import App, MarketplaceAlert, MarketplaceAlertRecipient

    app = App.objects.filter(pk=app_id).first() if app_id else None
    to: list[str] = []
    for r in MarketplaceAlertRecipient.objects.filter(is_active=True).prefetch_related("apps"):
        if not r.wants(kind, app):
            continue
        dedupe = f"mkt:alert:{kind}:{app_id}:{r.pk}"
        if not cache.add(dedupe, 1, max(1, r.min_interval_minutes) * 60):
            continue
        to.append(r.email)
    if not to and not MarketplaceAlertRecipient.objects.filter(is_active=True).exists():
        to = [email for _, email in getattr(settings, "ADMINS", []) if email]
    record = MarketplaceAlert.objects.create(
        kind=kind, app=app, subject=subject[:200], body=body, recipients=", ".join(to),
    )
    if not to:
        return 0
    try:
        send_mail(subject, body, getattr(settings, "DEFAULT_FROM_EMAIL", None), to, fail_silently=False)
        record.delivered = True
        record.save(update_fields=["delivered", "updated_at"])
    except Exception as exc:
        record.error = str(exc)[:2000]
        record.save(update_fields=["error", "updated_at"])
        logger.exception("marketplace alert delivery failed")
    return len(to)
