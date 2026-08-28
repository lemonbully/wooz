"""Platform billing: what a store owes OneWooz for running the store.

Distinct from ``Payment`` (money a shopper pays the store). A ``BillingConfig``
sets a per-order charge and a cycle; each qualifying order produces an
``OrderCharge``; charges are periodically rolled into a ``Bill`` the owner pays.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.utils import timezone

from core.models import TenantScopedModel


class BillingConfig(TenantScopedModel):
    """Per-store billing rules."""

    class Cycle(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        WEEKLY = "weekly", "Weekly"
        CUSTOM = "custom", "Custom days"

    class PerOrderType(models.TextChoices):
        FIXED = "fixed", "Fixed amount"
        PERCENT = "percent", "Percentage"

    class ChargeEvent(models.TextChoices):
        CREATED = "created", "On created"
        CONFIRMED = "confirmed", "On accepted"
        SHIPPED = "shipped", "On shipped"
        DELIVERED = "delivered", "On delivered"

    site = models.OneToOneField(
        "tenancy.Site", on_delete=models.CASCADE, related_name="billing_config"
    )
    enabled = models.BooleanField(default=True)
    per_order_charge_type = models.CharField(
        max_length=10, choices=PerOrderType.choices, default=PerOrderType.FIXED
    )
    per_order_charge_value = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    order_charge_event = models.CharField(
        max_length=12, choices=ChargeEvent.choices, default=ChargeEvent.CREATED
    )
    billing_cycle_type = models.CharField(
        max_length=12, choices=Cycle.choices, default=Cycle.MONTHLY
    )
    billing_cycle_days = models.PositiveIntegerField(null=True, blank=True)
    current_period_start = models.DateTimeField(default=timezone.now)
    last_bill_generated_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Billing config ({self.billing_cycle_type})"


class Bill(TenantScopedModel):
    """A generated bill for one billing period."""

    class Status(models.TextChoices):
        UNPAID = "unpaid", "Unpaid"
        PENDING_REVIEW = "pending_review", "Pending review"
        PAID = "paid", "Paid"

    class ScreenshotStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="bills"
    )
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    currency = models.CharField(max_length=3, default="INR")
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_orders = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.UNPAID, db_index=True
    )
    invoice_number = models.CharField(max_length=64, blank=True, default="")
    invoice_pdf = models.FileField(upload_to="bills/", null=True, blank=True)
    payment_screenshot = models.ImageField(upload_to="bill_payments/", null=True, blank=True)
    screenshot_status = models.CharField(
        max_length=10, choices=ScreenshotStatus.choices, default=ScreenshotStatus.PENDING
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-period_start",)
        constraints = [
            models.UniqueConstraint(
                fields=("site", "period_start", "period_end"),
                name="uniq_bill_period_per_site",
            )
        ]
        indexes = [models.Index(fields=("site", "status"))]

    def __str__(self) -> str:
        return f"Bill {self.invoice_number or self.pk}"


class OrderCharge(TenantScopedModel):
    """One per-order charge, later attached to a bill."""

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="order_charges"
    )
    order = models.ForeignKey(
        "commerce.Order", on_delete=models.CASCADE, related_name="charges"
    )
    event = models.CharField(max_length=12, choices=BillingConfig.ChargeEvent.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    charged_at = models.DateTimeField(default=timezone.now)
    bill = models.ForeignKey(
        Bill, on_delete=models.SET_NULL, null=True, blank=True, related_name="charges"
    )
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-charged_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("order", "event"), name="uniq_charge_per_order_event"
            )
        ]
        indexes = [models.Index(fields=("site", "bill"))]

    def __str__(self) -> str:
        return f"Charge {self.amount}"
