"""Prompts written in Django admin, and the per-store output generated from them."""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from core.models import BaseModel, TenantScopedModel


class Prompt(BaseModel):
    """A system prompt an admin writes once and OneWooz reuses for a generation
    job. Exactly one active prompt per type is used; edit it, or add a new one
    and switch the old one off, to change what every store gets next time.
    """

    class Kind(models.TextChoices):
        LLMS_TXT = "llms_txt", "LLMs.txt generation"
        ASK = "ask", "Ask OneWooz answers (onewooz.com/ask)"
        MAGIC_SEO = "magic_seo", "Magic SEO (Edit content > SEO tab)"

    kind = models.CharField(
        "type", max_length=40, choices=Kind.choices, default=Kind.LLMS_TXT, db_index=True,
    )
    name = models.CharField(
        max_length=120, help_text="A label for this prompt, for your own reference.",
    )
    prompt = models.TextField(
        help_text=(
            "The instructions sent to ChatGPT as the system prompt. For LLMs.txt "
            "generation the store's own data and list of URLs are sent with it; for "
            "Ask OneWooz answers the visitor's question is sent with it; for Magic SEO "
            "the store's facts, the owner's brief and a batch of its pages are sent "
            "with it. Write here everything the AI should know and how it should answer."
        ),
    )
    model = models.CharField(
        "OpenAI model", max_length=120, blank=True, default="",
        help_text="Optional model code (e.g. gpt-4.1). Blank uses the server default (OPENAI_TEXT_MODEL).",
    )
    is_active = models.BooleanField(
        default=True, help_text="Only one active prompt per type is used.",
    )

    class Meta:
        verbose_name = "prompt"
        verbose_name_plural = "prompts"
        indexes = [models.Index(fields=["kind", "is_active"], name="prompt_kind_active_idx")]

    def __str__(self) -> str:
        return f"{self.get_kind_display()}: {self.name}"

    def clean(self):
        super().clean()
        if self.is_active:
            clash = Prompt.objects.filter(kind=self.kind, is_active=True).exclude(pk=self.pk)
            if clash.exists():
                raise ValidationError(
                    {"is_active": "Another prompt of this type is already active. Switch that one off first."}
                )

    @classmethod
    def active(cls, kind: str) -> "Prompt | None":
        return (
            cls.objects.filter(kind=kind, is_active=True)
            .only("id", "kind", "name", "prompt", "model", "updated_at")
            .order_by("-updated_at")
            .first()
        )


