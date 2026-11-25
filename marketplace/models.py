"""OneWooz Apps: catalogue, ownership, per-store data, money and operations.

Isolation rules baked into the schema (see MARKETPLACE_PLAN.md section 5):

* References to owners are a plain UUID (``owner_ref``) plus an email snapshot,
  not a foreign key into ``accounts``. References to stores in ledgers are a
  plain UUID (``site_ref``). Only the two per-store tables
  (:class:`SiteAppConfig`, :class:`SiteAppRender`) are tenant-scoped with a real
  ``site`` key, because they *are* store data. This keeps a later move of the
  marketplace into its own database mechanical.
* Nothing in the catalogue can be deleted (``PROTECT`` everywhere). Retiring an
  app hides it from the catalogue but never takes it from owners who have it.
* Licences freeze the name, price and licence type at grant time, so editing an
  app's price in the admin never rewrites what somebody already bought.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.crypto import get_random_string

from core.fields import EncryptedTextField
from core.models import BaseModel, TenantScopedModel

from . import blocks

__all__ = [
    "MarketplaceSettings",
    "App",
    "AppScreenshot",
    "AppSettingField",
    "AppDashboardPlacement",
    "AppStorefrontPlacement",
    "AppItem",
    "CatalogRevision",
    "Installation",
    "Licence",
    "LicenceEvent",
    "SiteAppConfig",
    "SiteAppRender",
    "MarketOrder",
    "MarketOrderLine",
    "AppHealth",
    "MarketplaceAlertRecipient",
    "MarketplaceAlert",
]


#: Billing cycles an app can be sold on (``App.Pricing.CYCLES``), in display
#: order, with the licence length each one buys. Months come from
#: ``billing.cycles`` so apps and OneWooz plans count a "quarter" the same way.
CYCLE_DAYS = {"monthly": 30, "quarterly": 91, "half_yearly": 182, "yearly": 365}


class Fit(models.TextChoices):
    BOTH = "both", "D2C and B2B"
    D2C = "d2c", "D2C only"
    B2B = "b2b", "B2B only"


def _fits(value: str, business_model: str) -> bool:
    if value in ("", Fit.BOTH) or not business_model:
        return True
    bm = (business_model or "").lower()
    if bm == "both":
        return True
    return value == bm


# --------------------------------------------------------------------------
# Global switch
# --------------------------------------------------------------------------
class MarketplaceSettings(BaseModel):
    """One row: the marketplace's global switches, editable without a deploy."""

    is_enabled = models.BooleanField(
        default=True,
        help_text="Untick to close browsing, installs and purchases. Everything already "
                  "installed keeps working on every dashboard and store.",
    )
    closed_message = models.CharField(
        max_length=200, blank=True,
        default="The marketplace is closed for maintenance. Your installed apps keep working.",
    )
    reauth_window_minutes = models.PositiveSmallIntegerField(
        default=5,
        help_text="How long a re-authentication stays valid for installs and purchases. "
                  "0 = ask every single time.",
    )
    announcement = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        verbose_name = "marketplace settings"
        verbose_name_plural = "marketplace settings"

    def __str__(self) -> str:
        return "Marketplace settings"

    @classmethod
    def current(cls) -> "MarketplaceSettings":
        from django.core.cache import cache

        cached = cache.get("mkt:settings")
        if isinstance(cached, cls):
            return cached
        try:
            row = cls.objects.order_by("created_at").first()
            if row is None:
                row = cls.objects.create()
        except Exception:
            row = cls()  # table not migrated yet: defaults, unsaved
            return row
        cache.set("mkt:settings", row, 60)
        return row

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        from django.core.cache import cache

        cache.delete("mkt:settings")


