"""Catalogue: categories, products, product images and reviews."""

from __future__ import annotations

import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.utils.text import slugify

from core.models import BaseModel, SoftDeleteMixin, TenantScopedModel


class SlugFollowsNameMixin:
    """Keep a catalogue row's ``slug`` derived from its name, renames included.

    The slug is generated from ``SLUG_SOURCE`` on create *and* regenerated
    whenever that field is edited in a way that changes its slugified form, so a
    renamed category or product moves to a URL that matches its new name on the
    owner's storefront, the collaboration storefronts, sitemaps and schema
    every one of which reverses links from the live ``slug``.

    A rename records the slug it left behind in :class:`SlugRedirect`, so links
    already shared (search results, a creator's posts, bookmarks) 301 to the new
    URL instead of 404ing, and the home hero banners pointing at the row are
    re-linked (they store a resolved ``button_url`` in ``Site.content``).
    """

    SLUG_SOURCE = "name"
    SLUG_FALLBACK = "item"
    # The SlugRedirect.Kind value  also the hero banner's ``button_link_type``.
    SLUG_KIND = ""

    def save(self, *args, **kwargs):
        adding = self._state.adding
        update_fields = kwargs.get("update_fields")
        source_saved = update_fields is None or self.SLUG_SOURCE in update_fields
        previous = self.slug
        # "Follows the name" is checked against the slug itself rather than a
        # remembered old name, so a row renamed before this rule existed heals
        # the next time it is saved too.
        if not self.slug or (source_saved and not self._slug_matches_source()):
            self.slug = self._unique_slug()
            if update_fields is not None and "slug" not in update_fields:
                kwargs["update_fields"] = [*update_fields, "slug"]
        with transaction.atomic():
            super().save(*args, **kwargs)
            if self.slug != previous:
                _follow_slug_change(self, None if adding else previous)

    def _slug_base(self) -> str:
        # Capped below the 255-char column so a "-N" suffix always fits.
        base = slugify(getattr(self, self.SLUG_SOURCE) or "")[:240].strip("-")
        return base or self.SLUG_FALLBACK

    def _slug_matches_source(self) -> bool:
        """Is the slug the name's slug, or it plus a de-duplicating "-N"?"""
        pattern = rf"{re.escape(self._slug_base())}(?:-\d+)?"
        return re.fullmatch(pattern, self.slug or "") is not None

    def _unique_slug(self) -> str:
        base = self._slug_base()
        candidate = base
        suffix = 2
        siblings = type(self).all_objects.filter(site_id=self.site_id)
        if self.pk:
            siblings = siblings.exclude(pk=self.pk)
        while siblings.filter(slug=candidate).exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate


# The hero banner field that holds the linked row's id, and its URL pattern
# mirroring commerce.views.editor._resolve_banner_urls.
_BANNER_LINKS = {
    "category": ("button_link_category_id", "/category/{}/"),
    "product": ("button_link_product_id", "/products/{}/"),
}


def _follow_slug_change(instance, old_slug) -> None:
    """Bookkeeping after ``instance`` took a new slug (``old_slug`` on a rename)."""
    redirects = SlugRedirect.objects.for_site(instance.site_id)
    # The new slug is live, so no redirect may claim it any longer.
    redirects.filter(kind=instance.SLUG_KIND, old_slug=instance.slug).delete()
    if not old_slug:
        return
    redirects.update_or_create(
        site_id=instance.site_id,
        kind=instance.SLUG_KIND,
        old_slug=old_slug,
        defaults={"target_id": instance.pk},
    )
    _relink_hero_banners(instance)


def _relink_hero_banners(instance) -> None:
    """Point hero banners that link to ``instance`` at its new slug."""
    from tenancy.models import Site

    id_key, pattern = _BANNER_LINKS[instance.SLUG_KIND]
    site = Site.objects.select_for_update().filter(pk=instance.site_id).first()
    content = site.content if site is not None and isinstance(site.content, dict) else None
    banners = content.get("hero_banners") if content else None
    if not isinstance(banners, list):
        return
    url = pattern.format(instance.slug)
    changed = False
    for banner in banners:
        if (
            isinstance(banner, dict)
            and (banner.get("button_link_type") or "").strip() == instance.SLUG_KIND
            and str(banner.get(id_key) or "") == str(instance.pk)
            and banner.get("button_url") != url
        ):
            banner["button_url"] = url
            changed = True
    if changed:
        # update(), not save(): only the content blob changes.
        Site.objects.filter(pk=site.pk).update(content=content)


