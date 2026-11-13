"""The send engine: turn a due campaign into per-recipient deliveries.

Design goals, carried over from a proven predecessor but rebuilt clean:

* **Idempotent & resumable.** A batch is a :class:`CampaignSendLog` unique per
  ``(campaign, day)``. Re-running dispatch never double-sends: a delivery is
  unique per ``(send_log, recipient_email)``, only PENDING/FAILED deliveries are
  (re)enqueued, and the batch finalises from counters, not guesswork.
* **Tenant-safe.** Every query is scoped with ``.for_site(site)``; the periodic
  scan alone opts out via ``.across_all_sites()``.
* **Compliant.** Suppressed addresses are filtered before every send, and the
  owner's monthly email allowance (from the plan entitlement) is enforced.

The actual SMTP/SES send runs in the Celery worker (:func:`outreach.tasks.send_one`),
never inline in a web request  dispatch only *enqueues*.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from outreach.models import Campaign, CampaignSendLog, EmailDelivery, Suppression
from outreach.services.limits import limits_for_site

logger = logging.getLogger(__name__)

# A batch that has been ENQUEUED/SENDING within this window is left alone by a
# re-dispatch, so a slow worker isn't second-guessed; older ones are resumed.
_RESUME_AFTER = timedelta(minutes=10)


def _norm(email) -> str:
    return (email or "").strip().lower()


def _suppressed_emails(site) -> set[str]:
    return {
        _norm(e)
        for e in Suppression.objects.for_site(site).values_list("email", flat=True)
        if e
    }


def _relative_day(campaign: Campaign, date) -> int:
    if campaign.start_at:
        start = timezone.localtime(campaign.start_at).date()
    else:
        start = date
    rel = (date - start).days + 1
    return 1 if rel < 1 else rel


def _start_time_reached(campaign: Campaign, now=None) -> bool:
    """True once the campaign's scheduled start moment has passed.

    For a drip this also gates each day's send to the campaign's start
    time-of-day, so Day 2 doesn't fire at midnight.
    """
    if not campaign.start_at:
        return True
    now = now or timezone.now()
    if now < campaign.start_at:
        return False
    if campaign.is_drip:
        start_local = timezone.localtime(campaign.start_at)
        now_local = timezone.localtime(now)
        scheduled_today = now_local.replace(
            hour=start_local.hour,
            minute=start_local.minute,
            second=start_local.second,
            microsecond=0,
        )
        return now_local >= scheduled_today
    return True


def _monthly_remaining(site, limits: dict) -> int | None:
    """Emails the store may still send this calendar month (``None`` = unlimited)."""
    cap = int(limits.get("monthly_email_limit") or 0)
    if cap <= 0:
        return None
    month_start = timezone.localdate().replace(day=1)
    used = (
        EmailDelivery.objects.for_site(site)
        .filter(
            created_at__date__gte=month_start,
            send_status__in=[
                EmailDelivery.SendStatus.SENT,
                EmailDelivery.SendStatus.SENDING,
                EmailDelivery.SendStatus.PENDING,
            ],
        )
        .count()
    )
    return max(0, cap - used)


def _resolve_recipients(campaign: Campaign) -> list[dict]:
    """Every eligible recipient for a campaign, deduped by lower-cased email.

    Union of the chosen audience lists and  when ticked  every customer of the
    store. Suppression and caps are applied later, per batch.
    """
    site = campaign.site
    seen: set[str] = set()
    out: list[dict] = []

    for lst in campaign.audience_lists.all():
        for m in lst.members.all():
            e = _norm(m.email)
            if not e or e in seen:
                continue
            seen.add(e)
            out.append(
                {
                    "email": e,
                    "name": (m.name or "").strip(),
                    "audience_member_id": m.id,
                    "customer_id": m.customer_id,
                }
            )

    if campaign.send_to_all_customers:
        from commerce.models import Customer

        for c in Customer.objects.for_site(site).filter(is_deleted=False):
            e = _norm(c.email)
            if not e or e in seen:
                continue
            seen.add(e)
            out.append(
                {
                    "email": e,
                    "name": (c.name or "").strip(),
                    "audience_member_id": None,
                    "customer_id": c.id,
                }
            )
    return out


def _claim_batch(campaign: Campaign, site, day: int, subject: str):
    """Get-or-create the batch log and decide whether to (re)enqueue it.

    Returns ``(log, should_enqueue)``. Concurrency-safe against the unique
    ``(campaign, day)`` constraint.
    """
    defaults = {
        "site": site,
        "subject": subject[:255],
        "status": CampaignSendLog.Status.ENQUEUED,
    }
    try:
        log, created = CampaignSendLog.objects.for_site(site).get_or_create(
            campaign=campaign, day=day, defaults=defaults
        )
    except IntegrityError:
        created = False
        log = CampaignSendLog.objects.for_site(site).filter(campaign=campaign, day=day).first()
        if log is None:
            raise

    if created:
        return log, True
    if log.status == CampaignSendLog.Status.SENT:
        return log, False
    age = (timezone.now() - log.updated_at).total_seconds() if log.updated_at else 1e9
    if log.status in (CampaignSendLog.Status.ENQUEUED, CampaignSendLog.Status.SENDING) and age < _RESUME_AFTER.total_seconds():
        return log, False
    # Stale or previously failed → resume.
    CampaignSendLog.objects.for_site(site).filter(pk=log.pk).update(
        status=CampaignSendLog.Status.ENQUEUED, last_error="", last_error_at=None, sent_at=None
    )
    log.refresh_from_db()
    return log, True


def _send_batch(campaign: Campaign, site, *, day: int, subject: str, limits: dict) -> int:
    """Build/resume one batch and enqueue its per-recipient send tasks."""
    log, should = _claim_batch(campaign, site, day, subject)
    if not should:
        return 0

    recipients = _resolve_recipients(campaign)
    suppressed = _suppressed_emails(site)
    recipients = [r for r in recipients if r["email"] not in suppressed]

    max_recipients = int(limits.get("max_recipients_per_campaign") or 0)
    if max_recipients > 0:
        recipients = recipients[:max_recipients]

    remaining = _monthly_remaining(site, limits)
    if remaining is not None:
        if remaining <= 0:
            _finalise_batch(log)  # nothing enqueued → deterministic close
            return 0
        recipients = recipients[:remaining]

    if not recipients:
        _finalise_batch(log)
        return 0

    from outreach import tasks

    # Create every delivery row and record the batch total *before* any send is
    # queued. Queuing first let a fast worker (or eager mode in development)
    # finish a send while ``total_enqueued`` was still 0, which finalised the
    # batch as "no eligible recipients" and marked the campaign Sent mid-send.
    to_send: list[str] = []
    for r in recipients:
        try:
            delivery, created = EmailDelivery.objects.for_site(site).get_or_create(
                send_log=log,
                recipient_email=r["email"],
                defaults={
                    "site": site,
                    "campaign": campaign,
                    "recipient_name": r["name"],
                    "audience_member_id": r.get("audience_member_id"),
                    "customer_id": r.get("customer_id"),
                    "subject": subject[:255],
                },
            )
        except IntegrityError:
            delivery = (
                EmailDelivery.objects.for_site(site)
                .filter(send_log=log, recipient_email=r["email"])
                .first()
            )
            created = False
            if delivery is None:
                continue

        if not created and delivery.send_status in (
            EmailDelivery.SendStatus.SENT,
            EmailDelivery.SendStatus.SENDING,
        ):
            continue
        to_send.append(str(delivery.id))

    total = EmailDelivery.objects.for_site(site).filter(send_log=log).count()
    CampaignSendLog.objects.for_site(site).filter(pk=log.pk).update(
        total_enqueued=total,
        subject=subject[:255],
        status=CampaignSendLog.Status.ENQUEUED,
        last_error="",
        last_error_at=None,
    )

    if total == 0:
        log.refresh_from_db()
        _finalise_batch(log)
        return 0

    def _enqueue():
        for delivery_id in to_send:
            tasks.send_one.delay(delivery_id)

    # Only after the rows above are committed, so a worker never looks up a
    # delivery that is not visible yet.
    transaction.on_commit(_enqueue)
    if not to_send:
        # Everything was already sent/sending (a resumed batch): let the
        # counters close it if they are complete.
        log.refresh_from_db()
        _finalise_batch(log)
    return len(to_send)


def dispatch_campaign(campaign: Campaign, date=None) -> int:
    """Enqueue whatever is due for one campaign today. Returns emails enqueued."""
    date = date or timezone.localdate()
    now = timezone.now()
    site = campaign.site

    if campaign.is_deleted:
        return 0
    if campaign.status not in (Campaign.Status.ACTIVE, Campaign.Status.SCHEDULED):
        return 0
    if not _start_time_reached(campaign, now):
        return 0

    # Move a scheduled campaign into the sending state on its first due tick.
    if campaign.status == Campaign.Status.SCHEDULED:
        Campaign.objects.for_site(site).filter(pk=campaign.pk).update(
            status=Campaign.Status.ACTIVE, started_at=campaign.started_at or now
        )
        campaign.status = Campaign.Status.ACTIVE

    limits = limits_for_site(site)

    if campaign.is_drip:
        if not campaign.drip_id:
            return 0
        day = _relative_day(campaign, date)
        steps = campaign.drip.steps_for_day(day)
        if not steps:
            # Past the last day with no step → the sequence is complete.
            if day > (campaign.drip.max_day or 0):
                Campaign.objects.for_site(site).filter(pk=campaign.pk).update(
                    status=Campaign.Status.SENT, completed_at=now
                )
            return 0
        subject = (steps[0].get("subject") or campaign.name or "").strip()
        return _send_batch(campaign, site, day=day, subject=subject, limits=limits)

    # Broadcast  a single day-1 batch.
    subject = (campaign.subject or campaign.name or "").strip()
    return _send_batch(campaign, site, day=1, subject=subject, limits=limits)


def requeue_held(campaign: Campaign) -> int:
    """Re-queue the PENDING deliveries of a campaign's unfinished batches.

    A pause leaves queued deliveries PENDING (the worker skips them); resuming
    sends them again. Re-queuing a delivery twice is harmless: the worker
    claims each delivery atomically and sends it once.
    """
    from outreach import tasks

    site = campaign.site
    ids = [
        str(pk)
        for pk in EmailDelivery.objects.for_site(site)
        .filter(
            campaign=campaign,
            sent_at__isnull=True,
            send_status=EmailDelivery.SendStatus.PENDING,
            send_log__status__in=[CampaignSendLog.Status.ENQUEUED, CampaignSendLog.Status.SENDING],
        )
        .values_list("pk", flat=True)
    ]

    def _enqueue():
        for delivery_id in ids:
            tasks.send_one.delay(delivery_id)

    transaction.on_commit(_enqueue)
    return len(ids)


def dispatch_due(date=None) -> int:
    """Scan every store for due campaigns and dispatch them (the beat entry)."""
    now = timezone.now()
    date = date or timezone.localdate()
    from django.db.models import Q

    from core.queries import sorted_rows

    # Each row joins the campaign's email HTML to its store's whole ``content``
    # JSON; sorting such rows can exceed MySQL's sort buffer (error 1038) and
    # would stop every store's campaigns. Sort keys only, then load the rows.
    due = sorted_rows(
        Campaign.objects.across_all_sites()
        .filter(is_deleted=False, status__in=[Campaign.Status.ACTIVE, Campaign.Status.SCHEDULED])
        .filter(Q(start_at__isnull=True) | Q(start_at__lte=now))
        .select_related("site", "drip")
        .order_by("created_at")
    )
    total = 0
    for campaign in due:
        try:
            total += dispatch_campaign(campaign, date)
        except Exception:
            logger.exception("outreach dispatch failed for campaign_id=%s", campaign.pk)
    return total


# ── batch finalisation (counter-driven) ─────────────────────────────────────

def _finalise_batch(log: CampaignSendLog) -> bool:
    """Move a batch to SENT/FAILED/SENDING purely from its counters."""
    now = timezone.now()
    total = int(log.total_enqueued or 0)
    done = int(log.sent_count or 0) + int(log.failed_count or 0)
    site = log.site

    if total <= 0:
        updated = (
            CampaignSendLog.objects.for_site(site)
            .filter(pk=log.pk)
            .exclude(status=CampaignSendLog.Status.FAILED)
            .update(
                status=CampaignSendLog.Status.FAILED,
                last_error="No eligible recipients (all suppressed, empty, or over the monthly limit).",
                last_error_at=now,
            )
        )
        _maybe_complete_campaign(log)
        return bool(updated)

    if done < total:
        if 0 < done:
            CampaignSendLog.objects.for_site(site).filter(
                pk=log.pk, status=CampaignSendLog.Status.ENQUEUED
            ).update(status=CampaignSendLog.Status.SENDING)
        return False

    if int(log.failed_count or 0) > 0 and int(log.sent_count or 0) == 0:
        CampaignSendLog.objects.for_site(site).filter(pk=log.pk).update(
            status=CampaignSendLog.Status.FAILED,
            last_error=(log.last_error or "Every recipient send failed."),
            last_error_at=now,
        )
    else:
        CampaignSendLog.objects.for_site(site).filter(pk=log.pk).update(
            status=CampaignSendLog.Status.SENT, sent_at=now
        )
    _maybe_complete_campaign(log)
    return True


def _maybe_complete_campaign(log: CampaignSendLog) -> None:
    """A broadcast is done once its single batch finalises."""
    campaign = log.campaign
    if campaign.is_drip:
        return
    if campaign.status in (Campaign.Status.SENT, Campaign.Status.ARCHIVED):
        return
    Campaign.objects.for_site(log.site).filter(pk=campaign.pk).update(
        status=Campaign.Status.SENT, completed_at=timezone.now()
    )


def maybe_finalise(log_id) -> None:
    """Finalise a batch if its counters say it is done (called after each send)."""
    log = CampaignSendLog.all_objects.filter(pk=log_id).select_related("campaign", "site").first()
    if log is None:
        return
    if log.status in (CampaignSendLog.Status.ENQUEUED, CampaignSendLog.Status.SENDING):
        _finalise_batch(log)


def reconcile(limit: int = 200) -> int:
    """Sweep unfinished batches and finalise those whose counters are complete."""
    from core.queries import sorted_rows

    # Joined campaign HTML + store content: sort the keys only (see dispatch_due).
    logs = sorted_rows(
        CampaignSendLog.objects.across_all_sites()
        .filter(status__in=[CampaignSendLog.Status.ENQUEUED, CampaignSendLog.Status.SENDING])
        .select_related("campaign", "site")
        .order_by("updated_at"),
        limit=max(1, int(limit or 200)),
    )
    updated = 0
    for log in logs:
        try:
            if _finalise_batch(log):
                updated += 1
        except Exception:
            logger.exception("outreach reconcile failed for log_id=%s", log.pk)
    return updated