# --------------------------------------------------------------------------
# Catalogue (composed in the admin)
# --------------------------------------------------------------------------
class App(BaseModel):
    """One app listed on the marketplace, composed in the admin."""

    class Kind(models.TextChoices):
        SYSTEM = "system", "System (pre-installed, cannot be uninstalled)"
        STANDARD = "standard", "Standard"

    class Pricing(models.TextChoices):
        FREE = "free", "Free"
        ONE_TIME = "one_time", "One-time payment (yours for good)"
        YEARLY = "yearly", "Yearly (renew each year)"
        MONTHLY = "monthly", "Monthly (renew each month)"
        CYCLES = "cycles", "Billing cycles (owner picks monthly / quarterly / half-yearly / yearly)"
        PLAN_INCLUDED = "plan_included", "Included with a paid subscription plan"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft (hidden)"
        PUBLISHED = "published", "Published"
        UNLISTED = "unlisted", "Unlisted (direct link only)"
        RETIRED = "retired", "Retired (hidden; keeps working for owners who have it)"

    class Category(models.TextChoices):
        DESIGN = "design", "Design"
        MARKETING = "marketing", "Marketing"
        SALES = "sales", "Sales and conversion"
        TRUST = "trust", "Trust and reviews"
        SUPPORT = "support", "Customer support"
        OPERATIONS = "operations", "Operations"
        ANALYTICS = "analytics", "Analytics and tracking"
        OTHER = "other", "Other"

    slug = models.SlugField(
        max_length=60, unique=True,
        help_text="Address of the app (marketplace.onewooz.com/apps/<slug>/). Do not change after publishing.",
    )
    name = models.CharField(max_length=80)
    tagline = models.CharField(max_length=160, blank=True, default="")
    description = models.TextField(
        blank=True, default="", help_text="Shown on the app page. Basic formatting allowed; cleaned to safe HTML.",
    )
    icon = models.ImageField(upload_to="marketplace/icons/", blank=True)
    icon_key = models.CharField(
        max_length=20, choices=blocks.ICON_CHOICES, default="grid",
        help_text="Small icon used in the dashboard sidebar.",
    )
    accent = models.CharField(max_length=7, blank=True, default="#4b2bf5", help_text="Tile colour (hex).")
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.OTHER, db_index=True)
    developer = models.CharField(max_length=80, default="OneWooz")
    module = models.CharField(
        max_length=40, blank=True, default="",
        help_text="Optional code module that gives this app extra behaviour (pages, widgets, hooks).",
    )

    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.STANDARD)
    is_default = models.BooleanField(
        default=False, help_text="Installed for every owner automatically (no purchase needed).",
    )

    pricing = models.CharField(max_length=16, choices=Pricing.choices, default=Pricing.FREE)
    price = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00"),
        help_text="Price before GST. Ignored when free or plan-included. With billing cycles and "
                  "'Same price for every cycle' ticked, this is the price per month for every cycle.",
    )
    # -- Billing cycles (pricing = "cycles") --------------------------------
    # Each is a price PER MONTH, like OneWooz plans (billing.cycles): the owner
    # pays it x the months in the cycle, up front. Blank or 0 = cycle not offered.
    cycle_same_price = models.BooleanField(
        "same price for every cycle", default=False,
        help_text="Billing cycles only: offer all four cycles at the Price above (per month). "
                  "Untick to set each cycle's own price below.",
    )
    price_monthly = models.DecimalField(
        "monthly (per month)", max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Per month, billed every month. Blank = not offered.",
    )
    price_quarterly = models.DecimalField(
        "quarterly (per month)", max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Per month, billed every 3 months (owner pays 3 x this). Blank = not offered.",
    )
    price_half_yearly = models.DecimalField(
        "half-yearly (per month)", max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Per month, billed every 6 months (owner pays 6 x this). Blank = not offered.",
    )
    price_yearly = models.DecimalField(
        "yearly (per month)", max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Per month, billed every year (owner pays 12 x this). Blank = not offered.",
    )
    currency = models.CharField(max_length=3, default="INR")
    gst_applicable = models.BooleanField(default=True, help_text="Add GST at the subscription rate at checkout.")
    trial_days = models.PositiveSmallIntegerField(
        default=0, help_text="Free trial length for paid apps (0 = no trial).",
    )

    fits = models.CharField(max_length=4, choices=Fit.choices, default=Fit.BOTH)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    order = models.PositiveSmallIntegerField(default=100, db_index=True)
    featured = models.BooleanField(default=False, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)

    support_url = models.URLField(blank=True, default="")
    privacy_url = models.URLField(blank=True, default="")

    # -- Safety -----------------------------------------------------------
    is_paused = models.BooleanField(
        default=False, db_index=True,
        help_text="Kill switch: hides this app's dashboard pages and storefront widgets everywhere "
                  "at once. Licences and owners' settings are untouched.",
    )
    paused_until = models.DateTimeField(null=True, blank=True)
    pause_reason = models.CharField(max_length=200, blank=True, default="")
    breaker_threshold = models.PositiveIntegerField(
        default=50, help_text="Errors within the window that auto-pause this app.",
    )
    breaker_window_minutes = models.PositiveSmallIntegerField(default=5)
    breaker_pause_minutes = models.PositiveSmallIntegerField(
        default=15, help_text="First auto-pause length; doubles each time it trips again.",
    )
    auto_resume = models.BooleanField(default=True, help_text="Try again automatically after a pause.")
    allowed_script_origins = models.TextField(
        blank=True, default="",
        help_text="Tracking-pixel widgets only: https origins this app may load scripts from, one per "
                  "line (e.g. https://www.googletagmanager.com). Owners can never add origins.",
    )

    # -- AI provider (apps that call an AI model, e.g. Magic SEO) ------------
    # Set per app in the admin, so each AI app can run on its own key and
    # models. Blank = the server's OPENAI_* settings. Read through
    # marketplace.ai.credentials(); never sent to the browser or logged.
    ai_provider = models.CharField(
        "AI provider", max_length=20, choices=(("openai", "OpenAI (ChatGPT)"),), default="openai",
    )
    ai_api_key = EncryptedTextField(
        "AI API key", blank=True, default="",
        help_text="Encrypted at rest and never shown again after saving. Blank = the server's OPENAI_API_KEY.",
    )
    ai_text_model = models.CharField(
        "text model", max_length=120, blank=True, default="",
        help_text="Model ID used for writing text (e.g. gpt-4.1). Blank = the server default.",
    )
    ai_image_model = models.CharField(
        "image model", max_length=120, blank=True, default="",
        help_text="Model ID used for images (e.g. gpt-image-1). Blank = the server default.",
    )

    config_version = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        ordering = ("order", "name")
        indexes = [models.Index(fields=("status", "order"))]

    def __str__(self) -> str:
        return self.name

    # -- Helpers ----------------------------------------------------------
    @property
    def is_system(self) -> bool:
        return self.kind == self.Kind.SYSTEM

    @property
    def is_paid(self) -> bool:
        if self.pricing == self.Pricing.CYCLES:
            return bool(self.cycle_options())
        return self.pricing in (self.Pricing.ONE_TIME, self.Pricing.YEARLY, self.Pricing.MONTHLY) and (
            self.price or Decimal("0")
        ) > 0

    @property
    def is_periodic(self) -> bool:
        """Bought for a period that ends (and is renewed by buying again)."""
        return self.pricing in (self.Pricing.YEARLY, self.Pricing.MONTHLY, self.Pricing.CYCLES)

    @property
    def is_listed(self) -> bool:
        return self.status == self.Status.PUBLISHED

    @property
    def is_visible_by_link(self) -> bool:
        return self.status in (self.Status.PUBLISHED, self.Status.UNLISTED)

    @property
    def period_days(self) -> int | None:
        """Licence length of a single-period app. ``None`` for billing cycles,
        where the owner's chosen cycle decides it (see :meth:`cycle_option`)."""
        return {self.Pricing.YEARLY: 365, self.Pricing.MONTHLY: 30}.get(self.pricing)

    # -- Billing cycles ---------------------------------------------------
    def cycle_per_month(self, key: str) -> Decimal | None:
        """The per-month price of one cycle, or ``None`` when it is not offered."""
        if key not in CYCLE_DAYS:
            return None
        value = self.price if self.cycle_same_price else getattr(self, f"price_{key}", None)
        if value is None or Decimal(value) <= 0:
            return None
        return Decimal(value)

    def cycle_options(self) -> list[dict]:
        """The cycles this app is sold on, in display order:
        ``[{key, label, months, days, per_month, total, save_pct, billed}]``.
        ``total`` is the amount billed up front (before GST); ``save_pct`` is
        the saving against the dearest per-month rate offered."""
        if self.pricing != self.Pricing.CYCLES:
            return []
        from billing import cycles

        out = []
        for key in cycles.ORDER:
            per_month = self.cycle_per_month(key)
            if per_month is None:
                continue
            months = cycles.MONTHS[key]
            out.append({
                "key": key,
                "label": cycles.DURATION_LABELS[key],
                "months": months,
                "days": CYCLE_DAYS[key],
                "per_month": cycles.money(per_month),
                "total": cycles.money(per_month * months),
                "billed": cycles.CYCLE_LABELS[key],
                "save_pct": 0,
            })
        top = max((o["per_month"] for o in out), default=Decimal("0"))
        for o in out:
            if top > 0 and o["per_month"] < top:
                o["save_pct"] = int(((top - o["per_month"]) * 100 / top).to_integral_value())
        return out

    def cycle_option(self, key: str) -> dict | None:
        return next((o for o in self.cycle_options() if o["key"] == key), None)

    def default_cycle(self) -> str:
        """The cycle preselected for the owner: monthly when offered, else the
        shortest one offered."""
        opts = self.cycle_options()
        return opts[0]["key"] if opts else ""

    def fits_model(self, business_model: str) -> bool:
        return _fits(self.fits, business_model)

    def script_origins(self) -> list[str]:
        out = []
        for line in (self.allowed_script_origins or "").splitlines():
            line = line.strip().rstrip("/")
            if line.startswith("https://") and " " not in line and len(line) < 200:
                out.append(line)
        return out

    def pricing_label(self) -> str:
        if self.pricing == self.Pricing.PLAN_INCLUDED:
            return "Included with paid plans"
        if not self.is_paid:
            return "Included"
        if self.pricing == self.Pricing.CYCLES:
            opts = self.cycle_options()
            low = min(o["per_month"] for o in opts)
            many = len({o["per_month"] for o in opts}) > 1
            return f"{'From ' if many else ''}₹{low:,.0f} / month"
        suffix = {self.Pricing.YEARLY: " / year", self.Pricing.MONTHLY: " / month"}.get(self.pricing, " one-time")
        return f"₹{self.price:,.0f}{suffix}"

    def save(self, *args, **kwargs):
        if self.status == self.Status.PUBLISHED and not self.published_at:
            self.published_at = timezone.now()
        if self.pk and not kwargs.get("update_fields"):
            self.config_version = (self.config_version or 0) + 1
        super().save(*args, **kwargs)


