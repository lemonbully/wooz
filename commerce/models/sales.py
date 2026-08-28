"""Sales: coupons, orders, line items, payments, enquiries and order requests."""

from __future__ import annotations

from decimal import Decimal

from django.db import models, transaction
from django.utils import timezone

from core.models import SoftDeleteMixin, TenantScopedModel


class Coupon(TenantScopedModel, SoftDeleteMixin):
    """A discount code the store can hand out. Fixed or percentage."""

    class DiscountType(models.TextChoices):
        FIXED = "fixed", "Fixed amount"
        PERCENT = "percent", "Percentage"

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="coupons"
    )
    code = models.CharField(max_length=40)
    discount_type = models.CharField(max_length=10, choices=DiscountType.choices)
    value = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    min_order_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    max_discount_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    usage_limit = models.PositiveIntegerField(null=True, blank=True)
    times_used = models.PositiveIntegerField(default=0)
    # e.g. {"include_categories": [...], "exclude_products": [...]}
    conditions = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("site", "code"), name="uniq_coupon_code_per_site"
            )
        ]
        indexes = [models.Index(fields=("site", "code"))]

    def __str__(self) -> str:
        return self.code

    def save(self, *args, **kwargs):
        if self.code:
            self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def is_valid_now(self) -> bool:
        now = timezone.now()
        if not self.active:
            return False
        if self.starts_at and now < self.starts_at:
            return False
        if self.ends_at and now > self.ends_at:
            return False
        if self.usage_limit is not None and self.times_used >= self.usage_limit:
            return False
        return True


class Order(TenantScopedModel):
    """A customer order.

    Order numbers are per-store sequential (``number``) so a store owner sees
    #1, #2, #3 while the primary key stays an unguessable UUIDv7. Status
    transitions stamp their own timestamps for accurate analytics and billing.
    """

    class Status(models.TextChoices):
        CREATED = "created", "Created"
        CONFIRMED = "confirmed", "Confirmed"
        PAID = "paid", "Paid"
        SHIPPED = "shipped", "Shipped"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"
        ARCHIVED = "archived", "Archived"

    class Channel(models.TextChoices):
        # A store that is "both" fulfils consumer orders and business orders
        # through the same pipeline; the channel records which one this was.
        D2C = "d2c", "Consumer"
        B2B = "b2b", "Business"

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="orders"
    )
    number = models.PositiveIntegerField(default=0, db_index=True)
    channel = models.CharField(
        max_length=4, choices=Channel.choices, default=Channel.D2C, db_index=True
    )
    customer = models.ForeignKey(
        "commerce.Customer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )

    # Snapshot of who ordered — kept even if the Customer row is later removed.
    customer_name = models.CharField(max_length=120)
    customer_email = models.EmailField(blank=True, default="")
    customer_phone = models.CharField(max_length=20, blank=True, default="")

    shipping_address = models.TextField(blank=True, default="")
    shipping_city = models.CharField(max_length=100, blank=True, default="")
    shipping_state = models.CharField(max_length=100, blank=True, default="")
    shipping_pincode = models.CharField(max_length=10, blank=True, default="")
    billing_address = models.TextField(blank=True, default="")
    company_name = models.CharField(max_length=200, blank=True, default="")
    gstin = models.CharField(max_length=15, blank=True, default="")

    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.CREATED, db_index=True
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    shipping_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    payment_method = models.CharField(max_length=30, blank=True, default="")
    is_partial_payment = models.BooleanField(default=False)
    partial_paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    partial_cod_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    coupon = models.ForeignKey(
        Coupon, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders"
    )
    credit = models.ForeignKey(
        "commerce.CustomerCredit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )
    credit_discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    tracking_id = models.CharField(max_length=120, blank=True, default="")

    # B2B: a business order can require owner approval before it is confirmed.
    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("site", "number"), name="uniq_order_number_per_site"
            )
        ]
        indexes = [
            models.Index(fields=("site", "status")),
            models.Index(fields=("site", "-created_at")),
            models.Index(fields=("site", "channel")),
        ]

    def __str__(self) -> str:
        return f"Order #{self.number}"

    def save(self, *args, **kwargs):
        self._stamp_status_transition()
        if not self.pk and not self.number:
            self._assign_number()
        super().save(*args, **kwargs)

    def _stamp_status_transition(self) -> None:
        stamp = {
            self.Status.CONFIRMED: "confirmed_at",
            self.Status.SHIPPED: "shipped_at",
            self.Status.DELIVERED: "delivered_at",
            self.Status.CANCELLED: "cancelled_at",
        }.get(self.status)
        if stamp and getattr(self, stamp) is None:
            setattr(self, stamp, timezone.now())

    def _assign_number(self) -> None:
        """Assign the next per-store order number under a row lock."""
        with transaction.atomic():
            last = (
                Order.all_objects.filter(site_id=self.site_id)
                .select_for_update()
                .order_by("-number")
                .values_list("number", flat=True)
                .first()
            )
            self.number = (last or 0) + 1


