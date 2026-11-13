"""The tenant root.

A :class:`Site` is one store an owner runs on OneWooz. Every commerce record 
products, orders, customers, coupons  belongs to exactly one Site through
``core.models.TenantScopedModel``, and a queryset over those records refuses to
evaluate until it is narrowed with ``.for_site(site)``.

The reference (1matrix ``website.Website``) folded the entire rendered-site
content blob, SEO, theme and a credit-limit check into this one model. Here the
content schema is a separate Phase-2 concern (versioned Pydantic); Site stays a
small, structural anchor: who owns it, what kind of store it is, and whether it
is live.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from core.ids import uuid7
from core.models import BaseModel, SoftDeleteMixin

__all__ = ["Site", "BusinessCategory", "BusinessSubCategory", "ReservedSubdomain"]


def signature_upload_to(instance, filename: str) -> str:
    """Storage path for a store's authorised-signatory signature.

    A random name per upload (never the shopper-visible original filename), so
    a replaced signature never collides with, or is cached as, the old one.
    """
    import os
    import uuid

    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        ext = ".png"
    return "signatures/%s/%s%s" % (instance.pk or "new", uuid.uuid4().hex, ext)


class ReservedSubdomain(BaseModel):
    """A subdomain label no store may use (e.g. ``web``, ``app``, ``admin``).

    A store's ``slug`` becomes its public subdomain under ``.onewooz.site``, so a
    slug that collides with a platform host or a name we want to keep free would
    be a problem. Operators add keywords here from the Django admin; every store
    slug (and every UGC collaboration subdomain) is checked against this table
    plus a built-in set (see :mod:`tenancy.reserved`). Global, not tenant-scoped.
    """

    keyword = models.SlugField(
        max_length=63,
        unique=True,
        help_text="The subdomain label to block, e.g. “web”. Stored lower-case.",
    )
    note = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Optional reason, for your own reference.",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("keyword",)
        verbose_name = "reserved subdomain"
        verbose_name_plural = "reserved subdomains"

    def __str__(self) -> str:
        return self.keyword

    def save(self, *args, **kwargs):
        # Subdomains are compared case-insensitively; store the canonical form so
        # the unique constraint and the lookup agree.
        self.keyword = (self.keyword or "").strip().lower()
        super().save(*args, **kwargs)


class BusinessCategory(BaseModel):
    """A platform-wide business category a store belongs to.

    Global (not tenant-scoped) taxonomy managed from the admin, modelled on the
    1matrix fee-calculator ``Category``/``SubCategory`` pair. Every store picks a
    category and one of its subcategories at creation, so the storefront always
    has a classification.
    """

    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("order", "name")
        verbose_name = "business category"
        verbose_name_plural = "business categories"

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name) or "category"
            candidate, suffix = base, 2
            existing = BusinessCategory.objects.all()
            if self.pk:
                existing = existing.exclude(pk=self.pk)
            while existing.filter(slug=candidate).exists():
                candidate = f"{base}-{suffix}"
                suffix += 1
            self.slug = candidate
        super().save(*args, **kwargs)


class BusinessSubCategory(BaseModel):
    """A subcategory under a :class:`BusinessCategory`."""

    category = models.ForeignKey(
        "tenancy.BusinessCategory",
        on_delete=models.CASCADE,
        related_name="subcategories",
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("order", "name")
        verbose_name = "business subcategory"
        verbose_name_plural = "business subcategories"
        constraints = [
            models.UniqueConstraint(
                fields=("category", "name"), name="uniq_subcategory_name_per_category"
            )
        ]

    def __str__(self) -> str:
        return f"{self.category.name} → {self.name}"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name) or "subcategory"
        super().save(*args, **kwargs)


class Site(BaseModel, SoftDeleteMixin):
    """One store. The unit of tenancy."""

    class BusinessModel(models.TextChoices):
        # A store sells to end consumers, to businesses, or to both. The choice
        # changes which management pages appear and how several of them behave
        # (B2B adds order-request approval and quantity pricing; D2C adds
        # reviews, wishlists and abandoned-cart reminders).
        D2C = "d2c", "Direct to consumer"
        B2B = "b2b", "Business to business"
        BOTH = "both", "Both"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        LIVE = "live", "Live"
        SUSPENDED = "suspended", "Suspended"

    class Theme(models.TextChoices):
        # Two storefront designs ported from the reference. ``template1`` is the
        # B2B (catalogue) storefront, ``template2`` the D2C (modern) one. The
        # choice is decided at creation from the business model but stored
        # explicitly so an owner can switch designs later without changing what
        # they sell.
        TEMPLATE1 = "template1", "Catalogue storefront (B2B)"
        TEMPLATE2 = "template2", "Modern storefront (D2C)"

    class Currency(models.TextChoices):
        INR = "INR", "Indian rupee"
        USD = "USD", "US dollar"
        EUR = "EUR", "Euro"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sites",
    )
    name = models.CharField(max_length=120, default="My store")
    # The subdomain label under ``.onewooz.site``. Unique across the platform,
    # unguessable ids elsewhere mean this is the one human-facing handle.
    slug = models.SlugField(max_length=100, unique=True, blank=True)

    business_model = models.CharField(
        max_length=8,
        choices=BusinessModel.choices,
        default=BusinessModel.D2C,
        db_index=True,
    )

    # The platform-wide business classification chosen at store creation. Kept
    # nullable + SET_NULL so retiring a taxonomy entry never deletes a store,
    # and so existing stores created before the taxonomy stay valid.
    business_category = models.ForeignKey(
        "tenancy.BusinessCategory",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sites",
    )
    business_subcategory = models.ForeignKey(
        "tenancy.BusinessSubCategory",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sites",
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True
    )

    # Which storefront design renders publicly. Set at creation from the
    # business model; changeable later.
    theme = models.CharField(
        max_length=16, choices=Theme.choices, default=Theme.TEMPLATE1
    )

    # The rendered-site CMS blob the storefront templates read
    # (``site.content.*``): copy, colours, hero banners, testimonials, FAQs,
    # SEO, policy HTML, footer, social links, section toggles. The reference
    # kept this on ``Website.content``; kept here as JSON so the ported
    # templates and the section editor share one structure.
    content = models.JSONField(default=dict, blank=True)

    # Owner opt-in to inject their own tracking scripts (Meta Pixel, GA) into
    # the storefront <head>. Off by default; when on, the storefront CSP for
    # this host is relaxed to allow inline scripts. Never affects the dashboard.
    allows_custom_scripts = models.BooleanField(default=False)

    tagline = models.CharField(max_length=200, blank=True, default="")
    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.INR)
    country = models.CharField(max_length=2, default="IN")

    # Owner-facing contact details surfaced on the storefront and invoices.
    support_email = models.EmailField(blank=True, default="")
    support_phone = models.CharField(max_length=20, blank=True, default="")

    # Business / tax registration (Store settings → "Business & tax details").
    # All optional; stored normalised (upper-case, no spaces) and validated in
    # commerce.forms.SiteForm.
    legal_name = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Registered business name, as on the GST / PAN certificate.",
    )
    gstin = models.CharField(
        "GSTIN", max_length=15, blank=True, default="",
        help_text="15-character GST Identification Number.",
    )
    pan = models.CharField(
        "PAN", max_length=10, blank=True, default="",
        help_text="10-character Permanent Account Number of the business.",
    )
    msme_number = models.CharField(
        "MSME / Udyam registration number", max_length=19, blank=True, default="",
        help_text="Udyam Registration Number, e.g. UDYAM-MH-01-0012345.",
    )
    # Printed in the "Authorized Signatory" box of the tax invoice that ships
    # with every label (commerce.label_pdf). Uploaded on Store settings.
    authorized_signature = models.ImageField(
        "Authorized signatory signature", upload_to=signature_upload_to,
        blank=True, default="", max_length=255,
        help_text="Signature image printed on the tax invoice.",
    )

    # A custom domain, once verified, is served in addition to the subdomain.
    custom_domain = models.CharField(max_length=255, blank=True, default="")
    custom_domain_verified = models.BooleanField(default=False)

    # D2C-only: reminder emails for carts left abandoned. Off by default so a
    # store never mails customers without the owner opting in.
    abandoned_cart_emails_enabled = models.BooleanField(default=False)

    # Maintenance mode holds the public storefront behind a notice; the
    # dashboard is unaffected.
    maintenance_mode = models.BooleanField(default=False)

    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("owner", "-created_at")),
            models.Index(fields=("status",)),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._unique_slug()
        super().save(*args, **kwargs)

    def _unique_slug(self) -> str:
        from .reserved import is_reserved

        base = slugify(self.name) or "store"
        candidate = base
        suffix = 2
        # Uniqueness is platform-wide (the slug is the public subdomain), so we
        # check across every tenant deliberately, not per-owner. A reserved
        # label (a platform host or an admin-blocked keyword) is treated like a
        # taken slug, so a store named e.g. "Web" gets "web-2", never "web".
        existing = Site.objects.all()
        if self.pk:
            existing = existing.exclude(pk=self.pk)
        while existing.filter(slug=candidate).exists() or is_reserved(candidate):
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    # -- convenience ------------------------------------------------------

    @property
    def is_b2b(self) -> bool:
        return self.business_model in (self.BusinessModel.B2B, self.BusinessModel.BOTH)

    @property
    def is_d2c(self) -> bool:
        return self.business_model in (self.BusinessModel.D2C, self.BusinessModel.BOTH)

    @property
    def is_live(self) -> bool:
        return self.status == self.Status.LIVE

    @property
    def template_dir(self) -> str:
        """The template subfolder this site renders from, e.g. ``template1``."""
        return self.theme or self.Theme.TEMPLATE1

    @property
    def customer_from_address(self) -> str:
        """This store's branded customer sender address, e.g.
        ``jackandjones@via.onewooz.com``. See :mod:`tenancy.email`."""
        from tenancy.email import store_from_address

        return store_from_address(self)

    @property
    def customer_from_header(self) -> str:
        """The full ``From`` header for mail this store sends its customers:
        the store name in front of :attr:`customer_from_address`."""
        from tenancy.email import store_from_header

        return store_from_header(self)

    @classmethod
    def default_theme_for(cls, business_model: str) -> str:
        """The storefront design a business model gets by default.

        B2B → the catalogue storefront (template1); D2C → the modern
        storefront (template2); Both defaults to the D2C design.
        """
        return (
            cls.Theme.TEMPLATE1
            if business_model == cls.BusinessModel.B2B
            else cls.Theme.TEMPLATE2
        )

    def get_custom_policy_pages(self) -> list[dict]:
        """The owner's published custom policies (Edit Content → Policies, up to
        three), each ``{"n", "key", "title", "slug", "content"}``, served at
        ``/policies/<slug>/``. Read by the storefront footers and policy tabs."""
        from storefront.policies import custom_policies

        try:
            return custom_policies(self.content or {})
        except Exception:
            return []

    def storefront_url(self, request=None) -> str:
        """Absolute URL of this store's public homepage.

        In single-host development this is ``/s/<slug>/``; in production it is
        ``https://<slug>.onewooz.site/``. Resolving through the storefront
        origin keeps the editor preview and dashboard links correct in both.
        """
        from core import links

        return links.storefront_origin(self.slug, request) + "/"

    def publish(self, *, save: bool = True) -> None:
        self.status = self.Status.LIVE
        if not self.published_at:
            self.published_at = timezone.now()
        if save:
            self.save(update_fields=["status", "published_at", "updated_at"])
