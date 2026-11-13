"""Email marketing  audiences, campaigns, drip sequences, sends and tracking.

This is the store→customer sending engine. Every model is tenant-scoped: it
inherits :class:`core.models.TenantScopedModel` and carries a ``site`` foreign
key, so a queryset cannot be evaluated until narrowed with ``.for_site(site)``.
A store can only ever mail *its own* customers, from its own branded address on
the shared SES domain (see :mod:`tenancy.email` / :mod:`core.mail`).

The shape mirrors a proven design but is rebuilt on OneWooz's spine  UUIDv7
keys (nothing enumerable), structural tenant scoping, signed tracking tokens
(:mod:`core.tokens`), and a batch/delivery split so a send is idempotent and
reconciles from counters rather than guesswork:

* :class:`AudienceList` / :class:`AudienceMember`  reusable recipient lists,
  optionally built from the store's real :class:`~commerce.models.Customer` rows.
* :class:`Campaign`  a broadcast (one-off) or a drip (day-wise sequence).
* :class:`DripSequence`  the day-wise plan a drip campaign runs on.
* :class:`CampaignSendLog`  one batch per (campaign, day); the duplicate guard.
* :class:`EmailDelivery`  one row per recipient send; powers idempotency and
  every open/click/unsubscribe metric.
* :class:`Suppression`  a per-store do-not-contact list (unsubscribes/bounces).
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from core.models import BaseModel, SoftDeleteMixin, TenantScopedModel


class AudienceList(TenantScopedModel):
    """A named, reusable list of recipients belonging to one store."""

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="audience_lists"
    )
    name = models.CharField(max_length=120)
    is_archived = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("site", "-created_at"))]

    def __str__(self) -> str:
        return self.name


class AudienceMember(TenantScopedModel):
    """One recipient on an :class:`AudienceList`, keyed by lower-cased email.

    Optionally linked to a :class:`~commerce.models.Customer` when the list was
    built from the store's customer directory, so a member always resolves to a
    real shopper where one exists.
    """

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="audience_members"
    )
    audience_list = models.ForeignKey(
        AudienceList, on_delete=models.CASCADE, related_name="members"
    )
    customer = models.ForeignKey(
        "commerce.Customer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audience_memberships",
    )
    name = models.CharField(max_length=120, blank=True, default="")
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True, default="")

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("audience_list", "email"),
                name="uniq_audience_member_email_per_list",
            )
        ]
        indexes = [models.Index(fields=("site", "email"))]

    def __str__(self) -> str:
        return self.email

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()
        super().save(*args, **kwargs)


class DripSequence(TenantScopedModel):
    """A day-wise message plan a drip campaign sends on.

    ``steps`` is a list of ``{"day": <int ≥ 1>, "subject": str, "html": str}``.
    Day 1 is the campaign's start day, Day 2 the next day, and so on  the day
    is *relative to the campaign*, never a calendar day-of-month.
    """

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="drip_sequences"
    )
    name = models.CharField(max_length=120)
    steps = models.JSONField(default=list, blank=True)
    is_archived = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("site", "-created_at"))]

    def __str__(self) -> str:
        return self.name

    def clean(self):
        from django.core.exceptions import ValidationError

        data = self.steps
        if not isinstance(data, list):
            raise ValidationError({"steps": "Steps must be a list of day objects."})
        seen = set()
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                raise ValidationError({"steps": f"Step #{i + 1} must be an object."})
            day = item.get("day")
            if not isinstance(day, int) or day < 1 or day > 365:
                raise ValidationError(
                    {"steps": f"Step #{i + 1}: 'day' must be an integer between 1 and 365."}
                )
            if day in seen:
                raise ValidationError({"steps": f"Duplicate day {day} in steps."})
            seen.add(day)
            if not (item.get("subject") or "").strip():
                raise ValidationError({"steps": f"Step #{i + 1}: a subject is required."})

    def steps_for_day(self, day: int) -> list[dict]:
        if not isinstance(day, int):
            return []
        out = []
        for item in self.steps or []:
            try:
                if int(item.get("day")) == day:
                    out.append(item)
            except (TypeError, ValueError):
                continue
        return out

    @property
    def max_day(self) -> int:
        days = []
        for item in self.steps or []:
            try:
                days.append(int(item.get("day")))
            except (TypeError, ValueError):
                continue
        return max(days) if days else 0


class Campaign(TenantScopedModel, SoftDeleteMixin):
    """A broadcast (one-off) or a drip (day-wise) email campaign."""

    class Kind(models.TextChoices):
        BROADCAST = "broadcast", "Broadcast (one-off)"
        DRIP = "drip", "Drip sequence"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SCHEDULED = "scheduled", "Scheduled"
        ACTIVE = "active", "Sending"
        PAUSED = "paused", "Paused"
        SENT = "sent", "Sent"
        ARCHIVED = "archived", "Archived"

    class Builder(models.TextChoices):
        HTML = "html", "Custom HTML"
        AI = "ai", "AI builder"

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="email_campaigns"
    )
    name = models.CharField(max_length=160)
    kind = models.CharField(
        max_length=12, choices=Kind.choices, default=Kind.BROADCAST, db_index=True
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True
    )

    # Audience: any combination of chosen lists and/or "everyone who has bought".
    audience_lists = models.ManyToManyField(
        AudienceList, blank=True, related_name="campaigns"
    )
    send_to_all_customers = models.BooleanField(default=False)

    # Broadcast content. From-address is always the store's branded SES address
    # (never free-typed); the owner controls only the display name and reply-to.
    subject = models.CharField(max_length=255, blank=True, default="")
    preheader = models.CharField(max_length=255, blank=True, default="")
    from_name = models.CharField(max_length=120, blank=True, default="")
    reply_to = models.EmailField(blank=True, default="")
    builder = models.CharField(
        max_length=8, choices=Builder.choices, default=Builder.HTML
    )
    html = models.TextField(blank=True, default="")
    # A built-in, field-driven design (see :mod:`outreach.promo_design`). When
    # set, the email is rendered at send time from ``design_data`` (one value per
    # slot the owner filled in the composer) and ``html`` is left empty.
    design = models.CharField(max_length=24, blank=True, default="")
    design_data = models.JSONField(default=dict, blank=True)
    # A call-to-action the trackable button points at (defaults to the storefront).
    cta_label = models.CharField(max_length=60, blank=True, default="")
    cta_url = models.URLField(blank=True, default="")

    # Which products fill the template's {{products}} grid. ``single`` shows one
    # product; ``multi`` shows up to four. ``product_ids`` holds the chosen
    # product UUIDs (as strings) in display order; empty falls back to the
    # store's latest products, preserving the old behaviour.
    class ProductMode(models.TextChoices):
        SINGLE = "single", "Single product"
        MULTI = "multi", "Multiple products"
        PROMOTIONAL = "promotional", "Promotional"

    product_mode = models.CharField(
        max_length=12, choices=ProductMode.choices, default=ProductMode.MULTI
    )
    product_ids = models.JSONField(default=list, blank=True)

    # Drip content.
    drip = models.ForeignKey(
        DripSequence,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="campaigns",
    )

    # Scheduling. ``start_at`` is Day 1 for a drip and the send time for a
    # scheduled broadcast; null on a broadcast that is sent immediately.
    start_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("site", "status", "-created_at"))]

    def __str__(self) -> str:
        return self.name

    @property
    def is_drip(self) -> bool:
        return self.kind == self.Kind.DRIP


class CampaignSendLog(TenantScopedModel):
    """One batch of a campaign  a broadcast (day 1) or one day of a drip.

    Unique per ``(campaign, day)`` so a batch is never sent twice, and finalised
    from its counters (``sent_count`` + ``failed_count`` vs ``total_enqueued``)
    rather than by inferring state.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ENQUEUED = "enqueued", "Enqueued"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="email_send_logs"
    )
    campaign = models.ForeignKey(
        Campaign, on_delete=models.CASCADE, related_name="send_logs"
    )
    day = models.PositiveIntegerField(default=1)
    subject = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    total_enqueued = models.PositiveIntegerField(default=0)
    sent_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    last_error_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("campaign", "day"), name="uniq_send_log_per_campaign_day"
            )
        ]
        indexes = [
            models.Index(fields=("site", "status")),
            models.Index(fields=("campaign", "day")),
        ]

    def __str__(self) -> str:
        return f"{self.campaign_id} · day {self.day} · {self.status}"


