from __future__ import annotations

from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join

from custom_admin.admin import LeanModelAdmin, TenantSafeModelAdmin

from django import forms
from django.utils import timezone

from .models import AskQuestion, MagicSeoRun, Prompt, StoreLlmsTxt


@admin.register(Prompt)
class PromptAdmin(LeanModelAdmin):
    list_display = ("name", "kind", "is_active", "model", "updated_at")
    list_filter = ("kind", "is_active")
    search_fields = ("name", "prompt")
    fieldsets = (
        (None, {"fields": ("kind", "name", "is_active")}),
        ("Prompt", {
            "fields": ("prompt", "model"),
            "description": (
                "Sent to ChatGPT as the system prompt. For an LLMs.txt generation "
                "prompt, OneWooz adds the store's data, its list of allowed URLs and "
                "the output rules (llmstxt.org format, own links only, no em dashes) "
                "after it automatically. For an Ask OneWooz answers prompt, write "
                "everything the AI should know about OneWooz and how it should "
                "answer; OneWooz adds the visitor's question and the output rules "
                "(short plain answers, no invented facts, no em dashes) automatically. "
                "With no active Ask prompt, questions wait for a team reply. "
                "For a Magic SEO prompt (the pencil in Edit content > SEO, for owners with "
                "the Magic SEO app), write how the SEO should be written; OneWooz adds the "
                "store's facts, the owner's brief (keywords, audience, location, tone, "
                "language), a batch of pages with their content, and the output rules "
                "(JSON, length limits, no em dashes) automatically. A default Magic SEO "
                "prompt is created the first time an owner uses the pencil if none exists."
            ),
        }),
    )
    # Both add a "Generate llms.txt for waiting stores" button to the toolbar.
    change_list_template = "admin/prompts/prompt/change_list.html"
    change_form_template = "admin/prompts/prompt/change_form.html"

    def get_urls(self):
        # Before the object-id catch-all Django appends.
        mine = [
            path(
                "generate-llms-txt/",
                self.admin_site.admin_view(self.generate_llms_txt_view),
                name="prompts_prompt_generate_llms_txt",
            ),
        ]
        return mine + super().get_urls()

    def _generate_context(self) -> dict:
        from .tasks import eligible_site_ids

        try:
            waiting = len(eligible_site_ids())
        except Exception:
            waiting = None
        return {
            "llms_generate_url": reverse("admin:prompts_prompt_generate_llms_txt"),
            "llms_waiting_count": waiting,
        }

    def changelist_view(self, request, extra_context=None):
        return super().changelist_view(request, {**(extra_context or {}), **self._generate_context()})

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        return super().changeform_view(
            request, object_id, form_url, {**(extra_context or {}), **self._generate_context()},
        )

    def generate_llms_txt_view(self, request):
        """Queue one background job that generates llms.txt for every store on
        a verified domain without a published file whose content is ready."""
        from django.core.cache import cache

        from .tasks import BULK_LOCK_KEY, BULK_LOCK_SECONDS, generate_missing_store_llms_txt

        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        if not self.has_change_permission(request):
            raise PermissionDenied

        back = request.POST.get("next") or reverse("admin:prompts_prompt_changelist")
        if not back.startswith("/"):
            back = reverse("admin:prompts_prompt_changelist")

        if Prompt.active(Prompt.Kind.LLMS_TXT) is None:
            self.message_user(
                request,
                "There is no active 'LLMs.txt generation' prompt. Add or switch one on first.",
                level=messages.ERROR,
            )
            return HttpResponseRedirect(back)
        if not getattr(settings, "OPENAI_API_KEY", ""):
            self.message_user(request, "OPENAI_API_KEY is not set on the server.", level=messages.ERROR)
            return HttpResponseRedirect(back)

        try:
            free = cache.add(BULK_LOCK_KEY, 1, BULK_LOCK_SECONDS)
        except Exception:
            free = True
        if not free:
            self.message_user(
                request,
                "A generation run is already in progress. Check Store llms.txt files for results.",
                level=messages.WARNING,
            )
            return HttpResponseRedirect(back)

        try:
            generate_missing_store_llms_txt.delay()
        except Exception as exc:
            try:
                cache.delete(BULK_LOCK_KEY)
            except Exception:
                pass
            self.message_user(request, f"Could not start the job: {exc}", level=messages.ERROR)
            return HttpResponseRedirect(back)

        from core import audit

        try:
            audit.record("admin.llms_txt.bulk_generate", request=request, actor=request.user, actor_kind="admin")
        except Exception:
            pass
        self.message_user(
            request,
            "Started in the background. Every store on a verified domain without an "
            "llms.txt is checked; those with the required content filled in are "
            "generated one by one (about 20 seconds apart). Results appear under "
            "Store llms.txt files.",
            level=messages.SUCCESS,
        )
        return HttpResponseRedirect(back)


