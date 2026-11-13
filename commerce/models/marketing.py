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
    the reference's sprawling ``referral_marketing`` app  the manage tab only
    needs the owner-facing controls: who the collaborators are, their rate, and
    what they've earned.
    """

    class Status(models.TextChoices):
        INVITED = "invited", "Invited"
        ACTIVE = "active", "Active"
        VERIFIED = "verified", "Verified"
        PAUSED = "paused", "Paused"
        SUSPENDED = "suspended", "Suspended"
        EXPIRED = "expired", "Expired"

    class CommissionType(models.TextChoices):
        PERCENT = "percent", "Percentage"
        FIXED = "fixed", "Fixed per order"

    class Scope(models.TextChoices):
        ALL = "all", "All"
        SPECIFIC = "specific", "Specific"

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="collaborations"
    )
    name = models.CharField(max_length=120)
    email = models.EmailField(blank=True, default="")
    mobile_number = models.CharField(max_length=20, blank=True, default="")
    handle = models.SlugField(max_length=60)
    # Referral portal subdomain prefix (``<subdomain>.<referral root>``).
    subdomain = models.SlugField(max_length=60, blank=True, default="")

    # -- Creator storefront branding (set by the collaborator in their portal) --
    #: Optional storefront title; blank falls back to a title derived from the
    #: subdomain / name.
    storefront_title = models.CharField(max_length=120, blank=True, default="")
    #: Short "about my store" blurb shown in the storefront hero.
    about_storefront = models.CharField(max_length=280, blank=True, default="")
    profile_picture = models.ImageField(
        upload_to="collab/profile/", null=True, blank=True
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.INVITED, db_index=True
    )
    commission_type = models.CharField(
        max_length=10, choices=CommissionType.choices, default=CommissionType.FIXED
    )
    commission_value = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    # Curated scope for what the creator may promote.
    product_scope = models.CharField(max_length=10, choices=Scope.choices, default=Scope.ALL)
    category_scope = models.CharField(max_length=10, choices=Scope.choices, default=Scope.ALL)
    products = models.ManyToManyField("commerce.Product", blank=True, related_name="collaborations")
    categories = models.ManyToManyField("commerce.Category", blank=True, related_name="collaborations")

    total_orders = models.PositiveIntegerField(default=0)
    total_earned = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    # OTP + expiring smart-link contract onboarding (local flow: OTP and the
    # signed invite token are generated and stored; the owner shares the link).
    otp = models.CharField(max_length=8, blank=True, default="")
    otp_expires_at = models.DateTimeField(null=True, blank=True)
    invite_token = models.TextField(blank=True, default="")
    invite_token_expires_at = models.DateTimeField(null=True, blank=True)
    contract_signed_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("site", "handle"), name="uniq_collab_handle_per_site"
            ),
            # The subdomain is a public routing handle (<subdomain>.myugcstore.com),
            # so it must be unique across every store  not just per site.
            models.UniqueConstraint(
                fields=("subdomain",),
                condition=~models.Q(subdomain=""),
                name="uniq_collab_subdomain_global",
            ),
        ]
        indexes = [
            models.Index(fields=("site", "status")),
            models.Index(fields=("subdomain",)),
            # Collaborator portal: "my collaborations, newest first" by email.
            models.Index(fields=("email", "-created_at")),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def outstanding(self) -> Decimal:
        return (self.total_earned or Decimal("0.00")) - (self.total_paid or Decimal("0.00"))

    @property
    def is_suspended(self) -> bool:
        return self.status == self.Status.SUSPENDED


class CollaborationPayoutRequest(TenantScopedModel):
    """A collaborator's request to be paid out their outstanding commission.

    Raised by the collaborator from their portal dashboard (``myugcstore.com``);
    the owner fulfils it out of band and records the payout on the manage page
    (which increments :attr:`Collaboration.total_paid`). This row is the request
    log + audit trail: the amount asked for, a snapshot of the outstanding
    balance at the time, and its lifecycle status.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="payout_requests"
    )
    collaboration = models.ForeignKey(
        Collaboration, on_delete=models.CASCADE, related_name="payout_requests"
    )
    #: Amount the collaborator asked for (defaults to their outstanding balance).
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    #: Snapshot of the outstanding balance when the request was raised, for audit.
    outstanding_snapshot = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    note = models.CharField(max_length=280, blank=True, default="")
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("site", "status")),
            models.Index(fields=("collaboration", "status")),
        ]

    def __str__(self) -> str:
        return f"Payout ₹{self.amount} · {self.get_status_display()}"


