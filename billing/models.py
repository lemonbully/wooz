"""Platform subscription catalogue, discount codes, and purchase records.

This is OneWooz billing *itself*  the plans an owner buys to run their store 
not a tenant's storefront checkout (that lives in ``commerce``). These records
are global, managed from the Django admin, and rendered on the marketing pricing
page. Payment is taken through OneWooz's own Cashfree account (platform
credentials in settings), never a tenant's gateway.

Shapes of plan, chosen by :attr:`SubscriptionPlan.plan_type`:

* **free**  ₹0, the call-to-action sends the visitor to sign-up;
* **paid (recurring)**  one :attr:`~SubscriptionPlan.duration` (monthly /
  quarterly / half-yearly / yearly) and one per-month price, sold through the
  Cashfree checkout. Each duration you offer is its **own** plan object, so a
  "Growth Monthly" and a "Growth Yearly" are two separate rows; the pricing-page
  toggle then filters the cards by duration;
* **one-time**  bought once, runs a full year, a single up-front price;
* **custom**  no fixed price, the call-to-action sends the visitor to talk to us.

Money is only ever authored on paid/one-time plans. A recurring plan's price is a
**per-month** figure; the amount charged is that figure times the months in the
plan's duration (see :mod:`billing.cycles`), less any discount, plus GST.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.text import slugify

from core.models import BaseModel

from . import cycles

__all__ = [
    "SubscriptionPlan",
    "PlanFeature",
    "CommissionTier",
    "CommerceFeeBand",
    "ActivityPenalty",
    "Discount",
    "SubscriptionOrder",
    "UserAgreement",
    "AgreementAcceptance",
    "SubscriptionInvite",
    "OnboardingProfile",
    "AccountEntitlement",
    "PlatformPaymentAccount",
    "CommerceBillDetails",
    "WebsiteTemplate",
    "TemplatePurchase",
]

#: How long a subscription invite link stays usable, in minutes.
INVITE_TTL_MINUTES = 20


def _unique_slug(model, source: str, pk, fallback: str) -> str:
    base = slugify(source) or fallback
    candidate, suffix = base, 2
    existing = model.objects.all()
    if pk:
        existing = existing.exclude(pk=pk)
    while existing.filter(slug=candidate).exists():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


class SubscriptionPlan(BaseModel):
    """A plan shown on the pricing page and (if paid) sold through checkout."""

    class Kind(models.TextChoices):
        FREE = "free", "Free"
        PAID = "paid", "Paid (recurring)"
        ONE_TIME = "one_time", "One-time (one year, paid once)"
        CUSTOM = "custom", "Custom (talk to us)"

    class Duration(models.TextChoices):
        MONTHLY = cycles.MONTHLY, "Monthly"
        QUARTERLY = cycles.QUARTERLY, "Quarterly"
        HALF_YEARLY = cycles.HALF_YEARLY, "Half-yearly"
        YEARLY = cycles.YEARLY, "Yearly"

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    plan_type = models.CharField(
        max_length=10, choices=Kind.choices, default=Kind.PAID, db_index=True
    )
    # The billing period this plan covers. Each duration is authored as its own
    # plan object (a monthly plan and a yearly plan are two separate rows); the
    # pricing-page toggle filters the cards by this. Only meaningful for recurring
    # paid plans  free/custom ignore it, and one-time plans are always a year.
    duration = models.CharField(
        max_length=12,
        choices=Duration.choices,
        default=Duration.MONTHLY,
        db_index=True,
        help_text="Billing period for a recurring paid plan. Create a separate plan for each duration you sell.",
    )

    tagline = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="One line under the plan name on the pricing card.",
    )
    description = models.TextField(
        blank=True, default="", help_text="Optional longer blurb (shown on checkout)."
    )

    # The per-month price for this plan's duration. Meaningful only for recurring
    # paid plans; leave at zero for free/custom/one-time. The amount charged is
    # this figure × the months in ``duration`` (e.g. a yearly plan at ₹500/mo is
    # charged ₹6,000 for the year, before discount and GST).
    currency = models.CharField(max_length=3, default="INR")
    price_monthly = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00"),
        help_text="Per-month price for this plan's duration (charged × the months in the period).",
    )
    # A one-time plan is bought once and runs for a full year. Its whole price
    # is a single figure charged up front (not a per-month rate), so it lives in
    # its own field. Meaningful only when plan_type is "one_time".
    one_time_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00"),
        help_text="Total charged once, up front, for a full year (one-time plans only).",
    )

    # What the subscription grants its owner. Apply to every plan type.
    number_of_stores = models.PositiveIntegerField(
        default=1,
        help_text="How many stores the owner may create on this subscription.",
    )
    number_of_collaborations = models.PositiveIntegerField(
        default=0,
        help_text="How many collaborating users the owner may invite (0 = none).",
    )
    concurrent_login_sessions = models.PositiveIntegerField(
        default=1,
        help_text=(
            "How many devices the owner may be signed in on at the same time for "
            "one account. When a further device signs in, the least-recently "
            "active session beyond this limit is signed out, so only this many "
            "devices can manage the account at once. 0 = unlimited."
        ),
    )

    # The activity requirement + reactivation penalty is authored as one or more
    # :class:`ActivityPenalty` rows (see below)  different day windows, each
    # with its own order threshold and penalty  not a single fixed rule.

    # Presentation.
    badge = models.CharField(
        max_length=40, blank=True, default="",
        help_text='Small tag on the card, e.g. "Most popular" or "Best value".',
    )
    is_featured = models.BooleanField(
        default=False, help_text="Visually highlight this plan on the pricing page."
    )
    cta_label = models.CharField(
        max_length=40, blank=True, default="",
        help_text="Override the call-to-action label (defaults per plan type).",
    )
    custom_cta_url = models.CharField(
        max_length=300, blank=True, default="",
        help_text="Where a free/custom plan's button links (defaults to sign-up / contact).",
    )

    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)
    # Ticked = the plan is not offered on the owner dashboard's "Upgrade plan"
    # page (ud.views.upgrade). It stays on the public pricing page, the renewal
    # wall and checkout; only that in-dashboard list skips it.
    hide_on_dashboard_upgrade = models.BooleanField(
        "Hide on dashboard upgrade page",
        default=False,
        help_text=(
            "Tick to keep this plan off the “Upgrade plan” page inside the user "
            "dashboard. It still shows on the public pricing page."
        ),
    )

    # For a FREE plan: the dashboard feature keys it includes (see
    # commerce.features / commerce.context.NAV_GROUPS). Everything not listed is
    # locked behind an "Upgrade" wall. Empty = no gating. Ignored for paid plans.
    included_features = models.JSONField(default=list, blank=True)

    # For a FREE plan: which tabs of the store's *content editor* (Edit content)
    # the owner may edit  the section keys in commerce.views.editor.SECTIONS
    # (brand, home, blogs, about_page, footer, contact, seo, policies,
    # certifications, core_pages, scripts). Everything not listed shows locked as
    # an "Upgrade" inside the editor. Empty = no gating (every tab editable).
    # Ignored for paid plans.
    included_editor_tabs = models.JSONField(default=list, blank=True)

    # Dedicated email-marketing limits, kept as their own block (separate from
    # the generic feature/editor gating) so a store's outbound-mail allowance is
    # authored and frozen independently  see billing.entitlements. Shape:
    # {max_recipients_per_campaign, monthly_email_limit, emails_per_minute,
    #  allow_ai_builder (bool), scheduling ("simple"|"datetime")}. Availability of
    # the feature itself is the ordinary gate (included_features on a free plan;
    # always on for paid); this block only sizes the limits once available.
    email_marketing = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("order", "price_monthly", "name")
        verbose_name = "subscription plan"
        verbose_name_plural = "subscription plans"

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(SubscriptionPlan, self.name, self.pk, "plan")
        super().save(*args, **kwargs)

    # -- pricing helpers ---------------------------------------------------
    @property
    def is_paid(self) -> bool:
        """A recurring paid plan (per-month cadence prices)."""
        return self.plan_type == self.Kind.PAID

    @property
    def is_one_time(self) -> bool:
        """Bought once, runs for a full year; single up-front price."""
        return self.plan_type == self.Kind.ONE_TIME

    @property
    def is_purchasable(self) -> bool:
        """Anything that goes through the Cashfree checkout (paid or one-time)."""
        return self.plan_type in (self.Kind.PAID, self.Kind.ONE_TIME)

    @property
    def has_activity_requirement(self) -> bool:
        """True when this plan carries any activity/penalty rule."""
        return self.activity_penalties.exists()

    @property
    def billing_cycle(self) -> str:
        """The cadence this plan charges on: its ``duration``, or a year for a
        one-time plan (which is always a single yearly charge)."""
        if self.is_one_time:
            return cycles.YEARLY
        return cycles.normalise_cycle(self.duration)

    @property
    def months(self) -> int:
        """Number of months billed up front for this plan."""
        return cycles.MONTHS[self.billing_cycle]

    def per_month_price(self) -> Decimal:
        """The per-month figure for this plan.

        A one-time plan has no per-month rate; its yearly price spread over 12
        months is returned only so a "per month" display stays sensible.
        """
        if self.is_one_time:
            return cycles.money(self.one_time_price / Decimal(12))
        return cycles.money(self.price_monthly)

    def cycle_total(self) -> Decimal:
        """What buying this plan costs before discount and GST.

        A one-time plan is always its single ``one_time_price``; a recurring plan
        is its per-month price × the months in its duration.
        """
        if self.is_one_time:
            return cycles.money(self.one_time_price)
        return cycles.money(self.price_monthly * self.months)

    def commission_for(self, order_count: int) -> Decimal | None:
        """The per-order commission fee for a month of ``order_count`` orders.

        Reads the plan's :class:`CommissionTier` ranges (authored in the admin)
        and returns the fee whose range contains the count, or ``None`` when no
        tier matches (no commission configured for that volume).
        """
        for tier in self.commission_tiers.all():
            if order_count < tier.min_orders:
                continue
            if tier.max_orders is not None and order_count > tier.max_orders:
                continue
            return tier.per_order_fee
        return None


class PlanFeature(BaseModel):
    """One bullet point listed on a plan's pricing card.

    Kept as rows (not a text blob) so the operator adds, reorders and toggles
    individual features from the admin, and so a card can show an included /
    not-included distinction.
    """

    plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.CASCADE, related_name="features"
    )
    text = models.CharField(max_length=200)
    value = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text='Optional value shown to the right of the text, e.g. "Unlimited" or "5". Leave blank to show the text on its own.',
    )
    included = models.BooleanField(
        default=True, help_text="Untick to show the feature struck through / greyed."
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("order", "created_at")
        verbose_name = "plan feature"
        verbose_name_plural = "plan features"

    def __str__(self) -> str:
        return f"{self.plan.name}: {self.text}"


class CommissionTier(BaseModel):
    """One order-volume band and the per-order commission it charges.

    Commission is defined per plan as a set of ascending bands, authored inline
    in the admin  e.g. *0–300 orders → ₹45 each*, *301–1000 → ₹70 each*. Each
    row is one band: a lower bound, an optional upper bound (blank = "and above"),
    and the rupee fee charged per order while the owner's monthly order count
    falls inside it. Bands are read in ``order`` (see
    :meth:`SubscriptionPlan.commission_for`).
    """

    plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.CASCADE, related_name="commission_tiers"
    )
    min_orders = models.PositiveIntegerField(
        default=0, help_text="Lower bound of this band (inclusive)."
    )
    max_orders = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Upper bound (inclusive). Leave blank for “this many and above”.",
    )
    per_order_fee = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Commission charged per order while volume is in this band (₹).",
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("order", "min_orders")
        verbose_name = "commission tier"
        verbose_name_plural = "commission tiers"

    def __str__(self) -> str:
        upper = self.max_orders if self.max_orders is not None else "∞"
        return f"{self.plan.name}: {self.min_orders}–{upper} → ₹{self.per_order_fee}/order"


class CommerceFeeBand(BaseModel):
    """One selling-price band and the commerce fee charged per unit sold in it.

    This is how OneWooz prices its commerce fee: by the **selling price** of
    what was sold, not by a percentage of the order. Each delivered order line
    is matched by its unit selling price (the product variation combination's
    own price, else the product's price) and charged this band's fee per unit.
    Coupons never move the band: the price the item was listed at decides it.

    e.g. ₹0–499 → ₹15 per unit, ₹500–999 → ₹25, ₹1000 and above → ₹40.
    Leave a band's upper bound blank for "this price and above". When a plan
    has bands they take precedence over its order-count Commission tiers.
    """

    plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.CASCADE, related_name="commerce_fee_bands"
    )
    min_price = models.DecimalField(
        "Selling price from (₹)", max_digits=12, decimal_places=2, default=Decimal("0.00"),
        help_text="Lower bound of the selling price (inclusive).",
    )
    max_price = models.DecimalField(
        "Selling price up to (₹)", max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Upper bound (inclusive). Leave blank for “this price and above”.",
    )
    fee = models.DecimalField(
        "Commerce fee per unit (₹)", max_digits=10, decimal_places=2,
        help_text="Charged for every unit sold in this price band, on delivery.",
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("order", "min_price")
        verbose_name = "commerce fee band (by selling price)"
        verbose_name_plural = "commerce fee bands (by selling price)"

    def __str__(self) -> str:
        upper = f"₹{self.max_price}" if self.max_price is not None else "and above"
        return f"{self.plan.name}: ₹{self.min_price}–{upper} → ₹{self.fee}/unit"


class ActivityPenalty(BaseModel):
    """One activity window and the penalty for not hitting its order threshold.

    A plan can carry several of these  a different order threshold for each day
    window (30 / 50 / 70 / 100 days …), each with its own reactivation penalty.
    If the store does not reach ``threshold_orders`` orders within
    ``window_days`` days it is suspended, and lifting the suspension costs
    ``reactivation_penalty``  even on a one-time plan that was paid up front.
    Chiefly used by one-time plans, but stored per plan so any plan may set one.
    """

    plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.CASCADE, related_name="activity_penalties"
    )
    window_days = models.PositiveIntegerField(
        help_text="Length of the window in days (e.g. 30, 50, 70, 100).",
    )
    threshold_orders = models.PositiveIntegerField(
        help_text="Orders the store must reach within this window.",
    )
    reactivation_penalty = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00"),
        help_text="Amount charged to lift a suspension and reactivate the store (₹).",
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("order", "window_days")
        verbose_name = "activity penalty"
        verbose_name_plural = "activity penalties"

    def __str__(self) -> str:
        return (
            f"{self.plan.name}: {self.threshold_orders} orders / "
            f"{self.window_days} days → ₹{self.reactivation_penalty}"
        )


class Discount(BaseModel):
    """A code a buyer can apply at checkout to reduce the pre-GST amount.

    Validity is checked centrally in :meth:`validate_for` so the pricing quote,
    the checkout submit, and the payment confirmation all agree on whether a code
    applies  the amount is never trusted from the browser.
    """

    class Kind(models.TextChoices):
        PERCENT = "percent", "Percentage off"
        FIXED = "fixed", "Fixed amount off"

    code = models.CharField(max_length=40, unique=True, db_index=True)
    description = models.CharField(max_length=200, blank=True, default="")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.PERCENT)
    value = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Percent (0–100) for a percentage discount, or a rupee amount for a fixed one.",
    )

    # Optional guards.
    min_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00"),
        help_text="Minimum pre-GST amount the code applies to. 0 = no minimum.",
    )
    max_discount = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00"),
        help_text="Cap on a percentage discount's rupee value. 0 = uncapped.",
    )
    plans = models.ManyToManyField(
        SubscriptionPlan, blank=True, related_name="discounts",
        help_text="Restrict to these plans. Leave empty to allow on every plan.",
    )

    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    max_redemptions = models.PositiveIntegerField(
        default=0, help_text="Total times the code may be redeemed. 0 = unlimited."
    )
    times_redeemed = models.PositiveIntegerField(default=0, editable=False)

    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("code",)
        verbose_name = "discount code"
        verbose_name_plural = "discount codes"

    def __str__(self) -> str:
        return self.code

    def save(self, *args, **kwargs):
        # Codes are matched case-insensitively; store them upper so the unique
        # constraint and the lookup agree.
        self.code = (self.code or "").strip().upper()
        super().save(*args, **kwargs)

    # -- validity + amount -------------------------------------------------
    def validate_for(self, plan: SubscriptionPlan, amount: Decimal) -> str:
        """Return "" if the code applies, else a short human reason it does not.

        The reason is safe to show the buyer ("This code has expired.").
        """
        now = timezone.now()
        if not self.is_active:
            return "This code is not valid."
        if self.starts_at and now < self.starts_at:
            return "This code is not active yet."
        if self.ends_at and now > self.ends_at:
            return "This code has expired."
        if self.max_redemptions and self.times_redeemed >= self.max_redemptions:
            return "This code has reached its limit."
        if self.min_amount and amount < self.min_amount:
            return f"This code needs a minimum of ₹{self.min_amount:.0f}."
        # M2M is only meaningful once the row is saved; a restricted set that
        # excludes this plan means the code does not apply here.
        if self.pk and self.plans.exists() and not self.plans.filter(pk=plan.pk).exists():
            return "This code does not apply to this plan."
        return ""

    def amount_off(self, amount: Decimal) -> Decimal:
        """The rupee discount for a pre-GST ``amount`` (never more than it)."""
        amount = cycles.money(amount)
        if self.kind == self.Kind.PERCENT:
            off = amount * (self.value / Decimal(100))
            if self.max_discount and off > self.max_discount:
                off = self.max_discount
        else:
            off = self.value
        return cycles.money(min(max(off, Decimal("0.00")), amount))


class SubscriptionOrder(BaseModel):
    """A purchase of a paid plan: the amounts quoted, and the payment's state.

    On successful payment it doubles as the active subscription record
    (``current_period_start`` / ``current_period_end``). The whole money
    breakdown is stored, not recomputed, so an invoice reflects exactly what the
    buyer agreed to even if the plan's price later changes.
    """

    class Status(models.TextChoices):
        CREATED = "created", "Created"
        PENDING = "pending", "Pending payment"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    class Cycle(models.TextChoices):
        MONTHLY = cycles.MONTHLY, "Monthly"
        QUARTERLY = cycles.QUARTERLY, "Quarterly"
        HALF_YEARLY = cycles.HALF_YEARLY, "Half-yearly"
        YEARLY = cycles.YEARLY, "Yearly"

    number = models.CharField(max_length=20, unique=True, editable=False, db_index=True)

    plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.SET_NULL, null=True, related_name="orders"
    )
    plan_name = models.CharField(max_length=120)  # denormalised, survives plan edits
    cycle = models.CharField(max_length=12, choices=Cycle.choices)

    # The user agreement the buyer accepted (via the OTP-verified acceptance
    # step) before paying. Nullable so historic rows and edited/removed
    # agreements do not break the ledger.
    agreement = models.ForeignKey(
        "billing.UserAgreement",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )

    # Who is buying. Anonymous lead capture  email + phone only, no account.
    customer_name = models.CharField(max_length=120, blank=True, default="")
    customer_email = models.EmailField()
    customer_phone = models.CharField(max_length=20)

    # Frozen money breakdown.
    currency = models.CharField(max_length=3, default="INR")
    per_month_price = models.DecimalField(max_digits=10, decimal_places=2)
    months = models.PositiveSmallIntegerField()
    base_amount = models.DecimalField(max_digits=10, decimal_places=2)
    discount = models.ForeignKey(
        Discount, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders"
    )
    discount_code = models.CharField(max_length=40, blank=True, default="")
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    taxable_amount = models.DecimalField(max_digits=10, decimal_places=2)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("18.00"))
    gst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)

    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.CREATED, db_index=True
    )

    # Cashfree linkage. cf_order_id is our own id echoed to the gateway and used
    # to find this row from a webhook (which arrives without a session).
    cf_order_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    cf_payment_id = models.CharField(max_length=64, blank=True, default="")
    gateway_status = models.CharField(max_length=40, blank=True, default="")

    # Active-subscription window, filled when payment confirms.
    paid_at = models.DateTimeField(null=True, blank=True)
    current_period_start = models.DateField(null=True, blank=True)
    current_period_end = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "subscription order"
        verbose_name_plural = "subscription orders"
        indexes = [
            models.Index(fields=("status", "-created_at")),
            models.Index(fields=("customer_email", "-created_at")),
        ]

    def __str__(self) -> str:
        return f"{self.number} · {self.plan_name} ({self.get_cycle_display()})"

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = self._new_number()
        super().save(*args, **kwargs)

    @staticmethod
    def _new_number() -> str:
        # Human-quotable, non-sequential (the PK is already an unguessable
        # UUIDv7; this is just a friendly reference for support and invoices).
        for _ in range(10):
            candidate = "OWZ" + get_random_string(9, "0123456789")
            if not SubscriptionOrder.objects.filter(number=candidate).exists():
                return candidate
        return "OWZ" + get_random_string(12, "0123456789")

    @property
    def is_paid(self) -> bool:
        return self.status == self.Status.PAID


class UserAgreement(BaseModel):
    """The terms a buyer must review and accept before paying for a plan.

    Admin-managed and versioned: authoring a new active agreement supersedes the
    previous one (only the most recent active row is served to the checkout).
    The content is trusted HTML written by staff in the admin  it is rendered
    into the acceptance modal verbatim, so it is never populated from user input.
    """

    title = models.CharField(max_length=160, default="User Agreement")
    version = models.CharField(
        max_length=20, blank=True, default="",
        help_text='Optional label shown for your own reference, e.g. "1.0".',
    )
    content = models.TextField(
        help_text="The agreement body (HTML allowed). Shown in the acceptance modal.",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "user agreement"
        verbose_name_plural = "user agreements"

    def __str__(self) -> str:
        return f"{self.title}{f' v{self.version}' if self.version else ''}"

    @classmethod
    def active(cls) -> "UserAgreement | None":
        """The agreement currently in force (most recently authored active one)."""
        return (
            cls.objects.filter(is_active=True)
            .defer("content")
            .order_by("-created_at")
            .first()
        )

    @property
    def version_label(self) -> str:
        """How this agreement is named in records: "v2", else its authored date."""
        if self.version:
            v = self.version.strip()
            return v if v.lower().startswith("v") else f"v{v}"
        return f"{self.created_at:%d %b %Y}" if self.created_at else ""


class AgreementAcceptance(BaseModel):
    """A compliance record: this email accepted this agreement version, when, from where.

    Append-only. Each acceptance is its own row, so when a new agreement version
    is published and an existing owner re-accepts it, their earlier acceptance
    (version 1) stays on record beside the new one (version 2).

    Written once the person has passed an email OTP step (checkout, or the
    "we've updated our agreement" re-acceptance popup in the dashboard), so the
    record is evidence that whoever accepted controlled the address. Checkout
    purchases are anonymous, so the email is the identity; ``user`` is filled
    when the acceptance happened inside a signed-in dashboard.

    ``agreement_title`` / ``agreement_version`` are frozen copies taken when the
    row is written, so the record still names the exact version even if the
    agreement row is later edited or deleted.
    """

    class Source(models.TextChoices):
        CHECKOUT = "checkout", "Plan checkout"
        RENEWAL = "renewal", "Plan renewal / upgrade"
        UPDATE = "update", "Updated agreement (dashboard popup)"

    agreement = models.ForeignKey(
        UserAgreement, on_delete=models.SET_NULL, null=True, related_name="acceptances"
    )
    agreement_title = models.CharField(max_length=160, blank=True, default="")
    agreement_version = models.CharField(max_length=40, blank=True, default="")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agreement_acceptances",
    )
    email = models.EmailField(db_index=True)
    source = models.CharField(
        max_length=12, choices=Source.choices, default=Source.CHECKOUT, db_index=True
    )
    otp_verified = models.BooleanField(
        default=True,
        help_text="Whether an emailed one-time code was confirmed for this acceptance.",
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=400, blank=True, default="")

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "agreement acceptance"
        verbose_name_plural = "agreement acceptances"
        indexes = [
            models.Index(fields=("email", "-created_at")),
            models.Index(fields=("agreement", "email")),
        ]

    def __str__(self) -> str:
        label = self.agreement_version or self.agreement_title or "agreement"
        when = f"{self.created_at:%Y-%m-%d %H:%M}" if self.created_at else ""
        return f"{self.email} accepted {label} {when}".strip()

    def save(self, *args, **kwargs):
        # Freeze which version was accepted, so the record survives later edits.
        if self.agreement_id and not (self.agreement_title and self.agreement_version):
            agreement = self.agreement
            if agreement is not None:
                self.agreement_title = self.agreement_title or (agreement.title or "")[:160]
                self.agreement_version = self.agreement_version or agreement.version_label[:40]
        super().save(*args, **kwargs)

    @classmethod
    def has_accepted(cls, email: str, agreement) -> bool:
        """True when ``email`` has an acceptance on record for ``agreement``."""
        if not email or agreement is None:
            return False
        return cls.objects.filter(agreement=agreement, email__iexact=email.strip()).exists()


class SubscriptionInvite(BaseModel):
    """An admin-issued, custom-priced purchase link for one person.

    OneWooz staff pick a plan and a price for a specific email in the Django
    admin; this mints a temporary link (valid :data:`INVITE_TTL_MINUTES`
    minutes) that opens the *same* subscription checkout on the marketing site,
    but charges the fixed :attr:`custom_amount` (before GST) instead of the
    plan's list price. A zero amount grants the plan free  the checkout runs in
    full but never touches the payment gateway.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"

    email = models.EmailField(
        help_text="The person this link is for. The checkout emails an OTP here."
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.PROTECT,
        related_name="invites",
        help_text="The plan to grant. Its own duration (monthly / yearly / one-time …) is used.",
    )
    custom_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=(
            "Per-MONTH price, BEFORE 18% GST. It is multiplied by the plan's cycle "
            "(quarterly = ×3, yearly = ×12, one-time = ×12), then GST is added. "
            "e.g. 100 on a quarterly plan bills ₹300 + GST. Enter 0 to grant it "
            "free (no payment gateway)."
        ),
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    order = models.ForeignKey(
        SubscriptionOrder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="The order created when the invite was completed.",
    )
    created_by = models.CharField(
        max_length=254, blank=True, default="", help_text="Admin who issued the invite."
    )
    expires_at = models.DateTimeField(db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "subscription invite"
        verbose_name_plural = "subscription invites"

    def __str__(self) -> str:
        return f"{self.email} · {self.plan_id} · ₹{self.custom_amount}"

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(minutes=INVITE_TTL_MINUTES)
        super().save(*args, **kwargs)

    @property
    def is_free(self) -> bool:
        return (self.custom_amount or Decimal("0.00")) <= Decimal("0.00")

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_usable(self) -> bool:
        return self.status == self.Status.PENDING and not self.is_expired


class OnboardingProfile(BaseModel):
    """The billing/tax identity a buyer supplies right after paying.

    Purchases are anonymous until this point  a :class:`SubscriptionOrder`
    carries only an email and phone, and the subscription is tied to the owner
    by that email (see ``commerce.features.allowed_features_for_owner``). The
    onboarding step, reached once an order settles (paid or free), is where the
    buyer both **creates their account** (the linked :class:`User`) and records
    the business details an invoice needs: their tax identity, registered name,
    and address.

    One profile per account. It also references the order that led to it, so the
    first invoice can be raised against exactly what was captured here.
    """

    class Entity(models.TextChoices):
        COMPANY = "company", "Company"
        INDIVIDUAL = "individual", "Individual"
        GST = "gst", "GST registered"

    # The account this profile belongs to. Stored as a raw FK to the auth user
    # (not TenantScoped)  this is account-level, not per-store, data.
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="onboarding_profile",
    )
    # The order whose payment (or free grant) opened this onboarding. Nullable so
    # deleting a historic order never destroys the captured profile.
    order = models.ForeignKey(
        SubscriptionOrder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="onboarding_profiles",
    )

    entity_type = models.CharField(
        max_length=12, choices=Entity.choices, default=Entity.INDIVIDUAL
    )
    # Registered / trading name. Always captured; for an individual it is the
    # name they trade under.
    company_name = models.CharField(max_length=200, blank=True, default="")

    # Tax identity. An individual supplies a PAN; a company / GST-registered
    # buyer supplies a GSTIN. Both are stored upper-cased and validated at the
    # form (see ``billing.onboarding``); only the relevant one is filled.
    pan = models.CharField(max_length=10, blank=True, default="", db_index=True)
    gstin = models.CharField(max_length=15, blank=True, default="", db_index=True)

    # Contact + address for invoicing.
    phone = models.CharField(max_length=20, blank=True, default="")
    state = models.CharField(max_length=60, blank=True, default="")
    city = models.CharField(max_length=80, blank=True, default="")
    address = models.TextField(blank=True, default="")

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "onboarding profile"
        verbose_name_plural = "onboarding profiles"

    def __str__(self) -> str:
        return f"{self.company_name or self.user_id} ({self.get_entity_type_display()})"

    @property
    def tax_id(self) -> str:
        """The identifier that applies to this entity  GSTIN or PAN."""
        return self.gstin if self.entity_type != self.Entity.INDIVIDUAL else self.pan


class AccountEntitlement(BaseModel):
    """A frozen snapshot of what a subscription grants its owner.

    The entitlement gates the dashboard (limits, feature areas, editor tabs) read
    a **copy** of the plan's terms taken at the moment a subscription is settled 
    not the live :class:`SubscriptionPlan`. So editing a plan in the admin (its
    store/collaborator limits, included features, commission bands, penalties)
    never silently changes an owner who is already on it. The owner keeps exactly
    what they were granted until they next settle a subscription  a purchase, a
    free grant, or a **renewal**, each of which *re-snapshots* from whatever the
    plan says at that moment (see :mod:`billing.entitlements`).

    This mirrors what :class:`SubscriptionOrder` already does for the *money*
    breakdown (frozen so an invoice survives a price change); here the same
    principle is extended to the *entitlements*.

    Keyed by **email**, not a user FK, on purpose: an order settles before the
    buyer creates their account (see :class:`OnboardingProfile`), and every
    entitlement resolver already looks an owner up by email. One row per email 
    each settlement upserts it  so it always reflects the most recent grant, and
    :attr:`schema_version` marks the shape so it can evolve without a data
    migration reading stale keys.

    The ``commission`` and ``penalties`` blobs freeze the plan's
    :class:`CommissionTier` and :class:`ActivityPenalty` rows as plain dicts
    (money as strings). They are captured here so the terms are pinned per owner;
    what consumes them (per-store billing config, penalty enforcement) still reads
    live today and is wired onto this snapshot separately.
    """

    #: Bump when the frozen shape changes so readers can adapt old rows.
    SCHEMA_VERSION = 3

    email = models.EmailField(
        unique=True,
        db_index=True,
        help_text="The owner this grant belongs to (matched case-insensitively).",
    )

    # Provenance  nullable so deleting a historic plan/order never destroys the
    # frozen grant it produced.
    source_plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    source_order = models.ForeignKey(
        SubscriptionOrder, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    # Frozen identity of the plan at grant time.
    plan_type = models.CharField(
        max_length=10, choices=SubscriptionPlan.Kind.choices,
        default=SubscriptionPlan.Kind.FREE,
    )
    plan_name = models.CharField(max_length=120, blank=True, default="")

    # Frozen quantitative grants.
    number_of_stores = models.PositiveIntegerField(default=1)
    number_of_collaborations = models.PositiveIntegerField(default=0)
    # Frozen device/session cap (0 = unlimited), enforced by
    # accounts.session_limit against the owner's live login sessions.
    concurrent_login_sessions = models.PositiveIntegerField(default=1)

    # Frozen dashboard-area and editor-tab keys (empty = no gating for that
    # dimension). Only meaningful for a free plan, exactly as on the plan.
    included_features = models.JSONField(default=list, blank=True)
    included_editor_tabs = models.JSONField(default=list, blank=True)

    # Frozen email-marketing limits (its own block, mirrors the plan field).
    email_marketing = models.JSONField(default=dict, blank=True)

    # Frozen commission bands: [{min_orders, max_orders, per_order_fee}, …].
    commission = models.JSONField(default=list, blank=True)
    # Frozen commerce fee bands by selling price:
    # [{min_price, max_price, fee}, …] (money as strings). Empty on a grant taken
    # before bands existed; billing then reads the plan's live bands.
    commerce_fee_bands = models.JSONField(default=list, blank=True)
    # Frozen activity/penalty rules:
    # [{window_days, threshold_orders, reactivation_penalty}, …].
    penalties = models.JSONField(default=list, blank=True)

    schema_version = models.PositiveSmallIntegerField(default=SCHEMA_VERSION)
    granted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-granted_at",)
        verbose_name = "account entitlement"
        verbose_name_plural = "account entitlements"

    def __str__(self) -> str:
        return f"{self.email} · {self.plan_name or self.plan_type}"

    @property
    def is_free(self) -> bool:
        return self.plan_type == SubscriptionPlan.Kind.FREE

    @property
    def gates_features(self) -> bool:
        """True when this grant limits the dashboard areas the owner may open."""
        return self.is_free and bool(self.included_features)


class PlatformPaymentAccount(BaseModel):
    """Where a store owner sends the money they owe OneWooz for their bills.

    Operators create these in the Django admin, and the store's bills page shows
    the active ones so the owner can pay a commission invoice. Two kinds of
    object live in this one model:

    * **Bank account**  a bank name, IFSC code and account number for a manual
      NEFT/IMPS/RTGS transfer.
    * **QR code / UPI**  a UPI ID plus an uploaded QR image the owner scans.

    This is OneWooz's *own* collection detail (global), never a tenant's  so it
    is a plain :class:`~core.models.BaseModel`, not tenant-scoped.
    """

    class Kind(models.TextChoices):
        BANK = "bank", "Bank account"
        QR = "qr", "QR code / UPI"

    kind = models.CharField(
        max_length=8, choices=Kind.choices, default=Kind.BANK, db_index=True
    )
    #: Optional operator-facing name, e.g. "HDFC current account".
    label = models.CharField(max_length=120, blank=True, default="")
    is_active = models.BooleanField(
        default=True,
        help_text="Only active accounts are shown to store owners on the bills page.",
    )

    # -- Bank-account fields (Kind.BANK) -----------------------------------
    bank_name = models.CharField(max_length=120, blank=True, default="")
    account_holder = models.CharField(max_length=120, blank=True, default="")
    ifsc_code = models.CharField(max_length=20, blank=True, default="")
    account_number = models.CharField(max_length=40, blank=True, default="")

    # -- QR / UPI fields (Kind.QR) -----------------------------------------
    upi_id = models.CharField(max_length=120, blank=True, default="")
    qr_image = models.ImageField(upload_to="platform_payment_qr/", blank=True, null=True)

    #: A short instruction shown under the details (e.g. "Add your invoice number
    #: in the payment note").
    note = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ("kind", "-created_at")
        verbose_name = "platform payment account"
        verbose_name_plural = "platform payment accounts"

    def __str__(self) -> str:
        name = self.label or (self.bank_name if self.kind == self.Kind.BANK else self.upi_id)
        return f"{self.get_kind_display()} · {name or self.pk}"

    @property
    def is_bank(self) -> bool:
        return self.kind == self.Kind.BANK

    @property
    def is_qr(self) -> bool:
        return self.kind == self.Kind.QR

    @property
    def display_name(self) -> str:
        if self.label:
            return self.label
        if self.is_bank:
            return self.bank_name or "Bank account"
        return self.upi_id or "UPI / QR"


class InvoiceDetails(BaseModel):
    """The seller details printed on owners' subscription invoices.

    Managed in the Django admin as "Invoice details". The owner's bill (Profile
    → Subscription → View bill, see ``billing.bill``) uses the most recently
    added *active* object: company name, mobile, email, address, GSTIN, HSN
    code, state, the Terms & Conditions points and the authorised signatory's
    signature. Any blank field falls back to ``settings.PLATFORM_BILLING_ENTITY``
    (and the bundled signature image).

    ``state`` is detected from the GSTIN on save (its first two digits are the
    GST state code), and it is what decides CGST + SGST (buyer in the same
    state) versus IGST (any other state). Global (OneWooz's own), not
    tenant-scoped, like :class:`PlatformPaymentAccount`.
    """

    #: Operator-facing name, e.g. "Brandise Box LLP, 2026". Optional.
    label = models.CharField(max_length=120, blank=True, default="")

    company_name = models.CharField(max_length=200, blank=True, default="")
    mobile_number = models.CharField(max_length=20, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    address = models.TextField(blank=True, default="")
    gstin = models.CharField(
        "GSTIN", max_length=15, blank=True, default="",
        help_text="15 characters. The state below is detected from its first two digits.",
    )
    hsn_code = models.CharField("HSN code", max_length=8, blank=True, default="998313")
    state = models.CharField(
        "State (place of supply)", max_length=60, blank=True, default="",
        help_text=(
            "Filled automatically from the GSTIN. A buyer in this state is billed "
            "CGST 9% + SGST 9%; any other state IGST 18%."
        ),
    )
    #: Terms & Conditions, one string per numbered point.
    terms = models.JSONField("Terms & Conditions", default=list, blank=True)

    signature = models.ImageField(
        upload_to="billing/signatures/", blank=True,
        help_text="A transparent PNG works best. Shown about 56px tall on the bill.",
    )
    #: Printed under "Authorised Signatory". Blank = the default designation
    #: from settings.PLATFORM_BILLING_ENTITY.
    designation = models.CharField(
        max_length=120, blank=True, default="",
        help_text="Printed as “Designation: …” under the signature. Leave blank for the default.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="The newest active invoice details are the ones used on bills.",
    )

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "invoice details"
        verbose_name_plural = "invoice details"

    def __str__(self) -> str:
        return self.label or self.company_name or "Invoice details"

    def clean(self):
        from django.core.exceptions import ValidationError

        from . import gst

        self.gstin = gst.normalise_gstin(self.gstin)
        if self.gstin and not gst.is_valid_gstin(self.gstin):
            raise ValidationError({"gstin": "That doesn’t look like a valid 15-character GSTIN."})

    def save(self, *args, **kwargs):
        from . import gst

        self.gstin = gst.normalise_gstin(self.gstin)
        # The GSTIN is the registered place of supply, so its state wins.
        detected = gst.state_from_gstin(self.gstin)
        self.state = detected or gst.canonical_state(self.state) or (self.state or "").strip()
        self.terms = [
            " ".join(str(t).split()) for t in (self.terms if isinstance(self.terms, list) else [])
            if str(t).strip()
        ]
        super().save(*args, **kwargs)

    @classmethod
    def current(cls):
        """The invoice details bills use now, or None (settings defaults)."""
        return cls.objects.filter(is_active=True).order_by("-created_at").first()


class CommerceBillDetails(BaseModel):
    """The billing company and payment details printed on every commerce fee bill.

    Managed in the Django admin as "Commerce fee bill details". The store's
    commerce fee invoice (Bills page → PDF, and the bill page itself) prints the
    newest *active* object: who is billing (company, address, GSTIN, contact)
    and how to pay (bank account, IFSC, UPI). A blank company field falls back
    to the active "Invoice details" / ``settings.PLATFORM_BILLING_ENTITY``; with
    no payment details here, the active Platform payment accounts are printed.

    The company's state (detected from the GSTIN) decides the 18% GST split on
    the commerce fee: CGST 9% + SGST 9% for a store in the same state, IGST 18%
    otherwise. Global (OneWooz's own), not tenant-scoped.
    """

    label = models.CharField(
        max_length=120, blank=True, default="",
        help_text="Operator-facing name only, e.g. “Brandise Box LLP, 2026”.",
    )

    # -- Billing company -----------------------------------------------------
    company_name = models.CharField(max_length=200, blank=True, default="")
    address = models.TextField(blank=True, default="")
    gstin = models.CharField(
        "GSTIN", max_length=15, blank=True, default="",
        help_text="15 characters. The state below is detected from its first two digits.",
    )
    pan = models.CharField("PAN", max_length=10, blank=True, default="")
    state = models.CharField(
        "State", max_length=60, blank=True, default="",
        help_text="Filled from the GSTIN when there is one. Decides CGST + SGST vs IGST.",
    )
    sac_code = models.CharField(
        "SAC / HSN code", max_length=8, blank=True, default="998313",
        help_text="Printed against the commerce fee line.",
    )
    email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=20, blank=True, default="")

    # -- Payment details -----------------------------------------------------
    bank_name = models.CharField(max_length=120, blank=True, default="")
    account_holder = models.CharField(max_length=120, blank=True, default="")
    account_number = models.CharField(max_length=40, blank=True, default="")
    ifsc_code = models.CharField("IFSC code", max_length=20, blank=True, default="")
    branch = models.CharField(max_length=120, blank=True, default="")
    upi_id = models.CharField("UPI ID", max_length=120, blank=True, default="")
    payment_note = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Printed under the payment details, e.g. “Quote the invoice number in the remark.”",
    )

    is_active = models.BooleanField(
        default=True,
        help_text="The newest active object is the one printed on bills.",
    )

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "commerce fee bill details"
        verbose_name_plural = "commerce fee bill details"

    def __str__(self) -> str:
        return self.label or self.company_name or "Commerce fee bill details"

    def clean(self):
        from django.core.exceptions import ValidationError

        from . import gst

        self.gstin = gst.normalise_gstin(self.gstin)
        if self.gstin and not gst.is_valid_gstin(self.gstin):
            raise ValidationError({"gstin": "That doesn’t look like a valid 15-character GSTIN."})

    def save(self, *args, **kwargs):
        from . import gst

        self.gstin = gst.normalise_gstin(self.gstin)
        self.pan = (self.pan or "").strip().upper()
        detected = gst.state_from_gstin(self.gstin)
        self.state = detected or gst.canonical_state(self.state) or (self.state or "").strip()
        super().save(*args, **kwargs)

    @property
    def has_payment_details(self) -> bool:
        return bool(self.account_number or self.upi_id)

    @classmethod
    def current(cls):
        """The details commerce fee bills print now, or None (fallbacks apply)."""
        return cls.objects.filter(is_active=True).order_by("-created_at").first()


class PricingFeature(BaseModel):
    """One tab in the "features" strip under the pricing cards.

    Operators manage these in the Django admin: each object is a feature
    **title** (the tab label) and its **description** (shown when that tab is
    selected). Tabs run horizontally in ``order``; only active ones are shown.
    Independent of any one plan: it describes what the platform offers.
    """

    title = models.CharField(
        max_length=80,
        help_text="The tab label, e.g. “Custom domain”. Keep it short.",
    )
    description = models.TextField(
        help_text="Shown under the tabs when this one is selected. Line breaks are kept.",
    )
    order = models.PositiveIntegerField(
        default=0, db_index=True,
        help_text="Tabs are shown left to right in ascending order.",
    )
    is_active = models.BooleanField(
        default=True, db_index=True,
        help_text="Only active features appear on the pricing page.",
    )

    class Meta:
        ordering = ("order", "created_at")
        verbose_name = "pricing feature"
        verbose_name_plural = "pricing features"

    def __str__(self) -> str:
        return self.title

    @classmethod
    def active(cls):
        return list(cls.objects.filter(is_active=True).order_by("order", "created_at"))


# --------------------------------------------------------------------------
# Storefront website templates (designs) and their purchases
# --------------------------------------------------------------------------
class WebsiteTemplate(BaseModel):
    """One storefront design owners can pick for their store, as sold by OneWooz.

    The designs themselves are code (``templates/storefront/...``), built and
    deployed by OneWooz; every design in the code registry
    (:data:`billing.website_templates.REGISTRY`) gets one row here automatically,
    so a newly deployed design simply appears in this list. Rows cannot be added
    or deleted by hand; staff edit how each design is presented and sold:

    * its name, thumbnail and short description;
    * which stores may use it (D2C, B2B or both);
    * free, or priced (charged through OneWooz's own Cashfree account, plus GST);
    * a priced design's licence: **permanent** (bought once, bound to the buyer's
      email for good) or **yearly** (expires one year after purchase and must be
      bought again);
    * whether owners can see it at all (``is_available``).
    """

    class TemplateType(models.TextChoices):
        BOTH = "both", "D2C and B2B"
        D2C = "d2c", "D2C only"
        B2B = "b2b", "B2B only"

    class Pricing(models.TextChoices):
        FREE = "free", "Free"
        PRICED = "priced", "Priced"

    class License(models.TextChoices):
        PERMANENT = "permanent", "Fixed (permanent, bound to the buyer's email)"
        YEARLY = "yearly", "Yearly (expires one year after purchase)"

    key = models.CharField(
        max_length=20, unique=True, editable=False,
        help_text="The design's code identifier. Set by the deployed code; not editable.",
    )
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=300, blank=True, default="")
    thumbnail = models.ImageField(
        upload_to="website-templates/", blank=True,
        help_text="Preview image shown to owners on the design picker (about 4:3).",
    )
    template_type = models.CharField(
        max_length=8, choices=TemplateType.choices, default=TemplateType.BOTH,
        help_text="Which stores may use this design.",
    )
    pricing = models.CharField(
        max_length=8, choices=Pricing.choices, default=Pricing.FREE,
        help_text="Free designs can be used by anyone. Priced designs must be bought first.",
    )
    currency = models.CharField(max_length=3, default="INR")
    price = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00"),
        help_text="Price for a priced design (GST is added at checkout). Ignored when free.",
    )
    license_type = models.CharField(
        max_length=10, choices=License.choices, default=License.PERMANENT,
        help_text="Fixed: bought once, usable for good by that email. Yearly: expires a year after purchase.",
    )
    is_available = models.BooleanField(
        default=True, db_index=True,
        help_text="Show this design to store owners. Untick to hide it from every picker.",
    )
    order = models.PositiveSmallIntegerField(default=0, db_index=True)

    class Meta:
        ordering = ("order", "key")
        verbose_name = "website template"
        verbose_name_plural = "website templates"

    def __str__(self) -> str:
        return self.name or f"Template {self.key}"

    @property
    def is_priced(self) -> bool:
        return self.pricing == self.Pricing.PRICED and (self.price or Decimal("0")) > 0

    @property
    def is_yearly(self) -> bool:
        return self.license_type == self.License.YEARLY

    def fits(self, business_model: str) -> bool:
        """May a store with ``business_model`` (d2c / b2b / both) use this design?"""
        if self.template_type == self.TemplateType.BOTH:
            return True
        bm = (business_model or "").lower()
        if self.template_type == self.TemplateType.D2C:
            return bm in ("d2c", "both")
        return bm in ("b2b", "both")