@admin.register(StoreLlmsTxt)
class StoreLlmsTxtAdmin(TenantSafeModelAdmin):
    list_display = ("site", "status", "generated_at", "model_used")
    list_filter = ("status",)
    list_select_related = ("site",)
    search_fields = ("site__name", "site__custom_domain")
    readonly_fields = (
        "site", "status", "requirements", "error", "generated_at", "model_used",
        "prompt", "fingerprint", "body",
    )
    fields = readonly_fields
    actions = ("regenerate",)

    def has_add_permission(self, request):
        return False  # created by the generator, one per store

    @admin.display(description="Requirements")
    def requirements(self, obj):
        from .llms_txt import readiness

        try:
            checks = readiness(obj.site)
        except Exception as exc:  # never break the admin page
            return f"Could not check: {exc}"
        return format_html(
            "<ul style='margin:0;padding-left:1.1rem'>{}</ul>",
            format_html_join(
                "", "<li>{} {}</li>",
                (("✔" if c["ok"] else "✘", c["label"]) for c in checks),
            ),
        )

    @admin.action(description="Regenerate now (ignores the unchanged-data check)")
    def regenerate(self, request, queryset):
        from .tasks import generate_store_llms_txt

        count = 0
        for record in queryset.only("id", "site_id"):
            generate_store_llms_txt.delay(str(record.site_id), True)
            count += 1
        messages.success(request, f"Queued {count} store(s) for regeneration.")


@admin.register(MagicSeoRun)
class MagicSeoRunAdmin(TenantSafeModelAdmin):
    """Every Magic SEO pencil click: who, when, how far it got, and why it
    failed (owners only ever see a plain "try again" message)."""

    list_display = ("site", "status", "mode", "progress", "pages_written", "share_images", "model_used",
                    "created_at", "finished_at")
    list_filter = ("status", "mode")
    list_select_related = ("site",)
    search_fields = ("site__name", "site__slug")
    readonly_fields = (
        "site", "status", "mode", "brief", "pages_total", "pages_done", "pages_written",
        "images_total", "images_done", "images_made", "summary", "error",
        "model_used", "prompt", "requested_by", "started_at", "finished_at", "created_at",
    )
    fields = readonly_fields

    def has_add_permission(self, request):
        return False  # created by the pencil

    def has_change_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        # The undo snapshot can be large: never load it in the admin.
        return super().get_queryset(request).defer("previous", "report")

    @admin.display(description="Progress")
    def progress(self, obj):
        return f"{obj.pages_done} / {obj.pages_total}" if obj.pages_total else "-"

    @admin.display(description="Share images")
    def share_images(self, obj):
        return f"{obj.images_made} / {obj.images_total}" if obj.images_total else "-"


class AskQuestionForm(forms.ModelForm):
    email_asker = forms.BooleanField(
        label="Email the asker", required=False, initial=True,
        help_text=(
            "When you save a new or changed answer, email the visitor a link to it "
            "(only if they left an email address)."
        ),
    )

    class Meta:
        model = AskQuestion
        fields = "__all__"


