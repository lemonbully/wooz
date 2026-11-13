"""When an owner must renew before they can use the dashboard again.

This is the lifecycle counterpart to :mod:`commerce.features`. Feature gating
decides which *areas* a **free-plan** owner may open; this module decides the
harsher case: an owner who once held a **paid / one-time** subscription that has
now **lapsed**. Until they renew, the whole owner dashboard is walled behind a
universal, un-dismissable popup (see :mod:`billing.middleware`).

The two never fight:

* An owner with a currently-active subscription (paid, one-time *or* a free
  grant still inside its period) is not locked here.
* An owner who never bought a paid plan  a perpetual free-plan user, or someone
  who signed up and never subscribed  is not locked here either; their access
  is governed by :mod:`commerce.features`, which shows an *Upgrade* wall on the
  areas their free plan omits rather than sealing the dashboard shut.
* Only an owner whose most recent **paid / one-time** subscription has expired,
  with nothing active to replace it, is walled  because that is the person who
  paid, lapsed, and must renew to carry on.

The state is computed from the same :class:`~billing.models.SubscriptionOrder`
records the checkout writes and is memoised on the request, so a locked page
load costs at most one extra query.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

__all__ = [
    "RenewalState",
    "state_for_owner",
    "state",
    "renewal_cards",
    "renewal_card",
    "plan_allowed",
]


@dataclass(frozen=True)
class RenewalState:
    """The renewal standing of one owner.

    ``locked`` is the only thing the middleware needs; ``order`` and ``plan``
    describe the lapsed subscription so the popup can lead with *their* plan.
    ``plan`` is only set when that plan is still an active, purchasable plan the
    owner can actually renew onto  it may be ``None`` even while locked (the
    plan was retired), in which case the popup simply leads with the other
    plans.
    """

    locked: bool = False
    order: object | None = None  # SubscriptionOrder | None
    plan: object | None = None  # SubscriptionPlan | None

    @property
    def current_slug(self) -> str:
        return getattr(self.plan, "slug", "") or ""


_UNLOCKED = RenewalState(locked=False)


def _is_paid_subscription(order) -> bool:
    """True when ``order`` is a real paid / one-time subscription (not a free
    grant). A free grant is settled as a ₹0 PAID order too, so status alone
    can't tell them apart  the plan type (or, if the plan row is gone, a
    non-zero amount) does."""
    plan = getattr(order, "plan", None)
    if plan is not None:
        return bool(plan.is_purchasable)
    return (order.total_amount or Decimal("0")) > 0


def state_for_owner(user) -> RenewalState:
    """Compute an owner's :class:`RenewalState` (one or two queries)."""
    if not user or not getattr(user, "is_authenticated", False):
        return _UNLOCKED
    email = (getattr(user, "email", "") or "").strip()
    if not email:
        return _UNLOCKED

    from django.utils import timezone

    from .models import SubscriptionOrder

    today = timezone.localdate()
    settled = SubscriptionOrder.objects.filter(
        customer_email__iexact=email,
        status=SubscriptionOrder.Status.PAID,
        current_period_end__isnull=False,
    )

    # Anything still inside its paid period  of any kind, including a free
    # grant  means there is nothing to renew right now.
    if settled.filter(current_period_end__gte=today).exists():
        return _UNLOCKED

    # Nothing active. Look at the most recent subscription overall: if it was a
    # paid / one-time plan, it has lapsed and the owner must renew. If it was a
    # free grant (or there is nothing on record), they are not forced to renew.
    latest = (
        settled.select_related("plan")
        .order_by("-current_period_end", "-created_at")
        .first()
    )
    if latest is None or not _is_paid_subscription(latest):
        return _UNLOCKED

    plan = latest.plan
    current = plan if (plan is not None and plan.is_active and plan.is_purchasable) else None
    return RenewalState(locked=True, order=latest, plan=current)


_SENTINEL = object()


def state(request) -> RenewalState:
    """Request-memoised :func:`state_for_owner`."""
    cached = getattr(request, "_ow_renewal_state", _SENTINEL)
    if cached is not _SENTINEL:
        return cached
    value = state_for_owner(getattr(request, "user", None))
    try:
        request._ow_renewal_state = value
    except Exception:
        pass
    return value


def renewal_cards(current_slug: str = ""):
    """Active plans for the renewal popup, the owner's current plan first.

    Reuses :func:`billing.pricing.plan_cards` (same annotations the pricing page
    reads) and orders the lapsed plan to the front, flagged ``is_current`` so the
    template can label it. Every active plan is offered  a paid/one-time plan is
    renewed through the gateway, a free plan continues them for free, a custom
    plan links out to contact.
    """
    from . import pricing

    cards = pricing.plan_cards()
    for plan in cards:
        plan.is_current = bool(current_slug) and plan.slug == current_slug

    cards.sort(key=lambda p: (0 if p.is_current else 1, p.order, p.name))
    return cards


def renewal_card(renewal_state: RenewalState):
    """The lapsed plan, annotated exactly like a pricing card, or ``None``.

    A locked owner whose plan is still sold renews **that plan only**: the wall
    shows this one card and the checkout endpoints refuse every other plan (see
    :func:`plan_allowed`). ``None`` means the plan was retired, in which case the
    wall falls back to the full list so the owner is never trapped.
    """
    plan = getattr(renewal_state, "plan", None)
    if plan is None:
        return None
    from . import pricing

    for card in pricing.plan_cards():
        if card.pk == plan.pk:
            card.is_current = True
            return card
    return None


def plan_allowed(renewal_state: RenewalState, plan) -> bool:
    """May this owner check out ``plan`` right now?

    Unlocked owners may buy anything (the upgrade page shares these endpoints).
    A locked owner whose previous plan is still renewable may only renew that
    plan: no other paid plan, and no free plan, until it is active again. If the
    previous plan was retired there is nothing to pin them to, so any plan goes.
    """
    if not renewal_state.locked or renewal_state.plan is None:
        return True
    return plan is not None and plan.pk == renewal_state.plan.pk
