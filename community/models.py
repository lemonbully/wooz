"""Ask OneWooz community: topics, members, questions, answers, replies, votes.

Platform-level data (not tenant data), so everything is a plain
:class:`core.models.BaseModel` with a UUIDv7 key.

Counters (``score``, ``answer_count``, a member's ``reputation`` …) are kept
on the rows so feeds never aggregate at read time; :mod:`community.services`
is the only place that changes them.

Questions and answers carry TEXT columns, so every ordered list of them goes
through :mod:`core.queries` (only primary keys are sorted).
"""

from __future__ import annotations

from django.db import models
from django.urls import reverse
from django.utils import timezone

from core.models import BaseModel


class Visibility(models.TextChoices):
    PUBLISHED = "published", "Published"
    HELD = "held", "Held for review"
    HIDDEN = "hidden", "Hidden"


class Topic(BaseModel):
    """A room in the community (Payments, Shipping …)."""

    #: Created automatically the first time the community is opened with no
    #: topics, so there is nothing to set up by hand. (name, slug, description)
    DEFAULTS = (
        ("Starting out", "starting-out", "Registering, pricing and your first sales."),
        ("Payments", "payments", "Gateways, COD, payouts and refunds."),
        ("Shipping", "shipping", "Couriers, pickups, labels and returns."),
        ("GST and invoices", "gst-and-invoices", "Tax registration, HSN codes and invoices."),
        ("Domains", "domains", "Custom domains, DNS and email."),
        ("Marketing", "marketing", "Email, discounts, SEO and social."),
        ("Catalog", "catalog", "Products, variants, photos and stock."),
        ("Wholesale", "wholesale", "B2B buyers, quantity pricing and enquiries."),
    )

    name = models.CharField(max_length=60)
    slug = models.SlugField(max_length=70, unique=True)
    description = models.CharField(max_length=200, blank=True, default="")
    position = models.PositiveSmallIntegerField(default=0, help_text="Lower comes first.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("position", "name")
        verbose_name = "topic"
        verbose_name_plural = "topics"

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("ask:topic", kwargs={"slug": self.slug})

    @classmethod
    def ensure_defaults(cls) -> None:
        if cls.objects.exists():
            return
        cls.objects.bulk_create(
            [
                cls(name=name, slug=slug, description=desc, position=i * 10)
                for i, (name, slug, desc) in enumerate(cls.DEFAULTS)
            ],
            ignore_conflicts=True,
        )


#: Email domain of names the team writes under from the console ("posted by"
#: someone who has no account). ``.invalid`` can never receive mail or a
#: sign-in code, so nobody can sign in as one of these names.
PERSONA_DOMAIN = "persona.ask.invalid"


class Member(BaseModel):
    """A person who posts in the community, identified by a confirmed email.

    Never shown publicly: the email. Shown: display name, handle, level and,
    when the email owns a OneWooz store, a link to that store.

    A member whose email is on :data:`PERSONA_DOMAIN` is a name the team
    posts under from the console (see :mod:`community.authoring`).
    """

    #: Reputation needed for each level, highest first.
    LEVELS = ((200, "Expert"), (50, "Trusted"), (10, "Contributor"), (0, "New"))

    email = models.EmailField(unique=True)
    display_name = models.CharField(max_length=40)
    handle = models.SlugField(max_length=50, unique=True)
    bio = models.CharField(max_length=160, blank=True, default="")

    is_team = models.BooleanField(
        "OneWooz team", default=False,
        help_text="Shows the 'OneWooz team' label on everything this member posts.",
    )
    is_banned = models.BooleanField(
        default=False,
        help_text="A banned member can still read, but cannot ask, answer, reply, vote or report.",
    )
    ban_reason = models.CharField(max_length=300, blank=True, default="")
    banned_at = models.DateTimeField(null=True, blank=True)

    store_name = models.CharField(max_length=120, blank=True, default="")
    store_url = models.URLField(blank=True, default="")

    #: Optional, chosen by the member when they join or from their profile,
    #: and validated by community.profile. The photo is re-encoded to a
    #: 256 px square WebP, so the original upload's bytes are never served.
    avatar = models.ImageField("profile photo", upload_to="ask/avatars/", blank=True, default="")
    website = models.URLField(
        max_length=300, blank=True, default="",
        help_text="Shown next to their name on every question and answer (nofollow).",
    )
    brand_name = models.CharField(
        "brand name", max_length=60, blank=True, default="",
        help_text="Their store or brand, shown next to their name (as the website link text when both are set).",
    )

    notify_answers = models.BooleanField(
        "email me new answers", default=True,
        help_text="Email this member when someone answers their question (at most once an hour per question).",
    )

    reputation = models.IntegerField(default=0, db_index=True)
    question_count = models.PositiveIntegerField(default=0)
    answer_count = models.PositiveIntegerField(default=0)
    accepted_count = models.PositiveIntegerField(default=0)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "member"
        verbose_name_plural = "members"

    def __str__(self) -> str:
        return self.display_name

    def get_absolute_url(self) -> str:
        return reverse("ask:profile", kwargs={"handle": self.handle})

    @property
    def level(self) -> str:
        if self.is_team:
            return "OneWooz team"
        for floor, name in self.LEVELS:
            if self.reputation >= floor:
                return name
        return "New"

    @property
    def safe_website(self) -> str:
        """The website only when it is a plain http(s) address, else "".

        Templates link this, never ``website`` directly, so a value saved
        some other way (the admin accepts ftp:, a bad import) can't become a
        ``javascript:`` or other unexpected link.
        """
        from urllib.parse import urlsplit

        url = (self.website or "").strip()
        try:
            parts = urlsplit(url)
        except ValueError:
            return ""
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            return ""
        return url

    @property
    def site_label(self) -> str:
        """What the website link says: the brand name, else the address."""
        return self.brand_name or self.website_label

    @property
    def website_label(self) -> str:
        """The website as people read it: ``brand.com/shop``, no scheme or www."""
        from urllib.parse import urlsplit

        parts = urlsplit(self.safe_website)
        host = (parts.hostname or "").removeprefix("www.")
        path = parts.path.rstrip("/")
        label = host + path
        return label if len(label) <= 40 else label[:39] + "…"

    @property
    def is_persona(self) -> bool:
        """A name the team writes under from the console, not a real sign-in."""
        return (self.email or "").lower().endswith("@" + PERSONA_DOMAIN)

    @property
    def initials(self) -> str:
        parts = [p for p in self.display_name.split() if p]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()


class Question(BaseModel):
    topic = models.ForeignKey(
        Topic, on_delete=models.SET_NULL, null=True, blank=True, related_name="questions",
    )
    author = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="questions")
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=230, unique=True)
    body = models.TextField("details", blank=True, default="", max_length=5000)
    #: The instant AI answer the asker saw before posting, shown folded on the
    #: thread. Copied, so editing the prompt later never rewrites history.
    ai_answer = models.TextField("AI answer", blank=True, default="")
    instant = models.OneToOneField(
        "prompts.AskQuestion", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="community_question",
    )

    status = models.CharField(max_length=12, choices=Visibility.choices, default=Visibility.PUBLISHED)
    hold_reason = models.CharField(max_length=120, blank=True, default="")
    report_count = models.PositiveSmallIntegerField(default=0)

    score = models.IntegerField(default=0)
    answer_count = models.PositiveIntegerField(default=0)
    view_count = models.PositiveIntegerField(default=0)
    accepted_answer = models.ForeignKey(
        "Answer", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    has_team_answer = models.BooleanField(default=False)
    last_activity_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "question"
        verbose_name_plural = "questions"
        indexes = [
            models.Index(fields=["status", "created_at"], name="cq_status_created_idx"),
            models.Index(fields=["status", "score"], name="cq_status_score_idx"),
            models.Index(fields=["status", "last_activity_at"], name="cq_status_activity_idx"),
            models.Index(fields=["status", "answer_count", "created_at"], name="cq_unanswered_idx"),
            models.Index(fields=["topic", "status", "created_at"], name="cq_topic_idx"),
        ]

    def __str__(self) -> str:
        return self.title

    def get_absolute_url(self) -> str:
        return reverse("ask:question", kwargs={"slug": self.slug})


class Answer(BaseModel):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="answers")
    author = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="answers")
    body = models.TextField(max_length=5000)

    status = models.CharField(max_length=12, choices=Visibility.choices, default=Visibility.PUBLISHED)
    hold_reason = models.CharField(max_length=120, blank=True, default="")
    report_count = models.PositiveSmallIntegerField(default=0)

    score = models.IntegerField(default=0)
    is_accepted = models.BooleanField(default=False)

    class Meta:
        verbose_name = "answer"
        verbose_name_plural = "answers"
        indexes = [
            models.Index(fields=["question", "status"], name="ca_question_status_idx"),
            models.Index(fields=["author", "status", "created_at"], name="ca_author_idx"),
        ]

    def __str__(self) -> str:
        text = " ".join(self.body.split())
        return text if len(text) <= 80 else f"{text[:79]}…"


