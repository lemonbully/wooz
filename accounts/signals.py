"""Signal wiring for browser push notifications.

* A new bell :class:`~accounts.models.Notification`, however it was created
  (``accounts.notify.push``, the admin, a Celery job), is queued for push
  delivery once its transaction commits. One hook covers every notification
  on the platform, so no caller has to remember to push.
* A login session that ends takes its browser's push subscription with it:
  signing out, "end session" / "sign out other devices" on the Security page,
  the plan's device cap, ``clearsessions``, and the session-key rotation at
  sign-in all delete the ``Session`` row, and this removes the matching
  :class:`~accounts.models.PushSubscription`. Signing back in on that browser
  re-binds it silently (the permission is already granted).
"""

from __future__ import annotations

import logging

from django.contrib.sessions.models import Session
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Notification, PushSubscription

logger = logging.getLogger(__name__)


def _queue_push(notification_id: str) -> None:
    try:
        from .tasks import send_web_push

        send_web_push.delay(notification_id)
    except Exception:
        # Never let push break the action that raised the notification (in
        # development Celery runs inline and would otherwise propagate).
        logger.exception("Could not queue web push for notification %s", notification_id)


@receiver(post_save, sender=Notification, dispatch_uid="accounts.push.notification_created")
def push_new_notification(sender, instance, created, **kwargs):
    if not created or kwargs.get("raw"):
        return
    try:
        if not PushSubscription.objects.filter(user_id=instance.user_id).exists():
            return  # no browser to reach; skip the queue entirely
    except Exception:
        return
    notification_id = str(instance.pk)
    transaction.on_commit(lambda: _queue_push(notification_id))


@receiver(post_delete, sender=Session, dispatch_uid="accounts.push.session_ended")
def drop_push_for_ended_session(sender, instance, **kwargs):
    try:
        from . import webpush

        webpush.remove_for_session(instance.session_key)
    except Exception:
        logger.exception("Could not remove push subscriptions for an ended session")
