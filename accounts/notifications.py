"""Owner notifications  account and store activity delivered by email.

The dashboard already shows a store's recent activity as a timeline; this module
lets an owner also receive the *notification-worthy* slice of it by email, at the
address and cadence they choose on their profile:

* **Where**  :attr:`User.effective_notification_email` (the chosen address, or
  the sign-in email as a fallback).
* **What**  per-category switches (:meth:`User.wants`): security, orders,
  store activity, billing, product news.
* **When**  ``instant`` sends inline as things happen; ``daily`` defers to the
  ``send_notification_digests`` management command; ``off`` mutes everything but
  security.

Every send is best-effort: a mail failure is logged, never raised, so it can
never break the action that triggered it.
"""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.emails import _send

logger = logging.getLogger(__name__)
User = get_user_model()

# store.<area>.<verb> → notification category. Only owner-relevant, genuinely
# "worth an email" events are mapped; everything else stays dashboard-only so
# the owner's inbox isn't flooded with their own edits.
ACTION_CATEGORY = {
    "store.order.received": "orders",
    # An owner cancelling / editing / changing an order's status never emails
    # them: they did it themselves (it stays on the activity timeline).
    "store.enquiry.received": "orders",
    "store.request.created": "orders",
    "store.request.opened": "orders",
    "store.payment.received": "billing",
    "store.bill.generated": "billing",
    "store.domain.connected": "store_activity",
    "store.domain.verified": "store_activity",
    "store.review.received": "store_activity",
    "store.collaboration.created": "store_activity",
    "store.collaboration.payout_requested": "billing",
}


def category_for(action: str) -> str | None:
    return ACTION_CATEGORY.get(action)


def _deliver(user, *, category: str, subject: str, template: str, context: dict) -> bool:
    if user is None or not user.is_active:
        return False
    if not user.wants(category):
        return False
    to = user.effective_notification_email
    if not to:
        return False
    ok = _send(to, subject, f"notification/{template}", {**context, "recipient": to})
    if not ok:
        logger.warning("notification %r to %s not delivered", template, to)
    return ok


def on_store_activity(*, site, action: str, phrase: str, label: str = "") -> bool:
    """Instantly email a store's owner about one notification-worthy event.

    Called best-effort from :func:`commerce.activity.record`. A no-op unless the
    action maps to a category the owner wants and their cadence is ``instant``.
    """
    category = category_for(action)
    if category is None or site is None:
        return False
    owner = getattr(site, "owner", None)
    if owner is None:
        return False
    if owner.notification_digest == User.Digest.DAILY:
        return False  # the daily digest will include it
    line = f"{phrase}  {label}" if label else phrase
    return _deliver(
        owner,
        category=category,
        subject=f"{phrase} · {site.name}",
        template="activity",
        context={
            "site_name": site.name,
            "site_id": str(getattr(site, "id", "")),
            "phrase": phrase,
            "label": label,
            "line": line,
            "when": timezone.now(),
        },
    )


def send_security_alert(user, *, subject: str, headline: str, detail: str = "") -> bool:
    """Email a security-relevant account change (always honoured if enabled)."""
    return _deliver(
        user,
        category="security",
        subject=subject,
        template="security",
        context={"headline": headline, "detail": detail, "when": timezone.now()},
    )


def send_test(user) -> bool:
    """Send a sample notification so the owner can confirm delivery + address."""
    to = user.effective_notification_email
    if not to:
        return False
    return _send(
        to,
        "Test notification · OneWooz",
        "notification/test",
        {"recipient": to, "when": timezone.now(), "display_name": user.display_name},
    )
