"""Sales: coupons, orders, line items, payments, enquiries and order requests."""

from __future__ import annotations

from decimal import Decimal

from django.db import models, transaction
from django.utils import timezone

from core.managers import SiteScopedManager
from core.models import SoftDeleteMixin, TenantScopedModel


class Coupon(TenantScopedModel, SoftDeleteMixin):
    """A store discount, modelled on Shopify's four discount classes.

    A single row captures every variation the owner can configure:

    * **Class**  amount off *products*, amount off the whole *order*, free
      *shipping*, or *buy X get Y*.
    * **Method**  a *code* the shopper types, or an *automatic* discount that
      applies itself when the cart qualifies.
    * **Value**  a percentage or a fixed amount (the reward for BXGY).
    * **Applies to**  every product, a set of categories, or a set of products.
    * **Minimum requirement**  none, a minimum spend, or a minimum quantity.
    * **Customer eligibility**  everyone, or a named set of customers.
    * **Usage limits**  a total redemption cap and/or one-per-customer.
    * **Combinations**  whether it stacks with product/order/shipping discounts.
    * **Active window**  an optional start and end.

    The scalar knobs live in typed columns; product/category/customer selections
    live in ``*_ids`` JSON lists (re-resolved through ``.for_site`` at apply time,
    so a stale id is simply ignored and can never leak across tenants). The
    evaluation of all of this lives in :mod:`commerce.discounts`.
    """

    class DiscountClass(models.TextChoices):
        PRODUCT = "product", "Amount off products"
        ORDER = "order", "Amount off order"
        SHIPPING = "shipping", "Free shipping"
        BXGY = "bxgy", "Buy X get Y"

    class Method(models.TextChoices):
        CODE = "code", "Discount code"
        AUTOMATIC = "automatic", "Automatic discount"

    class DiscountType(models.TextChoices):
        FIXED = "fixed", "Fixed amount"
        PERCENT = "percent", "Percentage"

    class AppliesTo(models.TextChoices):
        ALL = "all", "All products"
        CATEGORIES = "categories", "Specific categories"
        PRODUCTS = "products", "Specific products"

    class MinRequirement(models.TextChoices):
        NONE = "none", "No minimum requirements"
        AMOUNT = "amount", "Minimum purchase amount"
        QUANTITY = "quantity", "Minimum quantity of items"

    class CustomerEligibility(models.TextChoices):
        ALL = "all", "All customers"
        SPECIFIC = "specific", "Specific customers"

    class BuyType(models.TextChoices):
        QUANTITY = "quantity", "Minimum quantity of items"
        AMOUNT = "amount", "Minimum purchase amount"

    class RewardType(models.TextChoices):
        PERCENT = "percent", "Percentage off"
        AMOUNT = "amount", "Amount off each"
        FREE = "free", "Free"

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="coupons"
    )

    # -- identity & method -------------------------------------------------
    discount_class = models.CharField(
        max_length=10, choices=DiscountClass.choices, default=DiscountClass.ORDER
    )
    method = models.CharField(max_length=10, choices=Method.choices, default=Method.CODE)
    # ``code`` is the shopper-facing code for coded discounts. Automatic
    # discounts have no code; ``title`` names them in the dashboard/analytics.
    code = models.CharField(max_length=40, blank=True, default="")
    title = models.CharField(max_length=120, blank=True, default="")

    # -- value (product / order classes; BXGY uses reward_* below) ----------
    discount_type = models.CharField(
        max_length=10, choices=DiscountType.choices, default=DiscountType.PERCENT
    )
    value = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    # -- applies to (product & BXGY "get" scope) ---------------------------
    applies_to = models.CharField(
        max_length=12, choices=AppliesTo.choices, default=AppliesTo.ALL
    )
    applies_product_ids = models.JSONField(default=list, blank=True)
    applies_category_ids = models.JSONField(default=list, blank=True)

    # -- minimum requirement -----------------------------------------------
    min_requirement = models.CharField(
        max_length=10, choices=MinRequirement.choices, default=MinRequirement.NONE
    )
    min_order_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    min_quantity = models.PositiveIntegerField(null=True, blank=True)
    # Percentage discounts can be capped so a big cart can't run away.
    max_discount_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # -- customer eligibility ----------------------------------------------
    customer_eligibility = models.CharField(
        max_length=10, choices=CustomerEligibility.choices, default=CustomerEligibility.ALL
    )
    eligible_customer_ids = models.JSONField(default=list, blank=True)

    # -- usage limits ------------------------------------------------------
    usage_limit = models.PositiveIntegerField(null=True, blank=True)
    once_per_customer = models.BooleanField(default=False)
    times_used = models.PositiveIntegerField(default=0)

    # -- combinations ------------------------------------------------------
    combines_with_product = models.BooleanField(default=False)
    combines_with_order = models.BooleanField(default=False)
    combines_with_shipping = models.BooleanField(default=False)

    # -- free-shipping options ---------------------------------------------
    # If set, only shipping rates at or below this amount are made free.
    shipping_max_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # -- buy X get Y -------------------------------------------------------
    buy_type = models.CharField(
        max_length=10, choices=BuyType.choices, default=BuyType.QUANTITY
    )
    buy_quantity = models.PositiveIntegerField(null=True, blank=True)
    buy_min_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    buy_product_ids = models.JSONField(default=list, blank=True)
    buy_category_ids = models.JSONField(default=list, blank=True)
    get_quantity = models.PositiveIntegerField(null=True, blank=True)
    get_product_ids = models.JSONField(default=list, blank=True)
    get_category_ids = models.JSONField(default=list, blank=True)
    reward_type = models.CharField(
        max_length=10, choices=RewardType.choices, default=RewardType.FREE
    )
    reward_value = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    # Cap how many reward sets a single order can earn (Shopify's "Set a maximum
    # number of uses per order"). Null = uncapped.
    bxgy_max_per_order = models.PositiveIntegerField(null=True, blank=True)

    # -- active window -----------------------------------------------------
    active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)

    # Free-form extension point (kept for forward compatibility).
    conditions = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            # Codes are unique per store, but only among coded discounts  an
            # automatic discount has an empty code and must not collide with it
            # or with another automatic.
            models.UniqueConstraint(
                fields=("site", "code"),
                condition=~models.Q(code=""),
                name="uniq_coupon_code_per_site",
            )
        ]
        indexes = [
            models.Index(fields=("site", "code")),
            models.Index(fields=("site", "method", "active")),
        ]

    def __str__(self) -> str:
        return self.code or self.title or "Discount"

    @property
    def display_name(self) -> str:
        return self.code or self.title or "Automatic discount"

    def save(self, *args, **kwargs):
        if self.code:
            self.code = self.code.strip().upper()
        # An automatic discount never carries a code.
        if self.method == self.Method.AUTOMATIC:
            self.code = ""
        super().save(*args, **kwargs)

    def is_scheduled(self) -> bool:
        """True when the discount is active but its start is still in the future."""
        return bool(self.active and self.starts_at and timezone.now() < self.starts_at)

    def is_expired(self) -> bool:
        return bool(self.ends_at and timezone.now() > self.ends_at)

    def is_valid_now(self) -> bool:
        """Time-window / active / total-usage validity, independent of any cart."""
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


