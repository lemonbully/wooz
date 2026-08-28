"""Catalogue: categories, products, product images and reviews."""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.text import slugify

from core.models import SoftDeleteMixin, TenantScopedModel


class Category(TenantScopedModel, SoftDeleteMixin):
    """A product category. May nest one level via ``parent``."""

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="categories"
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="children",
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, blank=True)
    description = models.TextField(blank=True, default="")
    image = models.ImageField(upload_to="categories/", blank=True, null=True)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    # At most five may be surfaced on the storefront home; the cap is enforced
    # in the view, not the schema, so an import can exceed it transiently.
    show_on_homepage = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ("order", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("site", "slug"), name="uniq_category_slug_per_site"
            )
        ]
        indexes = [models.Index(fields=("site", "is_active"))]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._unique_slug()
        super().save(*args, **kwargs)

    def _unique_slug(self) -> str:
        base = slugify(self.name) or "category"
        candidate = base
        suffix = 2
        siblings = Category.all_objects.filter(site_id=self.site_id)
        if self.pk:
            siblings = siblings.exclude(pk=self.pk)
        while siblings.filter(slug=candidate).exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate


class Product(TenantScopedModel, SoftDeleteMixin):
    """A sellable product.

    Faithful to the reference ``WebsiteProduct``: simple products carry a
    price/MRP/SKU/inventory directly, while variant products describe their
    axes in ``variants`` and per-combination data (sku, price, stock, images)
    in ``variant_combinations``. Physical, compliance and D2C-presentation
    details ride along as documented fields and JSON blobs.
    """

    class ReturnType(models.TextChoices):
        RETURNABLE = "returnable", "Returnable"
        NON_RETURNABLE = "non_returnable", "Non-returnable"
        REPLACEMENT = "replacement", "Replacement only"

    class LengthUnit(models.TextChoices):
        MM = "mm", "mm"
        CM = "cm", "cm"
        IN = "in", "inches"
        FT = "ft", "feet"

    class WeightUnit(models.TextChoices):
        G = "g", "gram"
        KG = "kg", "kilogram"

    class VideoOrientation(models.TextChoices):
        LANDSCAPE = "landscape", "Landscape"
        PORTRAIT = "portrait", "Portrait"

    class Currency(models.TextChoices):
        INR = "INR", "INR"
        USD = "USD", "USD"
        EUR = "EUR", "EURO"

    class DangerousGoods(models.TextChoices):
        GHS = "ghs", "GHS"
        STORAGE = "storage", "Storage"
        TRANSPORTATION = "transportation", "Transportation"
        WASTE = "waste", "Waste"
        OTHER = "other", "Other"
        NOT_APPLICABLE = "not_applicable", "Not applicable"
        UNKNOWN = "unknown", "Unknown"

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="products"
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
    )
    # Extra categories beyond the primary one.
    categories = models.ManyToManyField(Category, blank=True, related_name="tagged_products")

    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, blank=True)
    description = models.TextField(blank=True, default="")

    price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    mrp = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    sku = models.CharField(max_length=100, blank=True, default="")
    currency = models.CharField(max_length=4, choices=Currency.choices, default=Currency.INR)

    # Tax / compliance.
    hsn_code = models.CharField(max_length=32, blank=True, default="")
    gst_percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    country_of_origin = models.CharField(max_length=100, blank=True, default="")
    dangerous_goods = models.CharField(max_length=20, choices=DangerousGoods.choices, blank=True, default="")

    # Stock for the simple (no-variant) case. Variant stock lives per
    # combination inside ``variant_combinations``.
    inventory = models.PositiveIntegerField(null=True, blank=True)
    # Below this level the product surfaces as "low stock" on the dashboard.
    low_stock_threshold = models.PositiveIntegerField(default=5)

    variants = models.JSONField(null=True, blank=True)
    variant_combinations = models.JSONField(null=True, blank=True)

    # Physical dimensions and weight (product + package), for shipping rating.
    product_length = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    product_breadth = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    product_height = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    product_unit = models.CharField(max_length=4, choices=LengthUnit.choices, blank=True, default="")
    package_length = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    package_breadth = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    package_height = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    package_unit = models.CharField(max_length=4, choices=LengthUnit.choices, blank=True, default="")
    weight_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    weight_unit = models.CharField(max_length=4, choices=WeightUnit.choices, blank=True, default="")
    package_weight_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    package_weight_unit = models.CharField(max_length=4, choices=WeightUnit.choices, blank=True, default="")

    # Returns / warranty.
    return_type = models.CharField(max_length=16, choices=ReturnType.choices, blank=True, default="")
    guarantee_warranty = models.TextField(blank=True, default="")

    # Contacts required for regulated goods.
    manufacturer_contact = models.TextField(blank=True, default="")
    packer_contact = models.TextField(blank=True, default="")

    specifications = models.JSONField(null=True, blank=True)
    tags = models.JSONField(null=True, blank=True)
    faqs = models.JSONField(null=True, blank=True)
    trusted_icons = models.JSONField(null=True, blank=True)
    # D2C-only extras: bullet points, what's-in-the-box, handling time, etc.
    d2c_details = models.JSONField(null=True, blank=True)

    video_link = models.URLField(blank=True, default="")
    video_orientation = models.CharField(
        max_length=10, choices=VideoOrientation.choices, default=VideoOrientation.LANDSCAPE, blank=True
    )

    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)
    # Curated placements, each capped per store in the view.
    is_best_seller = models.BooleanField(default=False)
    is_recently_launched = models.BooleanField(default=False)

    whatsapp_clicks = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("site", "slug"), name="uniq_product_slug_per_site"
            )
        ]
        indexes = [
            models.Index(fields=("site", "is_active")),
            models.Index(fields=("site", "category")),
        ]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._unique_slug()
        super().save(*args, **kwargs)

    def _unique_slug(self) -> str:
        base = slugify(self.title) or "product"
        candidate = base
        suffix = 2
        siblings = Product.all_objects.filter(site_id=self.site_id)
        if self.pk:
            siblings = siblings.exclude(pk=self.pk)
        while siblings.filter(slug=candidate).exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    @property
    def has_variants(self) -> bool:
        return bool(self.variant_combinations)

    @property
    def stock_on_hand(self) -> int:
        """Total units across the product, whether simple or variant."""
        if self.variant_combinations:
            total = 0
            for combo in self.variant_combinations:
                try:
                    total += int(combo.get("inventory") or 0)
                except (TypeError, ValueError):
                    continue
            return total
        return int(self.inventory or 0)

    @property
    def is_out_of_stock(self) -> bool:
        return self.stock_on_hand <= 0

    @property
    def is_low_stock(self) -> bool:
        return 0 < self.stock_on_hand <= self.low_stock_threshold


