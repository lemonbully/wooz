"""Error accounting, the per-app circuit breaker, and the paused set.

* Every app error (storefront slot render, compile, hook, dashboard page) is
  counted in the cache per minute: ``mkt:err:<slug>:<minute>``.
* Over the app's threshold within its window the app is **auto-paused**: its
  slug joins the paused set ``mkt:paused`` (read by the storefront slot tag and
  the dashboard facade), staff are alerted, and after the pause it is tried
  again (half-open). Failing again doubles the pause.
* Pausing never touches licences, installations or owners' settings.

If the cache is down, :func:`paused_slugs` returns the database's view (or
nothing): fail-open to the last approved compiled output, never to an error page.
"""

from __future__ import annotations

import logging
import time
import traceback
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger("onewooz.marketplace")

PAUSED_KEY = "mkt:paused"
MAX_PAUSE_MINUTES = 60 * 24


def _slug(app) -> str:
    return app if isinstance(app, str) else getattr(app, "slug", "") or ""


def paused_slugs() -> list[str]:
    try:
        value = cache.get(PAUSED_KEY)
        if value is not None:
            return list(value)
    except Exception:
        return []
    return refresh_paused_set()


def refresh_paused_set() -> list[str]:
    try:
        from .models import App

        now = timezone.now()
        slugs = []
        for slug, until in App.objects.filter(is_paused=True).values_list("slug", "paused_until"):
            if until is None or until > now:
                slugs.append(slug)
        cache.set(PAUSED_KEY, slugs, 60 * 60)
        return slugs
    except Exception:
        return []


