"""Growth surfaces: collaborations/referrals, Google Merchant, tracking & ads,
and lightweight storefront analytics."""

from __future__ import annotations

from decimal import Decimal

from django.db import models

from core.fields import EncryptedTextField
from core.models import TenantScopedModel


class Collaboration(TenantScopedModel):
    """A referral / creator collaboration.

    A creator promotes the store through a referral handle and earns a
    commission on attributed orders. This is the OneWooz-native distillation of
    the reference's sprawling ``referral_marketing`` app — the manage tab only
    needs the owner-facing controls: who the collaborators are, their rate, and
    what they've earned.
    """

    class Status(models.TextChoices):
        INVITED = "invited", "Invited"
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"

    class CommissionType(models.TextChoices):
        PERCENT = "percent", "Percentage"
        FIXED = "fixed", "Fixed per order"

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="collaborations"
    )
    name = models.CharField(max_length=120)
    email = models.EmailField(blank=True, default="")
    handle = models.SlugField(max_length=60)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.INVITED, db_index=True
    )
    commission_type = models.CharField(
        max_length=10, choices=CommissionType.choices, default=CommissionType.PERCENT
    )
    commission_value = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    total_orders = models.PositiveIntegerField(default=0)
    total_earned = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("site", "handle"), name="uniq_collab_handle_per_site"
            )
        ]
        indexes = [models.Index(fields=("site", "status"))]

    def __str__(self) -> str:
        return self.name

    @property
    def outstanding(self) -> Decimal:
        return (self.total_earned or Decimal("0.00")) - (self.total_paid or Decimal("0.00"))


class GoogleMerchantConfig(TenantScopedModel):
    """A store's Google Merchant Center connection and sync preferences."""

    class Status(models.TextChoices):
        DISCONNECTED = "disconnected", "Disconnected"
        CONNECTED = "connected", "Connected"
        ERROR = "error", "Error"

    site = models.OneToOneField(
        "tenancy.Site", on_delete=models.CASCADE, related_name="google_merchant"
    )
    status = models.CharField(
        max_length=14, choices=Status.choices, default=Status.DISCONNECTED
    )
    merchant_id = models.CharField(max_length=64, blank=True, default="")
    service_account_json = EncryptedTextField(blank=True, default="")
    auto_sync = models.BooleanField(default=False)
    default_country = models.CharField(max_length=2, default="IN")
    default_language = models.CharField(max_length=5, default="en")
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")

    def __str__(self) -> str:
        return f"Google Merchant ({self.status})"


class AnalyticsSession(TenantScopedModel):
    """One storefront visit session (privacy-light, no cross-site identity)."""

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="analytics_sessions"
    )
    session_key = models.CharField(max_length=64, db_index=True)
    referrer = models.URLField(blank=True, default="")
    utm_source = models.CharField(max_length=120, blank=True, default="")
    utm_medium = models.CharField(max_length=120, blank=True, default="")
    utm_campaign = models.CharField(max_length=120, blank=True, default="")
    device = models.CharField(max_length=20, blank=True, default="")
    country = models.CharField(max_length=2, blank=True, default="")
    started_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-started_at",)
        indexes = [models.Index(fields=("site", "-started_at"))]

    def __str__(self) -> str:
        return self.session_key


class AnalyticsEvent(TenantScopedModel):
    """A single storefront event: page view, add-to-cart, checkout, purchase."""

    class Kind(models.TextChoices):
        PAGE_VIEW = "page_view", "Page view"
        PRODUCT_VIEW = "product_view", "Product view"
        ADD_TO_CART = "add_to_cart", "Add to cart"
        CHECKOUT = "checkout", "Checkout started"
        PURCHASE = "purchase", "Purchase"

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="analytics_events"
    )
    session = models.ForeignKey(
        AnalyticsSession,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="events",
    )
    kind = models.CharField(max_length=16, choices=Kind.choices, db_index=True)
    path = models.CharField(max_length=255, blank=True, default="")
    product = models.ForeignKey(
        "commerce.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="analytics_events",
    )
    value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("site", "kind", "-created_at")),
        ]

    def __str__(self) -> str:
        return self.kind
