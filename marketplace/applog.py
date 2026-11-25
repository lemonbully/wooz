"""Working logs for marketplace apps (``marketplace.AppLog``).

Any app code writes a line with :func:`log`::

    from marketplace import applog
    applog.log("magic-seo", "run.started", "Writing SEO for 41 pages", site=site, run=run.pk)

A log call never raises and never slows the caller down meaningfully: values
are cut short, context is reduced to plain strings, and any failure is only
reported to the Python logger. Secrets must never be passed in (API keys,
tokens); :func:`log` also drops any context key that looks like one.

Read in the marketplace console (Logs) and Django admin > App logs. Lines
older than :data:`RETENTION_DAYS` are deleted by
``marketplace.tasks.purge_app_logs`` (daily, Celery beat).
"""

from __future__ import annotations

import logging

from django.conf import settings

logger = logging.getLogger("onewooz.marketplace.applog")

RETENTION_DAYS = int(getattr(settings, "APP_LOG_RETENTION_DAYS", 30) or 30)
_SECRETISH = ("api_key", "apikey", "token", "secret", "password", "authorization", "credential")
_LEVELS = {"info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}


def _plain(value, limit: int = 1500):
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_plain(v, 200) for v in list(value)[:20]]
    return str(value)[:limit]


def log(app_slug: str, event: str, message: str, *, site=None, level: str = "info", run=None, **context) -> None:
    """Write one log line for ``app_slug``. Never raises."""
    level = level if level in _LEVELS else "info"
    try:
        logger.log(_LEVELS[level], "[%s] %s: %s", app_slug, event, message)
    except Exception:
        pass
    try:
        from .models import AppLog

        clean = {
            str(k)[:60]: _plain(v)
            for k, v in context.items()
            if not any(s in str(k).lower() for s in _SECRETISH)
        }
        AppLog.objects.create(
            app_slug=str(app_slug or "")[:60],
            level=level,
            event=str(event or "")[:60],
            message=str(message or "")[:500],
            site=site if getattr(site, "pk", None) else None,
            site_label=str(getattr(site, "name", "") or "")[:160],
            run_ref=str(run or "")[:40],
            context=clean,
        )
    except Exception:
        logger.warning("could not write app log line for %s/%s", app_slug, event, exc_info=True)


def purge(days: int | None = None) -> int:
    """Delete lines older than ``days`` (default :data:`RETENTION_DAYS`), in
    small batches so a big backlog never locks the table for long."""
    from datetime import timedelta

    from django.utils import timezone

    from .models import AppLog

    cutoff = timezone.now() - timedelta(days=days or RETENTION_DAYS)
    total = 0
    while True:
        ids = list(AppLog.objects.filter(created_at__lt=cutoff).order_by().values_list("pk", flat=True)[:2000])
        if not ids:
            return total
        total += AppLog.objects.filter(pk__in=ids).delete()[0]
