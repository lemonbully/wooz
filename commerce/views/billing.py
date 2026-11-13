"""Bills  what the store owes OneWooz for running on the platform.

The store owner sees their commission rate, the commission accruing this period,
and every generated bill. Generating a bill rolls the period's per-order
commission charges into one payable invoice (see :mod:`commerce.billing_service`).
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from billing.models import PlatformPaymentAccount
from commerce import activity, billing_service
from commerce import bill_export as bill_export_xlsx
from commerce.context import get_owned_site, store_context
from commerce.models import Bill, OrderCharge


def _owner_name(site) -> str:
    """The store owner's display name  the 'order user' on the export."""
    owner = getattr(site, "owner", None)
    if owner is None:
        return ""
    return getattr(owner, "display_name", "") or getattr(owner, "email", "") or ""


def _platform_payment_accounts():
    """Active bank + QR payment targets, split for the two modal tabs."""
    accounts = list(PlatformPaymentAccount.objects.filter(is_active=True))
    return {
        "bank_accounts": [a for a in accounts if a.is_bank],
        "qr_accounts": [a for a in accounts if a.is_qr],
        "has_payment_methods": bool(accounts),
    }


@login_required
def bills(request, site_id):
    site = get_owned_site(request, site_id)

    # Make sure every qualifying order is represented before we show the numbers,
    # that open bills follow the subscription's price ranges, and that older
    # unpaid bills carry their GST. A bill whose 15-day period has ended is
    # generated here too (the daily Celery task does the same in the background).
    billing_service.backfill_charges(site)
    billing_service.reprice_open_bills(site)
    billing_service.settle_legacy_bill_tax(site)
    billing_service.generate_due_bill(site)

    qs = Bill.objects.for_site(site)
    outstanding = (
        qs.exclude(status=Bill.Status.PAID).aggregate(t=Sum("total_amount"))["t"]
        or Decimal("0.00")
    )
    outstanding = Decimal(str(outstanding)).quantize(Decimal("0.01"))
    config = billing_service.get_billing_config(site)
    accrual = billing_service.accrual_summary(site)

    # Every delivered order the commerce fee was charged on, newest first, for
    # the "Delivered orders" card's popup: which order, and what it cost.
    charges_qs = OrderCharge.objects.for_site(site)
    delivered_total = charges_qs.count()
    delivered_fee = Decimal(
        str(charges_qs.aggregate(t=Sum("amount"))["t"] or 0)
    ).quantize(Decimal("0.01"))
    delivered_charges = list(
        charges_qs.select_related("order", "bill").order_by("-charged_at")[:500]
    )
    for c in delivered_charges:
        c.selling_value = billing_service.selling_value_of(c)

    context = store_context(request, site, "bills")
    context.update(
        {
            "bills": qs,
            "outstanding": outstanding,
            "config": config,
            "commission": billing_service.current_commission(site),
            "accrual": accrual,
            "bill_every_days": billing_service.BILL_EVERY_DAYS,
            "next_bill_due": billing_service.next_bill_due(config),
            "can_generate": accrual["orders"] > 0,
            "delivered_total": delivered_total,
            "delivered_fee": delivered_fee,
            "delivered_charges": delivered_charges,
        }
    )
    return render(request, "store/bills.html", context)


@login_required
@require_POST
def bill_generate(request, site_id):
    site = get_owned_site(request, site_id)
    bill = billing_service.generate_bill(site)
    if bill is None:
        messages.info(request, "Nothing to bill yet  no unbilled commerce fee this period.")
        return redirect("store:bills", site_id=site.id)
    activity.record(
        "store.bill.generated", request=request, site=site,
        target=bill, target_kind="Bill", label=bill.invoice_number,
    )
    messages.success(
        request,
        f"Bill {bill.invoice_number} generated  {bill.currency} {bill.total_amount} "
        f"across {bill.total_orders} order{'s' if bill.total_orders != 1 else ''}.",
    )
    return redirect("store:bill_detail", site_id=site.id, bill_id=bill.id)