class AppScreenshot(BaseModel):
    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="screenshots")
    image = models.ImageField(upload_to="marketplace/screens/")
    caption = models.CharField(max_length=120, blank=True, default="")
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("order", "created_at")


class AppSettingField(BaseModel):
    """One field of the form the owner fills in for this app (the form builder)."""

    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="settings_fields")
    key = models.SlugField(
        max_length=40, help_text="Lower-case identifier, e.g. text, link_url, tracking_id. "
                                 "Widgets read values by these keys.",
    )
    label = models.CharField(max_length=80)
    help_text = models.CharField(max_length=200, blank=True, default="")
    field_type = models.CharField(max_length=20, choices=blocks.field_type_choices(), default="text")
    required = models.BooleanField(default=False)
    default = models.CharField(max_length=500, blank=True, default="")
    choices = models.TextField(blank=True, default="", help_text="Dropdown / checkboxes: one per line as value|Label.")
    min_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    max_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    max_length = models.PositiveIntegerField(null=True, blank=True)
    group = models.CharField(max_length=60, blank=True, default="", help_text="Optional section heading.")
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("order", "created_at")
        constraints = [models.UniqueConstraint(fields=("app", "key"), name="mkt_field_unique_key")]

    def __str__(self) -> str:
        return f"{self.app.name}: {self.label}"

    def parsed_choices(self) -> list[tuple[str, str]]:
        out = []
        for line in (self.choices or "").splitlines():
            line = line.strip()
            if not line:
                continue
            value, _, label = line.partition("|")
            out.append((value.strip()[:80], (label or value).strip()[:120]))
        return out


