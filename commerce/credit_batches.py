"""Universal ("Bulk credits") grants: list them and delete one as a whole.

A bulk run gives every customer of the store their own
:class:`~commerce.models.CustomerCredit`. Runs made since ``batch_id`` existed
share one id. Runs made before it are recognised by what a bulk run always
produces: credits with identical settings (type, value, scope, categories,
expiry, note), one per distinct customer, created back to back. Two credits
given by hand to the same customer, or with any setting different, are never
grouped.

Deleting a grant hard-deletes its credits, exactly like deleting a single
credit. That is safe for history: ``Order.credit`` is ``SET_NULL``, an order
keeps its own ``credit_discount_amount`` and ``credit_usage`` copy, and both
``commerce.credits.book_for_order`` and ``restore_for_order`` already skip a
credit that no longer exists (so cancelling an old order never resurrects a
deleted credit).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from commerce.models import Category, CustomerCredit

#: A bulk run creates (and emails) one credit per customer in a single request,
#: so consecutive credits of a pre-``batch_id`` run sit seconds apart. Five
#: minutes leaves room for slow mail. Only credits from before ``batch_id`` are
#: ever matched this way; every run since carries its own id.
LEGACY_GAP = timedelta(minutes=5)

_FIELDS = (
    "id", "batch_id", "customer_email", "credit_type", "credit_value", "scope",
    "category_ids", "valid_until", "note", "is_active", "remaining_amount",
    "last_used_at", "created_at",
)


def _rows(site) -> list[dict]:
    # Narrow values() rows and no ORDER BY: grouping and sorting happen here.
    return list(CustomerCredit.objects.for_site(site).order_by().values(*_FIELDS))


def _signature(row: dict) -> tuple:
    return (
        row["credit_type"],
        str(row["credit_value"]),
        row["scope"],
        tuple(sorted(str(c) for c in (row["category_ids"] or []))),
        row["valid_until"],
        (row["note"] or "").strip(),
    )


def _groups(rows: list[dict]) -> dict[str, list[dict]]:
    """``{key: [rows]}`` for every universal grant. Keys: ``b:<batch id>`` for
    tagged runs, ``l:<first credit id>`` for runs from before tagging."""
    out: dict[str, list[dict]] = {}
    for row in rows:
        if row["batch_id"]:
            out.setdefault(f"b:{row['batch_id']}", []).append(row)

    legacy = sorted(
        (r for r in rows if not r["batch_id"]),
        key=lambda r: (_signature(r), r["created_at"], str(r["id"])),
    )
    cluster: list[dict] = []

    def flush():
        emails = {(m["customer_email"] or "").lower() for m in cluster}
        # A bulk run gives each customer exactly one credit.
        if len(cluster) >= 2 and len(emails) == len(cluster):
            out[f"l:{cluster[0]['id']}"] = list(cluster)

    prev = None
    for row in legacy:
        same_run = (
            prev is not None
            and _signature(row) == _signature(prev)
            and row["created_at"] - prev["created_at"] <= LEGACY_GAP
        )
        if not same_run:
            if cluster:
                flush()
            cluster = []
        cluster.append(row)
        prev = row
    if cluster:
        flush()
    return out


def _usable(row: dict, today) -> bool:
    if not row["is_active"]:
        return False
    if row["valid_until"] and row["valid_until"] < today:
        return False
    if row["credit_type"] == CustomerCredit.CreditType.PERCENT:
        return Decimal(str(row["credit_value"] or 0)) > 0
    left = row["remaining_amount"] if row["remaining_amount"] is not None else row["credit_value"]
    return Decimal(str(left or 0)) > 0


def _summary(key: str, members: list[dict], names: dict, today) -> dict:
    first = min(members, key=lambda r: r["created_at"])
    return {
        "key": key,
        "credit_type": first["credit_type"],
        "credit_value": str(first["credit_value"]),
        "scope": first["scope"],
        "category_names": [names.get(str(c), "") for c in (first["category_ids"] or [])],
        "valid_until": first["valid_until"].isoformat() if first["valid_until"] else None,
        "note": first["note"] or "",
        "created_at": first["created_at"].isoformat(),
        "customers": len(members),
        "usable": sum(1 for m in members if _usable(m, today)),
        "used": sum(1 for m in members if m["last_used_at"]),
    }


def batches(site) -> list[dict]:
    """Every universal grant on ``site``, newest first, ready for JSON."""
    groups = _groups(_rows(site))
    if not groups:
        return []
    names = {str(c.id): c.name for c in Category.objects.for_site(site).only("id", "name")}
    today = timezone.localdate()
    out = [_summary(k, members, names, today) for k, members in groups.items()]
    out.sort(key=lambda s: s["created_at"], reverse=True)
    return out


def delete_batch(site, key: str) -> tuple[int, dict | None]:
    """Delete every credit of the grant ``key``. Returns ``(credits deleted,
    summary of the grant as it was)``, or ``(0, None)`` when no such grant."""
    key = (key or "").strip()
    if not key.startswith(("b:", "l:")):
        return 0, None
    groups = _groups(_rows(site))
    members = groups.get(key)
    if not members:
        return 0, None
    summary = _summary(key, members, {}, timezone.localdate())
    ids = [m["id"] for m in members]
    deleted = 0
    with transaction.atomic():
        for start in range(0, len(ids), 500):
            _, per_model = (
                CustomerCredit.objects.for_site(site).filter(pk__in=ids[start:start + 500]).delete()
            )
            deleted += per_model.get(CustomerCredit._meta.label, 0)
    return deleted, summary
