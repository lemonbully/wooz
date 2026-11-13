"""Content models for onewooz.com  the public marketing site.

These are global (not tenant-scoped) records managed from the Django admin. They
mirror the storefront CMS an owner gets for their own store: a three-level core
page tree (page → subpage → child page) plus a blog. Content is rich HTML
authored with TinyMCE in the admin and rendered on the marketing site.
"""

from __future__ import annotations

from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from core.models import BaseModel

__all__ = [
    "MarketingCorePage",
    "MarketingCoreSubpage",
    "MarketingCoreChildPage",
    "MarketingBlog",
    "TutorialVideo",
    "DemoSiteLink",
    "LegalPolicy",
    "CustomerTestimonial",
    "MarketingSeoSettings",
    "MarketingPageSeo",
]


class DemoSiteLink(BaseModel):
    """A "see a live store" link the marketing site offers visitors.

    Operators build a real showcase store (D2C or B2B), then paste its public URL
    here from the Django admin. The pricing page renders the active links grouped
    by kind, so a visitor can open a finished D2C example and a finished B2B
    example without us auto-provisioning anything. Platform-wide, not
    tenant-scoped.
    """

    class Kind(models.TextChoices):
        D2C = "d2c", "D2C storefront"
        B2B = "b2b", "B2B storefront"

    kind = models.CharField(
        max_length=8, choices=Kind.choices, default=Kind.D2C, db_index=True,
        help_text="Which kind of store this demo shows.",
    )
    label = models.CharField(
        max_length=120,
        help_text='Button text, e.g. "Fashion store (D2C)".',
    )
    url = models.URLField(
        max_length=500,
        help_text="Full public URL of the demo storefront (https://…).",
    )
    description = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Optional one-line note shown with the link.",
    )
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("kind", "order", "created_at")
        verbose_name = "demo site link"
        verbose_name_plural = "demo site links"

    def __str__(self) -> str:
        return f"{self.get_kind_display()}: {self.label}"

    @classmethod
    def active(cls):
        """All active demo links, ordered (D2C first, then B2B), for the site."""
        return list(cls.objects.filter(is_active=True))


class TutorialVideo(BaseModel):
    """A short how-to video managed from the Django admin and shown on one
    dashboard page (identified by ``key``).

    Platform-wide, not tenant-scoped: OneWooz uploads it once and every store
    owner sees the same tutorial. The first published video for a key is used.
    """

    class Placement(models.TextChoices):
        CUSTOM_DOMAIN = "custom_domain", "Custom domain page"

    key = models.SlugField(
        max_length=60,
        unique=True,
        choices=Placement.choices,
        default=Placement.CUSTOM_DOMAIN,
        help_text="Which dashboard page shows this video.",
    )
    title = models.CharField(max_length=200, default="Tutorial video")
    video = models.FileField(
        upload_to="tutorials/",
        help_text="A landscape (16:9) MP4/WEBM plays best.",
    )
    poster = models.ImageField(
        upload_to="tutorials/posters/",
        blank=True,
        null=True,
        help_text="Optional thumbnail shown before the video plays.",
    )
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ("key",)
        verbose_name = "tutorial video"
        verbose_name_plural = "tutorial videos"

    def __str__(self) -> str:
        return self.title or self.get_key_display()


class MarketingCorePage(BaseModel):
    """A top-level marketing core page (e.g. Help Centre, Company)."""

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    content = models.TextField(blank=True, default="")
    order = models.PositiveIntegerField(default=0)
    is_published = models.BooleanField(default=True)
    show_in_footer = models.BooleanField(
        default=True, help_text="List this page in the site footer."
    )

    class Meta:
        ordering = ("order", "title")
        verbose_name = "core page"
        verbose_name_plural = "core pages"

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(MarketingCorePage, self.title, self.pk, "page")
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("web:core_page", kwargs={"slug": self.slug})


class MarketingCoreSubpage(BaseModel):
    """A subpage under a :class:`MarketingCorePage`."""

    core_page = models.ForeignKey(
        MarketingCorePage, on_delete=models.CASCADE, related_name="subpages"
    )
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=140, blank=True)
    content = models.TextField(blank=True, default="")
    order = models.PositiveIntegerField(default=0)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ("order", "title")
        verbose_name = "core subpage"
        verbose_name_plural = "core subpages"
        constraints = [
            models.UniqueConstraint(
                fields=("core_page", "slug"), name="uniq_subpage_slug_per_page"
            )
        ]

    def __str__(self) -> str:
        return f"{self.core_page.title} → {self.title}"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title) or "subpage"
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse(
            "web:core_subpage",
            kwargs={"slug": self.core_page.slug, "sub_slug": self.slug},
        )


