"""Unread counters on the store sidebar.

Orders, Requests, Reviews, Subscribers, Out of stock, Collaborations and Bills
each show a small round counter: how many new items reached that page since the
signed-in user last opened it.

* Orders: placed orders (online ones only once paid; replacements, which the
  owner makes, are not counted).
* Requests: customer cancel / return / replace / warranty / support requests.
* Reviews: product reviews from shoppers.
* Subscribers: newsletter sign-ups.
* Out of stock: products / variations that sold out and are not yet seen.
* Collaborations: payout requests raised by collaborators.
* Bills: commerce fee bills issued to the store.

Opening the page clears its counter (:func:`mark_seen`, called from
``commerce.context.store_context``). Each user keeps their own "seen" moment
per page (:class:`commerce.models.NavSeen`), so the owner and staff members
each see what *they* have not opened yet. The first time a user is counted the
baseline is "now", so history never floods the sidebar.

Counts are cached briefly per user and store so the sidebar costs nothing on
most page loads; opening a counted page drops the cache.
"""

from __future__ import annotations

import logging

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

#: Sidebar keys that carry a counter.
COUNTED_KEYS = (
    "orders", "requests", "reviews", "subscribers", "out_of_stock", "collaborations", "bills",
)
CACHE_SECONDS = 60
#: Shown as "99+" past this.
DISPLAY_MAX = 99
#: Out-of-stock scan bound (same as the dashboard overview's).
_STOCK_SCAN = 500
_REFS_MAX = 2000


def _cache_key(user, site) -> str:
    return f"navcount:v1:{user.pk}:{site.pk}"


def _user(request):
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    # Admin impersonation overlays request.user with the store owner: counting
    # still works, but mark_seen never records "seen" on the owner's behalf.
    return user


def _impersonating(request) -> bool:
    """An admin viewing the store as its owner (accounts.impersonation)."""
    return bool(getattr(request, "impersonation", None))


def _out_of_stock_refs(site) -> list[str]:
    """``"<product id>:<variation>"`` for every sold-out row the Out of stock
    page lists (bounded scan, as on the overview)."""
    from commerce.models import Product
    from commerce.views.catalog import _stock_rows

    live = list(
        Product.objects.for_site(site)
        .filter(is_deleted=False, is_active=True)
        .order_by()
        .only("id", "inventory", "variant_combinations", "low_stock_threshold")[:_STOCK_SCAN]
    )
    out, _low = _stock_rows(live)
    return sorted({f"{row['product'].pk}:{row['variant']}" for row in out})


def _count(site, key: str, row) -> int:
    from commerce.models import (
        Bill,
        CollaborationPayoutRequest,
        NewsletterSubscriber,
        Order,
        OrderRequest,
        Review,
    )

    since = row.seen_at
    if key == "orders":
        qs = Order.objects.for_site(site).filter(created_at__gt=since, replacement_of__isnull=True)
    elif key == "requests":
        qs = OrderRequest.objects.for_site(site).filter(created_at__gt=since)
    elif key == "reviews":
        qs = Review.objects.for_site(site).filter(created_at__gt=since, is_deleted=False)
    elif key == "subscribers":
        qs = NewsletterSubscriber.objects.for_site(site).filter(created_at__gt=since)
    elif key == "collaborations":
        qs = CollaborationPayoutRequest.objects.for_site(site).filter(created_at__gt=since)
    elif key == "bills":
        qs = Bill.objects.for_site(site).filter(created_at__gt=since)
    elif key == "out_of_stock":
        seen = set(row.seen_refs or [])
        return sum(1 for ref in _out_of_stock_refs(site) if ref not in seen)
    else:
        return 0
    return qs.order_by().count()


def _rows(site, user, keys) -> dict:
    """``{key: NavSeen}`` for ``keys``, creating any missing one with a
    baseline of now (so the first visit counts nothing old)."""
    from commerce.models import NavSeen

    rows = {r.key: r for r in NavSeen.objects.for_site(site).filter(user=user, key__in=list(keys)).order_by()}
    missing = [k for k in keys if k not in rows]
    if missing:
        now = timezone.now()
        new = []
        for k in missing:
            refs = _out_of_stock_refs(site)[:_REFS_MAX] if k == "out_of_stock" else []
            new.append(NavSeen(site=site, user=user, key=k, seen_at=now, seen_refs=refs))
        NavSeen.objects.bulk_create(new, ignore_conflicts=True)
        for r in new:
            rows[r.key] = r
    return rows


def counts_for(request, site, keys=None) -> dict:
    """``{sidebar key: unread count}`` for the signed-in user on ``site``
    (only the keys asked for that carry a counter). Never raises."""
    user = _user(request)
    if user is None or site is None:
        return {}
    wanted = [k for k in (keys if keys is not None else COUNTED_KEYS) if k in COUNTED_KEYS]
    if not wanted:
        return {}
    ck = _cache_key(user, site)
    try:
        cached = cache.get(ck)
    except Exception:
        cached = None
    if isinstance(cached, dict) and all(k in cached for k in wanted):
        return {k: cached[k] for k in wanted}
    try:
        rows = _rows(site, user, wanted)
        out = {}
        for k in wanted:
            try:
                out[k] = int(_count(site, k, rows[k]))
            except Exception:
                logger.warning("nav count %s failed for site %s", k, site.pk, exc_info=True)
                out[k] = 0
    except Exception:
        logger.warning("nav counts failed for site %s", getattr(site, "pk", None), exc_info=True)
        return {}
    try:
        cache.set(ck, out, CACHE_SECONDS)
    except Exception:
        pass
    return out


def mark_seen(request, site, key: str) -> None:
    """The user opened sidebar page ``key``: clear its counter. Never raises."""
    if key not in COUNTED_KEYS or site is None:
        return
    if getattr(request, "method", "GET") not in ("GET", "HEAD"):
        return
    user = _user(request)
    if user is None or _impersonating(request):
        return
    from commerce.models import NavSeen

    try:
        refs = _out_of_stock_refs(site)[:_REFS_MAX] if key == "out_of_stock" else []
        now = timezone.now()
        updated = (
            NavSeen.objects.for_site(site)
            .filter(user=user, key=key)
            .update(seen_at=now, seen_refs=refs)
        )
        if not updated:
            NavSeen.objects.bulk_create(
                [NavSeen(site=site, user=user, key=key, seen_at=now, seen_refs=refs)],
                ignore_conflicts=True,
            )
    except Exception:
        logger.warning("nav seen %s failed for site %s", key, site.pk, exc_info=True)
        return
    try:
        cache.delete(_cache_key(user, site))
    except Exception:
        pass


def attach(groups, counts: dict):
    """Put each item's display counter on the sidebar groups built by
    ``commerce.context.visible_groups`` (``count`` and ``count_label``)."""
    for group in groups or []:
        for item in group.get("items", []):
            n = 0 if item.get("locked") else int(counts.get(item.get("key"), 0) or 0)
            item["count"] = n
            item["count_label"] = f"{DISPLAY_MAX}+" if n > DISPLAY_MAX else str(n)
    return groups