class OrderManager(SiteScopedManager):
    """Default ``Order`` manager: hides orders still waiting on their online
    payment.

    An online checkout creates its order *before* the shopper pays (the gateway
    needs an order to charge against), but until the payment is confirmed that
    order must not exist for anyone: no dashboard row, no revenue, no customer,
    no coupon use, no notification. Hiding it here, in the default manager,
    covers every ``Order.objects…`` query and every related manager
    (``site.orders``, ``customer.orders``…) at once. Payment code reaches the
    hidden row through ``Order.all_objects`` / ``payment.order``.
    """

    def get_queryset(self):
        return super().get_queryset().exclude(status=Order.Status.AWAITING_PAYMENT)


class Order(TenantScopedModel):
    """A customer order.

    Order numbers are per-store sequential (``number``) so a store owner sees
    #1, #2, #3 while the primary key stays an unguessable UUIDv7. Status
    transitions stamp their own timestamps for accurate analytics and billing.
    """

    class Status(models.TextChoices):
        # Online checkout started, payment not confirmed yet. Invisible
        # everywhere (see OrderManager); becomes a real order only once the
        # gateway confirms the payment, and is discarded if it never does.
        AWAITING_PAYMENT = "awaiting", "Awaiting payment"
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

    # Snapshot of who ordered  kept even if the Customer row is later removed.
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
    # What each store credit took off this order (``credit`` is only the
    # first): ``[{"id", "type", "amount", "booked", "used", "restored"}]``.
    # See commerce.credits; a cancellation returns exactly what was used.
    credit_usage = models.JSONField(default=list, blank=True)
    # Every discount that applied (the coded one + automatics), so their
    # redemptions can be booked when the order is actually placed: at once for
    # COD, only after the payment is confirmed for an online order.
    applied_coupon_ids = models.JSONField(default=list, blank=True)

    tracking_id = models.CharField(max_length=120, blank=True, default="")
    # Whether this order's units have been taken off product stock (and not yet
    # given back). Keeps commerce.stock's deduct/release idempotent.
    stock_deducted = models.BooleanField(default=False)

    # UGC collaboration attribution: set when the order was placed on a
    # collaborator's referral storefront (<subdomain>.myugcstore.com). The
    # commission is computed at attribution and credited to the collaborator the
    # first time the order is paid/confirmed.
    collaboration = models.ForeignKey(
        "commerce.Collaboration",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )
    commission_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    collaboration_credited = models.BooleanField(default=False)

    # Replacement: when the owner approves a customer's replacement request, the
    # order is copied into a new order that re-enters Pending and ships like any
    # other. The copy keeps the original's order number (the owner and shopper
    # both see "#12 · Replacement"); ``replacement_seq`` (1, 2, … per original,
    # 0 for every ordinary order) keeps (site, number, replacement_seq) unique.
    replacement_of = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replacements",
    )
    replacement_seq = models.PositiveSmallIntegerField(default=0)

    objects = OrderManager()

    # B2B: a business order can require owner approval before it is confirmed.
    class Meta:
        ordering = ("-created_at",)
        constraints = [
            # Order numbers are unique per store among ordinary orders
            # (replacement_seq 0); a replacement reuses its original's number
            # with its own sequence.
            models.UniqueConstraint(
                fields=("site", "number", "replacement_seq"),
                name="uniq_order_number_per_site",
            )
        ]
        indexes = [
            models.Index(fields=("site", "status")),
            models.Index(fields=("site", "-created_at")),
            models.Index(fields=("site", "channel")),
        ]

    def __str__(self) -> str:
        if self.replacement_seq:
            return f"Order #{self.display_number} (replacement)"
        return f"Order #{self.number}"

    @property
    def is_replacement(self) -> bool:
        return bool(self.replacement_seq)

    @property
    def display_number(self) -> str:
        """The order ID people see: the order number, and for a replacement the
        same number with an ``RE`` suffix ("12-RE"; a second replacement of
        the same order "12-RE2"), so the original and its replacement read as
        one order yet are never confused."""
        base = str(self.number or "")
        if not self.replacement_seq:
            return base
        return f"{base}-RE" if self.replacement_seq == 1 else f"{base}-RE{self.replacement_seq}"

    @property
    def courier_ref(self) -> str:
        """The order id sent to a courier: the order number, or a replacement's
        ``12-RE`` ID, so it never collides with its original's booking
        (couriers refuse a reused order id)."""
        if self.replacement_seq:
            return self.display_number
        return str(self.number or self.pk)

    @property
    def courier_refs(self) -> tuple:
        """Every order id this order may be booked under with a courier: the
        current one and, for a replacement, the older ``12-R1`` form."""
        if self.replacement_seq:
            return (self.courier_ref, f"{self.number}-R{self.replacement_seq}")
        return (self.courier_ref,)

    @staticmethod
    def parse_courier_ref(ref: str):
        """``(number, replacement_seq)`` for a courier order id: ``12`` →
        (12, 0); a replacement ``12-RE`` / ``12-RE2`` (or the older ``12-R1``)
        → (12, 1) / (12, 2). ``None`` for anything else."""
        import re

        ref = (ref or "").strip()
        if ref.isdigit():
            return int(ref), 0
        m = re.fullmatch(r"(\d+)-(?:RE(\d*)|R(\d+))", ref, flags=re.IGNORECASE)
        if not m:
            return None
        seq = m.group(2) or m.group(3) or "1"
        return int(m.group(1)), int(seq)

    @classmethod
    def owner_statuses(cls) -> list:
        """Statuses an owner may see or set  never the hidden awaiting-payment
        state, which only the payment flow moves an order in or out of."""
        return [s for s in cls.Status if s != cls.Status.AWAITING_PAYMENT]

    @classmethod
    def owner_status_choices(cls) -> list[tuple[str, str]]:
        return [(s.value, s.label) for s in cls.owner_statuses()]

    # Order states that count as a settled sale for the collaborator's earnings.
    _COMMISSION_SETTLED = (Status.CONFIRMED, Status.PAID, Status.SHIPPED, Status.DELIVERED)

    # Order states that count as realised revenue in reporting. Every order a
    # shopper actually placed counts  only a cancelled or archived order voids
    # the sale. A COD order sits at "created"/"confirmed" and never reaches
    # "paid", so revenue must include those or a COD-based store always reads ₹0.
    REVENUE_STATUSES = (
        Status.CREATED, Status.CONFIRMED, Status.PAID, Status.SHIPPED, Status.DELIVERED,
    )

    def save(self, *args, **kwargs):
        self._stamp_status_transition()
        # New rows only. The PK can't gate this: BaseModel's UUIDv7 id is
        # generated in Python at instantiation, so self.pk is truthy even before
        # the first insert. _state.adding is the correct "is this an insert?"
        # signal  without it _assign_number never ran and every order kept the
        # default number 0, colliding on (site, number) from the second order on.
        if self._state.adding and not self.number:
            self._assign_number()
        super().save(*args, **kwargs)
        self._credit_collaboration_if_settled()
        self._accrue_platform_charge()

    def attribute_to(self, collaboration) -> None:
        """Attribute this order to a UGC collaborator and price the commission.

        Called at order creation on a referral storefront. Crediting the
        collaborator's running totals happens later, the first time the order
        settles (see :meth:`_credit_collaboration_if_settled`).
        """
        if collaboration is None:
            return
        self.collaboration = collaboration
        self.commission_amount = self._commission_for(collaboration)

    def _commission_for(self, collaboration) -> Decimal:
        base = self.total or Decimal("0.00")
        if collaboration.commission_type == collaboration.CommissionType.PERCENT:
            amount = base * (collaboration.commission_value or Decimal("0")) / Decimal("100")
        else:
            amount = collaboration.commission_value or Decimal("0.00")
        return amount.quantize(Decimal("0.01"))

    def _credit_collaboration_if_settled(self) -> None:
        """Credit the collaborator once, when the order first settles.

        Idempotent: guarded by ``collaboration_credited`` and applied with an
        atomic ``F()`` update so concurrent status changes can't double-count.
        A no-op for ordinary (non-referral) orders.
        """
        if self.collaboration_credited or not self.collaboration_id:
            return
        if self.status not in self._COMMISSION_SETTLED:
            return
        from .marketing import Collaboration

        Collaboration.all_objects.filter(pk=self.collaboration_id).update(
            total_orders=models.F("total_orders") + 1,
            total_earned=models.F("total_earned") + (self.commission_amount or Decimal("0.00")),
        )
        type(self).all_objects.filter(pk=self.pk).update(collaboration_credited=True)
        self.collaboration_credited = True

    def _accrue_platform_charge(self) -> None:
        """Record OneWooz's commission for this order when it reaches the billed
        milestone. Best-effort: a billing hiccup must never block an order save,
        and the bills page backfills anything missed here."""
        try:
            from commerce.billing_service import accrue_for_order

            accrue_for_order(self)
        except Exception:
            pass

    def _stamp_status_transition(self) -> None:
        stamp = {
            self.Status.CONFIRMED: "confirmed_at",
            self.Status.SHIPPED: "shipped_at",
            self.Status.DELIVERED: "delivered_at",
            self.Status.CANCELLED: "cancelled_at",
        }.get(self.status)
        if stamp and getattr(self, stamp) is None:
            setattr(self, stamp, timezone.now())

    # Orders awaiting payment draw their number from a separate range far above
    # any real order number, so an abandoned payment never leaves a gap in the
    # sequence the owner sees (#4, #6 … where is #5?). A real number is given
    # only when the order is placed (see assign_placed_number).
    HIDDEN_NUMBER_BASE = 1_000_000_000

    def _assign_number(self, *, hidden: bool | None = None) -> None:
        """Assign the next per-store order number under a row lock."""
        if hidden is None:
            hidden = self.status == self.Status.AWAITING_PAYMENT
        floor = self.HIDDEN_NUMBER_BASE if hidden else 0
        in_range = (
            {"number__gte": self.HIDDEN_NUMBER_BASE}
            if hidden
            else {"number__lt": self.HIDDEN_NUMBER_BASE}
        )
        with transaction.atomic():
            last = (
                Order.all_objects.filter(site_id=self.site_id, **in_range)
                .select_for_update()
                .order_by("-number")
                .values_list("number", flat=True)
                .first()
            )
            self.number = (last or floor) + 1

    def assign_placed_number(self) -> None:
        """Swap a placed order's temporary number for the next real one."""
        if self.number and self.number < self.HIDDEN_NUMBER_BASE:
            return
        with transaction.atomic():
            self._assign_number(hidden=False)
            type(self).all_objects.filter(pk=self.pk).update(number=self.number)


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
    #: The unit price before a quantity discount (null when none applied).
    regular_unit_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    #: The quantity-discount rule frozen at purchase (commerce.quantity_pricing.
    #: snapshot): tiers, type, the tier that applied. Lets an owner's later
    #: quantity edit re-price from the deal the shopper actually got.
    quantity_discount = models.JSONField(default=dict, blank=True)

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
    """A customer-initiated post-order request: cancel, return, replace, a
    warranty claim, or a support query about the order. The owner approves,
    rejects or marks it processed (a support query is answered and resolved;
    the reply is kept in ``meta``)."""

    class RequestType(models.TextChoices):
        CANCEL = "cancel", "Cancel"
        RETURN = "return", "Return"
        REPLACE = "replace", "Replace"
        WARRANTY = "warranty", "Warranty"
        SUPPORT = "support", "Support"

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
