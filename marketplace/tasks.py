"""Background jobs for the marketplace. All routed to the ``marketplace`` queue
(``settings.CELERY_TASK_ROUTES``), so a burst of marketplace work can never
delay order emails, courier syncs or bills on the default queue.
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger("onewooz.marketplace")


# --------------------------------------------------------------------------
# Enqueue helpers (never raise into the caller)
# --------------------------------------------------------------------------
def enqueue_compile_owner(owner_ref) -> None:
    try:
        compile_owner_task.delay(str(owner_ref))
    except Exception:
        logger.warning("could not queue compile for owner %s; compiling inline", owner_ref, exc_info=True)
        try:
            from . import compiler

            compiler.compile_owner(owner_ref)
        except Exception:
            logger.exception("inline compile failed")


def enqueue_recompile_app(app_id, force: bool = False) -> None:
    try:
        recompile_app_task.delay(str(app_id), force=force)
    except Exception:
        logger.warning("could not queue recompile for app %s", app_id, exc_info=True)


# --------------------------------------------------------------------------
# Tasks
# --------------------------------------------------------------------------
@shared_task(name="marketplace.tasks.compile_owner_task", ignore_result=True)
def compile_owner_task(owner_ref: str) -> int:
    from . import compiler

    return compiler.compile_owner(owner_ref)


@shared_task(name="marketplace.tasks.compile_site_task", ignore_result=True)
def compile_site_task(site_id: str) -> None:
    from tenancy.models import Site

    from . import compiler

    site = Site.objects.filter(pk=site_id, is_deleted=False).select_related("owner").defer("content").first()
    if site is not None:
        compiler.compile_site(site)


@shared_task(name="marketplace.tasks.recompile_app_task", ignore_result=True)
def recompile_app_task(app_id: str, batch: int = 500, force: bool = False) -> int:
    """An app changed in the admin: recompile every store of every owner who has
    it, in batches of owners (oldest first) so a popular app never floods."""
    from .models import App, Installation

    app = App.objects.filter(pk=app_id).first()
    if app is None:
        return 0
    if not force and not app.storefront_placements.exists():
        return 0  # nothing of this app can be on a storefront
    if app.is_default:
        from tenancy.models import Site

        refs = list(
            Site.objects.filter(is_deleted=False).order_by().values_list("owner_id", flat=True).distinct()
        )
    else:
        refs = list(
            Installation.objects.filter(app=app).order_by().values_list("owner_ref", flat=True).distinct()
        )
    for start in range(0, len(refs), batch):
        compile_owners_batch_task.delay([str(r) for r in refs[start:start + batch]])
    return len(refs)


@shared_task(name="marketplace.tasks.compile_owners_batch_task", ignore_result=True)
def compile_owners_batch_task(owner_refs: list) -> int:
    from . import compiler

    n = 0
    for ref in owner_refs:
        try:
            n += compiler.compile_owner(ref)
        except Exception:
            logger.exception("batch compile failed for owner %s", ref)
    return n


@shared_task(name="marketplace.tasks.expire_licences_task", ignore_result=True)
def expire_licences_task() -> int:
    from . import licences

    return licences.expire_due()


@shared_task(name="marketplace.tasks.renewal_reminders_task", ignore_result=True)
def renewal_reminders_task() -> int:
    from . import reminders

    return reminders.send_due()


@shared_task(name="marketplace.tasks.breaker_tick_task", ignore_result=True)
def breaker_tick_task() -> None:
    from . import health

    health.tick()


@shared_task(name="marketplace.tasks.library_sweep_task", ignore_result=True)
def library_sweep_task(batch: int = 300) -> int:
    """Migrate owners who never log in (the lazy ensure covers the rest)."""
    from django.core.cache import cache

    from accounts.models import User

    from . import library

    cursor = cache.get("mkt:sweep:cursor") or ""
    qs = User.objects.filter(is_staff=False).order_by("id")
    if cursor:
        qs = qs.filter(id__gt=cursor)
    users = list(qs.only("id", "email")[:batch])
    for u in users:
        library.ensure(u)
    cache.set("mkt:sweep:cursor", str(users[-1].id) if len(users) == batch else "", None)
    return len(users)


@shared_task(name="marketplace.tasks.reconcile_orders_task", ignore_result=True)
def reconcile_orders_task() -> int:
    from . import orders

    return orders.reconcile_recent()


@shared_task(name="marketplace.tasks.send_alert_task", ignore_result=True)
def send_alert_task(kind: str, app_id: str, subject: str, body: str) -> int:
    from . import alerts

    return alerts.deliver(kind, app_id, subject, body)


@shared_task(name="marketplace.tasks.purge_app_logs", ignore_result=True)
def purge_app_logs() -> int:
    """Daily: delete app log lines past their retention (marketplace.applog)."""
    from . import applog

    return applog.purge()