class MarketingCoreChildPage(BaseModel):
    """A child page under a :class:`MarketingCoreSubpage`."""

    subpage = models.ForeignKey(
        MarketingCoreSubpage, on_delete=models.CASCADE, related_name="children"
    )
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=140, blank=True)
    content = models.TextField(blank=True, default="")
    order = models.PositiveIntegerField(default=0)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ("order", "title")
        verbose_name = "core child page"
        verbose_name_plural = "core child pages"
        constraints = [
            models.UniqueConstraint(
                fields=("subpage", "slug"), name="uniq_child_slug_per_subpage"
            )
        ]

    def __str__(self) -> str:
        return f"{self.subpage.title} → {self.title}"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title) or "page"
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse(
            "web:core_child",
            kwargs={
                "slug": self.subpage.core_page.slug,
                "sub_slug": self.subpage.slug,
                "child_slug": self.slug,
            },
        )


class MarketingBlog(BaseModel):
    """A blog post on the marketing site."""

    title = models.CharField(max_length=220)
    slug = models.SlugField(max_length=160, unique=True, blank=True)
    excerpt = models.CharField(max_length=400, blank=True, default="")
    content = models.TextField(blank=True, default="")
    cover_image = models.ImageField(upload_to="marketing/blog/", blank=True, null=True)
    author = models.CharField(max_length=120, blank=True, default="")
    is_published = models.BooleanField(default=True)
    published_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-published_at",)
        verbose_name = "blog post"
        verbose_name_plural = "blog posts"
        # Serves the blog index / sitemap order without sorting article bodies.
        indexes = [models.Index(fields=("is_published", "-published_at"))]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(MarketingBlog, self.title, self.pk, "post")
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("web:blog_detail", kwargs={"slug": self.slug})


class LegalPolicy(BaseModel):
    """A legal policy page (refund, shipping, cookies, acceptable use…) authored
    in the Django admin with TinyMCE.

    Every published policy with ``show_in_footer`` is listed automatically under
    the footer's "Legal" column, after the built-in Privacy and Terms links, and
    is served at ``/legal/<slug>/`` in the same page shell as those two.

    The slugs ``privacy`` and ``terms`` are special: a published policy with one
    of them *replaces* the built-in page's content at its existing URL
    (``/privacy/``, ``/terms/``) without moving or duplicating its footer link.
    Without such a policy the built-in pages render exactly as before.
    """

    #: Slugs of the built-in legal pages (see web.views.PAGES).
    BUILT_IN_SLUGS = ("privacy", "terms")

    title = models.CharField(
        max_length=160,
        help_text='The policy name, e.g. "Refund Policy". Used as the page heading '
        "and the footer link text.",
    )
    slug = models.SlugField(
        max_length=140,
        unique=True,
        blank=True,
        help_text="URL part: /legal/your-slug/. Filled in from the name; edit it if "
        'you like. "privacy" or "terms" replace the built-in page\'s content.',
    )
    content = models.TextField(blank=True, default="")
    order = models.PositiveIntegerField(
        default=0, help_text="Lower numbers appear first in the footer."
    )
    is_published = models.BooleanField(default=True, db_index=True)
    show_in_footer = models.BooleanField(
        default=True, help_text='List this policy in the footer under "Legal".'
    )

    class Meta:
        ordering = ("order", "title")
        verbose_name = "policy"
        verbose_name_plural = "policies"

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        # Normalise a hand-typed slug; generate one (unique) when left blank.
        self.slug = slugify(self.slug or "")
        if not self.slug:
            self.slug = _unique_slug(LegalPolicy, self.title, self.pk, "policy")
        super().save(*args, **kwargs)

    @property
    def is_built_in(self) -> bool:
        return self.slug in self.BUILT_IN_SLUGS

    def get_absolute_url(self) -> str:
        if self.is_built_in:
            return reverse("web:page", kwargs={"slug": self.slug})
        return reverse("web:policy", kwargs={"slug": self.slug})

    @classmethod
    def footer_links(cls) -> list:
        """Published footer policies other than the built-in Privacy / Terms,
        whose footer links are fixed."""
        return list(
            cls.objects.filter(is_published=True, show_in_footer=True)
            .exclude(slug__in=cls.BUILT_IN_SLUGS)
            .only("title", "slug")
        )