class EmailDelivery(TenantScopedModel):
    """One email to one recipient  the unit of sending and of every metric."""

    class SendStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="email_deliveries"
    )
    campaign = models.ForeignKey(
        Campaign, on_delete=models.CASCADE, related_name="deliveries"
    )
    send_log = models.ForeignKey(
        CampaignSendLog,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="deliveries",
    )
    audience_member = models.ForeignKey(
        AudienceMember,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    customer = models.ForeignKey(
        "commerce.Customer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    recipient_email = models.EmailField(db_index=True)
    recipient_name = models.CharField(max_length=120, blank=True, default="")
    subject = models.CharField(max_length=255, blank=True, default="")

    send_status = models.CharField(
        max_length=12, choices=SendStatus.choices, default=SendStatus.PENDING, db_index=True
    )
    sending_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    send_attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    last_error_at = models.DateTimeField(null=True, blank=True)

    # Engagement.
    open_count = models.PositiveIntegerField(default=0)
    first_opened_at = models.DateTimeField(null=True, blank=True)
    last_opened_at = models.DateTimeField(null=True, blank=True)
    clicked_at = models.DateTimeField(null=True, blank=True)
    unsubscribed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("send_log", "recipient_email"),
                name="uniq_delivery_per_batch_recipient",
            )
        ]
        indexes = [
            models.Index(fields=("site", "campaign")),
            models.Index(fields=("site", "send_status")),
            models.Index(fields=("recipient_email",)),
        ]

    def __str__(self) -> str:
        return f"{self.recipient_email} · {self.send_status}"

    def save(self, *args, **kwargs):
        if self.recipient_email:
            self.recipient_email = self.recipient_email.strip().lower()
        super().save(*args, **kwargs)


