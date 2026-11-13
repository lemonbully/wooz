"""The one place that answers "what subscription is this owner on right now?".

Both the profile page (which *shows* the owner their plan) and the in-dashboard
upgrade page (which lets them move onto another) need the same reading of an
owner's current subscription, and it must agree with the two gates that already
exist:

* :mod:`commerce.features`  which dashboard *areas* a **free-plan** owner may
  open; and
* :mod:`billing.renewal`  whether a lapsed **paid** owner is sealed out until
  they renew.

So this module reads the same :class:`~billing.models.SubscriptionOrder` records
those gates read, resolves them to one plan the same way, and is memoised on the
request. It never decides access itself  it only describes the standing so the
UI can present it and offer the right next step.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

__all__ = ["CurrentSubscription", "current_for_owner", "current"]


@dataclass(frozen=True)
class CurrentSubscription:
    """A plain description of one owner's subscription standing.

    ``status`` is the single field the UI branches on:

    * ``"active_paid"``    a paid / one-time subscription inside its period;
    * ``"active_free"``    a free plan that was granted through checkout and is
      still inside its (free) period;
    * ``"free"``           the default free plan (never subscribed, or a free
      grant whose period has passed  a free plan never truly expires);
    * ``"expired"``        a paid / one-time subscription that has lapsed with
      nothing active to replace it (this owner is renewal-locked, see
      :mod:`billing.renewal`);
    * ``"none"``           no plan on record and no free plan configured.
    """

    plan: object | None = None  # SubscriptionPlan | None
    order: object | None = None  # SubscriptionOrder | None
    status: str = "none"
    period_end: object | None = None  # date | None
    # The frozen grant for this owner (billing.models.AccountEntitlement), taken
    # at their last settlement. ``None`` for an owner who never subscribed, who
    # falls back to the live plan's terms. The *values* below (store/collaborator
    # limits) are read from here so a later plan edit never moves the owner; the
    # lifecycle above (which plan, active/expired, dates) stays live.
    entitlement: object | None = None  # AccountEntitlement | None

    # ---- convenience the templates read -----------------------------------
    @property
    def is_active(self) -> bool:
        """True unless a paid subscription has lapsed (free is always active)."""
        return self.status != "expired" and self.status != "none"

    @property
    def is_free(self) -> bool:
        return self.status in ("active_free", "free")

    @property
    def is_paid(self) -> bool:
        return self.status == "active_paid"

    @property
    def is_expired(self) -> bool:
        return self.status == "expired"

    @property
    def plan_name(self) -> str:
        name = getattr(self.plan, "name", "") or ""
        if name:
            return name
        return "Free plan" if self.is_free else "No plan"

    @property
    def slug(self) -> str:
        return getattr(self.plan, "slug", "") or ""

    @property
    def number_of_stores(self) -> int | None:
        """Stores this subscription allows, or ``None`` when unlimited/unknown.

        Read from the frozen grant when the owner has one, so a later edit to the
        plan does not change how many stores an existing owner may build.
        """
        source = self.entitlement or self.plan
        n = getattr(source, "number_of_stores", None)
        return int(n) if n else None

    @property
    def number_of_collaborations(self) -> int:
        source = self.entitlement or self.plan
        return int(getattr(source, "number_of_collaborations", 0) or 0)

    @property
    def included_features(self) -> list:
        """Frozen dashboard-area keys this grant includes (free plans only)."""
        source = self.entitlement or self.plan
        return list(getattr(source, "included_features", None) or [])

    @property
    def included_editor_tabs(self) -> list:
        """Frozen content-editor tab keys this grant includes (free plans only)."""
        source = self.entitlement or self.plan
        return list(getattr(source, "included_editor_tabs", None) or [])

    @property
    def commission(self) -> list:
        """Frozen commission bands, as captured at grant time (may be empty)."""
        return list(getattr(self.entitlement, "commission", None) or [])

    @property
    def penalties(self) -> list:
        """Frozen activity/penalty rules, as captured at grant time."""
        return list(getattr(self.entitlement, "penalties", None) or [])

    @property
    def kind_label(self) -> str:
        """A short human label for the plan shape ("Paid · Monthly", "Free" …)."""
        plan = self.plan
        if plan is None:
            return "Free" if self.is_free else ""
        if plan.plan_type == plan.Kind.FREE:
            return "Free"
        if plan.plan_type == plan.Kind.ONE_TIME:
            return "One-time · 1 year"
        if plan.plan_type == plan.Kind.CUSTOM:
            return "Custom"
        return f"Paid · {plan.get_duration_display()}"


_NONE = CurrentSubscription(status="none")


def _default_free_plan():
    """The configured default free plan, or ``None`` if none is set up."""
    from .models import SubscriptionPlan

    return (
        SubscriptionPlan.objects.filter(
            plan_type=SubscriptionPlan.Kind.FREE, is_active=True
        )
        .order_by("order")
        .first()
    )


def _is_paid_order(order) -> bool:
    """True when ``order`` is a real paid / one-time purchase (not a free grant).

    A free grant is settled as a ₹0 PAID order too, so status alone can't tell
    them apart  the plan type (or, if the plan row is gone, a non-zero amount)
    does. Mirrors :func:`billing.renewal._is_paid_subscription`.
    """
    plan = getattr(order, "plan", None)
    if plan is not None:
        return bool(plan.is_purchasable)
    return (getattr(order, "total_amount", None) or Decimal("0")) > 0


def current_for_owner(user) -> CurrentSubscription:
    """Describe ``user``'s current subscription (at most two queries)."""
    if not user or not getattr(user, "is_authenticated", False):
        return _NONE
    email = (getattr(user, "email", "") or "").strip()
    if not email:
        plan = _default_free_plan()
        return CurrentSubscription(plan=plan, status="free" if plan else "none")

    from django.utils import timezone

    from . import entitlements
    from .models import SubscriptionOrder

    # The owner's frozen grant, if they have ever settled a subscription. When
    # present its values (limits, feature sets) win over the live plan.
    ent = entitlements.for_email(email)

    today = timezone.localdate()
    settled = SubscriptionOrder.objects.filter(
        customer_email__iexact=email,
        status=SubscriptionOrder.Status.PAID,
        current_period_end__isnull=False,
    )

    # Something still inside its paid period  the current subscription, of
    # whatever kind (a free grant counts as active too).
    active = (
        settled.filter(current_period_end__gte=today)
        .select_related("plan")
        .order_by("-current_period_end", "-created_at")
        .first()
    )
    if active is not None:
        plan = active.plan
        is_free = plan is not None and plan.plan_type == plan.Kind.FREE
        return CurrentSubscription(
            plan=plan,
            order=active,
            status="active_free" if is_free else "active_paid",
            period_end=active.current_period_end,
            entitlement=ent,
        )

    # Nothing active. If the most recent subscription was a paid / one-time plan
    # it has lapsed and the owner is renewal-locked; otherwise they fall back to
    # the free plan (a free grant never forces a renewal).
    latest = (
        settled.select_related("plan")
        .order_by("-current_period_end", "-created_at")
        .first()
    )
    if latest is not None and _is_paid_order(latest):
        return CurrentSubscription(
            plan=latest.plan,
            order=latest,
            status="expired",
            period_end=latest.current_period_end,
            entitlement=ent,
        )

    plan = _default_free_plan()
    return CurrentSubscription(
        plan=plan, status="free" if plan else "none", entitlement=ent
    )


_SENTINEL = object()


def store_usage(user, *, used: int | None = None) -> tuple[int, int | None]:
    """``(stores_used, store_limit)`` for an owner.

    ``store_limit`` is ``None`` when the plan grants unlimited stores. ``used``
    counts the owner's own (non-deleted) stores; pass it in to reuse a count the
    caller already has rather than re-querying.
    """
    if used is None:
        used = user.sites.filter(is_deleted=False).count()
    return used, current_for_owner(user).number_of_stores


def store_limit_reached(user, *, used: int | None = None) -> bool:
    """True when the owner has used every store their subscription allows.

    Unlimited plans (``number_of_stores`` is ``None``/0) never lock. Used both to
    badge the "Create a store" nav link and to refuse the create-store view.
    """
    used, limit = store_usage(user, used=used)
    return limit is not None and used >= limit


def current(request) -> CurrentSubscription:
    """Request-memoised :func:`current_for_owner`."""
    cached = getattr(request, "_ow_current_subscription", _SENTINEL)
    if cached is not _SENTINEL:
        return cached
    value = current_for_owner(getattr(request, "user", None))
    try:
        request._ow_current_subscription = value
    except Exception:
        pass
    return value
