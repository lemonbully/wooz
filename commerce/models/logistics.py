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

    # --- Shiprocket API credentials -------------------------------------
    # Shiprocket authenticates with the account's API user email + password and
    # returns a bearer token (valid ~10 days), which we cache and refresh. Both
    # secrets are encrypted at rest like every other third-party credential.
    api_email = EncryptedCharField(max_length=255, blank=True, default="")
    api_password = EncryptedTextField(blank=True, default="")
    access_token = EncryptedTextField(blank=True, default="")
    access_token_expires_at = models.DateTimeField(null=True, blank=True)
    # Must match a Pickup Location nickname configured in the Shiprocket
    # dashboard  create/adhoc rejects an order whose pickup_location is unknown.
    pickup_location = models.CharField(max_length=120, blank=True, default="")
    # Optional API base override (defaults to https://apiv2.shiprocket.in).
    base_url = models.CharField(max_length=200, blank=True, default="")

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

    def is_access_token_valid(self) -> bool:
        """True when a cached bearer token is present and not near expiry."""
        from django.utils import timezone

        if not (self.access_token or "").strip():
            return False
        if self.access_token_expires_at is None:
            return False
        # A minute of margin so a token that is about to expire is refreshed
        # rather than used for a request that would then 401.
        return self.access_token_expires_at > timezone.now() + timezone.timedelta(minutes=1)

    def store_access_token(self, token: str, expires_at) -> None:
        self.access_token = token or ""
        self.access_token_expires_at = expires_at
        self.save(update_fields=["access_token", "access_token_expires_at", "updated_at"])


class Shipment(TenantScopedModel):
    """A booked shipment against an order.

    Mirrors the reference courier lifecycle so the Orders tabs can route an
    order by its shipment state (Ready to Ship → Waiting for Pickup / Pickup
    Scheduled → Shipped → Delivered / RTO). ``awb`` is the courier tracking
    number; ``courier_name`` is the human label shown in the tracking popup.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CREATED = "created", "Created"
        BOOKED = "booked", "Booked"
        PICKUP_REQUESTED = "pickup_requested", "Pickup requested"
        PICKED_UP = "picked_up", "Picked up"
        IN_TRANSIT = "in_transit", "In transit"
        OUT_FOR_DELIVERY = "out_for_delivery", "Out for delivery"
        DELIVERED = "delivered", "Delivered"
        DELIVERY_FAILED = "delivery_failed", "Delivery failed"
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
    courier_name = models.CharField(max_length=120, blank=True, default="")
    tracking_url = models.URLField(blank=True, default="")
    label_pdf = models.FileField(upload_to="shipping_labels/", null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
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

    @property
    def provider_key(self) -> str:
        """The lowercase provider slug (``delhivery``/``shiprocket``/``manual``)
        this shipment was booked with, or an empty string if unlinked."""
        return (self.provider.provider if self.provider_id else "") or ""


class CourierWebhookEvent(TenantScopedModel):
    """A raw tracking update pushed by a courier (or synced locally).

    The Orders tracking popup reads the latest event for an order. The
    reference kept the courier's exact JSON in ``raw_payload`` and matched it to
    an order by an ``order_id`` key or the shipment AWB; we keep that shape so
    the same popup renders unchanged. ``raw_payload`` is normalised on write to
    ``{current_status, awb, courier_name, etd, scans:[{status, activity,
    location, date}]}`` so the client never has to know each courier's schema.
    """

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="courier_events"
    )
    order = models.ForeignKey(
        "commerce.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="courier_events",
    )
    shipment = models.ForeignKey(
        Shipment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="courier_events",
    )
    provider = models.CharField(max_length=20, blank=True, default="")
    topic = models.CharField(max_length=80, blank=True, default="")
    status = models.CharField(max_length=60, blank=True, default="")
    awb = models.CharField(max_length=120, blank=True, default="")
    raw_payload = models.JSONField(default=dict, blank=True)
    signature_ok = models.BooleanField(default=False)
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-received_at",)
        indexes = [
            models.Index(fields=("site", "-received_at")),
            models.Index(fields=("site", "awb")),
        ]

    def __str__(self) -> str:
        return f"{self.provider or 'courier'} · {self.status or self.topic}"
