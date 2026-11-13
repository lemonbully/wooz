"""Store activity  the owner-facing timeline on the dashboard.

A thin wrapper over :mod:`core.audit`: every consequential thing that happens in
a store (an owner editing content, connecting a domain, adding a product; a
shopper placing an order or sending an enquiry) is appended as one event tagged
with the site, so the dashboard can read it back as a friendly, human timeline
rather than a raw log.

It reuses ``AuditEvent``  which already carries a ``(site_id_ref, -created_at)``
index  so there is nothing new to migrate, and it inherits audit's guarantee of
never raising: recording an activity must never break the action it describes.

Action names follow ``store.<area>.<verb>`` so :func:`describe` (used by the
dashboard) can turn them into readable lines.
"""

from __future__ import annotations

from core import audit


def record(
    action: str,
    *,
    request=None,
    site=None,
    target=None,
    target_kind: str = "",
    label: str = "",
    actor=None,
    actor_kind: str | None = None,
    changes: dict | None = None,
) -> None:
    """Append one store-activity event. Best-effort; swallows its own errors."""
    audit.record(
        action,
        request=request,
        site=site,
        target=target,
        target_kind=target_kind,
        target_label=label,
        actor=actor,
        actor_kind=actor_kind,
        changes=changes or {},
    )
    _notify_owner(action, site, label)
    _push_bell(action, site, label, target)


# Actions worth a bell notification → (Notification.kind, Notification.level).
# Broader than the email set: the in-app bell shows every owner-relevant event,
# while email stays gated by the owner's per-category preferences.
#
# The owner's own order actions (accept, cancel, edit, status change, creating a
# replacement) are deliberately absent: they stay on the activity timeline but
# never ping the owner about something they just did themselves.
_BELL_ACTIONS = {
    "store.order.received": ("order", "success"),
    "store.shipment.updated": ("shipment", "info"),
    "store.enquiry.received": ("order", "info"),
    "store.request.created": ("order", "info"),
    "store.payment.received": ("order", "success"),
    "store.payment.failed": ("system", "warning"),
    "store.bill.generated": ("system", "info"),
    "store.domain.connected": ("system", "info"),
    "store.domain.verified": ("system", "success"),
    "store.review.received": ("system", "info"),
    "store.collaboration.created": ("system", "info"),
    "store.collaboration.payout_requested": ("system", "warning"),
    "store.subscriber.subscribed": ("system", "info"),
}


def _push_bell(action: str, site, label: str, target=None) -> None:
    """Create an in-dashboard bell notification for owner-relevant events.

    Best-effort and independent of the email path: a failure here never breaks
    the action, and the bell shows the event even when the owner has muted
    email for that category.
    """
    if site is None:
        return
    meta = _BELL_ACTIONS.get(action)
    if meta is None:
        return
    kind, level = meta
    try:
        from accounts import notify

        from .bell_links import url_for

        _icon, phrase = describe(action)
        notify.push_to_owner(
            site,
            headline=phrase,
            description=label or "",
            kind=kind,
            level=level,
            # "Open" in the bell goes straight to where this happened.
            action_url=url_for(action, site, target),
        )
    except Exception:
        pass


def _notify_owner(action: str, site, label: str) -> None:
    """Email the store owner about notification-worthy activity (best-effort).

    Only a curated set of owner-relevant actions notify (see
    :data:`accounts.notifications.ACTION_CATEGORY`); everything else stays
    dashboard-only. Never raises  a mail hiccup must not break the action.
    """
    if site is None:
        return
    try:
        from accounts import notifications

        if notifications.category_for(action) is None:
            return
        _icon, phrase = describe(action)
        notifications.on_store_activity(site=site, action=action, phrase=phrase, label=label or "")
    except Exception:
        pass


# Map each ``store.<area>.<verb>`` action to (icon key, human phrasing). The
# subject (a product/category/order/domain name) is carried on the event's
# ``target_label`` and shown alongside  so the phrasing is the verb, not the
# noun. Unknown actions fall back to a de-dotted, capitalised label.
_PHRASES = {
    "store.content.updated": ("edit", "Updated store content"),
    "store.product.created": ("product", "Added a product"),
    "store.product.updated": ("product", "Updated a product"),
    "store.product.duplicated": ("product", "Duplicated a product"),
    "store.product.deleted": ("product", "Removed a product"),
    "store.inventory.updated": ("inventory", "Updated inventory"),
    "store.category.created": ("category", "Added a category"),
    "store.category.updated": ("category", "Updated a category"),
    "store.category.deleted": ("category", "Removed a category"),
    "store.domain.connected": ("domain", "Connected a custom domain"),
    "store.domain.verified": ("domain", "Verified a custom domain"),
    "store.domain.removed": ("domain", "Removed the custom domain"),
    "store.order.received": ("order", "New order received"),
    "store.order.updated": ("order", "Updated an order"),
    "store.order.accepted": ("order", "Accepted an order"),
    "store.order.cancelled": ("order", "Cancelled an order"),
    "store.order.replacement_created": ("order", "Created a replacement order"),
    "store.shipment.updated": ("shipping", "Shipment status updated"),
    "store.enquiry.received": ("enquiry", "New enquiry received"),
    "store.request.created": ("request", "New customer request"),
    "store.request.resolved": ("request", "Resolved a request"),
    "store.customer.updated": ("customer", "Updated a customer"),
    "store.customer.deleted": ("customer", "Removed a customer"),
    "store.customer.credited": ("customer", "Added store credit"),
    "store.customer.credit_deleted": ("customer", "Deleted a customer's store credit"),
    "store.customer.credit_bulk_deleted": ("customer", "Deleted a bulk store credit"),
    "store.customer.account_deleted": ("customer", "A customer deleted their account"),
    "store.review.moderated": ("review", "Moderated a review"),
    "store.payments.updated": ("payments", "Updated payment settings"),
    "store.payment.failed": ("payments", "An online payment couldn’t start"),
    "store.shipping.updated": ("shipping", "Updated shipping settings"),
    "store.tracking.updated": ("settings", "Updated tracking & analytics"),
    "store.merchant.updated": ("settings", "Updated Google Merchant"),
    "store.coupon.created": ("coupon", "Created a coupon"),
    "store.coupon.updated": ("coupon", "Updated a coupon"),
    "store.coupon.deleted": ("coupon", "Removed a coupon"),
    "store.collaboration.created": ("collab", "Invited a collaborator"),
    "store.collaboration.updated": ("collab", "Updated a collaboration"),
    "store.collaboration.paid": ("collab", "Paid a collaborator"),
    "store.collaboration.payout_requested": ("collab", "A collaborator requested a payout"),
    "store.bill.generated": ("payments", "Generated a bill"),
    "store.subscriber.subscribed": ("customer", "New newsletter subscriber"),
}


def describe(action: str) -> tuple[str, str]:
    """(icon key, human phrasing) for an action; a sensible fallback otherwise."""
    if action in _PHRASES:
        return _PHRASES[action]
    tail = action.split(".")[-1].replace("_", " ").strip()
    return ("settings", tail.capitalize() if tail else "Activity")