# Product gallery image slots the storefront + overrides address, matching the
# reference (image1 … image7).
COLLAB_IMAGE_SLOTS = ("image1", "image2", "image3", "image4", "image5", "image6", "image7")


class CollaborationMedia(TenantScopedModel):
    """Media a collaborator uploads for their storefront: a cover and video reels.

    ``cover=True`` marks the single storefront hero cover; ``media_type='video'``
    rows with ``show_on_storefront`` are the autoplaying reels strip. Mirrors the
    reference's ``ReferralMedia``.
    """

    class Kind(models.TextChoices):
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="collab_media"
    )
    collaboration = models.ForeignKey(
        Collaboration, on_delete=models.CASCADE, related_name="media"
    )
    media_type = models.CharField(max_length=10, choices=Kind.choices, default=Kind.IMAGE)
    file = models.FileField(upload_to="collab/media/", max_length=500)
    title = models.CharField(max_length=200, blank=True, default="")
    cover = models.BooleanField(default=False)
    show_on_storefront = models.BooleanField(default=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("collaboration", "media_type")),
            models.Index(fields=("collaboration", "cover")),
        ]

    def __str__(self) -> str:
        return f"{self.media_type} · {self.collaboration_id}"


class CollaborationProductImage(TenantScopedModel):
    """A collaborator's per-product image override for one gallery slot.

    Lets a creator replace (or hide) a product photo on *their* storefront
    without touching the owner's catalogue. Mirrors ``ReferralProductImage``.
    """

    SLOT_CHOICES = tuple((s, f"Image {i}") for i, s in enumerate(COLLAB_IMAGE_SLOTS, 1))

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="collab_product_images"
    )
    collaboration = models.ForeignKey(
        Collaboration, on_delete=models.CASCADE, related_name="product_image_overrides"
    )
    product = models.ForeignKey(
        "commerce.Product", on_delete=models.CASCADE, related_name="collab_image_overrides"
    )
    slot = models.CharField(max_length=10, choices=SLOT_CHOICES)
    image = models.ImageField(upload_to="collab/product_overrides/", null=True, blank=True)
    is_visible = models.BooleanField(default=True)

    class Meta:
        ordering = ("-updated_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("collaboration", "product", "slot"),
                name="uniq_collab_product_slot",
            )
        ]
        indexes = [models.Index(fields=("collaboration", "product"))]

    def __str__(self) -> str:
        return f"{self.collaboration_id} · {self.product_id} · {self.slot}"


