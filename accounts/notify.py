"""Create in-dashboard notifications (the top-navbar bell).

Distinct from :mod:`accounts.notifications`, which sends *email*. This module
writes :class:`accounts.models.Notification` rows that surface in the owner's
bell. Every helper is best-effort: a failure is swallowed (and logged) so
creating a notification can never break the action that triggered it  placing
an order, saving a product, or handling a shipping webhook.

The email and in-app paths are complementary: an automatic event can both push
a bell notification (always) and, when the owner has that category enabled, send
an email (via :mod:`accounts.notifications`). Callers decide which they want.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def push(
    user,
    *,
    headline: str,
    description: str = "",
    kind: str = "admin",
    level: str = "info",
    attachment_url: str = "",
    action_url: str = "",
    site=None,
):
    """Create one bell notification for ``user``. Returns it, or ``None``.

    ``site`` may be a ``tenancy.Site`` (or anything with ``id`` / ``name``); its
    id and name are snapshotted so a later store deletion never orphans the row.
    """
    if user is None:
        return None
    from .models import Notification

    try:
        return Notification.objects.create(
            user=user,
            headline=(headline or "")[:160],
            description=description or "",
            kind=kind,
            level=level,
            attachment_url=attachment_url or "",
            action_url=action_url or "",
            site_id_ref=str(getattr(site, "id", "") or "") if site is not None else "",
            site_name=(getattr(site, "name", "") or "") if site is not None else "",
        )
    except Exception:
        logger.exception("Could not create in-app notification for user %s", getattr(user, "id", "?"))
        return None


def push_to_owner(site, **kwargs):
    """Push a notification to the owner of ``site`` (a no-op if it has none)."""
    owner = getattr(site, "owner", None)
    if owner is None:
        return None
    kwargs.setdefault("site", site)
    return push(owner, **kwargs)