class AppDashboardPlacement(BaseModel):
    """Where the app appears in the owner dashboard, and what opens there."""

    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="dashboard_placements")
    point = models.CharField(max_length=20, choices=list(blocks.PLACEMENT_POINTS.items()), default="sidebar")
    nav_group = models.CharField(
        max_length=30, choices=blocks.NAV_GROUP_CHOICES, default="Apps",
        help_text="Sidebar only: which group the link joins.",
    )
    order = models.PositiveSmallIntegerField(default=100, help_text="Lower comes first within the group / area.")
    label = models.CharField(max_length=60, help_text="Link or card title.")
    summary = models.CharField(max_length=160, blank=True, default="", help_text="Card text (cards and panels).")
    icon_key = models.CharField(max_length=20, choices=blocks.ICON_CHOICES, default="grid")
    page_kind = models.CharField(
        max_length=20, choices=list(blocks.PAGE_KINDS.items()), default="settings_form",
    )
    page_key = models.CharField(max_length=40, blank=True, default="", help_text="Module page key (module pages only).")
    external_url = models.URLField(blank=True, default="", help_text="External link / embedded page (https).")
    body = models.TextField(blank=True, default="", help_text="Information page text.")
    audience = models.CharField(max_length=4, choices=Fit.choices, default=Fit.BOTH)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("order", "created_at")

    def __str__(self) -> str:
        return f"{self.app.name}: {self.label} ({self.get_point_display()})"


