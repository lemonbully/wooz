"""Shipping: warehouses, courier providers and shipments."""

from __future__ import annotations

from django.db import models

from core.fields import EncryptedCharField, EncryptedTextField
from core.models import TenantScopedModel


class Warehouse(TenantScopedModel):
    """A pickup / return address for a store's shipments."""

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="warehouses"
    )
    name = models.CharField(max_length=120)
    contact_name = models.CharField(max_length=120, blank=True, default="")
    phone = models.CharField(max_length=20, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    address_line = models.CharField(max_length=255, blank=True, default="")
    city = models.CharField(max_length=120, blank=True, default="")
    state = models.CharField(max_length=120, blank=True, default="")
    pincode = models.CharField(max_length=10, blank=True, default="")
    country = models.CharField(max_length=2, default="IN")
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ("-is_default", "name")
        indexes = [models.Index(fields=("site", "is_default"))]

    def __str__(self) -> str:
        return self.name


class ShippingProvider(TenantScopedModel):
    """A courier integration for a store. Tokens encrypted at rest."""

    class Provider(models.TextChoices):
        DELHIVERY = "delhivery", "Delhivery"
        SHIPROCKET = "shiprocket", "Shiprocket"
        MANUAL = "manual", "Manual / self-ship"

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="shipping_providers"
    )
    provider = models.CharField(max_length=20, choices=Provider.choices)
    enabled = models.BooleanField(default=False)
    # Whether new orders auto-book a shipment on confirmation.
    auto_ship = models.BooleanField(default=False)
    api_token = EncryptedTextField(blank=True, default="")
    client_name = EncryptedCharField(max_length=255, blank=True, default="")
    default_warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="providers",
    )
    settings = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("provider",)
        constraints = [
            models.UniqueConstraint(
                fields=("site", "provider"), name="uniq_provider_per_site"
            )
        ]

    def __str__(self) -> str:
        return self.get_provider_display()


class Shipment(TenantScopedModel):
    """A booked shipment against an order."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        BOOKED = "booked", "Booked"
        IN_TRANSIT = "in_transit", "In transit"
        DELIVERED = "delivered", "Delivered"
        RTO = "rto", "Returned to origin"
        CANCELLED = "cancelled", "Cancelled"

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="shipments"
    )
    order = models.ForeignKey(
        "commerce.Order", on_delete=models.CASCADE, related_name="shipments"
    )
    provider = models.ForeignKey(
        ShippingProvider,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shipments",
    )
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shipments",
    )
    awb = models.CharField(max_length=120, blank=True, default="")
    tracking_url = models.URLField(blank=True, default="")
    label_pdf = models.FileField(upload_to="shipping_labels/", null=True, blank=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    shipping_charge = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("site", "status")),
            models.Index(fields=("site", "order")),
        ]

    def __str__(self) -> str:
        return f"Shipment {self.awb or self.pk}"