class ProductImage(TenantScopedModel):
    """An ordered gallery image for a product.

    The reference hung seven ``imageN`` columns off the product; a related
    table lets a store use as many as it needs and reorder them without a
    schema change.
    """

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="product_images"
    )
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="images"
    )
    image = models.ImageField(upload_to="products/")
    alt_text = models.CharField(max_length=255, blank=True, default="")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("order", "created_at")
        indexes = [models.Index(fields=("site", "product", "order"))]

    def __str__(self) -> str:
        return f"Image {self.order} for {self.product_id}"


class Review(TenantScopedModel, SoftDeleteMixin):
    """A customer product review (D2C). Owner can approve, hide or flag."""

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="reviews"
    )
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="reviews"
    )
    name = models.CharField(max_length=100, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    title = models.CharField(max_length=255, blank=True, default="")
    comment = models.TextField(blank=True, default="")
    photo = models.ImageField(upload_to="reviews/", blank=True, null=True)
    approved = models.BooleanField(default=False)
    reported = models.BooleanField(default=False)
    report_reason = models.TextField(blank=True, default="")

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("site", "product", "email"),
                name="uniq_review_per_email_product",
            )
        ]
        indexes = [models.Index(fields=("site", "product", "approved"))]

    def __str__(self) -> str:
        return f"{self.rating}★ on {self.product_id}"

    def clean(self):
        super().clean()
        if self.rating is None or not (1 <= self.rating <= 5):
            raise ValidationError({"rating": "Rating must be between 1 and 5."})
