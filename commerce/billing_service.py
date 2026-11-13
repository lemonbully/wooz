"""Platform billing engine  what a store owes OneWooz.

This is the working half of :mod:`commerce.models.billing`: it turns a store's
orders into per-order commission charges and rolls those charges into a payable
bill.

Flow:

* The commerce fee is priced by **selling price**: each delivered order line is
  matched, by its unit selling price (the product variation combination's own
  price, else the product's), to the owner's plan's
  :class:`~billing.models.CommerceFeeBand` and charged that band's fee per unit.
  Coupons never change the band (see :func:`charge_breakdown`).
* Each store has a :class:`~commerce.models.BillingConfig` carrying the order
  milestone that incurs the fee (always "delivered") and the billing cycle
  (always every BILL_EVERY_DAYS = 15 days, generated automatically by the
  ``commerce.tasks.generate_due_bills`` Beat task). Its old flat / percentage
  fee fields are no longer used: with no price ranges on the subscription,
  nothing is charged. It is auto-provisioned the first time billing runs.
* When an order reaches (or passes) that milestone, :func:`accrue_for_order`
  records one :class:`~commerce.models.OrderCharge` for it  idempotently, and
  reversing the charge if the order is later cancelled before it was billed.
* :func:`generate_bill` sums every still-unbilled charge into a
  :class:`~commerce.models.Bill` for the period and stamps the charges as billed,
  so the totals on the bill always equal the sum of its line items.

GST (18%) is added on top of the commerce fee on every bill: CGST 9% + SGST 9%
when the store is in the billing company's state, IGST 18% otherwise (see
:func:`bill_tax`).

Money is Decimal, rounded to paise, so a bill's total matches its charges to the
last unit.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from commerce.models import Bill, BillingConfig, Order, OrderCharge

_TWO = Decimal("0.01")
_ZERO = Decimal("0.00")

# The commerce fee is only ever charged once an order is **delivered**  a
# product rule, not a per-store option. Whatever an older BillingConfig has
# stored for its charge event, billing normalises it to this (see
# ``get_billing_config``), so a store is never billed for an order that has not
# actually reached the customer.
COMMERCE_FEE_EVENT = BillingConfig.ChargeEvent.DELIVERED

# A bill is generated automatically every 15 days (commerce.tasks
# .generate_due_bills, run daily by Celery Beat). Like the charge event this is
# a product rule: every store's billing config is pinned to it.
BILL_EVERY_DAYS = 15

# Order status progression, used to decide whether an order has reached the
# milestone that incurs the commission. "paid" sits between confirmed and
# shipped; it is a status but not itself a charge event.
_PROGRESSION = [
    Order.Status.CREATED,
    Order.Status.CONFIRMED,
    Order.Status.PAID,
    Order.Status.SHIPPED,
    Order.Status.DELIVERED,
]
# The charge event → the order status that first satisfies it.
_EVENT_STATUS = {
    BillingConfig.ChargeEvent.CREATED: Order.Status.CREATED,
    BillingConfig.ChargeEvent.CONFIRMED: Order.Status.CONFIRMED,
    BillingConfig.ChargeEvent.SHIPPED: Order.Status.SHIPPED,
    BillingConfig.ChargeEvent.DELIVERED: Order.Status.DELIVERED,
}
# The timestamp that best dates the charge (so it falls in the right period).
_EVENT_STAMP = {
    BillingConfig.ChargeEvent.CREATED: "created_at",
    BillingConfig.ChargeEvent.CONFIRMED: "confirmed_at",
    BillingConfig.ChargeEvent.SHIPPED: "shipped_at",
    BillingConfig.ChargeEvent.DELIVERED: "delivered_at",
}


def get_billing_config(site) -> BillingConfig:
    """The store's billing config, auto-provisioned from platform defaults.

    The charge event is always normalised to :data:`COMMERCE_FEE_EVENT`
    (delivered)  the commerce fee is only ever charged on delivered orders  so
    the value stored on the config, everything that displays it, and everything
    that accrues against it stay in agreement, even for a store whose config
    predates this rule.
    """
    cfg = BillingConfig.objects.for_site(site).first()
    if cfg is not None:
        # Pin any older/other charge event to "delivered" and the cycle to every
        # BILL_EVERY_DAYS days (once, idempotently).
        changed = []
        if cfg.order_charge_event != COMMERCE_FEE_EVENT:
            cfg.order_charge_event = COMMERCE_FEE_EVENT
            changed.append("order_charge_event")
        if cfg.billing_cycle_type != BillingConfig.Cycle.CUSTOM or cfg.billing_cycle_days != BILL_EVERY_DAYS:
            cfg.billing_cycle_type = BillingConfig.Cycle.CUSTOM
            cfg.billing_cycle_days = BILL_EVERY_DAYS
            changed += ["billing_cycle_type", "billing_cycle_days"]
        if changed:
            cfg.save(update_fields=changed)
        return cfg
    d = getattr(settings, "PLATFORM_BILLING", {})
    return BillingConfig.objects.create(
        site=site,
        enabled=bool(d.get("enabled", True)),
        per_order_charge_type=d.get("per_order_charge_type", BillingConfig.PerOrderType.PERCENT),
        per_order_charge_value=Decimal(str(d.get("per_order_charge_value", "0"))),
        # Always delivered  the commerce fee is charged on delivered orders only.
        order_charge_event=COMMERCE_FEE_EVENT,
        # Billed every BILL_EVERY_DAYS days.
        billing_cycle_type=BillingConfig.Cycle.CUSTOM,
        billing_cycle_days=BILL_EVERY_DAYS,
        current_period_start=timezone.now(),
    )


def _fee_bands(site) -> list[dict]:
    """The owner's commerce fee price ranges, or [] (none configured).

    Each range is ``{"min_price", "max_price", "fee"}``: a product sold at a
    price from the minimum value to the maximum value is charged ``fee``.
    Older order-count Commission tiers (a frozen entitlement or a plan not yet
    migrated) are read as price ranges too: the two numbers of the range are
    the minimum and maximum value. Nothing is ever charged by order count.
    """
    owner = getattr(site, "owner", None)
    if owner is None:
        return []
    try:
        from billing.entitlements import commerce_fee_bands_for_user, commission_tiers_for_user
        from billing.fee_bands import tiers_as_bands

        bands = commerce_fee_bands_for_user(owner)
        if not bands:
            bands = tiers_as_bands(commission_tiers_for_user(owner))
        return bands
    except Exception:
        return []


def band_for_price(bands: list[dict], price) -> dict | None:
    """The price range a product's selling price falls in.

    Both ends are included: with ₹0 – ₹299 → ₹9, a ₹150, ₹200 or ₹299 product
    is charged ₹9. A price in a gap between two ranges (e.g. ₹299.50 between
    ₹0 – ₹299 and ₹300 – ₹499) takes the highest range starting at or below
    it; a price below every range takes the lowest, above every range the
    highest.
    """
    if not bands:
        return None
    price = Decimal(price or 0)
    ordered = sorted(bands, key=lambda b: b["min_price"])
    for b in ordered:
        hi = b["max_price"]
        if price >= b["min_price"] and (hi is None or price <= hi):
            return b
    below = [b for b in ordered if b["min_price"] <= price]
    return below[-1] if below else ordered[0]


def _order_lines(order: Order) -> list:
    from commerce.models import OrderItem

    return list(
        OrderItem.all_objects.filter(order_id=order.pk).order_by("created_at")
    )


def _selling_value(lines) -> Decimal:
    """Σ unit selling price × quantity: what the items were listed at, before
    any coupon (a coupon is an order-level discount and never lowers this)."""
    return sum(
        (Decimal(li.unit_price or 0) * int(li.quantity or 0) for li in lines), _ZERO
    ).quantize(_TWO, rounding=ROUND_HALF_UP)


def _band_label(band: dict) -> str:
    """``₹0 – ₹299`` / ``₹2,000 and above``."""
    from billing.fee_bands import range_label

    return range_label(band["min_price"], band["max_price"])


def charge_breakdown(config: BillingConfig, order: Order, *, bands: list | None = None) -> tuple[Decimal, dict]:
    """The commerce fee on one order, and how it was worked out.

    Returns ``(amount, meta)``; ``meta`` is stored on the
    :class:`~commerce.models.OrderCharge` so the bill can show its basis.

    * **Selling-price bands** (the owner's plan's
      :class:`~billing.models.CommerceFeeBand`) take precedence: every line is
      matched by its unit selling price  the variation combination's own price
      when the line is a variation, else the product's price, exactly as it was
      sold  and charged its band's fee × quantity. A coupon applied to the
      order never moves an item into another band.
      (Older order-count tiers are read as price ranges, see :func:`_fee_bands`.)
    * Else (the subscription sets no price ranges) nothing is charged. There is
      no percentage or flat store-level fallback any more.

    A replacement order (a re-shipment of an order already charged) carries no
    fee of its own.
    """
    if getattr(order, "replacement_seq", 0):
        return _ZERO, {"basis": "replacement"}

    lines = _order_lines(order)
    selling_value = _selling_value(lines)
    site = config.site if getattr(config, "site_id", None) else order.site
    if bands is None:
        bands = _fee_bands(site)
    if bands:
        total = _ZERO
        rows = []
        for li in lines:
            price = Decimal(li.unit_price or 0).quantize(_TWO, rounding=ROUND_HALF_UP)
            qty = int(li.quantity or 0)
            band = band_for_price(bands, price)
            per_unit = Decimal(band["fee"] if band else 0).quantize(_TWO, rounding=ROUND_HALF_UP)
            fee = (per_unit * qty).quantize(_TWO, rounding=ROUND_HALF_UP)
            total += fee
            rows.append({
                "title": li.title,
                "variant": li.variant_label or "",
                "sku": li.sku or "",
                "quantity": qty,
                "selling_price": str(price),
                "band": _band_label(band) if band else "",
                "fee_per_unit": str(per_unit),
                "fee": str(fee),
            })
        return total.quantize(_TWO, rounding=ROUND_HALF_UP), {
            "basis": "selling_price",
            "selling_value": str(selling_value),
            "lines": rows,
        }

    # The owner's subscription sets no price ranges: nothing is charged. (The
    # store-level flat / percentage fee is retired; the commerce fee comes from
    # the subscription's price ranges only.)
    return _ZERO, {
        "basis": "no_price_ranges",
        "selling_value": str(selling_value),
    }


def charge_amount(config: BillingConfig, order: Order) -> Decimal:
    """The commerce fee on one order (see :func:`charge_breakdown`)."""
    return charge_breakdown(config, order)[0]


def selling_value_of(charge) -> Decimal | None:
    """The pre-coupon selling value a charge was worked out on, for display."""
    raw = (getattr(charge, "meta", None) or {}).get("selling_value")
    if raw not in (None, ""):
        try:
            return Decimal(str(raw)).quantize(_TWO)
        except Exception:
            pass
    order = getattr(charge, "order", None)
    if order is None:
        return None
    return _selling_value(_order_lines(order))


def fee_percent(fee, order_value):
    """``fee`` as a percentage of ``order_value`` (2 dp), or ``None``."""
    try:
        fee = Decimal(fee or 0)
        order_value = Decimal(order_value or 0)
    except Exception:
        return None
    if order_value <= 0:
        return None
    return (fee / order_value * Decimal("100")).quantize(_TWO, rounding=ROUND_HALF_UP)


def effective_fee_percent(site, charges=None):
    """The commerce fee as a percentage of order value, for display.

    Owners read the fee as a percent, whatever basis the plan charges on. For a
    fixed ₹/order band this is the real effective rate: total fee ÷ total value
    of the orders it was charged on (``charges`` narrows it, e.g. to one bill;
    by default every charge the store has accrued). ``None`` until an order has
    been charged.
    """
    qs = charges if charges is not None else OrderCharge.objects.for_site(site)
    agg = qs.aggregate(fee=Sum("amount"), value=Sum("order__total"))
    return fee_percent(agg["fee"], agg["value"])


def current_commission(site) -> dict:
    """How this store's commission reads *now*, for the Bills page.

    Returns ``{"type": "fixed"|"percent", "value": Decimal, "event": <label>}``.
    When the owner's plan prices by commission bands, this is the fixed ₹/order
    fee for the band the store's order volume currently sits in  the real basis
    being charged, without exposing the band ranges. Otherwise it falls back to
    the store's flat :class:`BillingConfig` charge.
    """
    config = get_billing_config(site)
    event = config.get_order_charge_event_display()
    bands = _fee_bands(site)
    if bands:
        ordered = sorted(bands, key=lambda b: b["min_price"])
        return {
            "type": "bands",
            "value": None,
            "event": event,
            "percent": None,
            "bands": [
                {"label": _band_label(b), "fee": Decimal(b["fee"]).quantize(_TWO)}
                for b in ordered
            ],
        }
    return {
        "type": "none",
        "value": None,
        "event": event,
        "percent": None,
        "bands": [],
    }


def _fixed_fee_percent(site, fee):
    """A fixed ₹/order fee as a percent of order value: the store's actual
    effective rate once orders have been charged, else the fee against its
    average order value so far. ``None`` when the store has no orders yet."""
    pct = effective_fee_percent(site)
    if pct is not None:
        return pct
    from django.db.models import Avg

    avg = (
        Order.objects.for_site(site)
        .exclude(status__in=[Order.Status.CANCELLED, Order.Status.ARCHIVED])
        .aggregate(a=Avg("total"))["a"]
    )
    return fee_percent(fee, avg)


def _qualifies(status, event) -> bool:
    if status not in _PROGRESSION:
        return False
    return _PROGRESSION.index(status) >= _PROGRESSION.index(_EVENT_STATUS[event])


def _qualifying_statuses(event):
    floor = _PROGRESSION.index(_EVENT_STATUS[event])
    return _PROGRESSION[floor:]


def accrue_for_order(order: Order) -> None:
    """Record (or reverse) the platform charge for one order.

    Idempotent  the ``(order, event)`` uniqueness means an order is charged at
    most once for a milestone, no matter how many times it is saved. If the order
    is cancelled or archived before its charge was rolled into a bill, the
    unbilled charge is reversed so a store is never billed for a sale that fell
    through.
    """
    if order.status in (Order.Status.CANCELLED, Order.Status.ARCHIVED) or getattr(
        order, "replacement_seq", 0
    ):
        # Cancelled/archived: the sale fell through. Replacement: a re-shipment
        # of an order whose own delivery is what the fee is charged on.
        OrderCharge.all_objects.filter(order_id=order.pk, bill__isnull=True).delete()
        return

    config = get_billing_config(order.site)
    if not config.enabled:
        return
    event = config.order_charge_event

    # The commerce fee is charged on delivered orders only. Drop any stale,
    # not-yet-billed charge recorded for a different milestone (e.g. an older
    # "on confirmed" charge from before this rule), so the fee only ever stands
    # against delivery. Only unbilled charges are touched  anything already
    # rolled into a bill is history and left alone.
    OrderCharge.all_objects.filter(
        order_id=order.pk, bill__isnull=True
    ).exclude(event=event).delete()

    if not _qualifies(order.status, event):
        return

    if OrderCharge.all_objects.filter(order_id=order.pk, event=event).exists():
        return
    stamp = getattr(order, _EVENT_STAMP.get(event, "created_at"), None) or timezone.now()
    amount, meta = charge_breakdown(config, order)
    OrderCharge.all_objects.get_or_create(
        order_id=order.pk,
        event=event,
        defaults={
            "site": order.site,
            "amount": amount,
            "charged_at": stamp,
            "meta": meta,
        },
    )


def backfill_charges(site) -> int:
    """Create any missing charges for already-qualifying orders.

    Keeps bills accurate for orders that reached their milestone before billing
    was wired (or if an accrual was ever missed). Returns the number created.
    """
    config = get_billing_config(site)
    if not config.enabled:
        return 0
    event = config.order_charge_event
    stamp_field = _EVENT_STAMP.get(event, "created_at")
    already = set(
        OrderCharge.objects.for_site(site).filter(event=event).values_list("order_id", flat=True)
    )
    orders = (
        Order.objects.for_site(site)
        .filter(status__in=_qualifying_statuses(event), replacement_seq=0)
        .exclude(pk__in=already)
    )
    new = []
    bands = _fee_bands(site)
    for o in orders:
        amount, meta = charge_breakdown(config, o, bands=bands)
        new.append(
            OrderCharge(
                site=site,
                order=o,
                event=event,
                amount=amount,
                meta=meta,
                charged_at=getattr(o, stamp_field, None) or o.created_at,
            )
        )
    if new:
        OrderCharge.objects.bulk_create(new, ignore_conflicts=True)
    reprice_unbilled(site, config)
    return len(new)


def reprice_unbilled(site, config: BillingConfig | None = None) -> int:
    """Re-work every not-yet-billed charge on the current basis.

    A charge accrued before the selling-price bands were configured (or before
    the plan's bands changed) is brought in line before it is shown or billed.
    Anything already rolled into a bill is history and never touched. Returns
    the number of charges whose amount changed.
    """
    config = config or get_billing_config(site)
    bands = _fee_bands(site)
    changed = 0
    unbilled = OrderCharge.objects.for_site(site).filter(bill__isnull=True).select_related("order")
    for charge in unbilled:
        if charge.order is None:
            continue
        amount, meta = charge_breakdown(config, charge.order, bands=bands)
        if amount != charge.amount or (charge.meta or {}) != meta:
            OrderCharge.all_objects.filter(pk=charge.pk).update(amount=amount, meta=meta)
            if amount != charge.amount:
                changed += 1
    return changed


def unbilled_charges(site):
    return OrderCharge.objects.for_site(site).filter(bill__isnull=True)


def accrual_summary(site) -> dict:
    """The commission accrued this period but not yet billed."""
    agg = unbilled_charges(site).aggregate(total=Sum("amount"), orders=Count("order", distinct=True))
    # SQLite sums decimals as floats (1.98000000000000…); round to paise.
    total = Decimal(str(agg["total"] or 0)).quantize(_TWO, rounding=ROUND_HALF_UP)
    return {"total": total, "orders": agg["orders"] or 0}


def _next_invoice_number(site, now) -> str:
    seq = Bill.objects.for_site(site).count() + 1
    return f"OW-{now:%Y%m}-{seq:04d}"


def generate_bill(site, *, now=None) -> Bill | None:
    """Roll every unbilled charge into a new bill for the current period.

    Returns the created :class:`~commerce.models.Bill`, or ``None`` when there is
    nothing to bill. The bill's ``total_amount`` and ``total_orders`` are computed
    from the charges attached to it, so they always reconcile.
    """
    now = now or timezone.now()
    config = get_billing_config(site)
    # Make sure every qualifying order is represented before we tally.
    backfill_charges(site)

    charges = list(unbilled_charges(site).filter(charged_at__lte=now))
    if not charges:
        return None

    subtotal = sum((c.amount for c in charges), _ZERO).quantize(_TWO)
    if subtotal <= _ZERO:
        # Nothing to pay (no price ranges on the subscription): no bill.
        return None
    tax = compute_tax(subtotal, site)
    total = tax["total"]
    order_ids = {c.order_id for c in charges}
    period_start = config.current_period_start or min(c.charged_at for c in charges)
    if period_start > now:
        period_start = min(c.charged_at for c in charges)

    with transaction.atomic():
        bill = Bill.objects.create(
            site=site,
            period_start=period_start,
            period_end=now,
            currency=(site.currency or "INR")[:3],
            total_amount=total,
            total_orders=len(order_ids),
            invoice_number=_next_invoice_number(site, now),
            status=Bill.Status.UNPAID,
            meta={"tax": _tax_to_meta(tax), "pdf_v2": True},
        )
        OrderCharge.objects.for_site(site).filter(pk__in=[c.pk for c in charges]).update(bill=bill)
        config.current_period_start = now
        config.last_bill_generated_at = now
        config.save(update_fields=["current_period_start", "last_bill_generated_at"])

    # Render and attach the PDF once the bill and its charges are committed.
    # Best-effort: if rendering ever fails the bill still stands and the download
    # view regenerates it on demand.
    try:
        _render_and_store_pdf(bill, site, config)
    except Exception:
        pass
    return bill


def _render_and_store_pdf(bill: Bill, site, config: BillingConfig | None = None) -> bytes:
    """Render the invoice PDF for ``bill`` and save it onto ``bill.invoice_pdf``."""
    from django.core.files.base import ContentFile

    from commerce import invoice_pdf

    config = config or get_billing_config(site)
    charges = bill.charges.select_related("order").order_by("charged_at")
    data = invoice_pdf.render_bill_pdf(
        bill=bill, site=site, charges=charges, config=config,
        tax=bill_tax(bill, site), seller=bill_seller(), buyer=bill_buyer(site),
        payment=bill_payment_details(),
    )
    bill.invoice_pdf.save(f"{bill.invoice_number or bill.pk}.pdf", ContentFile(data), save=True)
    return data


def bill_pdf_bytes(bill: Bill, site) -> bytes:
    """Return the bill's PDF, reusing the stored file or rendering it fresh.

    Robust to a missing media file (e.g. ephemeral storage): if the stored file
    can't be read, it is re-rendered and, where possible, re-saved.
    """
    if not (bill.meta or {}).get("pdf_v2"):
        data = _render_and_store_pdf(bill, site)
        bill.meta = {**(bill.meta or {}), "pdf_v2": True}
        Bill.all_objects.filter(pk=bill.pk).update(meta=bill.meta)
        return data
    field = bill.invoice_pdf
    if field:
        try:
            field.open("rb")
            try:
                data = field.read()
            finally:
                field.close()
            if data:
                return data
        except (FileNotFoundError, ValueError, OSError):
            pass
    return _render_and_store_pdf(bill, site)


# ---------------------------------------------------------------------------
# GST on the commerce fee, and the parties printed on a bill
# ---------------------------------------------------------------------------
#: GST on OneWooz's commerce fee (a SaaS service): 18%, split CGST 9% + SGST 9%
#: within the billing company's state, IGST 18% across states.
COMMERCE_FEE_GST_RATE = Decimal("18")


def bill_seller() -> dict:
    """Who bills the store: the admin's active "Commerce fee bill details",
    any blank field filled from "Invoice details" / settings."""
    from billing import gst
    from billing.bill import seller as platform_seller

    base = platform_seller()
    try:
        from billing.models import CommerceBillDetails

        obj = CommerceBillDetails.current()
    except Exception:  # table not migrated yet: fall back, keep bills working
        obj = None

    def pick(attr, fallback=""):
        val = (getattr(obj, attr, "") or "") if obj is not None else ""
        val = val.strip() if isinstance(val, str) else val
        return val or fallback or ""

    obj_gstin = gst.normalise_gstin(getattr(obj, "gstin", "") if obj is not None else "")
    obj_state = ((getattr(obj, "state", "") or "").strip()) if obj is not None else ""
    if obj_gstin or obj_state:
        gstin, raw_state = obj_gstin, obj_state
    else:
        gstin, raw_state = base.get("gstin") or "", base.get("state") or ""
    return {
        "company_name": pick("company_name", base.get("company_name")) or "OneWooz",
        "address": pick("address", base.get("address")),
        "gstin": gstin,
        "pan": pick("pan"),
        "state": gst.effective_state(raw_state, gstin),
        "sac": pick("sac_code", base.get("hsn")) or "998313",
        "email": pick("email", base.get("email")),
        "phone": pick("phone", base.get("mobile")),
    }


def bill_payment_details() -> dict:
    """How to pay a bill: the admin's "Commerce fee bill details" payment
    fields, else the active Platform payment accounts.

    ``{"banks": [{bank_name, account_holder, account_number, ifsc_code, branch}],
    "upis": [str], "note": str}``  empty lists when nothing is configured.
    """
    banks, upis, note = [], [], ""
    try:
        from billing.models import CommerceBillDetails

        obj = CommerceBillDetails.current()
    except Exception:
        obj = None
    if obj is not None and obj.has_payment_details:
        if obj.account_number:
            banks.append({
                "bank_name": obj.bank_name, "account_holder": obj.account_holder,
                "account_number": obj.account_number, "ifsc_code": obj.ifsc_code,
                "branch": obj.branch,
            })
        if obj.upi_id:
            upis.append(obj.upi_id)
        note = obj.payment_note
    else:
        try:
            from billing.models import PlatformPaymentAccount

            for acc in PlatformPaymentAccount.objects.filter(is_active=True):
                if acc.is_bank and acc.account_number:
                    banks.append({
                        "bank_name": acc.bank_name, "account_holder": acc.account_holder,
                        "account_number": acc.account_number, "ifsc_code": acc.ifsc_code,
                        "branch": "",
                    })
                elif acc.is_qr and acc.upi_id:
                    upis.append(acc.upi_id)
                if acc.note and not note:
                    note = acc.note
        except Exception:
            pass
    return {"banks": banks, "upis": upis, "note": note}


def bill_buyer(site) -> dict:
    """The store being billed: its legal name, GSTIN and address (the default
    warehouse), whose state decides CGST + SGST vs IGST."""
    from billing import gst

    from commerce.models import Warehouse

    wh = (
        Warehouse.objects.for_site(site).filter(is_default=True).first()
        or Warehouse.objects.for_site(site).first()
    )
    gstin = gst.normalise_gstin(getattr(site, "gstin", ""))
    address = ""
    if wh is not None:
        address = ", ".join(p for p in (wh.address_line, wh.city, wh.state, wh.pincode) if p)
    return {
        "name": getattr(site, "legal_name", "") or site.name,
        "brand": site.name,
        "gstin": gstin,
        "state": gst.effective_state(wh.state if wh else "", gstin),
        "address": address,
        "email": getattr(site, "support_email", "") or "",
        "phone": getattr(site, "support_phone", "") or "",
    }


def compute_tax(subtotal, site) -> dict:
    """18% GST on a commerce fee ``subtotal`` for ``site``."""
    from billing import gst

    subtotal = Decimal(subtotal or 0).quantize(_TWO, rounding=ROUND_HALF_UP)
    seller = bill_seller()
    buyer = bill_buyer(site)
    intra = gst.is_intra_state(buyer["state"], buyer["gstin"], seller["state"], seller["gstin"])
    gst_total = (subtotal * COMMERCE_FEE_GST_RATE / Decimal("100")).quantize(_TWO, rounding=ROUND_HALF_UP)
    if intra:
        cgst = (gst_total / 2).quantize(_TWO, rounding=ROUND_HALF_UP)
        sgst, igst = gst_total - cgst, _ZERO
    else:
        cgst, sgst, igst = _ZERO, _ZERO, gst_total
    return {
        "subtotal": subtotal,
        "rate": COMMERCE_FEE_GST_RATE,
        "half_rate": COMMERCE_FEE_GST_RATE / 2,
        "intra": intra,
        "cgst": cgst,
        "sgst": sgst,
        "igst": igst,
        "gst": gst_total,
        "total": (subtotal + gst_total).quantize(_TWO),
        "applied": True,
    }


def _tax_to_meta(tax: dict) -> dict:
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in tax.items()}


def _tax_from_meta(raw: dict) -> dict:
    out = {}
    for k, v in (raw or {}).items():
        if k in ("intra", "applied"):
            out[k] = bool(v)
        else:
            try:
                out[k] = Decimal(str(v))
            except Exception:
                out[k] = v
    return out


def bill_tax(bill: Bill, site) -> dict:
    """The GST breakup of a bill.

    A bill generated with GST carries it in ``meta["tax"]``. An older unpaid bill
    (from before GST was added) gets its GST worked out and saved now, so its
    amount due includes it; an older bill already paid or under review is left
    as it was issued (no GST).
    """
    raw = (bill.meta or {}).get("tax")
    if raw:
        return _tax_from_meta(raw)
    subtotal = Decimal(bill.total_amount or 0).quantize(_TWO)
    if bill.status == Bill.Status.UNPAID:
        tax = compute_tax(subtotal, site)
        bill.meta = {**(bill.meta or {}), "tax": _tax_to_meta(tax)}
        bill.total_amount = tax["total"]
        Bill.all_objects.filter(pk=bill.pk).update(meta=bill.meta, total_amount=bill.total_amount)
        if bill.invoice_pdf:
            # The stored PDF predates the GST; drop it so it is re-rendered.
            Bill.all_objects.filter(pk=bill.pk).update(invoice_pdf=None)
            bill.invoice_pdf = None
        return tax
    return {
        "subtotal": subtotal, "rate": _ZERO, "half_rate": _ZERO, "intra": False,
        "cgst": _ZERO, "sgst": _ZERO, "igst": _ZERO, "gst": _ZERO,
        "total": subtotal, "applied": False,
    }


def reprice_open_bills(site) -> int:
    """Re-work the commerce fee on bills that are still open.

    A bill issued before the fee followed the subscription's price ranges (for
    example at a flat 2% of selling value) is recalculated: every charge on it is
    worked out again from the owner's price ranges, and the bill's GST and total
    follow. Only UNPAID bills with no payment proof submitted are touched; a bill
    being paid, under review or paid stays exactly as it was issued. Returns the
    number of bills changed.
    """
    from django.utils import timezone as _tz

    config = get_billing_config(site)
    bands = _fee_bands(site)
    changed_bills = 0
    for bill in Bill.objects.for_site(site).filter(status=Bill.Status.UNPAID):
        if bill.payment_screenshot:
            continue
        charges = list(OrderCharge.all_objects.filter(bill_id=bill.pk).select_related("order"))
        changed = False
        for charge in charges:
            if charge.order is None:
                continue
            amount, meta = charge_breakdown(config, charge.order, bands=bands)
            if amount != charge.amount or (charge.meta or {}) != meta:
                OrderCharge.all_objects.filter(pk=charge.pk).update(amount=amount, meta=meta)
                charge.amount = amount
                changed = True
        if not changed:
            continue
        subtotal = sum((c.amount for c in charges), _ZERO).quantize(_TWO)
        tax = compute_tax(subtotal, site)
        meta = {**(bill.meta or {}), "tax": _tax_to_meta(tax), "repriced_at": _tz.now().isoformat()}
        # The stored PDF shows the old amounts; drop it so it is re-rendered.
        Bill.all_objects.filter(pk=bill.pk).update(total_amount=tax["total"], meta=meta, invoice_pdf=None)
        changed_bills += 1
    return changed_bills


def next_bill_due(config: BillingConfig):
    """When the next automatic bill is due (BILL_EVERY_DAYS after the period began)."""
    from datetime import timedelta

    start = config.current_period_start or config.last_bill_generated_at or timezone.now()
    return start + timedelta(days=BILL_EVERY_DAYS)


def generate_due_bill(site, *, now=None) -> Bill | None:
    """Generate this store's bill if its BILL_EVERY_DAYS-day period has ended.

    With nothing to bill at that point the period simply moves on, so the next
    bill is again BILL_EVERY_DAYS days later (never "immediately" when the next
    order lands). Returns the bill, or ``None``.
    """
    now = now or timezone.now()
    config = get_billing_config(site)
    if not config.enabled or next_bill_due(config) > now:
        return None
    reprice_open_bills(site)
    bill = generate_bill(site, now=now)
    if bill is None:
        config.current_period_start = now
        config.save(update_fields=["current_period_start"])
    return bill


def generate_all_due_bills(*, now=None, limit: int = 2000) -> int:
    """Celery Beat (daily): generate the bill of every store whose
    BILL_EVERY_DAYS-day period has ended, and tell its owner in the dashboard
    bell. Returns the number of bills generated. One store failing never stops
    the rest."""
    import logging

    from core.queries import sorted_rows

    log = logging.getLogger("onewooz.audit")
    now = now or timezone.now()
    made = 0
    configs = sorted_rows(
        BillingConfig.objects.across_all_sites()
        .select_related("site")
        .filter(enabled=True, site__is_deleted=False)
        .order_by("pk"),
        limit=limit,
    )
    for config in configs:
        site = config.site
        try:
            bill = generate_due_bill(site, now=now)
        except Exception:
            log.exception("automatic bill failed site=%s", getattr(site, "id", "?"))
            continue
        if bill is None:
            continue
        made += 1
        _notify_bill_generated(site, bill)
    return made


def _notify_bill_generated(site, bill: Bill) -> None:
    """Bell notification for an automatically generated bill (best-effort)."""
    try:
        from accounts import notify
        from commerce import bell_links

        try:
            url = bell_links._absolute(bell_links._app_reverse("bill_detail", site_id=site.id, bill_id=bill.id))
        except Exception:
            url = ""
        notify.push_to_owner(
            site,
            headline=f"New bill {bill.invoice_number} · {bill.currency} {bill.total_amount:,.2f}",
            description=(
                f"Your {BILL_EVERY_DAYS}-day commerce fee bill for {site.name} is ready: "
                f"{bill.total_orders} delivered order{'s' if bill.total_orders != 1 else ''}, "
                f"{bill.currency} {bill.total_amount:,.2f} including GST."
            ),
            kind="system",
            level="info",
            action_url=url,
        )
    except Exception:
        pass


def settle_legacy_bill_tax(site) -> None:
    """Bring every older unpaid bill up to GST-inclusive amounts (idempotent)."""
    for bill in Bill.objects.for_site(site).filter(status=Bill.Status.UNPAID):
        if not (bill.meta or {}).get("tax"):
            bill_tax(bill, site)
