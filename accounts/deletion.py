"""Deleting and restoring owner accounts and stores.

Nothing here ever erases a row. "Deleting" is a soft delete with a recovery
window of ``settings.ACCOUNT_DELETION_RETENTION_DAYS`` (60 by default):

* **A store** (Store settings → Delete this store, behind an emailed code) is
  soft-deleted: it leaves the dashboard and its storefront goes offline, while
  its products, orders and customers stay on the server. The owner recovers it
  by contacting support, who restore it from the admin (``restore_site``).

* **An account** (Profile → Delete My Account, behind an emailed code) is
  marked deleted, every live store it owns is soft-deleted with it (flagged
  ``deleted_with_account``), and every session is ended. The owner recovers it
  by simply signing in again within the window:

  - plan active (or free)  → the account and those stores come back at once;
  - paid plan lapsed       → sign-in works but the dashboard shows only the
    renewal wall; the account and stores come back the moment the renewal
    settles (``restore_if_unlocked``, called by the renewal gate middleware);
  - window passed          → the account is closed (``is_active`` off) and can
    no longer sign in. Its data still stays on the server.

The admin's "Deleted users" and "Expiring users" pages read from here too.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core import audit

logger = logging.getLogger(__name__)

__all__ = [
    "retention_days",
    "recover_until",
    "is_lapsed",
    "plan_summary",
    "delete_site",
    "restore_site",
    "delete_account",
    "restore_account",
    "on_sign_in",
    "restore_if_unlocked",
    "close_account",
    "close_lapsed_accounts",
    "RESTORED",
    "PENDING_RENEWAL",
    "CLOSED",
    "NOT_DELETED",
]

#: Outcomes of :func:`on_sign_in`.
NOT_DELETED = "not_deleted"
RESTORED = "restored"
PENDING_RENEWAL = "pending_renewal"
CLOSED = "closed"


# --------------------------------------------------------------------------
# Window
# --------------------------------------------------------------------------


def retention_days() -> int:
    """Days a deleted store or account stays recoverable."""
    try:
        return max(1, int(getattr(settings, "ACCOUNT_DELETION_RETENTION_DAYS", 60)))
    except (TypeError, ValueError):
        return 60


def recover_until(deleted_at) -> dt.datetime | None:
    """The last moment something deleted at ``deleted_at`` can be recovered."""
    if not deleted_at:
        return None
    return deleted_at + dt.timedelta(days=retention_days())


def is_lapsed(deleted_at, *, now=None) -> bool:
    """True once the recovery window for ``deleted_at`` has passed."""
    until = recover_until(deleted_at)
    return bool(until and (now or timezone.now()) > until)


def days_left(deleted_at, *, now=None) -> int:
    """Whole days left to recover (0 on the last day, never negative)."""
    until = recover_until(deleted_at)
    if not until:
        return 0
    return max(0, (until - (now or timezone.now())).days)


def plan_summary(user) -> str:
    """One line describing the owner's plan right now, e.g. ``Pro, paid, until 12 Dec 2026``."""
    try:
        from billing.subscriptions import current_for_owner

        sub = current_for_owner(user)
    except Exception:
        return ""
    if sub.status == "none":
        return "No plan"
    parts = [sub.plan_name]
    parts.append({"active_paid": "paid", "active_free": "free", "free": "free", "expired": "expired"}.get(sub.status, sub.status))
    if sub.period_end:
        word = "ended" if sub.is_expired else "until"
        parts.append(f"{word} {sub.period_end:%d %b %Y}")
    return ", ".join(p for p in parts if p)[:160]


def _live_sites(user):
    from tenancy.models import Site

    return Site.objects.filter(owner=user, is_deleted=False).defer("content").order_by()


