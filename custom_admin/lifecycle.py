"""Data for the admin "Deleted users" and "Expiring users" pages.

* **Deleted users**: owners who used Profile → Delete My Account (see
  ``accounts.deletion``), filtered by when they deleted: last 24 hours, 7 days,
  30 days, all time, or a custom date range.
* **Expiring users**: owners whose paid / one-time subscription is still active
  but ends inside a window: next 24 hours, 7 days, 30 days, or a custom range.
  Expired plans are not listed (the Export Excel "Expired" report covers those);
  a free grant never lapses into a renewal, so it is never "expiring" either.

Both are read-only listings. Queries stay off wide-row sorts (CLAUDE.md, MySQL
error 1038): users are ordered by the indexed ``account_deleted_at`` with only
narrow columns loaded; everything else is sorted in Python.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from django.utils import timezone

# ---------------------------------------------------------------------------
# Duration filter
# ---------------------------------------------------------------------------

#: (key, label) of the quick ranges, shared by both pages.
DELETED_RANGES = (
    ("24h", "Last 24 hours"),
    ("7d", "Last 7 days"),
    ("30d", "Last 30 days"),
    ("all", "All time"),
    ("custom", "Custom range"),
)
EXPIRING_RANGES = (
    ("24h", "Next 24 hours"),
    ("7d", "Next 7 days"),
    ("30d", "Next 30 days"),
    ("custom", "Custom range"),
)
_DAYS = {"24h": 1, "7d": 7, "30d": 30}


@dataclass
class Window:
    key: str
    label: str
    start: object = None  # datetime (deleted) / date (expiring) or None
    end: object = None
    date_from: str = ""
    date_to: str = ""
    error: str = ""


def _parse(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        raise ValueError(f"“{raw}” is not a valid date.") from None


def _custom(get, window: Window) -> tuple:
    window.date_from = (get.get("from") or "").strip()
    window.date_to = (get.get("to") or "").strip()
    try:
        first, last = _parse(window.date_from), _parse(window.date_to)
    except ValueError as exc:
        window.error = str(exc)
        return None, None
    if first and last and first > last:
        window.error = "The start date is after the end date."
        return None, None
    return first, last


def _day_start(day):
    return timezone.make_aware(dt.datetime.combine(day, dt.time.min))


def deleted_window(get) -> Window:
    """The deletion-time window chosen on the Deleted users page (default 30 days)."""
    key = (get.get("range") or "30d").strip()
    labels = dict(DELETED_RANGES)
    if key not in labels:
        key = "30d"
    window = Window(key=key, label=labels[key])
    now = timezone.now()
    if key in _DAYS:
        window.start, window.end = now - dt.timedelta(days=_DAYS[key]), None
    elif key == "custom":
        first, last = _custom(get, window)
        window.start = _day_start(first) if first else None
        window.end = _day_start(last + dt.timedelta(days=1)) if last else None
        if first or last:
            window.label = "Deleted " + _span_words(first, last)
    return window


def expiring_window(get) -> Window:
    """The plan-end window chosen on the Expiring users page (default 7 days)."""
    key = (get.get("range") or "7d").strip()
    labels = dict(EXPIRING_RANGES)
    if key not in labels:
        key = "7d"
    window = Window(key=key, label=labels[key])
    today = timezone.localdate()
    if key in _DAYS:
        window.start, window.end = today, today + dt.timedelta(days=_DAYS[key])
    else:
        first, last = _custom(get, window)
        # Expiring means "not expired yet": a range never reaches into the past.
        window.start = max(first, today) if first else today
        window.end = last
        window.label = "Plan ends " + _span_words(window.start, last)
    return window


def _span_words(first, last) -> str:
    fmt = lambda d: d.strftime("%d %b %Y")  # noqa: E731
    if first and last:
        return f"{fmt(first)} to {fmt(last)}"
    if first:
        return f"from {fmt(first)}"
    if last:
        return f"up to {fmt(last)}"
    return "any time"


# ---------------------------------------------------------------------------
# Shared owner details
# ---------------------------------------------------------------------------


def _profiles(user_ids) -> dict:
    from billing.models import OnboardingProfile

    out = {}
    ids = list(user_ids)
    for i in range(0, len(ids), 500):
        for p in OnboardingProfile.objects.filter(user_id__in=ids[i:i + 500]).order_by():
            out[p.user_id] = p
    return out


def _sites(user_ids, *, include_deleted: bool) -> dict:
    from tenancy.models import Site

    out: dict = {}
    ids = list(user_ids)
    for i in range(0, len(ids), 500):
        qs = Site.objects.filter(owner_id__in=ids[i:i + 500])
        if not include_deleted:
            qs = qs.filter(is_deleted=False)
        qs = qs.only(
            "id", "owner_id", "name", "slug", "custom_domain", "custom_domain_verified",
            "is_deleted", "deleted_at", "deleted_with_account", "created_at",
        ).order_by()
        for site in qs:
            out.setdefault(site.owner_id, []).append(site)
    for lst in out.values():
        lst.sort(key=lambda s: (s.created_at, s.name.lower()))
    return out


def _site_view(site) -> dict:
    domain = site.custom_domain.strip().lower() if site.custom_domain and site.custom_domain_verified else ""
    if not domain:
        try:
            from core import links

            domain = links.storefront_origin(site.slug, None).split("://", 1)[-1]
        except Exception:
            domain = site.slug
    return {
        "name": site.name,
        "address": domain,
        "is_deleted": site.is_deleted,
        "with_account": site.deleted_with_account,
    }


# ---------------------------------------------------------------------------
# Deleted users
# ---------------------------------------------------------------------------


@dataclass
class DeletedRow:
    user: object
    name: str
    email: str
    phone: str
    company: str
    deleted_at: object
    recover_until: object
    days_left: int
    state: str  # "recoverable" | "awaiting_renewal" | "closed"
    state_label: str
    reason: str
    plan_at_deletion: str
    subscription: object  # billing.subscriptions.CurrentSubscription
    sites: list = field(default_factory=list)
    sites_at_deletion: str = ""


def deleted_users(window: Window) -> list:
    """Every owner who deleted their account inside ``window``, newest first."""
    from accounts import deletion
    from accounts.models import User

    if window.error:
        return []
    qs = User.objects.filter(account_deleted_at__isnull=False, is_staff=False)
    if window.start is not None:
        qs = qs.filter(account_deleted_at__gte=window.start)
    if window.end is not None:
        qs = qs.filter(account_deleted_at__lt=window.end)
    # Ordered by an indexed column over narrow fields only (no wide-row sort).
    users = list(
        qs.only(
            "id", "email", "full_name", "phone", "is_active", "account_deleted_at",
            "account_deletion_reason", "account_deletion_plan", "account_deletion_sites",
            "account_closed_at",
        ).order_by("-account_deleted_at")
    )
    if not users:
        return []
    ids = [u.pk for u in users]
    profiles = _profiles(ids)
    sites = _sites(ids, include_deleted=True)

    from billing.renewal import state_for_owner
    from billing.subscriptions import current_for_owner

    rows = []
    for user in users:
        profile = profiles.get(user.pk)
        until = deletion.recover_until(user.account_deleted_at)
        if user.account_closed_at or deletion.is_lapsed(user.account_deleted_at):
            state, label = "closed", "Closed (window passed)"
        elif state_for_owner(user).locked:
            state, label = "awaiting_renewal", "Recoverable after renewal"
        else:
            state, label = "recoverable", "Recoverable by signing in"
        rows.append(DeletedRow(
            user=user,
            name=user.full_name or (getattr(profile, "company_name", "") or ""),
            email=user.email,
            phone=(getattr(profile, "phone", "") or "") or user.phone,
            company=getattr(profile, "company_name", "") or "",
            deleted_at=user.account_deleted_at,
            recover_until=until,
            days_left=deletion.days_left(user.account_deleted_at),
            state=state,
            state_label=label,
            reason=user.account_deletion_reason,
            plan_at_deletion=user.account_deletion_plan,
            subscription=current_for_owner(user),
            sites=[_site_view(s) for s in sites.get(user.pk, [])],
            sites_at_deletion=user.account_deletion_sites,
        ))
    return rows


# ---------------------------------------------------------------------------
# Expiring users
# ---------------------------------------------------------------------------


@dataclass
class ExpiringRow:
    order: object
    user: object
    name: str
    email: str
    phone: str
    company: str
    plan: str
    cycle: str
    amount: object
    currency: str
    period_start: object
    period_end: object
    days_left: int
    account_deleted: bool
    sites: list = field(default_factory=list)


def expiring_users(window: Window) -> list:
    """Owners whose paid plan is active and ends inside ``window``, soonest first."""
    from django.db.models.functions import Lower

    from accounts.models import User

    from .exports import _latest_subscriptions

    if window.error:
        return []
    today = timezone.localdate()
    chosen = []
    for order in _latest_subscriptions():
        end = order.current_period_end
        if end is None or end < today:
            continue  # expired: not "expiring"
        if window.start is not None and end < window.start:
            continue
        if window.end is not None and end > window.end:
            continue
        chosen.append(order)
    if not chosen:
        return []

    emails = list({(o.customer_email or "").strip().lower() for o in chosen})
    users: dict = {}
    for i in range(0, len(emails), 500):
        for user in (
            User.objects.annotate(ow_email_lc=Lower("email"))
            .filter(ow_email_lc__in=emails[i:i + 500])
            .only("id", "email", "full_name", "phone", "account_deleted_at")
            .order_by()
        ):
            users.setdefault(user.email.strip().lower(), user)
    ids = [u.pk for u in users.values()]
    profiles = _profiles(ids)
    sites = _sites(ids, include_deleted=False)

    rows = []
    for order in chosen:
        user = users.get((order.customer_email or "").strip().lower())
        profile = profiles.get(getattr(user, "pk", None))
        end = order.current_period_end
        rows.append(ExpiringRow(
            order=order,
            user=user,
            name=(getattr(user, "full_name", "") or "") or order.customer_name,
            email=order.customer_email,
            phone=(getattr(profile, "phone", "") or "") or (getattr(user, "phone", "") or "") or order.customer_phone,
            company=getattr(profile, "company_name", "") or "",
            plan=order.plan_name,
            cycle=order.get_cycle_display(),
            amount=order.total_amount,
            currency=order.currency,
            period_start=order.current_period_start,
            period_end=end,
            days_left=(end - today).days,
            account_deleted=bool(getattr(user, "account_deleted_at", None)),
            sites=[_site_view(s) for s in sites.get(getattr(user, "pk", None), [])],
        ))
    rows.sort(key=lambda r: (r.period_end, r.email.lower()))
    return rows
