"""Owner-dashboard content managed by OneWooz.

:class:`BusinessHubPost` is the Business Hub: announcements OneWooz authors in
the Django admin and shows inside every targeted owner's dashboard, split into
three tabs (News, Help, Important). A post goes either to *all* owners or to a
chosen set of them; each recipient also gets a bell notification and an email
(see :mod:`ud.hub`). Platform-wide, not tenant-scoped.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models import Q

from core.models import BaseModel

HUB_VIDEO_EXTENSIONS = ("mp4", "webm", "mov", "m4v")

_YT_ID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")


def video_link_embed(url: str) -> str:
    """The embeddable player URL for a YouTube or Vimeo link, else ``""``.

    Accepts the shapes people paste: youtube.com/watch?v=…, youtu.be/…,
    /shorts/…, /embed/…, /live/…, and vimeo.com/<id>. YouTube plays from the
    privacy-enhanced youtube-nocookie.com host (admitted by the app CSP).
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    parts = [p for p in parsed.path.split("/") if p]
    video_id = ""
    if host == "youtu.be" and parts:
        video_id = parts[0]
    elif host in ("youtube.com", "youtube-nocookie.com", "music.youtube.com"):
        if parts[:1] == ["watch"]:
            video_id = (parse_qs(parsed.query).get("v") or [""])[0]
        elif len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
            video_id = parts[1]
    if video_id:
        return f"https://www.youtube-nocookie.com/embed/{video_id}" if _YT_ID.match(video_id) else ""
    if host in ("vimeo.com", "player.vimeo.com"):
        digits = next((p for p in reversed(parts) if p.isdigit()), "")
        if digits:
            return f"https://player.vimeo.com/video/{digits}"
    return ""


class BusinessHubPost(BaseModel):
    """One Business Hub post, shown under its tab in the owner dashboard."""

    class Kind(models.TextChoices):
        NEWS = "news", "News"
        HELP = "help", "Help"
        IMPORTANT = "important", "Important"

    kind = models.CharField(
        "type", max_length=12, choices=Kind.choices, default=Kind.NEWS, db_index=True,
        help_text="Which Business Hub tab this appears under.",
    )
    title = models.CharField(max_length=200)
    description = models.TextField(
        blank=True, default="", help_text="The post body shown in the dashboard."
    )
    # Optional video, played inside the post (below its description). Either an
    # uploaded file or a YouTube / Vimeo link; the upload wins when both are set.
    video = models.FileField(
        "video file",
        upload_to="business_hub/videos/",
        blank=True,
        null=True,
        validators=[FileExtensionValidator(HUB_VIDEO_EXTENSIONS)],
        help_text="Optional. Upload a video (MP4 recommended; WebM, MOV and M4V also "
        "work) to play inside the post.",
    )
    video_url = models.URLField(
        "video link",
        max_length=500,
        blank=True,
        default="",
        help_text="Optional. Or paste a YouTube or Vimeo link instead of uploading. "
        "Ignored when a video file is uploaded.",
    )
    send_to_all = models.BooleanField(
        "all users", default=False,
        help_text="Tick to send this to every store owner. Leave unticked to send "
        "only to the customers selected below.",
    )
    recipients = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="business_hub_posts",
        verbose_name="customers",
        help_text="One or more store owners who should receive this post "
        "(ignored when “All users” is ticked).",
    )
    is_published = models.BooleanField(
        default=True, db_index=True,
        help_text="Unpublished posts are hidden and not sent.",
    )

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "business hub post"
        verbose_name_plural = "business hub posts"

    def __str__(self) -> str:
        return f"{self.get_kind_display()}: {self.title}"

    @property
    def video_file_url(self) -> str:
        try:
            return self.video.url if self.video else ""
        except ValueError:
            return ""

    @property
    def video_embed_url(self) -> str:
        """Player URL for the video link (only used when no file is uploaded)."""
        return "" if self.video_file_url else video_link_embed(self.video_url)

    @property
    def has_video(self) -> bool:
        return bool(self.video_file_url or self.video_embed_url)

    @classmethod
    def visible_to(cls, user):
        """Published posts this owner may see: sent to everyone, or to them."""
        return (
            cls.objects.filter(is_published=True)
            .filter(Q(send_to_all=True) | Q(recipients=user))
            .distinct()
        )