@login_required
def bill_detail(request, site_id, bill_id):
    site = get_owned_site(request, site_id)
    # An open bill is re-worked on the subscription's price ranges first.
    billing_service.reprice_open_bills(site)
    bill = get_object_or_404(Bill.objects.for_site(site), pk=bill_id)
    charges = list(
        bill.charges.select_related("order").order_by("charged_at")
    )
    # Each order's pre-coupon selling value and the per-line basis of its fee.
    for c in charges:
        c.selling_value = billing_service.selling_value_of(c)
        c.fee_lines = (c.meta or {}).get("lines") or []
    tax = billing_service.bill_tax(bill, site)
    context = store_context(request, site, "bills")
    context.update({
        "bill": bill,
        "charges": charges,
        "tax": tax,
        "seller": billing_service.bill_seller(),
        "buyer": billing_service.bill_buyer(site),
        "payment": billing_service.bill_payment_details(),
        "config": billing_service.get_billing_config(site),
        "commission": billing_service.current_commission(site),
    })
    context.update(_platform_payment_accounts())
    context["payment_reference"] = bill.meta.get("payment_reference", "")
    return render(request, "store/bill_detail.html", context)


@login_required
def bill_export(request, site_id, bill_id):
    """Download the bill's commerce fee breakdown as a styled Excel workbook."""
    site = get_owned_site(request, site_id)
    bill = get_object_or_404(Bill.objects.for_site(site), pk=bill_id)
    charges = bill.charges.select_related("order").prefetch_related(
        "order__items__product__category"
    ).order_by("charged_at")
    config = billing_service.get_billing_config(site)

    data = bill_export_xlsx.render_bill_xlsx(
        bill=bill, site=site, charges=charges, config=config,
        owner_name=_owner_name(site), tax=billing_service.bill_tax(bill, site),
    )
    base = bill.invoice_number or f"invoice-{bill.pk}"
    filename = f"{slugify(site.name) or 'onewooz'}-{slugify(base)}-commerce-fee.xlsx"
    response = HttpResponse(
        data,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
@require_POST
def bill_payment_submit(request, site_id, bill_id):
    """Record the owner's payment proof: store the screenshot and put the bill
    into review so an operator can confirm it in the Django admin."""
    site = get_owned_site(request, site_id)
    bill = get_object_or_404(Bill.objects.for_site(site), pk=bill_id)

    screenshot = request.FILES.get("screenshot")
    if not screenshot:
        messages.error(request, "Please attach a screenshot of your payment.")
        return redirect("store:bill_detail", site_id=site.id, bill_id=bill.id)

    bill.payment_screenshot = screenshot
    bill.screenshot_status = Bill.ScreenshotStatus.PENDING
    bill.status = Bill.Status.PENDING_REVIEW
    reference = (request.POST.get("reference") or "").strip()[:120]
    bill.meta = {**(bill.meta or {})}
    bill.meta["payment_reference"] = reference
    bill.meta["payment_submitted_at"] = timezone.now().isoformat()
    bill.save(update_fields=["payment_screenshot", "screenshot_status", "status", "meta", "updated_at"])

    activity.record(
        "store.bill.payment_submitted", request=request, site=site,
        target=bill, target_kind="Bill", label=bill.invoice_number,
    )
    messages.success(
        request,
        "Payment proof submitted. OneWooz will review it and mark the bill paid "
        "once confirmed.",
    )
    return redirect("store:bill_detail", site_id=site.id, bill_id=bill.id)


@login_required
def bill_pdf(request, site_id, bill_id):
    """Serve the bill's PDF invoice  inline by default, or as a download."""
    site = get_owned_site(request, site_id)
    bill = get_object_or_404(Bill.objects.for_site(site), pk=bill_id)
    data = billing_service.bill_pdf_bytes(bill, site)

    base = bill.invoice_number or f"invoice-{bill.pk}"
    filename = f"{slugify(site.name) or 'onewooz'}-{base}.pdf"
    disposition = "attachment" if request.GET.get("download") else "inline"
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    return response
