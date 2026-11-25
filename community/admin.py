from __future__ import annotations

from django.contrib import admin, messages

from custom_admin.admin import LeanModelAdmin

from . import services
from .models import (
    Answer, DigestIssue, DigestSubscriber, Member, Question, Reply, Report, Topic, Visibility,
)


@admin.register(Topic)
class TopicAdmin(LeanModelAdmin):
    list_display = ("name", "slug", "position", "is_active")
    list_editable = ("position", "is_active")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)


@admin.register(Member)
class MemberAdmin(LeanModelAdmin):
    list_display = ("display_name", "handle", "email", "level_label", "reputation", "answer_count",
                    "is_team", "is_banned", "created_at")
    list_filter = ("is_team", "is_banned")
    search_fields = ("display_name", "handle", "email")
    ordering = ("-created_at",)
    readonly_fields = ("reputation", "question_count", "answer_count", "accepted_count",
                       "last_seen_at", "created_at")
    fieldsets = (
        (None, {"fields": ("display_name", "handle", "email", "bio", "avatar", "brand_name", "website")}),
        ("Labels and moderation", {"fields": ("is_team", "is_banned", "ban_reason", "banned_at",
                                              "notify_answers", "store_name", "store_url")}),
        ("Activity", {"fields": readonly_fields}),
    )
    actions = ("recount",)

    @admin.display(description="Level")
    def level_label(self, obj):
        return obj.level

    @admin.action(description="Recount posts and reputation")
    def recount(self, request, queryset):
        for member in queryset:
            services.refresh_member(member)
        self.message_user(request, "Recounted.")


class _ModerationMixin:
    """Publish / hold / hide actions that keep the counters right."""

    actions = ("publish", "hide")

    def _apply(self, request, queryset, status, label):
        for obj in queryset:
            services.set_status(obj, status, "" if status == Visibility.PUBLISHED else "Moderated by the team")
        self.message_user(request, f"{queryset.count()} {label}.", messages.SUCCESS)

    @admin.action(description="Publish")
    def publish(self, request, queryset):
        self._apply(request, queryset, Visibility.PUBLISHED, "published")

    @admin.action(description="Hide")
    def hide(self, request, queryset):
        self._apply(request, queryset, Visibility.HIDDEN, "hidden")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if "status" in form.changed_data:
            services.set_status(obj, obj.status, obj.hold_reason, previous=form.initial.get("status"))
        elif {"score", "author"} & set(form.changed_data) and hasattr(obj, "author"):
            # A vote count set by hand moves the author's reputation too.
            services.refresh_member(obj.author)
            previous = form.initial.get("author")
            if previous and str(previous) != str(obj.author_id):
                for member in Member.objects.filter(pk=previous):
                    services.refresh_member(member)


@admin.register(Question)
class QuestionAdmin(_ModerationMixin, LeanModelAdmin):
    list_display = ("title", "topic", "author", "status", "score", "answer_count", "report_count", "created_at")
    list_filter = ("status", "topic", "has_team_answer")
    list_select_related = ("topic", "author")
    search_fields = ("title", "body")
    ordering = ("-created_at",)
    raw_id_fields = ("author", "instant", "accepted_answer")
    # Votes (score) and views can be set by hand; readers' votes and visits add
    # to them. To write a whole question with answers, dates and names, use the
    # Ask OneWooz console: Questions, Write a question.
    readonly_fields = ("answer_count", "has_team_answer", "last_activity_at", "report_count", "created_at")
    fieldsets = (
        (None, {"fields": ("title", "slug", "topic", "author", "body")}),
        ("Moderation", {"fields": ("status", "hold_reason", "report_count")}),
        ("Instant AI answer", {"fields": ("ai_answer", "instant"), "classes": ("collapse",)}),
        ("Activity", {"fields": ("score", "answer_count", "view_count", "accepted_answer",
                                 "has_team_answer", "last_activity_at", "created_at")}),
    )


@admin.register(Answer)
class AnswerAdmin(_ModerationMixin, LeanModelAdmin):
    list_display = ("__str__", "question", "author", "status", "score", "is_accepted", "report_count", "created_at")
    list_filter = ("status", "is_accepted")
    list_select_related = ("question", "author")
    search_fields = ("body",)
    ordering = ("-created_at",)
    raw_id_fields = ("question", "author")
    readonly_fields = ("is_accepted", "report_count", "created_at")


@admin.register(Reply)
class ReplyAdmin(_ModerationMixin, LeanModelAdmin):
    list_display = ("__str__", "author", "status", "report_count", "created_at")
    list_filter = ("status",)
    list_select_related = ("author",)
    search_fields = ("body",)
    ordering = ("-created_at",)
    raw_id_fields = ("answer", "author")


@admin.register(Report)
class ReportAdmin(LeanModelAdmin):
    list_display = ("target_label", "reason", "reporter", "is_resolved", "created_at")
    list_filter = ("is_resolved", "reason")
    list_select_related = ("reporter",)
    ordering = ("-created_at",)
    raw_id_fields = ("reporter", "question", "answer", "reply")
    actions = ("resolve", "hide_target")

    @admin.display(description="Reported post")
    def target_label(self, obj):
        target = obj.target
        return f"{target.__class__.__name__}: {target}" if target else "-"

    @admin.action(description="Mark resolved (keep the post)")
    def resolve(self, request, queryset):
        updated = queryset.update(is_resolved=True)
        self.message_user(request, f"{updated} report(s) resolved.")

    @admin.action(description="Hide the reported post and resolve")
    def hide_target(self, request, queryset):
        count = 0
        for rep in queryset:
            if rep.target is not None:
                services.set_status(rep.target, Visibility.HIDDEN, "Hidden after a report")
                count += 1
        queryset.update(is_resolved=True)
        self.message_user(request, f"{count} post(s) hidden.")


@admin.register(DigestSubscriber)
class DigestSubscriberAdmin(LeanModelAdmin):
    list_display = ("email", "member", "is_confirmed", "confirmed_at", "unsubscribed_at", "created_at")
    list_filter = ("is_confirmed",)
    list_select_related = ("member",)
    search_fields = ("email",)
    ordering = ("-created_at",)
    raw_id_fields = ("member",)


@admin.register(DigestIssue)
class DigestIssueAdmin(LeanModelAdmin):
    list_display = ("__str__", "started_at", "recipients")
    ordering = ("-year", "-week")
    readonly_fields = ("year", "week", "started_at", "recipients")

    def has_add_permission(self, request):
        return False  # created by the Monday job