class AppStorefrontPlacement(BaseModel):
    """A widget the app may place on storefronts, and where."""

    class Devices(models.TextChoices):
        ALL = "all", "All devices"
        MOBILE = "mobile", "Phones only"
        DESKTOP = "desktop", "Desktop only"

    app = models.ForeignKey(App, on_delete=models.CASCADE, related_name="storefront_placements")
    label = models.CharField(max_length=80, help_text="What the owner sees, e.g. 'Top bar'.")
    widget_type = models.CharField(max_length=20, choices=blocks.widget_type_choices(), default="announcement")
    allowed_slots = models.JSONField(
        default=list, blank=True,
        help_text="Slot keys the widget may use (empty = every slot the widget type allows).",
    )
    default_slot = models.CharField(max_length=40, choices=blocks.slot_choices(), default="header.above")
    owner_can_choose_slot = models.BooleanField(default=True)
    pages = models.JSONField(default=list, blank=True, help_text='Page kinds, e.g. ["all"] or ["home", "pdp"].')
    devices = models.CharField(max_length=8, choices=Devices.choices, default=Devices.ALL)
    options = models.JSONField(
        default=dict, blank=True,
        help_text="Fixed values / widget options (staff only), e.g. {\"bg_color\": \"#111\"}.",
    )
    order = models.PositiveSmallIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("order", "created_at")

    def __str__(self) -> str:
        return f"{self.app.name}: {self.label}"

    def slots(self) -> list[str]:
        allowed = blocks.allowed_slots_for(self.widget_type)
        chosen = [s for s in (self.allowed_slots or []) if s in allowed]
        return chosen or list(allowed)

    def page_list(self) -> list[str]:
        pages = [p for p in (self.pages or []) if p in blocks.STOREFRONT_PAGES]
        return pages or ["all"]


class AppItem(BaseModel):
    """Something sold inside an app; for OneWooz Templates, a storefront design."""

    class Pricing(models.TextChoices):
        FREE = "free", "Free"
        PRICED = "priced", "Priced"

    class LicenceType(models.TextChoices):
        PERMANENT = "permanent", "Permanent (bought once)"
        YEARLY = "yearly", "Yearly (expires a year after purchase)"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft (hidden)"
        PUBLISHED = "published", "Published"
        RETIRED = "retired", "Retired (hidden; owners keep it)"

    app = models.ForeignKey(App, on_delete=models.PROTECT, related_name="items")
    key = models.CharField(max_length=40, help_text="Code identifier (for designs: the template key).")
    name = models.CharField(max_length=80)
    tagline = models.CharField(max_length=160, blank=True, default="")
    description = models.TextField(blank=True, default="")
    thumbnail = models.ImageField(upload_to="marketplace/items/", blank=True)
    preview_url = models.URLField(blank=True, default="", help_text="Live demo link (optional).")
    fits = models.CharField(max_length=4, choices=Fit.choices, default=Fit.BOTH)
    industries = models.CharField(max_length=200, blank=True, default="", help_text="Comma-separated tags.")
    pricing = models.CharField(max_length=8, choices=Pricing.choices, default=Pricing.FREE)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    licence_type = models.CharField(max_length=10, choices=LicenceType.choices, default=LicenceType.PERMANENT)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    is_system_default = models.BooleanField(
        default=False, editable=False,
        help_text="Everyone has it; it can never be priced, hidden or retired (the safe fallback).",
    )
    order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        ordering = ("order", "name")
        constraints = [models.UniqueConstraint(fields=("app", "key"), name="mkt_item_unique_key")]

    def __str__(self) -> str:
        return f"{self.app.name}: {self.name}"

    @property
    def is_priced(self) -> bool:
        return self.pricing == self.Pricing.PRICED and (self.price or Decimal("0")) > 0 and not self.is_system_default

    @property
    def is_yearly(self) -> bool:
        return self.licence_type == self.LicenceType.YEARLY

    @property
    def is_listed(self) -> bool:
        return self.status == self.Status.PUBLISHED or self.is_system_default

    def fits_model(self, business_model: str) -> bool:
        return _fits(self.fits, business_model)

    def tags(self) -> list[str]:
        return [t.strip() for t in (self.industries or "").split(",") if t.strip()]

    def pricing_label(self) -> str:
        if not self.is_priced:
            return "Included"
        return f"₹{self.price:,.0f}" + (" / year" if self.is_yearly else " one-time")

    def save(self, *args, **kwargs):
        if self.is_system_default:
            # The safe fallback can never be priced, hidden or retired.
            self.pricing = self.Pricing.FREE
            self.price = Decimal("0.00")
            self.status = self.Status.PUBLISHED
        super().save(*args, **kwargs)