class GoogleMerchantConfig(TenantScopedModel):
    """A store's Google Merchant Center connection and sync preferences.

    The owner connects with one Google sign-in (OAuth 2.0, offline access); the
    refresh token is kept encrypted and every Merchant API call mints a
    short-lived access token from it (see :mod:`commerce.google_merchant`).
    Products are written to one API-input primary data source on the chosen
    Merchant Center account.
    """

    class Status(models.TextChoices):
        DISCONNECTED = "disconnected", "Disconnected"
        # Signed in with Google, but no Merchant Center account chosen yet
        # (several were found, none were found, or listing them failed).
        PENDING = "pending", "Choose an account"
        CONNECTED = "connected", "Connected"
        ERROR = "error", "Error"

    class SyncState(models.TextChoices):
        IDLE = "idle", "Idle"
        RUNNING = "running", "Syncing"

    site = models.OneToOneField(
        "tenancy.Site", on_delete=models.CASCADE, related_name="google_merchant"
    )
    status = models.CharField(
        max_length=14, choices=Status.choices, default=Status.DISCONNECTED
    )
    merchant_id = models.CharField(max_length=64, blank=True, default="")
    # Legacy (pre-OAuth) field; no longer used for new connections.
    service_account_json = EncryptedTextField(blank=True, default="")
    auto_sync = models.BooleanField(default=True)
    default_country = models.CharField(max_length=2, default="IN")
    default_language = models.CharField(max_length=5, default="en")
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")

    # OAuth: the Google account that connected, and its tokens.
    google_email = models.EmailField(blank=True, default="")
    refresh_token = EncryptedTextField(blank=True, default="")
    access_token = EncryptedTextField(blank=True, default="")
    access_token_expires_at = models.DateTimeField(null=True, blank=True)
    granted_scopes = models.TextField(blank=True, default="")
    connected_at = models.DateTimeField(null=True, blank=True)

    # The Merchant Center account and the API data source products go into.
    account_name = models.CharField(max_length=255, blank=True, default="")
    data_source = models.CharField(max_length=255, blank=True, default="")
    feed_label = models.CharField(max_length=20, blank=True, default="")

    # Website verification + claim (Google only shows products from a
    # verified, claimed website).
    site_verification_token = models.CharField(max_length=255, blank=True, default="")
    website_url = models.URLField(max_length=500, blank=True, default="")
    website_verified = models.BooleanField(default=False)
    website_claimed = models.BooleanField(default=False)
    website_error = models.TextField(blank=True, default="")

    # Background sync progress and the latest approval tally from Google.
    sync_state = models.CharField(
        max_length=10, choices=SyncState.choices, default=SyncState.IDLE
    )
    sync_started_at = models.DateTimeField(null=True, blank=True)
    sync_progress = models.JSONField(default=dict, blank=True)
    status_summary = models.JSONField(default=dict, blank=True)
    statuses_refreshed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Google Merchant ({self.status})"

    @property
    def is_connected(self) -> bool:
        return self.status == self.Status.CONNECTED and bool(self.merchant_id and self.refresh_token)

    @property
    def effective_feed_label(self) -> str:
        return (self.feed_label or self.default_country or "IN").upper()

    @property
    def content_language(self) -> str:
        return (self.default_language or "en").split("-", 1)[0].lower()


class GoogleMerchantProductStatus(TenantScopedModel):
    """What happened to one product in Google Merchant Center.

    A product becomes one offer, or one per variation (sharing an item group).
    ``offer_ids`` remembers exactly which offers were written, so a removed
    variation, a deactivated product or a deleted one can be taken down again.
    ``payload_hash`` lets a sync skip products whose feed data hasn't changed.
    """

    class State(models.TextChoices):
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved"
        DISAPPROVED = "disapproved", "Disapproved"
        WARNING = "warning", "Approved with warnings"
        INVALID = "invalid", "Missing details"
        ERROR = "error", "Sync failed"
        REMOVED = "removed", "Removed"

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="google_merchant_statuses"
    )
    product = models.OneToOneField(
        "commerce.Product", on_delete=models.CASCADE, related_name="google_merchant_status"
    )
    state = models.CharField(max_length=12, choices=State.choices, default=State.PENDING, db_index=True)
    offer_ids = models.JSONField(default=list, blank=True)
    payload_hash = models.CharField(max_length=64, blank=True, default="")
    issues = models.JSONField(default=list, blank=True)
    last_error = models.TextField(blank=True, default="")
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=("site", "state"))]

    def __str__(self) -> str:
        return f"{self.product_id}: {self.state}"


class AnalyticsSession(TenantScopedModel):
    """One storefront visit session (privacy-light, no cross-site identity)."""

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="analytics_sessions"
    )
    session_key = models.CharField(max_length=64, db_index=True)
    # First-party visitor id (the ``ow_vid`` cookie). Lets the dashboard count
    # unique visitors and split new vs returning without any cross-site identity.
    visitor_key = models.CharField(max_length=64, blank=True, default="", db_index=True)
    is_new_visitor = models.BooleanField(default=True)
    referrer = models.URLField(blank=True, default="")
    utm_source = models.CharField(max_length=120, blank=True, default="")
    utm_medium = models.CharField(max_length=120, blank=True, default="")
    utm_campaign = models.CharField(max_length=120, blank=True, default="")
    device = models.CharField(max_length=20, blank=True, default="")
    # Geolocation, resolved once at session start from the visitor's IP via a
    # best-effort lookup (see accounts.geo / storefront.analytics). The raw IP is
    # never stored  only the derived place  so this stays privacy-light while
    # giving the dashboard an accurate "where did visitors come from".
    country = models.CharField(max_length=2, blank=True, default="")
    region = models.CharField(max_length=120, blank=True, default="")
    city = models.CharField(max_length=120, blank=True, default="")
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
