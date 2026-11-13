"""Customers, their addresses, store credit and wishlists.

Storefront shoppers are not OneWooz accounts  a customer is identified by
email within a single store. The same email at two different stores is two
distinct customers, and nothing here can join them.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models

from core.models import SoftDeleteMixin, TenantScopedModel


class Customer(TenantScopedModel, SoftDeleteMixin):
    """A shopper on one store, keyed by lower-cased email."""

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="customers"
    )
    name = models.CharField(max_length=120, blank=True, default="")
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True, default="")
    # Set when the shopper deleted their own account from the storefront
    # (storefront.account_profile.delete_account). Nothing is erased from the
    # database: the row is hidden like a soft-deleted customer, its live email
    # becomes a placeholder (so the shopper's data can never surface again and
    # a later order starts a fresh customer), and the original email / phone
    # are kept below for the platform admin. The store owner sees only the name
    # with an "Account deleted" badge.
    account_deleted_at = models.DateTimeField(null=True, blank=True, editable=False)
    deleted_email = models.EmailField(blank=True, default="", editable=False)
    deleted_phone = models.CharField(max_length=20, blank=True, default="", editable=False)

    class Meta:
        ordering = ("-updated_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("site", "email"), name="uniq_customer_email_per_site"
            )
        ]
        indexes = [models.Index(fields=("site", "email"))]

    def __str__(self) -> str:
        return self.email

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()
        super().save(*args, **kwargs)


class CustomerAddress(TenantScopedModel):
    """A saved shipping/billing address, optionally with GST company details."""

    class AddressType(models.TextChoices):
        BOTH = "both", "Shipping & billing"
        SHIPPING = "shipping", "Shipping"
        BILLING = "billing", "Billing"

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="customer_addresses"
    )
    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="addresses"
    )

    # Whether this address is used for shipping, billing, or both.
    address_type = models.CharField(
        max_length=10, choices=AddressType.choices, default=AddressType.BOTH
    )
    # The shopper's own name for it ("Home", "Office", "Mom's place"). Blank
    # for addresses saved automatically from orders.
    label = models.CharField(max_length=40, blank=True, default="")

    has_gst = models.BooleanField(default=False)
    company_name = models.CharField(max_length=200, blank=True, default="")
    gstin = models.CharField(max_length=15, blank=True, default="")

    phone = models.CharField(max_length=20, blank=True, default="")
    country_code = models.CharField(max_length=2, default="IN")
    house_no = models.CharField(max_length=120, blank=True, default="")
    street_area = models.CharField(max_length=200, blank=True, default="")
    city = models.CharField(max_length=120, blank=True, default="")
    state = models.CharField(max_length=120, blank=True, default="")
    pincode = models.CharField(max_length=10, blank=True, default="")
    address_text = models.TextField(blank=True, default="")
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ("-updated_at",)
        indexes = [
            models.Index(fields=("site", "customer")),
            models.Index(fields=("site", "pincode")),
        ]

    def __str__(self) -> str:
        return f"{self.city} {self.pincode}".strip()


class CustomerCredit(TenantScopedModel):
    """Store credit granted to a customer, applied automatically at checkout."""

    class CreditType(models.TextChoices):
        AMOUNT = "amount", "Amount"
        PERCENT = "percent", "Percent"

    class Scope(models.TextChoices):
        ALL = "all", "All categories"
        CATEGORIES = "categories", "Selected categories"

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="customer_credits"
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="credits",
    )
    customer_email = models.EmailField()

    credit_type = models.CharField(
        max_length=10, choices=CreditType.choices, default=CreditType.AMOUNT
    )
    scope = models.CharField(max_length=12, choices=Scope.choices, default=Scope.ALL)
    category_ids = models.JSONField(default=list, blank=True)

    credit_value = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    remaining_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    is_active = models.BooleanField(default=True)
    valid_until = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True, default="")
    last_used_at = models.DateTimeField(null=True, blank=True)
    # Shared by every credit one "Bulk credits" run created, so the owner can
    # later remove that whole universal grant at once. Null for a credit given
    # to one customer (and for bulk grants made before this field existed,
    # which ``commerce.credit_batches`` matches by their identical settings).
    batch_id = models.UUIDField(null=True, blank=True, db_index=True, editable=False)

    class Meta:
        ordering = ("-updated_at",)
        indexes = [
            models.Index(fields=("site", "customer_email")),
            models.Index(fields=("site", "is_active")),
        ]

    def __str__(self) -> str:
        return f"Credit for {self.customer_email}"

    def save(self, *args, **kwargs):
        if self.customer_email:
            self.customer_email = self.customer_email.strip().lower()
        super().save(*args, **kwargs)


class WishlistItem(TenantScopedModel):
    """A product a shopper saved for later (D2C)."""

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="wishlist_items"
    )
    product = models.ForeignKey(
        "commerce.Product", on_delete=models.CASCADE, related_name="wishlisted_in"
    )
    customer_email = models.EmailField()

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("site", "product", "customer_email"),
                name="uniq_wishlist_item",
            )
        ]
        indexes = [models.Index(fields=("site", "customer_email"))]

    def save(self, *args, **kwargs):
        if self.customer_email:
            self.customer_email = self.customer_email.strip().lower()
        super().save(*args, **kwargs)


class NewsletterSubscriber(TenantScopedModel):
    """Someone who subscribed to a store's newsletter (storefront footer form).

    Store-wide and keyed by lower-cased email per store, so subscribing twice is
    idempotent.
    """

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="newsletter_subscribers"
    )
    email = models.EmailField()
    #: Where they subscribed from (footer, checkout, …)  context for the owner.
    source = models.CharField(max_length=40, blank=True, default="footer")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("site", "email"), name="uniq_newsletter_email_per_site"
            )
        ]
        indexes = [models.Index(fields=("site", "-created_at"))]

    def __str__(self) -> str:
        return self.email

    #: How each ``source`` reads on the owner's Subscribers page.
    SOURCE_LABELS = {
        "newsletter_popup": "Subscribed from newsletter",
        "footer": "Footer form",
    }

    @property
    def source_label(self) -> str:
        key = (self.source or "footer").strip()
        return self.SOURCE_LABELS.get(key, key.replace("_", " ").capitalize())

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()
        super().save(*args, **kwargs)