class CatalogRevision(BaseModel):
    """Before/after of every admin save to the catalogue (history + audit)."""

    app = models.ForeignKey(App, on_delete=models.SET_NULL, null=True, blank=True, related_name="revisions")
    app_slug = models.CharField(max_length=60, db_index=True)
    object_kind = models.CharField(max_length=40)
    object_id = models.CharField(max_length=64)
    actor_id = models.CharField(max_length=64, blank=True, default="")
    actor_label = models.CharField(max_length=255, blank=True, default="")
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("app_slug", "-created_at"))]


# --------------------------------------------------------------------------
# Ownership
# --------------------------------------------------------------------------
class Installation(BaseModel):
    """An app installed on an owner's dashboard (account level, every store)."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DISABLED = "disabled", "Disabled by staff"
        UNINSTALLED = "uninstalled", "Uninstalled"

    class Source(models.TextChoices):
        DEFAULT = "default", "Pre-installed"
        OWNER = "owner", "Installed by the owner"
        ADMIN = "admin", "Installed by staff"
        PLAN = "plan", "Included with plan"

    owner_ref = models.UUIDField(db_index=True)
    owner_email = models.EmailField()
    app = models.ForeignKey(App, on_delete=models.PROTECT, related_name="installations")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.OWNER)
    installed_at = models.DateTimeField(default=timezone.now)
    uninstalled_at = models.DateTimeField(null=True, blank=True)
    reauth_method = models.CharField(max_length=20, blank=True, default="", help_text="How the owner re-authenticated.")
    reauth_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("owner_ref", "app"), name="mkt_install_unique")]
        indexes = [models.Index(fields=("owner_ref", "status"))]

    def __str__(self) -> str:
        return f"{self.app.name} · {self.owner_email}"


class Licence(BaseModel):
    """The single answer to: may this owner use this app / item, why, until when."""

    class Source(models.TextChoices):
        DEFAULT = "default", "Default"
        FREE_ADD = "free_add", "Added free"
        PURCHASE = "purchase", "Purchase"
        TRIAL = "trial", "Trial"
        PLAN = "plan", "Included with plan"
        ADMIN_GRANT = "admin_grant", "Granted by staff"
        GRANDFATHERED = "grandfathered", "Grandfathered (in use before the marketplace)"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        REVOKED = "revoked", "Revoked"

    owner_ref = models.UUIDField(db_index=True)
    owner_email = models.EmailField(db_index=True)
    app = models.ForeignKey(App, on_delete=models.PROTECT, related_name="licences")
    item = models.ForeignKey(AppItem, on_delete=models.PROTECT, null=True, blank=True, related_name="licences")
    item_key = models.CharField(max_length=40, blank=True, default="")
    source = models.CharField(max_length=14, choices=Source.choices)
    site_ref = models.UUIDField(
        null=True, blank=True, help_text="Grandfathered licences: valid for this one store only.",
    )
    starts_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True, help_text="Empty = permanent.")
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    order_line = models.OneToOneField(
        "MarketOrderLine", on_delete=models.PROTECT, null=True, blank=True, related_name="licence",
    )
    legacy_ref = models.CharField(max_length=64, blank=True, default="", db_index=True)
    name_snapshot = models.CharField(max_length=120, blank=True, default="")
    price_snapshot = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    licence_type_snapshot = models.CharField(max_length=20, blank=True, default="")
    revoked_reason = models.CharField(max_length=300, blank=True, default="")
    revoked_by = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=("owner_ref", "status", "app")),
            models.Index(fields=("status", "expires_at")),
        ]

    def __str__(self) -> str:
        return f"{self.name_snapshot or self.app.name} · {self.owner_email} ({self.get_status_display()})"

    @property
    def is_current(self) -> bool:
        if self.status != self.Status.ACTIVE:
            return False
        return self.expires_at is None or self.expires_at > timezone.now()


class LicenceEvent(BaseModel):
    """Append-only history of every licence change."""

    licence = models.ForeignKey(Licence, on_delete=models.PROTECT, related_name="events")
    event = models.CharField(max_length=20, db_index=True)
    actor_kind = models.CharField(max_length=20, default="system")
    actor_label = models.CharField(max_length=255, blank=True, default="")
    reason = models.CharField(max_length=300, blank=True, default="")
    context = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-created_at",)


# --------------------------------------------------------------------------
# Per-store app data (tenant scoped)
# --------------------------------------------------------------------------
class SiteAppConfig(TenantScopedModel):
    """An owner's settings for one app on one store."""

    site = models.ForeignKey("tenancy.Site", on_delete=models.CASCADE, related_name="app_configs")
    app = models.ForeignKey(App, on_delete=models.PROTECT, related_name="site_configs")
    values = models.JSONField(default=dict, blank=True)
    secrets = models.JSONField(
        default=dict, blank=True, help_text="Encrypted values of 'secret' fields (never rendered).",
    )
    slot_choices = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=False)
    updated_by_ref = models.UUIDField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("site", "app"), name="mkt_siteconfig_unique")]