class Reply(BaseModel):
    """A short reply under an answer. One level only, never nested."""

    MAX_LENGTH = 600

    answer = models.ForeignKey(Answer, on_delete=models.CASCADE, related_name="replies")
    author = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="replies")
    body = models.TextField(max_length=MAX_LENGTH)
    status = models.CharField(max_length=12, choices=Visibility.choices, default=Visibility.PUBLISHED)
    hold_reason = models.CharField(max_length=120, blank=True, default="")
    report_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = "reply"
        verbose_name_plural = "replies"
        indexes = [models.Index(fields=["answer", "status", "created_at"], name="cr_answer_idx")]

    def __str__(self) -> str:
        text = " ".join(self.body.split())
        return text if len(text) <= 80 else f"{text[:79]}…"


class Vote(BaseModel):
    """One member's upvote on one question or one answer."""

    member = models.ForeignKey(Member, on_delete=models.CASCADE, related_name="votes")
    question = models.ForeignKey(
        Question, on_delete=models.CASCADE, null=True, blank=True, related_name="votes",
    )
    answer = models.ForeignKey(
        Answer, on_delete=models.CASCADE, null=True, blank=True, related_name="votes",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["member", "question"], condition=models.Q(question__isnull=False),
                name="cv_one_vote_per_question",
            ),
            models.UniqueConstraint(
                fields=["member", "answer"], condition=models.Q(answer__isnull=False),
                name="cv_one_vote_per_answer",
            ),
        ]


