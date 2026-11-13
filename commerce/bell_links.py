"""Where a store-activity bell notification's "Open" takes the owner.

Every notification pushed by :mod:`commerce.activity` gets an ``action_url``
pointing at the exact place the event lives: the order's page for an order or
shipment, the enquiry itself (highlighted) for an enquiry, the bill for a bill,
the domain page for a domain event, and so on.

Events are often recorded on a *storefront* request (a shopper placing an order
or sending an enquiry), whose urlconf cannot reverse dashboard routes, so the
link is reversed against the app urlconf and made absolute on the app surface.
The bell rebuilds it on the live origin when it renders
(``ud.context_processors.bell_href``), so it works locally and in production.
"""

from __future__ import annotations

import logging

from django.urls import NoReverseMatch, get_script_prefix, reverse, set_script_prefix

logger = logging.getLogger("onewooz.audit")

# action → (url name, how to build its extra kwargs / query from the target)
_ORDER_ACTIONS = {
    "store.order.received", "store.order.accepted", "store.order.cancelled",
    "store.order.updated", "store.payment.received",
}
_LIST_PAGES = {
    "store.request.created": "requests",
    "store.request.resolved": "requests",
    "store.payment.failed": "orders",
    "store.domain.connected": "domain",
    "store.domain.verified": "domain",
    "store.domain.removed": "domain",
    "store.review.received": "reviews",
    "store.review.moderated": "reviews",
    "store.collaboration.created": "collaborations",
    "store.collaboration.updated": "collaborations",
    "store.collaboration.paid": "collaborations",
    "store.collaboration.payout_requested": "collaborations",
    "store.subscriber.subscribed": "subscribers",
    "store.bill.generated": "bills",
    "store.bill.payment_submitted": "bills",
    "store.enquiry.received": "enquiries",
    "store.shipment.updated": "orders",
    "store.order.received": "orders",
    "store.order.accepted": "orders",
    "store.order.cancelled": "orders",
    "store.order.updated": "orders",
    "store.payment.received": "orders",
}


def _app_reverse(name: str, **kwargs) -> str:
    """Root-relative path on the app surface, whatever request is current."""
    previous = get_script_prefix()
    try:
        set_script_prefix("/")
        return reverse(f"store:{name}", urlconf="onewooz.urls.app", kwargs=kwargs)
    finally:
        set_script_prefix(previous)


def _absolute(path: str) -> str:
    from core import links
    from core.hosts import Surface

    return links.surface_url(Surface.APP, path)


def _target_path(action: str, site_id, target) -> str:
    pk = getattr(target, "pk", None)
    if target is None or pk is None:
        return ""
    if action in _ORDER_ACTIONS:
        return _app_reverse("order_detail", site_id=site_id, order_id=pk)
    if action == "store.shipment.updated":
        order_id = getattr(target, "order_id", None)
        return _app_reverse("order_detail", site_id=site_id, order_id=order_id) if order_id else ""
    if action == "store.enquiry.received":
        return _app_reverse("enquiries", site_id=site_id) + f"?focus={pk}#enquiry-{pk}"
    if action in ("store.bill.generated", "store.bill.payment_submitted"):
        return _app_reverse("bill_detail", site_id=site_id, bill_id=pk)
    return ""


def page_url(name: str, site) -> str:
    """Absolute dashboard URL of one store page (``store:<name>``), usable from
    a worker or a storefront request alike."""
    site_id = getattr(site, "pk", site)
    return _absolute(_app_reverse(name, site_id=site_id))


def url_for(action: str, site, target=None) -> str:
    """Absolute dashboard URL for one store event, or ``""`` if none fits."""
    site_id = getattr(site, "pk", site)
    if not site_id:
        return ""
    try:
        path = _target_path(action, site_id, target)
        if not path and action in _LIST_PAGES:
            path = _app_reverse(_LIST_PAGES[action], site_id=site_id)
        if not path:
            path = _app_reverse("overview", site_id=site_id)
        return _absolute(path)
    except (NoReverseMatch, ValueError, TypeError):
        logger.exception("bell link failed action=%s site=%s", action, site_id)
        return ""


def url_for_headline(headline: str, site_id: str) -> str:
    """Best destination for an older notification saved without a link: match
    its headline back to the event that wrote it, then open that area of the
    store it was about."""
    if not site_id:
        return ""
    from .activity import _PHRASES

    text = (headline or "").strip().lower()
    for action, (_icon, phrase) in _PHRASES.items():
        if phrase.lower() == text:
            return url_for(action, site_id)
    return ""
