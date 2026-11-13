"""Store credit at checkout: what a shopper's credits take off an order, booking
that use when the order is placed, and giving it back when it is cancelled.

A :class:`~commerce.models.CustomerCredit` is granted by the owner (Customers
page → Credits) to one email. It comes off automatically, for every credit the
shopper still holds, but only for the *verified* shopper: the storefront's
email-OTP session (:mod:`storefront.customer_accounts`). A typed checkout email
is never enough on its own, since anyone could type somebody else's address and
spend their credit; the checkout offers a one-time code to prove the email.

Two kinds of credit:

* **amount**: a balance (``remaining_amount``) drawn down across orders until
  it is spent, then switched off;
* **percent**: a one-time percentage off the eligible lines, used up by the
  order it applies to.

Either may be limited to categories. Credits come off after the coupon
discounts (spread over the lines pro rata) and never take the goods below zero;
GST and shipping are not reduced. Each order keeps what every credit took in
``Order.credit_usage`` so a cancellation can return exactly that.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from commerce.models import CustomerCredit, Order

logger = logging.getLogger("onewooz.audit")

_CENT = Decimal("0.01")
_ZERO = Decimal("0.00")


def _money(value) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(_CENT, rounding=ROUND_HALF_UP)
    except Exception:
        return _ZERO


def _balance(credit: CustomerCredit) -> Decimal:
    """What is left on an amount credit."""
    if credit.remaining_amount is None:
        return _money(credit.credit_value)
    return _money(credit.remaining_amount)


@dataclass
class CreditResult:
    amount: Decimal = _ZERO
    #: ``[(credit, amount taken)]`` in the order they were applied.
    allocations: list = field(default_factory=list)

    @property
    def primary(self):
        return self.allocations[0][0] if self.allocations else None

    def usage(self) -> list[dict]:
        """The JSON kept on the order (``Order.credit_usage``)."""
        return [
            {"id": str(c.pk), "type": c.credit_type, "amount": str(a)}
            for c, a in self.allocations
        ]


def usable_credits(site, email: str) -> list[CustomerCredit]:
    """Every credit ``email`` can spend right now, in the order they apply.

    Percent credits go first (a percentage is worth most on the full basket),
    category-limited before store-wide (so a category credit isn't left unused
    on lines a store-wide one could have covered), soonest expiry first.
    """
    email = (email or "").strip().lower()
    if not email:
        return []
    today = timezone.localdate()
    qs = (
        CustomerCredit.objects.for_site(site)
        .filter(customer_email__iexact=email, is_active=True)
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=today))
    )
    out = []
    for credit in qs:
        if credit.credit_type == CustomerCredit.CreditType.PERCENT:
            if _money(credit.credit_value) <= 0:
                continue
        elif _balance(credit) <= 0:
            continue
        out.append(credit)
    out.sort(
        key=lambda c: (
            0 if c.credit_type == CustomerCredit.CreditType.PERCENT else 1,
            0 if c.scope == CustomerCredit.Scope.CATEGORIES else 1,
            c.valid_until or datetime.date.max,
            c.created_at,
        )
    )
    return out


def _line_categories(lines) -> dict[str, set[str]]:
    """``{product id: {category ids}}``: the primary category plus the extras."""
    out: dict[str, set[str]] = {}
    pks = []
    for ln in lines:
        product = ln.get("product")
        if product is None:
            continue
        pks.append(product.pk)
        cats = out.setdefault(str(product.pk), set())
        if getattr(product, "category_id", None):
            cats.add(str(product.category_id))
    if pks:
        try:
            from commerce.models import Product

            through = Product.categories.through
            for pid, cid in through.objects.filter(product_id__in=pks).values_list(
                "product_id", "category_id"
            ):
                out.setdefault(str(pid), set()).add(str(cid))
        except Exception:
            logger.exception("reading product categories for store credit failed")
    return out


def compute(site, email: str, *, lines, subtotal, discount) -> CreditResult:
    """What ``email``'s credits take off a basket of ``lines`` (cart line dicts)
    whose coupon discounts already come to ``discount``.

    ``email`` must be a *verified* shopper email (see the module docstring).
    """
    result = CreditResult()
    if not lines:
        return result
    credits = usable_credits(site, email)
    if not credits:
        return result

    subtotal = _money(subtotal)
    payable = max(_ZERO, subtotal - _money(discount))
    if subtotal <= 0 or payable <= 0:
        return result

    # What is still to pay on each line once the coupon discounts are spread
    # over the basket pro rata. Kept unrounded; amounts are rounded per credit.
    ratio = payable / subtotal
    left = [Decimal(str(ln.get("line_total") or 0)) * ratio for ln in lines]
    cats = _line_categories(lines)

    for credit in credits:
        if credit.scope == CustomerCredit.Scope.CATEGORIES:
            wanted = {str(x) for x in (credit.category_ids or [])}
            keys = [
                i for i, ln in enumerate(lines)
                if ln.get("product") is not None
                and cats.get(str(ln["product"].pk), set()) & wanted
            ]
        else:
            keys = list(range(len(lines)))
        base = sum((left[i] for i in keys), Decimal("0"))
        if base <= 0:
            continue

        if credit.credit_type == CustomerCredit.CreditType.PERCENT:
            pct = min(Decimal(str(credit.credit_value or 0)), Decimal("100"))
            take = _money(base * pct / Decimal("100"))
        else:
            take = min(_balance(credit), _money(base))
        # Never more than is still to pay, including rounding.
        take = min(take, _money(payable - result.amount))
        if take <= 0:
            continue

        share = take / base
        for i in keys:
            left[i] = max(Decimal("0"), left[i] - left[i] * share)
        result.allocations.append((credit, take))
        result.amount += take

    result.amount = _money(result.amount)
    return result


def book_for_order(order: Order) -> None:
    """Draw the order's credits down, once, when the order is actually placed
    (at once for COD, after the confirmed payment for an online order)."""
    usage = [dict(e) for e in (order.credit_usage or []) if isinstance(e, dict)]
    if not any(not e.get("booked") for e in usage):
        return
    now = timezone.now()
    with transaction.atomic():
        for entry in usage:
            if entry.get("booked"):
                continue
            planned = _money(entry.get("amount"))
            used = _ZERO
            credit = (
                CustomerCredit.objects.for_site(order.site_id)
                .select_for_update()
                .filter(pk=entry.get("id"))
                .first()
            )
            if credit is not None:
                if credit.credit_type == CustomerCredit.CreditType.PERCENT:
                    used = planned
                    credit.is_active = False
                else:
                    balance = _balance(credit)
                    used = min(balance, planned)
                    credit.remaining_amount = balance - used
                    if credit.remaining_amount <= 0:
                        credit.is_active = False
                credit.last_used_at = now
                credit.save(update_fields=["remaining_amount", "is_active", "last_used_at", "updated_at"])
                if used < planned:
                    # Spent elsewhere between checkout and payment confirmation.
                    logger.warning(
                        "store credit %s short for order %s: planned %s, used %s",
                        credit.pk, order.pk, planned, used,
                    )
            entry["booked"] = True
            entry["used"] = str(used)
        Order.all_objects.filter(pk=order.pk).update(credit_usage=usage)
    order.credit_usage = usage


def restore_for_order(order: Order) -> bool:
    """Give back what a cancelled order took from the shopper's credits, once.
    Returns True when anything was returned."""
    try:
        with transaction.atomic():
            raw = (
                Order.all_objects.select_for_update()
                .filter(pk=order.pk)
                .values_list("credit_usage", flat=True)
                .first()
            )
            usage = [dict(e) for e in (raw or []) if isinstance(e, dict)]
            todo = [e for e in usage if e.get("booked") and not e.get("restored")]
            if not todo:
                return False
            for entry in todo:
                credit = (
                    CustomerCredit.objects.for_site(order.site_id)
                    .select_for_update()
                    .filter(pk=entry.get("id"))
                    .first()
                )
                if credit is not None:
                    if credit.credit_type == CustomerCredit.CreditType.AMOUNT:
                        credit.remaining_amount = _balance(credit) + _money(entry.get("used"))
                    credit.is_active = True
                    credit.save(update_fields=["remaining_amount", "is_active", "updated_at"])
                entry["restored"] = True
            Order.all_objects.filter(pk=order.pk).update(credit_usage=usage)
            order.credit_usage = usage
        return True
    except Exception:
        logger.exception("restoring store credit failed order=%s", order.pk)
        return False