class SiteAppRender(TenantScopedModel):
    """The compiled storefront output for one store. The only marketplace data a
    storefront ever reads."""

    site = models.OneToOneField("tenancy.Site", on_delete=models.CASCADE, related_name="app_render")
    bundle = models.JSONField(default=dict, blank=True)
    script_origins = models.JSONField(default=list, blank=True)
    compiled_from = models.JSONField(default=dict, blank=True)
    compiled_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")


# --------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------
class MarketOrder(BaseModel):
    """One marketplace checkout through OneWooz's platform Cashfree account."""

    class Status(models.TextChoices):
        CREATED = "created", "Created"
        PENDING = "pending", "Pending payment"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"

    number = models.CharField(max_length=20, unique=True, editable=False)
    owner_ref = models.UUIDField(db_index=True)
    owner_email = models.EmailField(db_index=True)
    site_ref = models.UUIDField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CREATED, db_index=True)
    currency = models.CharField(max_length=3, default="INR")
    base_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    gst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    cf_order_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    cf_payment_id = models.CharField(max_length=64, blank=True, default="")
    gateway_status = models.CharField(max_length=40, blank=True, default="")
    paid_at = models.DateTimeField(null=True, blank=True)
    reauth_method = models.CharField(max_length=20, blank=True, default="")

    class Meta:
        indexes = [models.Index(fields=("status", "-created_at"))]

    def __str__(self) -> str:
        return f"{self.number} · {self.owner_email}"

    @property
    def is_paid(self) -> bool:
        return self.status == self.Status.PAID

    def save(self, *args, **kwargs):
        if not self.number:
            for _ in range(10):
                candidate = "OWM" + get_random_string(9, "0123456789")
                if not MarketOrder.objects.filter(number=candidate).exists():
                    self.number = candidate
                    break
            else:
                self.number = "OWM" + get_random_string(12, "0123456789")
        super().save(*args, **kwargs)


class MarketOrderLine(BaseModel):
    order = models.ForeignKey(MarketOrder, on_delete=models.CASCADE, related_name="lines")
    app = models.ForeignKey(App, on_delete=models.PROTECT, related_name="order_lines")
    item = models.ForeignKey(AppItem, on_delete=models.PROTECT, null=True, blank=True, related_name="order_lines")
    description = models.CharField(max_length=160)
    licence_type = models.CharField(max_length=20)
    period_days = models.PositiveIntegerField(null=True, blank=True)
    base_amount = models.DecimalField(max_digits=10, decimal_places=2)


