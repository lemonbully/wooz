"""Bills — what the store owes OneWooz for running on the platform."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import render

from commerce.context import get_owned_site, store_context
from commerce.models import Bill, BillingConfig


@login_required
def bills(request, site_id):
    site = get_owned_site(request, site_id)
    qs = Bill.objects.for_site(site)

    outstanding = (
        qs.exclude(status=Bill.Status.PAID).aggregate(t=Sum("total_amount"))["t"]
        or Decimal("0.00")
    )
    config = BillingConfig.objects.for_site(site).first()

    context = store_context(request, site, "bills")
    context.update(
        {
            "bills": qs,
            "outstanding": outstanding,
            "config": config,
        }
    )
    return render(request, "store/bills.html", context)