class StoreLlmsTxt(TenantScopedModel):
    """One store's generated ``/llms.txt`` and where its generation stands.

    ``fingerprint`` hashes the exact store data + prompt the file was built
    from, so a save that changes nothing relevant never spends an API call.
    """

    class Status(models.TextChoices):
        NOT_READY = "not_ready", "Waiting for required content"
        PENDING = "pending", "Generating"
        READY = "ready", "Published"
        FAILED = "failed", "Generation failed"

    site = models.OneToOneField(
        "tenancy.Site", on_delete=models.CASCADE, related_name="llms_txt",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NOT_READY)
    body = models.TextField(blank=True, default="")
    fingerprint = models.CharField(max_length=64, blank=True, default="")
    #: The readiness checks that failed last time, as shown to staff in admin.
    missing = models.JSONField(blank=True, default=list)
    error = models.TextField(blank=True, default="")
    model_used = models.CharField(max_length=120, blank=True, default="")
    prompt = models.ForeignKey(
        Prompt, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    generated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "store llms.txt"
        verbose_name_plural = "store llms.txt files"

    def __str__(self) -> str:
        return f"llms.txt ({self.get_status_display()})"


class MagicSeoRun(TenantScopedModel):
    """One click of the Magic SEO pencil in Edit content > SEO.

    The run fills the SEO tab (the brand set ``content["seo"]`` and every page
    in ``content["seo_pages"]``) from the store's own content, the owner's
    optional brief and the active "Magic SEO" admin prompt. ``previous`` keeps
    the SEO tab exactly as it was, so the owner can undo the run in one click.
    """

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Writing"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"
        UNDONE = "undone", "Undone by the owner"

    class Mode(models.TextChoices):
        REPLACE = "replace", "Write every field (replace what is there)"
        FILL = "fill", "Fill empty fields only"

    site = models.ForeignKey("tenancy.Site", on_delete=models.CASCADE, related_name="magic_seo_runs")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.REPLACE)
    #: The owner's optional brief: focus keywords, audience / location, tone.
    brief = models.JSONField(blank=True, default=dict)
    pages_total = models.PositiveIntegerField(default=0)
    pages_done = models.PositiveIntegerField(default=0)
    #: Pages the AI wrote (the rest, if any, got the page's own auto values).
    pages_written = models.PositiveIntegerField(default=0)
    #: Share images (OG + X card) created with ChatGPT images for pages that
    #: had none (prompts.seo_images): planned, attempted so far, and made.
    images_total = models.PositiveIntegerField(default=0)
    images_done = models.PositiveIntegerField(default=0)
    images_made = models.PositiveIntegerField(default=0)
    #: What the run changed and kept, per page and field (the editor's
    #: "Changed / Not changed" view). Can be large: load only when shown.
    report = models.JSONField(blank=True, default=dict)
    #: Counts from ``report`` (pages changed, fields changed / kept), small
    #: enough to send with every status poll.
    summary = models.JSONField(blank=True, default=dict)
    #: The SEO tab before this run: {"seo", "seo_pages", "seo_mode"}.
    previous = models.JSONField(blank=True, default=dict)
    error = models.TextField(blank=True, default="")
    model_used = models.CharField(max_length=120, blank=True, default="")
    prompt = models.ForeignKey(Prompt, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Magic SEO run"
        verbose_name_plural = "Magic SEO runs"
        indexes = [models.Index(fields=["site", "created_at"], name="magicseo_site_created_idx")]

    def __str__(self) -> str:
        return f"Magic SEO ({self.get_status_display()})"

    @property
    def is_active(self) -> bool:
        return self.status in (self.Status.QUEUED, self.Status.RUNNING)


class AskQuestion(BaseModel):
    """A question a visitor asked on onewooz.com/ask, and its answer.

    Answered by ChatGPT with the active "Ask OneWooz answers" prompt, by staff
    in Django admin, or both. A staff answer always wins: once written, it is
    what the visitor sees, whatever the AI said. Platform-level, not tenant
    data, so it is a plain :class:`BaseModel`.

    The visitor reaches their question only through a signed capability URL
    (``core.tokens`` purpose ``ask.question``). It is never listed publicly; to
    go public the visitor posts it to the community, which copies it into a
    ``community.Question`` (reverse accessor ``community_question``).
    """

    class Status(models.TextChoices):
        PENDING = "pending", "AI is answering"
        ANSWERED = "answered", "Answered"
        WAITING = "waiting", "Waiting for a team reply"

    class Source(models.TextChoices):
        NONE = "", "Not answered"
        AI = "ai", "AI"
        STAFF = "staff", "OneWooz team"

    #: Shortest and longest question accepted from the public form.
    MIN_LENGTH = 8
    MAX_LENGTH = 1000

    question = models.TextField(max_length=MAX_LENGTH)
    email = models.EmailField(
        blank=True, default="",
        help_text="Given by the visitor to be told when the team replies. Never shown publicly.",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)

    ai_answer = models.TextField("AI answer", blank=True, default="")
    ai_model = models.CharField("AI model", max_length=120, blank=True, default="")
    ai_error = models.TextField("AI error", blank=True, default="")
    prompt = models.ForeignKey(
        Prompt, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )

    staff_answer = models.TextField(
        "your answer", blank=True, default="",
        help_text=(
            "Write your own answer here. When filled, the visitor sees this instead "
            "of the AI answer. Plain text: blank lines start a new paragraph, lines "
            "starting with '- ' become a list, **text** is bold."
        ),
    )
    answered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    answered_at = models.DateTimeField(null=True, blank=True)
    notified_at = models.DateTimeField(
        "asker emailed", null=True, blank=True,
        help_text="When the visitor was last emailed about a team reply.",
    )

    #: Hashed visitor IP, so abuse from one source can be spotted without
    #: storing the address itself.
    ip_hash = models.CharField(max_length=64, blank=True, default="", editable=False)

    class Meta:
        verbose_name = "Ask OneWooz question"
        verbose_name_plural = "Ask OneWooz questions"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["status", "created_at"], name="ask_status_created_idx"),
        ]

    def __str__(self) -> str:
        text = " ".join((self.question or "").split())
        return text if len(text) <= 80 else f"{text[:79]}…"

    @property
    def answer(self) -> str:
        """What the visitor sees: the team's answer, else the AI's."""
        return (self.staff_answer or "").strip() or (self.ai_answer or "").strip()

    @property
    def answer_source(self) -> str:
        if (self.staff_answer or "").strip():
            return self.Source.STAFF
        if (self.ai_answer or "").strip():
            return self.Source.AI
        return self.Source.NONE

    def public_token(self) -> str:
        from core import tokens

        return tokens.sign("ask.question", {"q": str(self.pk)})