class OrderItem(TenantScopedModel):
    """A line on an order. Product detail is snapshotted so edits don't rewrite
    history, and the FK is PROTECTed so a product in an order can't be hard
    deleted out from under it."""

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="order_items"
    )
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(
        "commerce.Product", on_delete=models.PROTECT, related_name="order_items"
    )
    title = models.CharField(max_length=255)
    variant_label = models.CharField(max_length=255, blank=True, default="")
    sku = models.CharField(max_length=100, blank=True, default="")
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ("created_at",)
        indexes = [models.Index(fields=("site", "order"))]

    def __str__(self) -> str:
        return f"{self.quantity}× {self.title}"


class Payment(TenantScopedModel):
    """A payment attempt/record against an order."""

    class Gateway(models.TextChoices):
        RAZORPAY = "razorpay", "Razorpay"
        CASHFREE = "cashfree", "Cashfree"
        COD = "cod", "Cash on delivery"

    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="payments"
    )
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    gateway = models.CharField(max_length=20, choices=Gateway.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="INR")
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PROCESSING, db_index=True
    )
    provider_order_id = models.CharField(max_length=120, blank=True, default="")
    provider_payment_id = models.CharField(max_length=120, blank=True, default="")
    provider_signature = models.CharField(max_length=256, blank=True, default="")

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("site", "status"))]

    def __str__(self) -> str:
        return f"{self.gateway} · {self.status}"


class Enquiry(TenantScopedModel, SoftDeleteMixin):
    """A contact / product enquiry from the storefront.

    B2B stores lean on these as lead capture (category + product + quantity);
    D2C stores mostly receive contact-form messages. Both shapes fit here.
    """

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="enquiries"
    )
    categories = models.ManyToManyField(
        "commerce.Category", blank=True, related_name="enquiries"
    )
    products = models.ManyToManyField(
        "commerce.Product", blank=True, related_name="enquiries"
    )
    quantity = models.PositiveIntegerField(null=True, blank=True)

    name = models.CharField(max_length=100, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    mobile_number = models.CharField(max_length=20, blank=True, default="")
    subject = models.CharField(max_length=200, blank=True, default="")
    message = models.TextField(blank=True, default="")
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("site", "is_read"))]

    def __str__(self) -> str:
        return f"Enquiry from {self.name or self.email or 'anonymous'}"


class OrderRequest(TenantScopedModel):
    """A customer-initiated post-order request: cancel, return, replace or a
    warranty claim. The owner approves, rejects or marks it processed."""

    class RequestType(models.TextChoices):
        CANCEL = "cancel", "Cancel"
        RETURN = "return", "Return"
        REPLACE = "replace", "Replace"
        WARRANTY = "warranty", "Warranty"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        PROCESSED = "processed", "Processed"

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="order_requests"
    )
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="requests"
    )
    request_type = models.CharField(max_length=12, choices=RequestType.choices)
    reason = models.CharField(max_length=255, blank=True, default="")
    description = models.TextField(blank=True, default="")
    attachment = models.FileField(upload_to="order_requests/", blank=True, null=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    processed_at = models.DateTimeField(null=True, blank=True)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("site", "order", "request_type"))]

    def __str__(self) -> str:
        return f"{self.request_type} · {self.status}"