def renamed_slug(site, kind: str, slug: str) -> str:
    """The current slug of the category/product that used to live at ``slug``.

    Returns ``""`` when nothing was renamed away from it, or its row is gone.
    Callers look the live slug up first; this is only the fallback.
    """
    if not slug:
        return ""
    target = (
        SlugRedirect.objects.for_site(site)
        .filter(kind=kind, old_slug=slug)
        .values_list("target_id", flat=True)
        .first()
    )
    if target is None:
        return ""
    model = Category if kind == SlugRedirect.Kind.CATEGORY else Product
    current = (
        model.objects.for_site(site)
        .filter(pk=target, is_deleted=False)
        .values_list("slug", flat=True)
        .first()
    )
    return current if current and current != slug else ""


def display_ordered_categories(queryset):
    """Order categories the way they should read on the storefront and dashboard.

    The owner's ``order`` field is a 1-based left-to-right position. Categories
    the owner has positioned (``order`` >= 1) come first, ascending by that
    position; categories still at the default ``order`` of 0 (never positioned)
    trail behind them, alphabetically. Django's plain ``ORDER BY order`` would
    instead float every unpositioned (0) category to the *front*, so a category
    the owner explicitly set to "1" would appear after all the untouched ones,
    which is the exact complaint this fixes.

    Applied on top of any already-scoped queryset (i.e. after ``.for_site``).
    """
    return queryset.annotate(
        _unpositioned=models.Case(
            models.When(order=0, then=models.Value(1)),
            default=models.Value(0),
            output_field=models.IntegerField(),
        )
    ).order_by("_unpositioned", "order", "name")


class Category(SlugFollowsNameMixin, TenantScopedModel, SoftDeleteMixin):
    """A product category. May nest one level via ``parent``."""

    SLUG_SOURCE = "name"
    SLUG_FALLBACK = "category"
    SLUG_KIND = "category"

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


class Product(SlugFollowsNameMixin, TenantScopedModel, SoftDeleteMixin):
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

    SLUG_SOURCE = "title"
    SLUG_FALLBACK = "product"
    SLUG_KIND = "product"

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
    # The brand printed on the product (e.g. a third-party brand the store
    # resells). Blank means the store's own brand. Feeds Product.brand in the
    # product page's structured data.
    brand = models.CharField(max_length=120, blank=True, default="")

    price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    mrp = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    sku = models.CharField(max_length=100, blank=True, default="")
    currency = models.CharField(max_length=4, choices=Currency.choices, default=Currency.INR)

    # Tax / compliance.
    hsn_code = models.CharField(max_length=32, blank=True, default="")
    gst_percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    # Whether ``price`` already includes GST. When True (the default), the price
    # is tax-inclusive and checkout adds no extra tax line. When False, GST is
    # excluded from the price and is added on top at checkout, computed from
    # ``gst_percentage`` for each line.
    gst_inclusive = models.BooleanField(default=True)
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
    # The return / replacement window in days. Always 0 for a non-returnable
    # product; required for returnable and replacement-only ones (ProductForm).
    # Drives the product page's "Returnable within N days" line and the
    # product schema's merchantReturnDays, so the two can never disagree.
    return_days = models.PositiveSmallIntegerField(null=True, blank=True)
    # A non-returnable product may still be *replaced* (damaged / wrong item)
    # within a window. Asked only for non-returnable products; 0 means no
    # replacement either. Shown on the product page next to "Non-returnable".
    replacement_days = models.PositiveSmallIntegerField(null=True, blank=True)
    guarantee_warranty = models.TextField(blank=True, default="")

    # Contacts required for regulated goods.
    manufacturer_contact = models.TextField(blank=True, default="")
    packer_contact = models.TextField(blank=True, default="")
    # Importer of record (imported goods): name, phone, address. Shown on the
    # product page's "Origin, Manufacturer & Packer" tab.
    importer_contact = models.TextField(blank=True, default="")

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
            # Serves the default newest-first listing per store (dashboard,
            # storefront), so MySQL reads in order instead of sorting whole
            # product rows (rich descriptions + JSON) in its sort buffer.
            models.Index(fields=("site", "-created_at")),
        ]

    def __str__(self) -> str:
        return self.title

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

    @property
    def effective_return_type(self) -> str:
        """The return type customers are actually told. A "Non returnable"
        trust badge on the product *is* its return policy (the product form
        locks the field to it), so it wins even on products saved before that
        lock existed."""
        from commerce.trust_badges import is_non_returnable

        if any(is_non_returnable(n) for n in (self.trusted_icons or [])):
            return self.ReturnType.NON_RETURNABLE
        return self.return_type or ""

    @property
    def effective_return_days(self):
        """0 for a non-returnable product; the window in days otherwise (or
        ``None`` when the owner hasn't given one)."""
        rt = self.effective_return_type
        if rt == self.ReturnType.NON_RETURNABLE:
            return 0
        if rt and self.return_days:
            return int(self.return_days)
        return None

    @property
    def effective_replacement_days(self):
        """The replacement window of a non-returnable product (``None`` when
        it has none, or the product isn't non-returnable)."""
        if self.effective_return_type != self.ReturnType.NON_RETURNABLE:
            return None
        days = self.replacement_days
        return int(days) if days else None

    @property
    def return_policy_label(self) -> str:
        """The product page's "Return Type" line: "Non-Returnable",
        "Returnable within 7 days", "Replacement within 10 days" …"""
        rt = self.effective_return_type
        if not rt:
            return ""
        if rt == self.ReturnType.NON_RETURNABLE:
            rdays = self.effective_replacement_days
            if rdays:
                unit = "day" if rdays == 1 else "days"
                return f"Non-Returnable · Replacement within {rdays} {unit}"
            return "Non-Returnable"
        days = self.effective_return_days
        if days:
            unit = "day" if days == 1 else "days"
            verb = "Replacement" if rt == self.ReturnType.REPLACEMENT else "Returnable"
            return f"{verb} within {days} {unit}"
        return self.get_return_type_display()


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


