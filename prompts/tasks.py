"""Celery jobs for store llms.txt generation (see :mod:`prompts.llms_txt`)."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger("onewooz.llms_txt")


@shared_task(bind=True)
def generate_store_llms_txt(self, site_id: str, force: bool = False) -> str:
    """Check one store and (re)generate its llms.txt when its data changed."""
    from django.core.cache import cache

    from tenancy.models import Site

    from . import llms_txt

    # The debounce slot opens again so a save made during this run queues the
    # next one; the run lock stops two workers generating the same store.
    try:
        cache.delete(f"llms_txt:queued:{site_id}")
        if not cache.add(f"llms_txt:running:{site_id}", 1, 600):
            return "busy"
    except Exception:
        pass
    try:
        site = Site.objects.filter(pk=site_id).first()
        if site is None:
            return "missing"
        return llms_txt.run(site, force=force)
    finally:
        try:
            cache.delete(f"llms_txt:running:{site_id}")
        except Exception:
            pass


#: Seconds between two stores' API calls when a sweep queues many at once.
_SPREAD_SECONDS = 20
#: Held while an admin "Generate" sweep is running, so a double click (or two
#: admins) cannot queue every store twice.
BULK_LOCK_KEY = "llms_txt:bulk_generate"
BULK_LOCK_SECONDS = 15 * 60


def eligible_site_ids() -> list:
    """Stores live on a verified custom domain whose llms.txt is not published
    yet (no record, or waiting / failed / stuck generating). Cheap: ids only."""
    from tenancy.models import Site

    from .models import StoreLlmsTxt

    published = set(
        StoreLlmsTxt.objects.across_all_sites()
        .filter(status=StoreLlmsTxt.Status.READY)
        .order_by()
        .values_list("site_id", flat=True)
    )
    ids = (
        Site.objects.filter(custom_domain_verified=True, maintenance_mode=False)
        .exclude(custom_domain="")
        .order_by()
        .values_list("pk", flat=True)
    )
    return [pk for pk in ids if pk not in published]


@shared_task
def generate_missing_store_llms_txt() -> dict:
    """Admin "Generate" button (Prompts page): generate llms.txt for every store
    that does not have one yet but whose required content is filled in.

    Each store is checked against the readiness requirements here, so a store
    still missing content costs nothing beyond its status being recorded; ready
    ones are queued one by one, spread out, through the normal per-store job.
    """
    from django.core.cache import cache

    from tenancy.models import Site

    from . import llms_txt
    from .models import StoreLlmsTxt

    queued = waiting = 0
    try:
        for pk in eligible_site_ids():
            site = Site.objects.filter(pk=pk).first()
            if site is None:
                continue
            try:
                missing = llms_txt.missing_requirements(site)
            except Exception:
                logger.exception("llms.txt readiness check failed for site %s", pk)
                continue
            if missing:
                # Record why, so admin's "Store llms.txt files" list shows it.
                record = StoreLlmsTxt.objects.for_site(site).first() or StoreLlmsTxt(site=site)
                if record.status != StoreLlmsTxt.Status.NOT_READY or record.missing != missing:
                    record.status, record.missing = StoreLlmsTxt.Status.NOT_READY, missing
                    record.save()
                waiting += 1
                continue
            generate_store_llms_txt.apply_async(
                args=[str(pk), True], countdown=queued * _SPREAD_SECONDS,
            )
            queued += 1
    finally:
        try:
            cache.delete(BULK_LOCK_KEY)
        except Exception:
            pass
    logger.info("llms.txt bulk generate: %s queued, %s waiting for content", queued, waiting)
    return {"queued": queued, "waiting_for_content": waiting}


@shared_task
def refresh_all_store_llms_txt() -> int:
    """Daily: re-check every store on a verified custom domain, so catalogue
    changes made outside Edit Content reach its llms.txt. A store whose data is
    unchanged costs no API call (fingerprint match)."""
    from tenancy.models import Site

    ids = list(
        Site.objects.filter(custom_domain_verified=True, maintenance_mode=False)
        .exclude(custom_domain="")
        .order_by()
        .values_list("pk", flat=True)
    )
    for i, pk in enumerate(ids):
        # Spread the runs out instead of hitting the API in one burst.
        generate_store_llms_txt.apply_async(args=[str(pk)], countdown=i * _SPREAD_SECONDS)
    return len(ids)


@shared_task(bind=True, ignore_result=True)
def answer_ask_question(self, question_id: str) -> str:
    """Answer one onewooz.com/ask question with the active Ask prompt."""
    from django.core.cache import cache

    from . import ask
    from .models import AskQuestion

    lock = f"ask:answering:{question_id}"
    try:
        if not cache.add(lock, 1, 300):
            return "busy"
    except Exception:
        pass
    try:
        question = AskQuestion.objects.filter(pk=question_id).first()
        if question is None:
            return "missing"
        return ask.answer(question)
    finally:
        try:
            cache.delete(lock)
        except Exception:
            pass


#: A big catalogue is written in many API calls; give the job room beyond the
#: global 10-minute task limit (runs report progress as they go).
MAGIC_SEO_TIME_LIMIT = 30 * 60


@shared_task(bind=True, ignore_result=True, time_limit=MAGIC_SEO_TIME_LIMIT,
             soft_time_limit=MAGIC_SEO_TIME_LIMIT - 60)
def generate_magic_seo(self, run_id: str) -> str:
    """Write the SEO tab of one store (one Magic SEO pencil click)."""
    from django.core.cache import cache

    from . import magic_seo
    from .models import MagicSeoRun

    lock = f"magic_seo:running:{run_id}"
    try:
        if not cache.add(lock, 1, MAGIC_SEO_TIME_LIMIT):
            return "busy"
    except Exception:
        pass
    try:
        run = MagicSeoRun.objects.across_all_sites().select_related("site").filter(pk=run_id).first()
        if run is None:
            return "missing"
        try:
            return magic_seo.execute(run)
        except Exception as exc:  # never leave a run spinning in the editor
            logger.exception("magic seo run %s crashed", run_id)
            MagicSeoRun.objects.across_all_sites().filter(
                pk=run_id, status__in=[MagicSeoRun.Status.QUEUED, MagicSeoRun.Status.RUNNING]
            ).update(status=MagicSeoRun.Status.FAILED, error=str(exc)[:2000])
            return "failed"
    finally:
        try:
            cache.delete(lock)
        except Exception:
            pass
