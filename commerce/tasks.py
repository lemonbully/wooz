"""Periodic commerce housekeeping (run by Celery Beat)."""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger("onewooz.audit")

# An online order stays hidden while its payment is outstanding. Gateways keep a
# checkout payable for a while (Cashfree's default order expiry is 30 days), so
# only orders older than that are treated as abandoned  and even then only
# after one last authoritative check that the payment never came in.
_ABANDONED_AFTER = timedelta(days=35)


@shared_task(bind=True)
def purge_abandoned_checkouts(self, limit: int = 500) -> int:
    """Delete online orders whose payment never completed. Returns how many."""
    from commerce.checkout import discard_unpaid_order
    from commerce.models import Order, Payment
    from commerce.payments import confirm_by_fetch, gateway_config

    from core.queries import sorted_rows

    cutoff = timezone.now() - _ABANDONED_AFTER
    # Orders joined to their store's whole ``content`` JSON: sort the keys only,
    # so a large store can't run MySQL out of sort memory (error 1038).
    stale = sorted_rows(
        Order.all_objects.filter(status=Order.Status.AWAITING_PAYMENT, created_at__lt=cutoff)
        .select_related("site")
        .order_by("created_at"),
        limit=limit,
    )
    purged = 0
    for order in stale:
        try:
            cfg = gateway_config(order.site)
            paid = False
            for payment in Payment.all_objects.filter(order=order).exclude(provider_order_id=""):
                if payment.status == Payment.Status.PAID or (
                    cfg is not None and confirm_by_fetch(payment, cfg)
                ):
                    paid = True
                    break
            if not paid and discard_unpaid_order(order):
                purged += 1
        except Exception:
            logger.exception("purging abandoned checkout failed: order=%s", order.pk)
    return purged


# --------------------------------------------------------------------------
# Google Merchant Center (see commerce.google_merchant)
# --------------------------------------------------------------------------
def _merchant_config(site_id):
    from commerce.models import GoogleMerchantConfig

    cfg = GoogleMerchantConfig.all_objects.filter(site_id=site_id).select_related("site").first()
    return cfg if cfg is not None and cfg.is_connected else None


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def google_merchant_sync_product(self, product_id: str) -> str:
    """Push one product (after a save) to Google Merchant Center."""
    from commerce import google_merchant as gm
    from commerce.models import Product

    product = (
        Product.all_objects.filter(pk=product_id)
        .select_related("site", "category", "category__parent").first()
    )
    if product is None:
        return "missing"
    cfg = _merchant_config(product.site_id)
    if cfg is None or not cfg.auto_sync:
        return "skipped"
    try:
        client = gm.MerchantClient(cfg)
        client.ensure_data_source()
        return gm.sync_product(product, cfg, client=client)
    except gm.MerchantError as exc:
        if not exc.fatal and exc.status in (429, 500, 502, 503, 504, None):
            raise self.retry(exc=exc)
        logger.info("merchant product sync failed product=%s: %s", product_id, exc.message)
        return "error"


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def google_merchant_remove_offers(self, site_id: str, offer_ids: list) -> int:
    """Take a hard-deleted product's offers down."""
    from commerce import google_merchant as gm

    cfg = _merchant_config(site_id)
    if cfg is None:
        return 0
    gm.remove_offers(cfg, offer_ids)
    return len(offer_ids)


@shared_task(bind=True, soft_time_limit=3300, time_limit=3600)
def google_merchant_full_sync(self, site_id: str, force: bool = False, purge: dict | None = None) -> dict:
    """Sync a store's whole catalogue and read back approval statuses."""
    from commerce import google_merchant as gm

    cfg = _merchant_config(site_id)
    if cfg is None:
        return {}
    return gm.full_sync(cfg, force=force, purge=purge)


@shared_task(bind=True)
def google_merchant_refresh_all(self) -> int:
    """Beat: re-sync every connected auto-sync store (changed products only;
    unchanged ones are skipped by their payload hash) and refresh statuses.
    Every 7th day a forced push also renews offers before Google's 30-day
    expiry."""
    from commerce.models import GoogleMerchantConfig

    force = timezone.now().weekday() == 0 and timezone.now().hour < 6
    stuck_before = timezone.now() - timedelta(hours=2)
    count = 0
    configs = GoogleMerchantConfig.all_objects.filter(
        status=GoogleMerchantConfig.Status.CONNECTED, auto_sync=True
    ).exclude(merchant_id="")
    for cfg in configs.only("pk", "site_id", "sync_state", "sync_started_at"):
        if cfg.sync_state == GoogleMerchantConfig.SyncState.RUNNING and (
            cfg.sync_started_at and cfg.sync_started_at > stuck_before
        ):
            continue  # a sync is already running for this store
        google_merchant_full_sync.delay(str(cfg.site_id), force)
        count += 1
    return count


@shared_task(bind=True, soft_time_limit=900, time_limit=1000)
def check_shiprocket_wallets(self) -> int:
    """Beat: read every Shiprocket store's wallet balance, so an owner whose
    balance falls below ₹100 is emailed and notified even if they never open
    the dashboard (see :func:`commerce.shipping.check_all_shiprocket_wallets`)."""
    from commerce import shipping

    return shipping.check_all_shiprocket_wallets()


@shared_task(bind=True)
def send_order_update_email(self, order_id: str, kind: str, changes: list | None = None) -> bool:
    """Email the shopper that their order was updated / cancelled / returned
    (see :mod:`commerce.order_notify`). Best-effort: a failure is logged."""
    from commerce import order_notify

    return order_notify.send(order_id, kind, changes or [])


@shared_task(bind=True, soft_time_limit=600, time_limit=660)
def send_abandoned_cart_reminders(self) -> int:
    """Beat (every 5 minutes): email signed-in shoppers who left items in their
    cart, 1, 6, 24, 48 and 72 hours after their last cart change, at stores
    that switched abandoned cart emails on (see :mod:`commerce.abandoned_cart`)."""
    from commerce import abandoned_cart

    return abandoned_cart.send_due_reminders()


@shared_task(bind=True)
def generate_due_bills(self) -> int:
    """Beat (daily): every store's commerce fee bill is generated automatically
    once its 15-day billing period ends (see
    :func:`commerce.billing_service.generate_all_due_bills`)."""
    from commerce import billing_service

    return billing_service.generate_all_due_bills()


# Not acks_late: if a worker is killed mid-encode the message is dropped rather
# than redelivered forever; the video simply stays as uploaded.
@shared_task(bind=True, soft_time_limit=900, time_limit=960, ignore_result=True)
def optimise_video(self, key: str) -> str:
    """Re-encode one uploaded video in place (commerce.video_optimise).

    Queued by commerce.media_storage.MediaStorage after every video upload and
    by ``manage.py optimise_videos`` for videos uploaded before this existed.
    Routed to the ``media`` queue when ONEWOOZ_MEDIA_QUEUE is set, so a long
    encode never delays order emails on the default queue.
    """
    from commerce.video_optimise import optimise

    outcome = optimise(key)
    logging.getLogger("onewooz.media").info("optimise_video %s: %s", key, outcome)
    return outcome