def record_error(app, where: str, exc: BaseException | None = None, context: dict | None = None) -> None:
    """Count one error for ``app``; trip the breaker when over threshold. Never raises."""
    slug = _slug(app)
    if not slug:
        return
    try:
        minute = int(time.time() // 60)
        key = f"mkt:err:{slug}:{minute}"
        if not cache.add(key, 1, 60 * 70):
            try:
                cache.incr(key)
            except ValueError:
                cache.set(key, 1, 60 * 70)
        sample = {
            "where": where,
            "error": (f"{type(exc).__name__}: {exc}" if exc else "")[:500],
            "trace": ("".join(traceback.format_exception(exc))[-2000:] if exc else ""),
            "context": {k: str(v)[:200] for k, v in (context or {}).items()},
            "at": timezone.now().isoformat(),
        }
        cache.set(f"mkt:errsample:{slug}", sample, 60 * 60 * 24)
        # One log line per app, place and minute: an outage must not flood
        # the logs table with thousands of identical rows.
        if cache.add(f"mkt:applog:err:{slug}:{where}:{minute}", 1, 120):
            from . import applog

            applog.log(slug, "error", f"{where}: {sample['error'] or 'error'}", level="error",
                       where=where, details=sample["context"], trace=sample["trace"][-1500:])
        _maybe_trip(slug, minute, sample)
    except Exception:
        logger.exception("marketplace error accounting failed")


def errors_in_window(slug: str, minutes: int, now_minute: int | None = None) -> int:
    now_minute = now_minute if now_minute is not None else int(time.time() // 60)
    keys = [f"mkt:err:{slug}:{m}" for m in range(now_minute - max(1, minutes) + 1, now_minute + 1)]
    try:
        return sum(int(v or 0) for v in cache.get_many(keys).values())
    except Exception:
        return 0


def _maybe_trip(slug: str, minute: int, sample: dict) -> None:
    from .models import App, AppHealth

    # Cheap guard: only look at the DB once a minute per app while erroring.
    if not cache.add(f"mkt:tripcheck:{slug}:{minute}", 1, 120):
        return
    app = App.objects.filter(slug=slug).first()
    if app is None or app.is_paused:
        return
    health, _ = AppHealth.objects.get_or_create(app=app)
    health.errors_total = (health.errors_total or 0) + 1
    health.last_error_at = timezone.now()
    health.last_error = sample.get("error", "")
    health.last_error_context = sample
    health.save()
    count = errors_in_window(slug, app.breaker_window_minutes, minute)
    if count < max(1, app.breaker_threshold):
        return
    minutes = app.breaker_pause_minutes or 15
    if health.state == AppHealth.State.HALF_OPEN and health.current_pause_minutes:
        minutes = min(MAX_PAUSE_MINUTES, health.current_pause_minutes * 2)
    pause(app, minutes=minutes, reason=f"Auto-paused: {count} errors in {app.breaker_window_minutes} min",
          by="circuit breaker", auto=True)


def pause(app, *, minutes: int | None, reason: str, by: str, auto: bool = False) -> None:
    from core import audit

    from .models import AppHealth

    now = timezone.now()
    app.is_paused = True
    app.paused_until = (now + timedelta(minutes=minutes)) if (minutes and app.auto_resume) else None
    app.pause_reason = reason[:200]
    app.save(update_fields=["is_paused", "paused_until", "pause_reason", "updated_at"])
    health, _ = AppHealth.objects.get_or_create(app=app)
    health.state = AppHealth.State.OPEN
    health.opened_at = now
    health.open_count = (health.open_count or 0) + 1
    health.current_pause_minutes = minutes or 0
    health.save()
    refresh_paused_set()
    from . import applog

    applog.log(app.slug, "app.paused", f"Paused by {by}: {reason}", level="warning", minutes=minutes, auto=auto)
    audit.record(
        "marketplace.app.paused", actor_kind="system" if auto else "admin", target=app,
        target_kind="App", target_label=app.name, severity="warning",
        context={"reason": reason, "by": by, "minutes": minutes},
    )
    from . import alerts

    alerts.notify(
        "app_paused", app,
        subject=f"[OneWooz Apps] {app.name} was paused",
        body=(
            f"{app.name} ({app.slug}) was paused by {by}.\n\nReason: {reason}\n"
            f"Pause length: {minutes or 'until resumed by staff'} minutes.\n\n"
            "Its dashboard pages and storefront widgets are hidden on every store until it resumes. "
            "Licences and owners' settings are untouched.\n\n"
            f"Last error: {(health.last_error or '-')}"
        ),
    )


def resume(app, *, by: str, half_open: bool = False) -> None:
    from core import audit

    from .models import AppHealth

    app.is_paused = False
    app.paused_until = None
    app.pause_reason = ""
    app.save(update_fields=["is_paused", "paused_until", "pause_reason", "updated_at"])
    health, _ = AppHealth.objects.get_or_create(app=app)
    health.state = AppHealth.State.HALF_OPEN if half_open else AppHealth.State.CLOSED
    if not half_open:
        health.current_pause_minutes = 0
    health.save()
    refresh_paused_set()
    from . import applog

    applog.log(app.slug, "app.resumed", f"Resumed by {by}" + (" (trying again)" if half_open else ""))
    audit.record("marketplace.app.resumed", actor_kind="system" if half_open else "admin",
                 target=app, target_kind="App", target_label=app.name, context={"by": by})
    from . import alerts

    alerts.notify("app_resumed", app, subject=f"[OneWooz Apps] {app.name} resumed",
                  body=f"{app.name} ({app.slug}) was resumed by {by}.")


def tick() -> None:
    """Beat, every minute: resume apps whose pause ended (half-open) and close
    the breaker on apps that stayed healthy for a full window."""
    from .models import App, AppHealth

    now = timezone.now()
    for app in App.objects.filter(is_paused=True, paused_until__isnull=False, paused_until__lte=now):
        try:
            resume(app, by="circuit breaker (trying again)", half_open=True)
        except Exception:
            logger.exception("auto-resume failed for %s", app.slug)
    for health in AppHealth.objects.filter(state=AppHealth.State.HALF_OPEN).select_related("app"):
        app = health.app
        if app.is_paused:
            continue
        if errors_in_window(app.slug, app.breaker_window_minutes) == 0:
            health.state = AppHealth.State.CLOSED
            health.current_pause_minutes = 0
            health.save(update_fields=["state", "current_pause_minutes", "updated_at"])