class ProductPriceChange(TenantScopedModel):
    """One point in a product's selling-price history.

    A row is written whenever the effective selling price of the product  or
    of one of its variations  differs from the last recorded one (see
    ``commerce.price_history.record``, run on every Product save). The
    storefront's "Price history" drawer reads these to draw the last 30 days as
    a stepped trend: each row is a step, the price holding until the next row.

    ``variant_key`` is the variation combination's key (``Size-M__Color-Black``)
    or ``""`` for the product's own base price. Append-only.
    """

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="product_price_changes"
    )
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="price_changes"
    )
    variant_key = models.CharField(max_length=255, blank=True, default="")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    mrp = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    changed_at = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ("changed_at", "created_at")
        indexes = [models.Index(fields=("site", "product", "variant_key", "changed_at"))]

    def __str__(self) -> str:
        return f"{self.product_id} [{self.variant_key or 'base'}] → {self.price} @ {self.changed_at:%Y-%m-%d}"


class SlugRedirect(TenantScopedModel):
    """A slug a category or product was renamed away from.

    Written by :class:`SlugFollowsNameMixin` on rename. The storefront detail
    views fall back to it when a slug matches nothing live, and 301 to the row's
    current slug  so shared and indexed links survive a rename. A target is
    referenced by id (not a foreign key) because two models share the table; a
    missing or deleted target simply resolves to nothing.
    """

    class Kind(models.TextChoices):
        CATEGORY = "category", "Category"
        PRODUCT = "product", "Product"

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="slug_redirects"
    )
    kind = models.CharField(max_length=10, choices=Kind.choices)
    old_slug = models.SlugField(max_length=255)
    target_id = models.UUIDField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("site", "kind", "old_slug"), name="uniq_slug_redirect_per_site"
            )
        ]
        indexes = [models.Index(fields=("site", "kind", "target_id"))]

    def __str__(self) -> str:
        return f"{self.kind} /{self.old_slug}/ → {self.target_id}"


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


class TrustBadge(BaseModel):
    """A platform-curated trust badge (a name plus an icon image or SVG).

    Global, not tenant-scoped: OneWooz staff add these in the Django admin and
    the whole platform shares one curated set. Store owners then tick which
    badges to show on each product (the product form's badge picker lists the
    active ones), and the storefront renders the badge's image beside its name.

    ``image`` is a ``FileField`` rather than an ``ImageField`` on purpose  an
    ImageField runs Pillow validation, which rejects SVG; a FileField accepts
    PNG/JPG/WEBP *and* SVG. Uploads come only from trusted platform staff.
    """

    name = models.CharField(max_length=80, unique=True)
    image = models.FileField(
        upload_to="trust_badges/",
        blank=True,
        null=True,
        help_text="Icon shown with the badge. PNG, JPG, WEBP or SVG.",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("order", "name")
        verbose_name = "trust badge"
        verbose_name_plural = "trust badges"

    def __str__(self) -> str:
        return self.name


class ProductFieldTooltip(BaseModel):
    """The hover help for one input on the owner's create / edit product form.

    Global, not tenant-scoped: OneWooz staff write these in the Django admin and
    every store's product form shows them. One row per form input (the keys are
    :data:`commerce.field_tips.PRODUCT_FIELDS`; the admin seeds a blank row for
    each automatically). A row with a description renders a small "!" circle
    beside that field's label; hovering it shows ``description``.
    """

    field_key = models.CharField(
        max_length=60,
        unique=True,
        help_text="Which product-form input this tooltip belongs to.",
    )
    label = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="The field's label on the product form (for reference only).",
    )
    # A bounded CharField (not TextField), so sorting the admin list never
    # sorts a TEXT column (see core.queries / MySQL error 1038).
    description = models.CharField(
        max_length=600,
        blank=True,
        default="",
        help_text="Shown when the owner hovers the ! icon beside the field. Leave blank to show no icon.",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("order", "field_key")
        verbose_name = "product field tooltip"
        verbose_name_plural = "product field tooltips"

    def __str__(self) -> str:
        return self.label or self.field_key
