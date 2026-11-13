"""Celery tasks for email marketing.

The split matters for the deployment: **Celery Beat** (its own service) fires
:func:`dispatch_due_campaigns` on a schedule, which only *claims batches and
enqueues* work; the **background worker** (a separate service) runs
:func:`send_one`, where the actual SES/SMTP send happens. No email is ever sent
inside a web request  the dashboard "Send now" button just enqueues.

Reliability mirrors the platform's Celery config (``task_acks_late``): a send is
idempotent per delivery, so a worker dying mid-batch and requeuing never sends a
duplicate.
"""

from __future__ import annotations

import logging
import random
from datetime import timedelta
from email.utils import formataddr

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db.models import F
from django.utils import timezone

logger = logging.getLogger(__name__)

_STALE_SENDING = timedelta(minutes=15)


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=5, retry_kwargs={"max_retries": 5})
def dispatch_due_campaigns(self) -> int:
    """Beat entry: scan every store for due campaigns and enqueue their sends."""
    from outreach.services import engine

    return engine.dispatch_due()


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=2, retry_kwargs={"max_retries": 3})
def dispatch_campaign(self, campaign_id: str) -> int:
    """Dispatch one campaign now (used by the dashboard 'Send now' / resume)."""
    from outreach.models import Campaign
    from outreach.services import engine

    campaign = (
        Campaign.all_objects.filter(pk=campaign_id)
        .select_related("site", "drip")
        .first()
    )
    if campaign is None:
        return 0
    return engine.dispatch_campaign(campaign)


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=2, retry_kwargs={"max_retries": 3})
def resume_campaign(self, campaign_id: str) -> int:
    """Resume a paused campaign: re-queue the deliveries the pause held back,
    then dispatch anything newly due."""
    from outreach.models import Campaign
    from outreach.services import engine

    campaign = (
        Campaign.all_objects.filter(pk=campaign_id)
        .select_related("site", "drip")
        .first()
    )
    if campaign is None:
        return 0
    return engine.requeue_held(campaign) + engine.dispatch_campaign(campaign)


@shared_task(bind=True)
def reconcile_batches(self, limit: int = 200) -> int:
    from outreach.services import engine

    try:
        return int(engine.reconcile(limit=limit))
    except Exception:
        logger.exception("outreach reconcile_batches failed")
        return 0


# Per-worker ceiling on sends, so a large list never outruns the SES / SMTP
# sending rate (throttled sends would burn their retries and fail).
_SEND_RATE = getattr(settings, "OUTREACH_SEND_RATE_LIMIT", "10/s")


@shared_task(bind=True, max_retries=6, rate_limit=_SEND_RATE)
def send_one(self, delivery_id: str) -> bool:
    """Send a single delivery. Idempotent and self-finalising for its batch."""
    from outreach.models import CampaignSendLog, EmailDelivery
    from outreach.services import engine, rendering
    from core.mail import store_mail_connection
    from tenancy.email import store_from_address

    now = timezone.now()

    delivery = (
        EmailDelivery.all_objects.filter(pk=delivery_id)
        .select_related("campaign", "campaign__drip", "site", "send_log")
        .first()
    )
    if delivery is None:
        return False
    if delivery.sent_at:
        return True

    site = delivery.site
    campaign = delivery.campaign
    log = delivery.send_log

    # A paused (or removed) campaign holds its queued sends: leave the delivery
    # PENDING so resuming picks it up again (see resume_campaign).
    if campaign.is_deleted or campaign.status in (
        campaign.Status.PAUSED,
        campaign.Status.ARCHIVED,
        campaign.Status.DRAFT,
    ):
        return False

    # ── idempotency: claim this delivery (PENDING / FAILED / stale SENDING) ──
    from django.db.models import Q

    stale_before = now - _STALE_SENDING
    claimed = (
        EmailDelivery.all_objects.filter(pk=delivery.pk, sent_at__isnull=True)
        .filter(
            Q(send_status__in=[EmailDelivery.SendStatus.PENDING, EmailDelivery.SendStatus.FAILED])
            | Q(send_status=EmailDelivery.SendStatus.SENDING, sending_at__lt=stale_before)
            | Q(send_status=EmailDelivery.SendStatus.SENDING, sending_at__isnull=True)
        )
        .update(
            send_status=EmailDelivery.SendStatus.SENDING,
            sending_at=now,
            send_attempts=F("send_attempts") + 1,
        )
    )
    if claimed == 0:
        return True  # already sent, or another worker owns it

    # ── build ──
    step = None
    if campaign.is_drip and campaign.drip_id and log is not None:
        steps = campaign.drip.steps_for_day(log.day)
        step = steps[0] if steps else None

    try:
        subject, text, html = rendering.render_delivery(campaign, delivery, step)

        reply_to = (campaign.reply_to or "").strip() or (getattr(site, "support_email", "") or "")

        if getattr(settings, "USE_ONEWOOZ_EMAIL", False):
            # TEMPORARY: send from OneWooz's own identity (hello@onewooz.com) over
            # the already-configured platform SMTP credentials, not the store's
            # branded SES address. A reply-to still points back to the store.
            from_email = settings.DEFAULT_FROM_EMAIL
            connection = get_connection(fail_silently=False)
        else:
            from_name = (campaign.from_name or "").strip()
            if from_name:
                from_email = formataddr((from_name, store_from_address(site)))
            else:
                from_email = site.customer_from_header
            connection = store_mail_connection(fail_silently=False)

        # One-click unsubscribe (RFC 8058): Gmail and Yahoo require it of bulk
        # senders and show their own "Unsubscribe" button from it; without it
        # marketing mail is far more likely to land in spam.
        unsub_url = rendering.tracking_urls(delivery)["unsubscribe_url"]
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text,
            from_email=from_email,
            to=[delivery.recipient_email],
            reply_to=[reply_to] if reply_to else None,
            connection=connection,
            headers={
                "List-Unsubscribe": f"<{unsub_url}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            },
        )
        msg.attach_alternative(html, "text/html")
        msg.send(fail_silently=False)
    except Exception as exc:
        err = str(exc)[:2000]
        if self.request.retries < self.max_retries:
            EmailDelivery.all_objects.filter(pk=delivery.pk, sent_at__isnull=True).update(
                send_status=EmailDelivery.SendStatus.PENDING,
                sending_at=None,
                last_error=err,
                last_error_at=now,
            )
            # Exponential backoff with jitter: a throttled provider needs the
            # queue to spread out, not to retry in lock-step.
            delay = min(2 ** (self.request.retries + 2), 300) + random.randint(0, 15)
            raise self.retry(exc=exc, countdown=delay)

        # Final failure.
        failed = EmailDelivery.all_objects.filter(pk=delivery.pk, sent_at__isnull=True).exclude(
            send_status=EmailDelivery.SendStatus.SENT
        ).update(
            send_status=EmailDelivery.SendStatus.FAILED,
            last_error=err,
            last_error_at=now,
        )
        if failed and log is not None:
            CampaignSendLog.all_objects.filter(pk=log.pk).update(
                failed_count=F("failed_count") + 1, last_error=err, last_error_at=now
            )
            engine.maybe_finalise(log.pk)
        return False

    # ── success ──
    marked = EmailDelivery.all_objects.filter(pk=delivery.pk, sent_at__isnull=True).update(
        send_status=EmailDelivery.SendStatus.SENT,
        sent_at=now,
        last_error="",
        last_error_at=None,
    )
    if marked and log is not None:
        CampaignSendLog.all_objects.filter(pk=log.pk).update(sent_count=F("sent_count") + 1)
        engine.maybe_finalise(log.pk)
    return True