@admin.register(AskQuestion)
class AskQuestionAdmin(LeanModelAdmin):
    """Questions from onewooz.com/ask. Write "Your answer" to answer one
    yourself; it replaces the AI answer on the visitor's page."""

    form = AskQuestionForm
    list_display = ("short_question", "status", "answer_source_label", "posted_label", "created_at")
    list_filter = ("status",)
    search_fields = ("question",)
    list_per_page = 50
    actions = ("regenerate_ai",)
    readonly_fields = (
        "visitor_page", "email", "ai_answer_view", "ai_model", "ai_error", "prompt",
        "answered_by", "answered_at", "notified_at", "created_at",
    )

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            # Staff adding a question themselves (e.g. a common one to list on /ask).
            return ((None, {"fields": ("question", "staff_answer")}),)
        return (
            (None, {"fields": ("question", "created_at", "status", "visitor_page", "email")}),
            ("Your answer", {
                "fields": ("staff_answer", "email_asker"),
                "description": (
                    "Leave blank to keep the AI answer. Anything you write here is "
                    "what the visitor sees instead."
                ),
            }),
            ("AI answer", {
                "fields": ("ai_answer_view", "ai_model", "prompt", "ai_error"),
                "classes": ("collapse",) if (obj.staff_answer or "").strip() else (),
            }),
            ("History", {"fields": ("answered_by", "answered_at", "notified_at")}),
        )

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        return ("question", "status") + self.readonly_fields

    def get_form(self, request, obj=None, change=False, **kwargs):
        # Remember the request so "Visitor's page" links work in local
        # path-prefix development as well as on the real hosts.
        self._request = request
        return super().get_form(request, obj, change=change, **kwargs)

    # ------------------------------------------------------------- columns
    @admin.display(description="Question", ordering="created_at")
    def short_question(self, obj):
        return str(obj)

    @admin.display(description="Answered by")
    def answer_source_label(self, obj):
        return dict(AskQuestion.Source.choices).get(obj.answer_source, "")

    @admin.display(description="Visitor's page")
    def visitor_page(self, obj):
        from .ask import public_url

        if not obj.pk:
            return "-"
        try:
            url = public_url(obj, request=getattr(self, "_request", None))
        except Exception:
            return "-"
        return format_html('<a href="{}" target="_blank" rel="noopener">Open the answer page</a>', url)

    @admin.display(description="AI answer")
    def ai_answer_view(self, obj):
        from .ask import render_html

        if not (obj.ai_answer or "").strip():
            return "-"
        return format_html(
            '<div style="max-width:46rem;line-height:1.55">{}</div>', render_html(obj.ai_answer),
        )

    # -------------------------------------------------------------- saving
    def save_model(self, request, obj, form, change):
        answer_changed = "staff_answer" in form.changed_data
        obj.staff_answer = (obj.staff_answer or "").strip()
        if obj.staff_answer and (answer_changed or not change):
            obj.status = AskQuestion.Status.ANSWERED
            obj.answered_by = request.user
            obj.answered_at = timezone.now()
        elif not obj.staff_answer and not (obj.ai_answer or "").strip():
            if not change or answer_changed:
                obj.status = AskQuestion.Status.WAITING
        super().save_model(request, obj, form, change)

        if (
            change and answer_changed and obj.staff_answer and obj.email
            and form.cleaned_data.get("email_asker")
        ):
            self._email_asker(request, obj)

    def _email_asker(self, request, obj):
        from accounts.emails import send_ask_answer

        from .ask import public_url

        preview = " ".join(obj.staff_answer.replace("**", "").split())
        if len(preview) > 280:
            preview = preview[:279].rsplit(" ", 1)[0] + "…"
        try:
            sent = send_ask_answer(obj.email, question=obj.question, preview=preview, url=public_url(obj))
        except Exception:
            sent = False
        if sent:
            AskQuestion.objects.filter(pk=obj.pk).update(notified_at=timezone.now())
            self.message_user(request, "The asker was emailed a link to your answer.", messages.SUCCESS)
        else:
            self.message_user(request, "Saved, but the email to the asker could not be sent.", messages.WARNING)

    # ------------------------------------------------------------- actions
    @admin.action(description="Generate the AI answer again")
    def regenerate_ai(self, request, queryset):
        from . import ask

        if not ask.ai_configured():
            self.message_user(
                request,
                "No active 'Ask OneWooz answers' prompt, or OPENAI_API_KEY is not set.",
                messages.ERROR,
            )
            return
        count = 0
        for question in queryset.only("id", "staff_answer"):
            if not (question.staff_answer or "").strip():
                AskQuestion.objects.filter(pk=question.pk).update(status=AskQuestion.Status.PENDING)
            ask.queue(question)
            count += 1
        self.message_user(request, f"Queued {count} question(s) for a fresh AI answer.", messages.SUCCESS)

    @admin.display(description="Posted to community", boolean=True)
    def posted_label(self, obj):
        return getattr(obj, "community_question", None) is not None