class CustomerTestimonial(BaseModel):
    """A customer testimonial card on the marketing Customers page.

    Managed in bulk from one admin screen (many rows at once). Active rows
    scroll across the page's "More field reports" strip in ``order``.
    """

    industry = models.CharField(max_length=80, help_text='e.g. "Apparel".')
    company_name = models.CharField(max_length=120, help_text='e.g. "Northbound".')
    review = models.TextField(help_text="The quote, without quotation marks.")
    name = models.CharField(max_length=120, help_text='Who said it, e.g. "Dev M.".')
    order = models.PositiveIntegerField(default=0, help_text="Lower numbers appear first.")
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("order", "created_at")
        verbose_name = "customer testimonial"
        verbose_name_plural = "customer testimonials"

    def __str__(self) -> str:
        return f"{self.name} · {self.company_name}"


#: Shown on the Customers page until testimonials are added in the admin, so the
#: section is never empty (the page's original three).
DEFAULT_TESTIMONIALS = (
    {"company_name": "Northbound", "industry": "Apparel", "name": "Dev M.",
     "review": "The store looked finished the moment it appeared. I changed two photos and hit publish."},
    {"company_name": "Forgeworks", "industry": "Industrial", "name": "S. Rao",
     "review": "Our buyers send enquiries straight off the product page now. We quote the same day."},
    {"company_name": "Kettle&Co", "industry": "Food", "name": "Anjali P.",
     "review": "Live rates and labels saved me an hour a day I used to lose to the courier site."},
)


def _unique_slug(model, source: str, pk, fallback: str) -> str:
    base = slugify(source) or fallback
    candidate, suffix = base, 2
    existing = model.objects.all()
    if pk:
        existing = existing.exclude(pk=pk)
    while existing.filter(slug=candidate).exists():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


# ---------------------------------------------------------------------------
# onewooz.com SEO, managed in the Django admin
# ---------------------------------------------------------------------------
# The marketing site's own version of the per-page SEO a store owner gets on
# Edit Content → SEO (storefront.seo_pages): one site-wide set, and, in
# "Different for every page" mode, a row per page. Text columns are bounded
# CharFields (never TEXT), so admin ordering can never make MySQL sort wide rows.
# Applied by web.seo_overrides on top of the built-in registry (web.schema).


class _SeoFieldsMixin(models.Model):
    """The SEO attributes a page (or the whole site) can carry. Every field is
    optional: a blank one keeps the page's built-in value."""

    meta_title = models.CharField(
        max_length=200, blank=True, default="",
        help_text="The page title shown in search results and the browser tab. Aim for 50-60 characters.",
    )
    meta_description = models.CharField(
        max_length=400, blank=True, default="",
        help_text="The snippet under the title in search results. Aim for 150-160 characters.",
    )
    keywords = models.CharField(
        max_length=500, blank=True, default="", help_text="Comma-separated keywords.",
    )
    og_title = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Open Graph title (Facebook, WhatsApp, LinkedIn). Blank uses the meta title.",
    )
    og_description = models.CharField(
        max_length=400, blank=True, default="",
        help_text="Open Graph description. Blank uses the meta description.",
    )
    og_image = models.ImageField(
        upload_to="marketing/seo/", blank=True, null=True,
        help_text="Share image. Recommended 1200 × 630 px.",
    )
    twitter_title = models.CharField(
        max_length=200, blank=True, default="", help_text="Blank uses the Open Graph title.",
    )
    twitter_description = models.CharField(
        max_length=400, blank=True, default="", help_text="Blank uses the Open Graph description.",
    )
    twitter_image = models.ImageField(
        upload_to="marketing/seo/", blank=True, null=True,
        help_text="Blank uses the Open Graph image. Recommended 1200 × 630 px.",
    )
    schema_markup = models.TextField(
        blank=True, default="",
        help_text=(
            "Structured data (schema.org JSON-LD) for this page. Paste the JSON, or the whole "
            "script block(s) exactly as a schema generator gives them; several blocks are fine. "
            "It is checked on save and rendered in the page head."
        ),
    )
    replace_built_in_schema = models.BooleanField(
        default=False,
        help_text=(
            "Tick to use only the schema pasted above on this page. Unticked, it is added "
            "next to OneWooz's built-in schema (organisation, website, FAQ, breadcrumbs)."
        ),
    )

    class Meta:
        abstract = True