# --------------------------------------------------------------------------
# Operations
# --------------------------------------------------------------------------
class AppHealth(BaseModel):
    """Circuit-breaker state for one app."""

    class State(models.TextChoices):
        CLOSED = "closed", "Healthy"
        OPEN = "open", "Paused by the breaker"
        HALF_OPEN = "half_open", "Trying again"

    app = models.OneToOneField(App, on_delete=models.CASCADE, related_name="health")
    state = models.CharField(max_length=10, choices=State.choices, default=State.CLOSED)
    errors_total = models.PositiveIntegerField(default=0)
    last_error_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    last_error_context = models.JSONField(default=dict, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    open_count = models.PositiveIntegerField(default=0)
    current_pause_minutes = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name_plural = "app health"


class MarketplaceAlertRecipient(BaseModel):
    """Who is told when an app is paused, a payment mismatches, etc."""

    class AlertType(models.TextChoices):
        APP_PAUSED = "app_paused", "App auto-paused"
        APP_RESUMED = "app_resumed", "App resumed"
        COMPILE_FAILED = "compile_failed", "Storefront compile failures"
        PAYMENT_MISMATCH = "payment_mismatch", "Payment / licence mismatch"
        HOOK_FAILED = "hook_failed", "Module hook failures"
        INSTALL_SPIKE = "install_spike", "Unusual install spike"

    name = models.CharField(max_length=80)
    email = models.EmailField()
    staff_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        limit_choices_to={"is_staff": True}, related_name="+",
        help_text="Optional OneWooz staff account (alerts also listed in the admin).",
    )
    phone = models.CharField(max_length=20, blank=True, default="", help_text="For a future SMS channel.")
    is_active = models.BooleanField(default=True)
    alert_types = models.JSONField(default=list, blank=True, help_text="Empty = every alert type.")
    apps = models.ManyToManyField(App, blank=True, related_name="+", help_text="Empty = every app.")
    min_interval_minutes = models.PositiveSmallIntegerField(
        default=30, help_text="Do not repeat the same alert about the same app more often than this.",
    )

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return f"{self.name} <{self.email}>"

    def wants(self, kind: str, app) -> bool:
        if not self.is_active:
            return False
        types = [t for t in (self.alert_types or []) if isinstance(t, str)]
        if types and kind not in types:
            return False
        if app is not None and self.pk:
            ids = set(self.apps.values_list("pk", flat=True))
            if ids and app.pk not in ids:
                return False
        return True


class MarketplaceAlert(BaseModel):
    kind = models.CharField(max_length=20, db_index=True)
    app = models.ForeignKey(App, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    subject = models.CharField(max_length=200)
    body = models.TextField(blank=True, default="")
    recipients = models.TextField(blank=True, default="")
    delivered = models.BooleanField(default=False)
    error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ("-created_at",)


class AppLog(BaseModel):
    """One line of an app's working log: what it did, for which store, and how
    it went (Magic SEO runs, batches, images; breaker errors; pauses).

    Written through :func:`marketplace.applog.log`, which never raises and
    keeps lines short. Read in the marketplace console (Logs) and the Django
    admin. Lines older than ``APP_LOG_RETENTION_DAYS`` are purged daily.
    Platform-level (staff only), so a plain model with an optional store.
    """

    class Level(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        ERROR = "error", "Error"

    app_slug = models.CharField(max_length=60, db_index=True)
    level = models.CharField(max_length=10, choices=Level.choices, default=Level.INFO, db_index=True)
    #: Short machine name, e.g. "run.started", "image.failed".
    event = models.CharField(max_length=60)
    message = models.CharField(max_length=500)
    site = models.ForeignKey("tenancy.Site", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    #: Store name at the time, kept if the store is deleted.
    site_label = models.CharField(max_length=160, blank=True, default="")
    #: Groups the lines of one job (a Magic SEO run id, say).
    run_ref = models.CharField(max_length=40, blank=True, default="", db_index=True)
    context = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "app log line"
        verbose_name_plural = "app logs"
        # The console lists newest first, per app and per level: these indexes
        # serve those sorts (the rows carry JSON, so MySQL must never sort them).
        indexes = [
            models.Index(fields=("created_at",), name="mkt_applog_created_idx"),
            models.Index(fields=("app_slug", "created_at"), name="mkt_applog_app_created_idx"),
            models.Index(fields=("level", "created_at"), name="mkt_applog_level_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.app_slug} {self.event}: {self.message[:60]}"