def _forget_active_site(user, site_ids, request=None) -> None:
    wanted = {str(s) for s in site_ids}
    if str(getattr(user, "active_site_id", "") or "") in wanted:
        user.__class__.objects.filter(pk=user.pk).update(active_site_id=None, active_site_path="")
        user.active_site_id = None
        user.active_site_path = ""
    if request is not None:
        try:
            if request.session.get("active_site_id") in wanted:
                del request.session["active_site_id"]
        except Exception:
            pass


# --------------------------------------------------------------------------
# Stores
# --------------------------------------------------------------------------


def delete_site(site, *, request=None, actor=None) -> None:
    """Soft-delete one store (owner action, after the emailed code)."""
    label = site.name
    site.deleted_with_account = False
    site.soft_delete(save=False)
    site.save(update_fields=["is_deleted", "deleted_at", "deleted_with_account", "updated_at"])
    audit.record(
        "store.deleted",
        request=request,
        actor=actor,
        target=site,
        target_kind="Site",
        target_label=label,
        site=site,
        severity="notice",
        context={"recover_until": f"{recover_until(site.deleted_at):%Y-%m-%d}"},
    )
    if actor is not None:
        _forget_active_site(actor, [site.pk], request)


def restore_site(site, *, request=None, actor=None, force: bool = False) -> tuple[bool, str]:
    """Bring a deleted store back (support, from the admin).

    Refused once the recovery window has passed unless ``force``; refused while
    the owner's account itself is deleted (restore the account instead, which
    brings the store back with it).
    """
    if not site.is_deleted:
        return False, "This store is not deleted."
    if not force and is_lapsed(site.deleted_at):
        return False, f"The {retention_days()}-day recovery window for this store has passed."
    owner = getattr(site, "owner", None)
    if owner is not None and owner.account_deleted_at:
        return False, "The owner's account is deleted. It comes back, with its stores, when the owner signs in."
    site.deleted_with_account = False
    site.restore(save=False)
    site.save(update_fields=["is_deleted", "deleted_at", "deleted_with_account", "updated_at"])
    audit.record(
        "store.restored",
        request=request,
        actor=actor,
        actor_kind="admin" if actor is not None and getattr(actor, "is_staff", False) else "",
        target=site,
        target_kind="Site",
        target_label=site.name,
        site=site,
        severity="notice",
    )
    return True, f"“{site.name}” is restored."


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------


def delete_account(user, *, request=None, reason: str = "") -> int:
    """Soft-delete ``user``'s account and every live store. Returns the store count.

    The caller signs the current session out afterwards; every other session
    of the account is ended here.
    """
    now = timezone.now()
    with transaction.atomic():
        sites = list(_live_sites(user).only("id", "name", "owner_id", "is_deleted", "deleted_at"))
        names = ", ".join(s.name for s in sites)
        plan = plan_summary(user)
        site_ids = [s.pk for s in sites]
        if site_ids:
            from tenancy.models import Site

            Site.objects.filter(pk__in=site_ids).update(
                is_deleted=True, deleted_at=now, deleted_with_account=True, updated_at=now
            )
        type(user).objects.filter(pk=user.pk).update(
            account_deleted_at=now,
            account_deletion_reason=(reason or "").strip()[:300],
            account_deletion_plan=plan,
            account_deletion_sites=names[:600],
            account_closed_at=None,
            active_site_id=None,
            active_site_path="",
            updated_at=now,
        )
        user.account_deleted_at = now
        user.active_site_id = None
        user.active_site_path = ""

    audit.record(
        "account.deleted",
        request=request,
        actor=user,
        target=user,
        target_kind="User",
        target_label=user.email,
        severity="notice",
        context={
            "stores": len(site_ids),
            "plan": plan,
            "recover_until": f"{recover_until(now):%Y-%m-%d}",
        },
    )

    # End every other device's session; the caller ends this one.
    try:
        from . import sessions as session_registry

        current = request.session.session_key if request is not None else ""
        session_registry.end_others(user, current or "")
    except Exception:
        logger.exception("could not end sessions for deleted account %s", user.pk)
    return len(site_ids)