class MarketingSeoSettings(_SeoFieldsMixin, BaseModel):
    """The one SEO configuration for onewooz.com (a single object).

    * **Same for every page**  every page keeps its own built-in, keyword-tuned
      title, description and FAQ schema; the set entered here is the home
      page's SEO and fills what a page lacks (keywords, share image). This is
      exactly how a store's "Same for every page" SEO works.
    * **Different for every page**  additionally, each page listed under
      "Page SEO" uses the values entered for it; a blank field still uses that
      page's built-in value.
    """

    class Mode(models.TextChoices):
        SAME = "same", "Same for every page (auto-tailored to each page)"
        CUSTOM = "custom", "Different for every page (edit each page)"

    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.SAME)

    # OneWooz's own brand assets, used everywhere the OneWooz mark shows
    # (onewooz.com, sign-in, owner dashboard, admin). Blank keeps the built-in
    # files; each is rendered in the same box as the built-in one.
    site_logo = models.ImageField(
        upload_to="marketing/brand/", blank=True, null=True,
        help_text=(
            "Replaces the OneWooz logo in the site header, sign-in pages, dashboard sidebar and "
            "admin. Shown at the same height as today (about 26 px tall, width follows the "
            "image), so upload a PNG or SVG-exported PNG with a transparent background, at "
            "least 104 px tall. Blank keeps the built-in logo."
        ),
    )
    favicon = models.ImageField(
        upload_to="marketing/brand/", blank=True, null=True,
        help_text=(
            "The browser-tab icon on every OneWooz page. Upload a square PNG or ICO, "
            "512 × 512 px (it is also used as the home-screen icon). Blank keeps the built-in icon."
        ),
    )
    favicon_dark = models.ImageField(
        upload_to="marketing/brand/", blank=True, null=True,
        help_text=(
            "Optional: a version for browsers in dark mode (for example a light mark). "
            "Same size as the favicon. Blank uses the favicon above in both modes."
        ),
    )

    class Meta:
        verbose_name = "onewooz.com SEO"
        verbose_name_plural = "onewooz.com SEO"

    def __str__(self) -> str:
        return "onewooz.com SEO"

    @classmethod
    def current(cls) -> "MarketingSeoSettings":
        """The single settings object, created on first use."""
        obj = cls.objects.order_by("created_at").first()
        if obj is None:
            obj = cls.objects.create()
        return obj

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        from .seo_overrides import invalidate

        invalidate()

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        from .seo_overrides import invalidate

        invalidate()
        return result


class MarketingPageSeo(_SeoFieldsMixin, BaseModel):
    """One onewooz.com page's own SEO (used in "Different for every page" mode).

    ``page_key`` names the page, not its path: a fixed page by name (``home``,
    ``pricing``…), ``page:privacy`` / ``page:terms``, ``policy:<slug>``,
    ``blog:<slug>`` or ``core:<page>[/<sub>[/<child>]]`` (see
    web.seo_overrides.page_catalog).
    """

    settings = models.ForeignKey(
        MarketingSeoSettings, on_delete=models.CASCADE, related_name="pages",
    )
    page_key = models.CharField(max_length=220, help_text="Which page these values apply to.")
    canonical_url = models.URLField(
        max_length=500, blank=True, default="",
        help_text="Only if this page's preferred address differs from its own URL.",
    )
    noindex = models.BooleanField(
        default=False,
        help_text="Hide this page from search engines (and leave it out of the sitemap).",
    )

    class Meta:
        verbose_name = "page SEO"
        verbose_name_plural = "page SEO"
        constraints = [
            models.UniqueConstraint(fields=("settings", "page_key"), name="web_page_seo_unique_page"),
        ]

    def __str__(self) -> str:
        from .seo_overrides import label_for

        return label_for(self.page_key)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        from .seo_overrides import invalidate

        invalidate()

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        from .seo_overrides import invalidate

        invalidate()
        return result