class Suppression(TenantScopedModel):
    """A per-store do-not-contact record for one email.

    Written when a recipient unsubscribes (or a bounce/complaint is recorded),
    and honoured by the engine before every send. Store-scoped: unsubscribing
    from one store never silences another.
    """

    class Reason(models.TextChoices):
        UNSUBSCRIBED = "unsubscribed", "Unsubscribed"
        BOUNCED = "bounced", "Bounced"
        COMPLAINED = "complained", "Marked as spam"
        MANUAL = "manual", "Added by store"

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="email_suppressions"
    )
    email = models.EmailField(db_index=True)
    reason = models.CharField(
        max_length=14, choices=Reason.choices, default=Reason.UNSUBSCRIBED
    )
    note = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("site", "email"), name="uniq_suppression_email_per_site"
            )
        ]
        indexes = [models.Index(fields=("site", "email"))]

    def __str__(self) -> str:
        return f"{self.email} · {self.reason}"

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()
        super().save(*args, **kwargs)


class EmailTemplate(BaseModel):
    """A platform-curated email template that stores pick when composing.

    Global (not tenant-scoped): an admin authors these once in the admin
    template builder and every store may use them. The HTML carries the
    pre-defined ``{{variables}}`` (see :mod:`outreach.variables`) that each store
    fills at send time. Only ``PUBLISHED`` templates are offered to stores  a
    template stays ``DRAFT`` while the admin iterates on it in the builder and
    becomes usable only when the admin marks it complete.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    class ProductType(models.TextChoices):
        SINGLE = "single", "Single product"
        MULTI = "multi", "Multiple products"
        ANY = "any", "Either (single or multiple)"

    name = models.CharField(max_length=160)
    description = models.CharField(max_length=255, blank=True, default="")
    html = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    # Which product layout the design is built for. A store composing a campaign
    # picks "single" or "multiple" products; only templates matching that choice
    # (or ``any``) are offered. ``any`` = works for both. Legacy rows default to
    # ``any`` so nothing already published disappears.
    product_type = models.CharField(
        max_length=8, choices=ProductType.choices, default=ProductType.ANY, db_index=True
    )
    # Denormalised author email (admins are staff accounts; kept as a string so
    # the template survives the account being removed).
    created_by = models.CharField(max_length=254, blank=True, default="")

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("status", "-created_at"))]

    def __str__(self) -> str:
        return self.name

    @property
    def is_published(self) -> bool:
        return self.status == self.Status.PUBLISHED