class TemplatePurchase(BaseModel):
    """A store owner's purchase of a priced :class:`WebsiteTemplate`.

    Bound to the buyer's **email** (like subscriptions), so a design bought from
    one store can be used on every store the same owner runs. A permanent
    licence never expires; a yearly one expires a year after payment (buying
    again while still active extends it from the current end).

    Payment runs through OneWooz's own Cashfree account and is confirmed only by
    fetching the order's real status from Cashfree (never from a redirect or a
    webhook body), exactly like subscription orders. The design's name, price
    and licence are frozen on the row, so later admin edits never rewrite it.
    """

    class Status(models.TextChoices):
        CREATED = "created", "Created"
        PENDING = "pending", "Pending payment"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"

    number = models.CharField(max_length=20, unique=True, editable=False, db_index=True)
    template = models.ForeignKey(
        WebsiteTemplate, on_delete=models.SET_NULL, null=True, blank=True, related_name="purchases"
    )
    template_key = models.CharField(max_length=20, db_index=True)
    template_name = models.CharField(max_length=80)
    license_type = models.CharField(max_length=10, choices=WebsiteTemplate.License.choices)

    email = models.EmailField(db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="template_purchases",
    )
    # The store the design was bought from; it is switched to the design as soon
    # as payment confirms. A plain UUID, not a foreign key: the licence belongs to
    # the email, not to one store.
    site_ref = models.UUIDField(null=True, blank=True)

    currency = models.CharField(max_length=3, default="INR")
    base_amount = models.DecimalField(max_digits=10, decimal_places=2)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("18.00"))
    gst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.CREATED, db_index=True
    )
    cf_order_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    cf_payment_id = models.CharField(max_length=64, blank=True, default="")
    gateway_status = models.CharField(max_length=40, blank=True, default="")
    paid_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(
        null=True, blank=True, help_text="Empty for a permanent licence.",
    )

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "template purchase"
        verbose_name_plural = "template purchases"
        indexes = [
            models.Index(fields=("email", "template_key", "status")),
            models.Index(fields=("status", "-created_at")),
        ]

    def __str__(self) -> str:
        return f"{self.number} · {self.template_name} · {self.email}"

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = self._new_number()
        super().save(*args, **kwargs)

    @staticmethod
    def _new_number() -> str:
        for _ in range(10):
            candidate = "OWT" + get_random_string(9, "0123456789")
            if not TemplatePurchase.objects.filter(number=candidate).exists():
                return candidate
        return "OWT" + get_random_string(12, "0123456789")

    @property
    def is_paid(self) -> bool:
        return self.status == self.Status.PAID

    @property
    def is_current(self) -> bool:
        """Paid and not yet expired."""
        if not self.is_paid:
            return False
        return self.expires_at is None or self.expires_at > timezone.now()