class Report(BaseModel):
    """A reader flagging a question, answer or reply for the team to check."""

    class Reason(models.TextChoices):
        SPAM = "spam", "Spam or advertising"
        RUDE = "rude", "Rude or abusive"
        WRONG = "wrong", "Wrong or misleading"
        PRIVATE = "private", "Shares private details"
        OTHER = "other", "Something else"

    #: Reports that hold a post automatically until the team looks at it.
    AUTO_HOLD_AT = 3

    reporter = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, related_name="+")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    answer = models.ForeignKey(Answer, on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    reply = models.ForeignKey(Reply, on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    reason = models.CharField(max_length=12, choices=Reason.choices, default=Reason.SPAM)
    is_resolved = models.BooleanField(default=False, db_index=True)

    class Meta:
        verbose_name = "report"
        verbose_name_plural = "reports"
        constraints = [
            models.UniqueConstraint(
                fields=["reporter", "question"], condition=models.Q(question__isnull=False),
                name="crep_one_per_question",
            ),
            models.UniqueConstraint(
                fields=["reporter", "answer"], condition=models.Q(answer__isnull=False),
                name="crep_one_per_answer",
            ),
            models.UniqueConstraint(
                fields=["reporter", "reply"], condition=models.Q(reply__isnull=False),
                name="crep_one_per_reply",
            ),
        ]

    @property
    def target(self):
        return self.question or self.answer or self.reply


class DigestSubscriber(BaseModel):
    """Someone who gets the Ask OneWooz weekly digest every Monday.

    Members subscribe with their already-confirmed email in one click; anyone
    else confirms through a signed link first (``ask.digest.confirm``).
    """

    email = models.EmailField(unique=True)
    member = models.ForeignKey(
        Member, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    is_confirmed = models.BooleanField(default=False, db_index=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    unsubscribed_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        verbose_name = "digest subscriber"
        verbose_name_plural = "digest subscribers"

    def __str__(self) -> str:
        return self.email

    @property
    def is_active(self) -> bool:
        return self.is_confirmed and self.unsubscribed_at is None


class DigestIssue(BaseModel):
    """One sent weekly digest (ISO year + week). The unique pair is what stops
    the hourly Beat job from sending the same week twice."""

    year = models.PositiveSmallIntegerField()
    week = models.PositiveSmallIntegerField()
    started_at = models.DateTimeField(default=timezone.now)
    recipients = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "digest issue"
        verbose_name_plural = "digest issues"
        constraints = [
            models.UniqueConstraint(fields=["year", "week"], name="cdi_one_per_week"),
        ]

    def __str__(self) -> str:
        return f"Week {self.week}, {self.year}"


class CommunitySettings(BaseModel):
    """Site-wide switches for Ask OneWooz, edited in the admin console
    (Ask OneWooz → Settings). One row; :meth:`current` creates it on first use.
    """

    posting_open = models.BooleanField(
        default=True,
        help_text="Off makes the community read-only: nobody can post questions, answers or replies.",
    )
    approve_all = models.BooleanField(
        default=False, help_text="Hold every new question, answer and reply until the team approves it.",
    )
    hold_links = models.BooleanField(
        default=True, help_text="Hold posts that contain a link (OneWooz team posts are never held).",
    )
    ai_answers_enabled = models.BooleanField(
        "instant AI answers", default=True,
        help_text="Off sends every question from the ask box straight to the team instead of the AI.",
    )
    digest_enabled = models.BooleanField(
        "weekly digest email", default=True, help_text="Off pauses the Monday digest email.",
    )
    report_threshold = models.PositiveSmallIntegerField(
        default=3, help_text="Reader reports that hold a post automatically until the team checks it.",
    )
    blocked_phrases = models.TextField(
        blank=True, default="",
        help_text="One per line. A post containing any of these is held for review.",
    )
    notice = models.CharField(
        max_length=240, blank=True, default="",
        help_text="Optional message shown at the top of every Ask OneWooz page.",
    )

    class Meta:
        verbose_name = "community settings"
        verbose_name_plural = "community settings"

    def __str__(self) -> str:
        return "Ask OneWooz settings"

    @classmethod
    def current(cls) -> "CommunitySettings":
        row = cls.objects.order_by().first()
        if row is None:
            row = cls.objects.create()
        return row

    @property
    def phrases(self) -> list[str]:
        """Every blocked entry, lower-cased, one word or several."""
        return [term.lower() for term in split_terms(self.blocked_phrases)]

    @property
    def blocked_terms(self) -> list[tuple[str, str]]:
        """``[(term, "keyword" | "phrase"), ...]``: one word is a keyword,
        more than one is a phrase."""
        return [(term, term_kind(term)) for term in self.phrases]


#: Longest single blocked keyword or phrase kept.
MAX_TERM_LENGTH = 120


def split_terms(text: str) -> list[str]:
    """Blocked keywords and phrases from what an admin typed: separated by
    commas or new lines, inner spaces collapsed, blanks and repeats dropped
    (case-insensitively), original order kept."""
    seen, terms = set(), []
    for raw in (text or "").replace("\r", "\n").replace(",", "\n").split("\n"):
        term = " ".join(raw.split())[:MAX_TERM_LENGTH].strip()
        if term and term.lower() not in seen:
            seen.add(term.lower())
            terms.append(term)
    return terms


def term_kind(term: str) -> str:
    return "phrase" if len(term.split()) > 1 else "keyword"
