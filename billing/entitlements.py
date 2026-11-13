"""Freeze what a subscription grants, so plan edits never move existing owners.

The dashboard gates used to read the **live** :class:`~billing.models.SubscriptionPlan`
on every request  so editing a plan in the admin silently changed everyone on
it. This module is the fix: when a subscription is settled (a purchase, a free
grant, or a renewal), we take a **snapshot** of the plan's terms into an
:class:`~billing.models.AccountEntitlement` keyed by the buyer's email, and the
gates read that copy instead.

Semantics agreed with the product:

* The grant is **re-snapshotted on every settlement**, renewal included  a
  renewal takes whatever the plan says *now*, not what the owner held before.
* Between settlements the grant is **frozen**  later plan edits do not touch it.
* An owner who never settled anything (signed up, never subscribed) has no
  snapshot; the resolvers fall back to the live default free plan for them, so
  nothing regresses. Their first free grant creates the snapshot.

Money inside ``commission`` / ``penalties`` is stored as strings so the blob is
JSON-safe and lossless.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.utils import timezone

from .models import AccountEntitlement, SubscriptionOrder, SubscriptionPlan

__all__ = [
    "snapshot_values",
    "grant_from_order",
    "for_email",
    "for_user",
    "concurrent_sessions_for_user",
    "commission_tiers_for_user",
    "commerce_fee_bands_for_user",
    "team_allowance_for_user",
    "EMAIL_MARKETING_DEFAULTS",
    "normalize_email_marketing",
    "email_marketing_limits",
]

logger = logging.getLogger("onewooz.audit")

# Dedicated email-marketing limits block. Kept here (not in outreach) because it
# is frozen onto the entitlement snapshot; the outreach engine reads it through
# ``outreach.services.limits``. 0 means "unlimited" for every numeric cap.
EMAIL_MARKETING_DEFAULTS = {
    "max_recipients_per_campaign": 0,
    "monthly_email_limit": 0,
    "emails_per_minute": 0,
    "allow_ai_builder": False,
    "scheduling": "datetime",
}


def normalize_email_marketing(raw) -> dict:
    """Coerce a stored/authored email-marketing block into the full, typed shape."""
    data = raw if isinstance(raw, dict) else {}
    out = dict(EMAIL_MARKETING_DEFAULTS)
    for key in ("max_recipients_per_campaign", "monthly_email_limit", "emails_per_minute"):
        try:
            out[key] = max(0, int(data.get(key, 0) or 0))
        except (TypeError, ValueError):
            out[key] = 0
    out["allow_ai_builder"] = bool(data.get("allow_ai_builder", False))
    scheduling = str(data.get("scheduling") or "datetime").strip().lower()
    out["scheduling"] = scheduling if scheduling in ("simple", "datetime") else "datetime"
    return out


def _norm(email: str | None) -> str:
    return (email or "").strip().lower()


def snapshot_values(plan: SubscriptionPlan) -> dict:
    """Read a live ``plan`` (and its child rows) into the frozen field values.

    Returns a plain dict of the :class:`AccountEntitlement` value fields  no
    provenance, no email  so both the grant path and the backfill build a
    snapshot the same way.
    """
    commission = [
        {
            "min_orders": int(tier.min_orders or 0),
            "max_orders": (int(tier.max_orders) if tier.max_orders is not None else None),
            "per_order_fee": str(tier.per_order_fee),
        }
        for tier in plan.commission_tiers.all().order_by("order", "min_orders")
    ]
    fee_bands = _bands_from_plan(plan)
    penalties = [
        {
            "window_days": int(p.window_days or 0),
            "threshold_orders": int(p.threshold_orders or 0),
            "reactivation_penalty": str(p.reactivation_penalty),
        }
        for p in plan.activity_penalties.all().order_by("order", "window_days")
    ]
    return {
        "plan_type": plan.plan_type,
        "plan_name": plan.name,
        "number_of_stores": int(plan.number_of_stores or 0),
        "number_of_collaborations": int(plan.number_of_collaborations or 0),
        "concurrent_login_sessions": int(plan.concurrent_login_sessions or 0),
        "included_features": [k for k in (plan.included_features or []) if isinstance(k, str)],
        "included_editor_tabs": [k for k in (plan.included_editor_tabs or []) if isinstance(k, str)],
        "email_marketing": normalize_email_marketing(getattr(plan, "email_marketing", None)),
        "commission": commission,
        "commerce_fee_bands": [
            {
                "min_price": str(b["min_price"]),
                "max_price": (str(b["max_price"]) if b["max_price"] is not None else None),
                "fee": str(b["fee"]),
            }
            for b in fee_bands
        ],
        "penalties": penalties,
        "schema_version": AccountEntitlement.SCHEMA_VERSION,
    }


def grant_from_order(order: SubscriptionOrder) -> AccountEntitlement | None:
    """Snapshot the order's plan into the buyer's entitlement (upsert).

    Called from the settlement choke point (:func:`billing.cashfree._mark_paid`)
    for every kind of settlement  paid, free grant, invite, renewal  so the
    grant is always taken from the plan as it stands at that moment. One row per
    email: a renewal overwrites the previous snapshot.

    Best-effort: returns ``None`` (and never raises) when there is no plan or
    email to snapshot, so a snapshot problem can never undo a confirmed payment.
    The caller treats a failure the same way it treats the notification email.
    """
    email = _norm(getattr(order, "customer_email", ""))
    plan = getattr(order, "plan", None)
    if not email or plan is None:
        return None

    values = snapshot_values(plan)
    entitlement, _ = AccountEntitlement.objects.update_or_create(
        email=email,
        defaults={
            "source_plan": plan,
            "source_order": order,
            "granted_at": timezone.now(),
            **values,
        },
    )
    return entitlement


def for_email(email: str | None) -> AccountEntitlement | None:
    """The frozen entitlement for an email, or ``None`` if none was ever granted."""
    email = _norm(email)
    if not email:
        return None
    return AccountEntitlement.objects.filter(email=email).first()


def for_user(user) -> AccountEntitlement | None:
    """The frozen entitlement for a signed-in owner, or ``None``."""
    if not user or not getattr(user, "is_authenticated", False):
        return None
    return for_email(getattr(user, "email", ""))


def _live_plan_for_user(user) -> SubscriptionPlan | None:
    """The owner's live subscription plan (any type), or the default free plan.

    Used only when the owner has no frozen snapshot yet. Mirrors the live lookup
    in :func:`commerce.features._gating_free_plan`, but returns the plan whatever
    its type  the session cap applies to every plan, not only free ones.
    """
    email = _norm(getattr(user, "email", ""))
    if email:
        active = (
            SubscriptionOrder.objects.filter(
                customer_email__iexact=email,
                status=SubscriptionOrder.Status.PAID,
                current_period_end__gte=timezone.localdate(),
            )
            .select_related("plan")
            .order_by("-current_period_end")
            .first()
        )
        if active is not None and active.plan is not None:
            return active.plan
    return (
        SubscriptionPlan.objects.filter(
            plan_type=SubscriptionPlan.Kind.FREE, is_active=True
        )
        .order_by("order")
        .first()
    )


def _tiers_from_blob(blob) -> list[dict]:
    """Normalise a frozen ``commission`` blob into typed tier dicts."""
    out: list[dict] = []
    for tier in blob or []:
        try:
            out.append(
                {
                    "min_orders": int(tier.get("min_orders") or 0),
                    "max_orders": (
                        int(tier["max_orders"]) if tier.get("max_orders") is not None else None
                    ),
                    "per_order_fee": Decimal(str(tier.get("per_order_fee") or "0")),
                }
            )
        except (TypeError, ValueError, KeyError):
            continue
    return out


def commission_tiers_for_user(user) -> list[dict]:
    """The commission bands that apply to an owner, cheapest-band-first.

    Reads the frozen entitlement snapshot first (so a plan edit never moves an
    existing owner), then the owner's live plan's :class:`CommissionTier` rows,
    then ``[]`` when nothing is configured  in which case the store's flat
    :class:`~commerce.models.BillingConfig` charge applies unchanged.

    Each band: ``{"min_orders": int, "max_orders": int|None, "per_order_fee": Decimal}``.
    A band is a *fixed rupee fee per order* within an order-count range; this is
    how OneWooz actually prices commission (see the admin's Commission Tiers).
    """
    if not user or not getattr(user, "is_authenticated", False):
        return []
    ent = for_user(user)
    if ent is not None and ent.commission:
        return _tiers_from_blob(ent.commission)
    plan = _live_plan_for_user(user)
    if plan is not None:
        return [
            {
                "min_orders": int(t.min_orders or 0),
                "max_orders": (int(t.max_orders) if t.max_orders is not None else None),
                "per_order_fee": Decimal(str(t.per_order_fee)),
            }
            for t in plan.commission_tiers.all().order_by("order", "min_orders")
        ]
    return []


def _bands_from_plan(plan) -> list[dict]:
    """A live plan's :class:`~billing.models.CommerceFeeBand` rows, typed."""
    return [
        {
            "min_price": Decimal(str(b.min_price or 0)),
            "max_price": (Decimal(str(b.max_price)) if b.max_price is not None else None),
            "fee": Decimal(str(b.fee or 0)),
        }
        for b in plan.commerce_fee_bands.all().order_by("order", "min_price")
    ]


def _bands_from_blob(blob) -> list[dict]:
    """Normalise a frozen ``commerce_fee_bands`` blob into typed band dicts."""
    out: list[dict] = []
    for band in blob or []:
        try:
            out.append(
                {
                    "min_price": Decimal(str(band.get("min_price") or "0")),
                    "max_price": (
                        Decimal(str(band["max_price"]))
                        if band.get("max_price") not in (None, "")
                        else None
                    ),
                    "fee": Decimal(str(band.get("fee") or "0")),
                }
            )
        except (TypeError, ValueError, KeyError, ArithmeticError):
            continue
    return out


def commerce_fee_bands_for_user(user) -> list[dict]:
    """The selling-price commerce fee bands that apply to an owner.

    Frozen entitlement first (plan edits never move an existing owner), then the
    plan the grant came from / the owner's live plan, then ``[]``. A grant taken
    before bands existed has an empty frozen list, so it reads the live plan
    until the next settlement re-snapshots it.

    Each band: ``{"min_price": Decimal, "max_price": Decimal|None, "fee": Decimal}``
     a fixed rupee fee per unit sold whose selling price falls in the range.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return []
    ent = for_user(user)
    if ent is not None and getattr(ent, "commerce_fee_bands", None):
        return _bands_from_blob(ent.commerce_fee_bands)
    plan = getattr(ent, "source_plan", None) if ent is not None else None
    if plan is None:
        plan = _live_plan_for_user(user)
    if plan is not None:
        return _bands_from_plan(plan)
    return []


def team_allowance_for_user(user) -> int:
    """How many staff/team members this owner may add (their plan's grant).

    Reads the frozen entitlement first, then the owner's live plan, then the
    default free plan; ``0`` when nothing is configured (no team seats). This is
    the ``number_of_collaborations`` grant  the "team collaborators" a plan
    includes.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return 0
    ent = for_user(user)
    if ent is not None:
        return int(getattr(ent, "number_of_collaborations", 0) or 0)
    plan = _live_plan_for_user(user)
    if plan is not None:
        return int(getattr(plan, "number_of_collaborations", 0) or 0)
    return 0


def email_marketing_limits(user) -> dict:
    """The email-marketing limits that apply to an owner.

    Reads the frozen entitlement first (so a plan edit never moves an existing
    owner), then their live plan, then the defaults. Always fully populated.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return dict(EMAIL_MARKETING_DEFAULTS)
    ent = for_user(user)
    if ent is not None:
        return normalize_email_marketing(getattr(ent, "email_marketing", None))
    plan = _live_plan_for_user(user)
    if plan is not None:
        return normalize_email_marketing(getattr(plan, "email_marketing", None))
    return dict(EMAIL_MARKETING_DEFAULTS)


def concurrent_sessions_for_user(user) -> int:
    """How many devices this owner may be signed in on at once (0 = unlimited).

    Reads the frozen entitlement first (so a plan edit never moves an existing
    owner), then their live active subscription plan, then the configured default
    free plan. Returns 0 (no cap) when nothing is configured  so the limit is
    opt-in per plan and a store with no billing set up is never locked out.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return 0
    ent = for_user(user)
    if ent is not None:
        return int(getattr(ent, "concurrent_login_sessions", 0) or 0)
    plan = _live_plan_for_user(user)
    if plan is not None:
        return int(getattr(plan, "concurrent_login_sessions", 0) or 0)
    return 0
