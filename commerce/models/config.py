"""Per-store integration configuration that holds third-party secrets.

Credentials use ``core.fields.EncryptedTextField`` (HKDF-derived key, ``v1:``
version prefix, raises on failure). The reference stored these as plain
``TextField``s populated by an ad-hoc ``encrypt_value`` helper that returned the
ciphertext on a decrypt error — so a rotated key silently produced garbage. The
encrypted field type makes that impossible.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models

from core.fields import EncryptedCharField
from core.models import TenantScopedModel


class PaymentGatewayConfig(TenantScopedModel):
    """A store's checkout payment configuration."""

    class Gateway(models.TextChoices):
        RAZORPAY = "razorpay", "Razorpay"
        CASHFREE = "cashfree", "Cashfree"

    class PartialType(models.TextChoices):
        FIXED = "fixed", "Fixed amount"
        PERCENT = "percent", "Percentage"

    site = models.OneToOneField(
        "tenancy.Site", on_delete=models.CASCADE, related_name="payment_config"
    )
    gateway = models.CharField(max_length=20, choices=Gateway.choices, blank=True, default="")
    key_id = EncryptedCharField(max_length=255, blank=True, default="")
    key_secret = EncryptedCharField(max_length=255, blank=True, default="")
    enabled = models.BooleanField(default=False)

    cod_enabled = models.BooleanField(default=False)
    partial_payment_enabled = models.BooleanField(default=False)
    partial_payment_type = models.CharField(
        max_length=10, choices=PartialType.choices, default=PartialType.PERCENT
    )
    partial_payment_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # Invoicing identity printed on receipts.
    company_name = models.CharField(max_length=200, blank=True, default="")
    gstin = models.CharField(max_length=15, blank=True, default="")
    state = models.CharField(max_length=100, blank=True, default="")
    pincode = models.CharField(max_length=6, blank=True, default="")
    mobile_number = models.CharField(max_length=20, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    address = models.TextField(blank=True, default="")
    digital_signature = models.ImageField(upload_to="signatures/", blank=True, null=True)

    def __str__(self) -> str:
        return f"Payment config ({self.gateway or 'unset'})"

    def clean(self):
        super().clean()
        if self.partial_payment_enabled:
            if not self.enabled:
                raise ValidationError(
                    {"partial_payment_enabled": "Enable the payment gateway first."}
                )
            if not self.cod_enabled:
                raise ValidationError(
                    {"partial_payment_enabled": "Enable COD first — the balance is collected as COD."}
                )
            if self.partial_payment_value is None or self.partial_payment_value <= 0:
                raise ValidationError(
                    {"partial_payment_value": "Enter a value greater than zero."}
                )
            if (
                self.partial_payment_type == self.PartialType.PERCENT
                and self.partial_payment_value > Decimal("100")
            ):
                raise ValidationError(
                    {"partial_payment_value": "Percentage cannot exceed 100."}
                )


class TrackingConfig(TenantScopedModel):
    """Storefront marketing tags: analytics and ad pixels (Tracking & Ads).

    IDs, not secrets — these render into the public storefront's head — so they
    are plain fields. The conversions API token, which is a secret, is
    encrypted.
    """

    site = models.OneToOneField(
        "tenancy.Site", on_delete=models.CASCADE, related_name="tracking_config"
    )
    google_analytics_id = models.CharField(max_length=40, blank=True, default="")
    google_tag_manager_id = models.CharField(max_length=40, blank=True, default="")
    google_ads_id = models.CharField(max_length=40, blank=True, default="")
    meta_pixel_id = models.CharField(max_length=40, blank=True, default="")
    meta_capi_token = EncryptedCharField(max_length=255, blank=True, default="")
    tiktok_pixel_id = models.CharField(max_length=40, blank=True, default="")
    pinterest_tag_id = models.CharField(max_length=40, blank=True, default="")
    microsoft_uet_id = models.CharField(max_length=40, blank=True, default="")
    custom_head_html = models.TextField(blank=True, default="")

    def __str__(self) -> str:
        return "Tracking config"
