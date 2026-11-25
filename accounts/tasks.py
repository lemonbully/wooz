"""Background delivery for browser push notifications (see :mod:`accounts.webpush`)."""

from __future__ import annotations

from celery import shared_task


@shared_task(ignore_result=True)
def send_web_push(notification_id: str) -> int:
    """Pop one bell notification up on each of its owner's browsers.

    Not auto-retried: each browser is attempted once and its outcome recorded
    on the subscription, so a retry could only ever duplicate a popup that
    already reached the other browsers.
    """
    from accounts import webpush

    try:
        return webpush.send_notification(notification_id)
    except Exception:
        import logging

        logging.getLogger(__name__).exception("web push for notification %s failed", notification_id)
        return 0


@shared_task(name="accounts.tasks.close_lapsed_deleted_accounts", ignore_result=True)
def close_lapsed_deleted_accounts() -> int:
    """Close deleted owner accounts whose recovery window has passed.

    Nothing is erased (see :mod:`accounts.deletion`); the account simply can no
    longer sign in. Sign-in also checks the window itself, so a late run of this
    task never lets a lapsed account back in.
    """
    from .deletion import close_lapsed_accounts

    return close_lapsed_accounts()