class BusinessHubReceipt(BaseModel):
    """Records that one owner has been sent (notified + emailed) one post.

    Makes delivery idempotent: saving a post again, or adding more customers to
    it later, only reaches the owners who haven't been sent it yet.
    """

    post = models.ForeignKey(
        BusinessHubPost, on_delete=models.CASCADE, related_name="receipts"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="business_hub_receipts"
    )
    emailed = models.BooleanField(default=False)

    class Meta:
        verbose_name = "business hub delivery"
        verbose_name_plural = "business hub deliveries"
        constraints = [
            models.UniqueConstraint(fields=("post", "user"), name="uniq_hub_receipt_per_user")
        ]

    def __str__(self) -> str:
        return f"{self.post_id} → {self.user_id}"


# --------------------------------------------------------------------------
# Support tickets (raised from the Business Hub)
# --------------------------------------------------------------------------
SUPPORT_ATTACHMENT_EXTENSIONS = (
    "png", "jpg", "jpeg", "webp", "gif", "pdf", "txt", "csv", "doc", "docx", "xls", "xlsx",
)
#: Largest attachment accepted on a ticket.
SUPPORT_ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024


def _ticket_attachment_path(instance, filename):
    """A random folder per upload, so attachment URLs can't be guessed."""
    import uuid as _uuid

    from django.utils.text import get_valid_filename

    name = get_valid_filename(filename or "attachment")[-120:] or "attachment"
    return f"support/tickets/{_uuid.uuid4().hex}/{name}"


class SupportTicket(BaseModel):
    """A support request a store owner raises from the Business Hub.

    The owner writes a subject, picks a topic / priority / store, describes the
    issue and may attach one file. OneWooz staff work it in the admin: they set
    its status and write a reply, which the owner sees on the Business Hub (and
    is told about via the bell). Platform-wide, not tenant-scoped: a ticket
    belongs to the owner's account; ``site`` only records which store it is about.
    """

    class Category(models.TextChoices):
        GENERAL = "general", "General question"
        STORE = "store", "Store & content"
        ORDERS = "orders", "Orders & shipping"
        PAYMENTS = "payments", "Payments & billing"
        DOMAIN = "domain", "Custom domain"
        ACCOUNT = "account", "Account & login"
        BUG = "bug", "Something isn't working"
        OTHER = "other", "Other"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="support_tickets"
    )
    # An owner's reply on an existing ticket is raised as a new ticket of its
    # own (so it lands in the admin queue like any other), linked back to the
    # ticket it follows up. Always points at the thread's first ticket.
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True,
        related_name="follow_ups", verbose_name="follow-up of",
    )
    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="support_tickets", verbose_name="store",
    )
    subject = models.CharField(max_length=160)
    category = models.CharField(
        "topic", max_length=12, choices=Category.choices, default=Category.GENERAL
    )
    priority = models.CharField(
        max_length=8, choices=Priority.choices, default=Priority.NORMAL, db_index=True
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    message = models.TextField()
    attachment = models.FileField(
        upload_to=_ticket_attachment_path,
        blank=True,
        null=True,
        max_length=255,
        validators=[FileExtensionValidator(SUPPORT_ATTACHMENT_EXTENSIONS)],
    )
    attachment_name = models.CharField(max_length=255, blank=True, default="")
    reply = models.TextField(
        "reply from OneWooz", blank=True, default="",
        help_text="Shown to the owner on their Business Hub. Saving a new reply notifies them.",
    )
    replied_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "support ticket"
        verbose_name_plural = "support tickets"
        indexes = [models.Index(fields=("user", "-created_at"))]

    def __str__(self) -> str:
        return f"{self.reference} · {self.subject}"

    @property
    def reference(self) -> str:
        """Short human reference ("T-1A2B3C4D") derived from the UUID."""
        return "T-" + str(self.pk).replace("-", "")[-8:].upper() if self.pk else "T-NEW"

    @property
    def attachment_url(self) -> str:
        try:
            return self.attachment.url if self.attachment else ""
        except ValueError:
            return ""

    @property
    def is_open(self) -> bool:
        return self.status in (self.Status.OPEN, self.Status.IN_PROGRESS)

    @property
    def thread_root(self):
        """The first ticket of this conversation (itself when not a follow-up)."""
        return self.parent if self.parent_id else self