def restore_account(user, *, request=None) -> int:
    """Undo :func:`delete_account`. Returns how many stores came back."""
    from tenancy.models import Site

    now = timezone.now()
    with transaction.atomic():
        count = Site.objects.filter(owner=user, is_deleted=True, deleted_with_account=True).update(
            is_deleted=False, deleted_at=None, deleted_with_account=False, updated_at=now
        )
        type(user).objects.filter(pk=user.pk).update(
            account_deleted_at=None, account_closed_at=None, account_restored_at=now, updated_at=now
        )
        user.account_deleted_at = None
        user.account_closed_at = None
        user.account_restored_at = now
    audit.record(
        "account.restored",
        request=request,
        actor=user,
        target=user,
        target_kind="User",
        target_label=user.email,
        severity="notice",
        context={"stores": count},
    )
    return count


def _renewal_locked(user) -> bool:
    try:
        from billing import renewal

        return bool(renewal.state_for_owner(user).locked)
    except Exception:
        logger.exception("renewal state failed for %s", getattr(user, "pk", None))
        return False


def _welcome_back(request, stores: int) -> None:
    if request is None:
        return
    try:
        from django.contrib import messages

        if stores:
            text = f"Welcome back. Your account and {stores} store{'s' if stores != 1 else ''} are restored."
        else:
            text = "Welcome back. Your account is restored."
        messages.success(request, text, fail_silently=True)
    except Exception:
        pass


def on_sign_in(request, user) -> str:
    """Called by every sign-in path just before the session is established.

    Restores a deleted account still inside its window (unless its paid plan
    lapsed, see module docstring), or closes one whose window has passed.
    """
    if not getattr(user, "account_deleted_at", None):
        return NOT_DELETED
    if is_lapsed(user.account_deleted_at):
        close_account(user)
        return CLOSED
    if _renewal_locked(user):
        return PENDING_RENEWAL
    stores = restore_account(user, request=request)
    _welcome_back(request, stores)
    return RESTORED


def restore_if_unlocked(request, user) -> bool:
    """Finish a restore that waited on a renewal. Cheap when nothing is pending.

    Called by ``billing.middleware.SubscriptionRenewalGateMiddleware`` on every
    dashboard request, so the account and its stores return on the first page
    load after the renewal payment settles.
    """
    if not getattr(user, "account_deleted_at", None):
        return False
    if is_lapsed(user.account_deleted_at):
        return False  # Closed on its next sign-in / by the periodic sweep.
    if _renewal_locked(user):
        return False
    stores = restore_account(user, request=request)
    _welcome_back(request, stores)
    return True


def close_account(user) -> None:
    """End self-service recovery for a deleted account. Data stays."""
    now = timezone.now()
    type(user).objects.filter(pk=user.pk, account_closed_at__isnull=True).update(
        is_active=False, account_closed_at=now, updated_at=now
    )
    user.is_active = False
    user.account_closed_at = user.account_closed_at or now
    audit.record(
        "account.closed",
        target=user,
        target_kind="User",
        target_label=user.email,
        actor_kind="system",
        severity="notice",
        context={"deleted_at": f"{user.account_deleted_at:%Y-%m-%d}"},
    )


def close_lapsed_accounts() -> int:
    """Close every deleted account whose recovery window has passed."""
    from .models import User

    cutoff = timezone.now() - dt.timedelta(days=retention_days())
    ids = list(
        User.objects.filter(
            account_deleted_at__isnull=False,
            account_deleted_at__lt=cutoff,
            account_closed_at__isnull=True,
        ).order_by().values_list("pk", flat=True)[:1000]
    )
    closed = 0
    for user in User.objects.filter(pk__in=ids).order_by():
        try:
            close_account(user)
            closed += 1
        except Exception:
            logger.exception("could not close lapsed deleted account %s", user.pk)
    return closed
