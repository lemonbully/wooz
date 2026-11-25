"""The team desk (/ask/desk/): a two-pane workspace for OneWooz team members
to work through questions quickly.

Left: a queue (unanswered, no team answer yet, reported, recent). Right: the
selected thread with an answer box and an AI-drafted answer from the active
Ask prompt. Keyboard: J / K move through the queue, A focuses the answer box,
/ focuses the filter, Ctrl+Enter posts.

Only members marked "OneWooz team" can open it (everyone else gets a 404).
Moderation (hold / hide / publish) stays in Django admin, behind passkeys.
"""

from __future__ import annotations

from datetime import timedelta

from django.core.cache import cache
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.cache import never_cache

from core import ratelimit
from core.queries import sorted_rows
from prompts.ask import render_html

from . import services
from .models import Answer, Question, Visibility
from .views import _base

PUBLISHED = Visibility.PUBLISHED
DESK_TABS = (
    ("unanswered", "Unanswered"),
    ("no_team", "No team answer"),
    ("reported", "Reported"),
    ("recent", "Recent activity"),
)
_TAB_KEYS = {k for k, _ in DESK_TABS}
QUEUE_LIMIT = 60
_FIELDS = ("id", "title", "slug", "created_at", "answer_count", "report_count", "score",
           "last_activity_at", "topic", "topic__name", "author", "author__display_name")


def _team_or_404(request):
    member = services.current_member(request)
    if member is None or not member.is_team:
        raise Http404
    return member


def _queue(tab: str):
    since = timezone.now() - timedelta(days=30)
    pub = Question.objects.filter(status=PUBLISHED)
    if tab == "no_team":
        return pub.filter(has_team_answer=False, created_at__gte=since).order_by("-created_at")
    if tab == "reported":
        return pub.filter(report_count__gt=0).order_by("-report_count", "-created_at")
    if tab == "recent":
        return pub.order_by("-last_activity_at")
    return pub.filter(answer_count=0, created_at__gte=since).order_by("created_at")


@never_cache
def desk(request):
    _team_or_404(request)
    tab = request.GET.get("tab", "unanswered")
    tab = tab if tab in _TAB_KEYS else "unanswered"
    search = (request.GET.get("s") or "").strip()[:80]

    qs = _queue(tab)
    if search:
        qs = qs.filter(title__icontains=search)
    items = sorted_rows(qs.select_related("topic", "author").only(*_FIELDS), limit=QUEUE_LIMIT)

    def url(**over):
        params = {"tab": tab, "s": search}
        params.update(over)
        return reverse("ask:desk") + "?" + urlencode({k: v for k, v in params.items() if v})

    selected_slug = request.GET.get("q") or (items[0].slug if items else "")
    current = None
    if selected_slug:
        current = (
            Question.objects.select_related("author", "topic")
            .filter(slug=selected_slug, status=PUBLISHED).first()
        )

    answers, prev_url, next_url = [], "", ""
    if current is not None:
        answers = sorted_rows(
            Answer.objects.filter(question=current, status=PUBLISHED).select_related("author")
            .order_by("-is_accepted", "-score", "created_at"),
        )
        for a in answers:
            a.html = render_html(a.body)
        slugs = [i.slug for i in items]
        if current.slug in slugs:
            at = slugs.index(current.slug)
            prev_url = url(q=slugs[at - 1]) if at > 0 else ""
            next_url = url(q=slugs[at + 1]) if at + 1 < len(slugs) else ""

    counts = {
        "unanswered": _queue("unanswered").count(),
        "no_team": _queue("no_team").count(),
        "reported": _queue("reported").count(),
    }
    suggestion = cache.get(f"ask:suggest:{current.pk}") if current is not None else None
    request.seo_extra = {"noindex": True, "title": "Team desk | Ask OneWooz"}
    response = render(request, "community/desk.html", _base(
        request,
        desk_tabs=DESK_TABS,
        tab=tab,
        search=search,
        items=[{"q": i, "url": url(q=i.slug)} for i in items],
        current=current,
        current_html=render_html(current.body) if current else "",
        ai_html=render_html(current.ai_answer) if current else "",
        answers=answers,
        prev_url=prev_url,
        next_url=next_url,
        counts=counts,
        here_url=url(q=current.slug) if current else url(),
        tab_urls={k: reverse("ask:desk") + "?" + urlencode({"tab": k}) for k in _TAB_KEYS},
        suggestion=suggestion if (suggestion or {}).get("status") == "ready" else None,
        suggest_url=reverse("ask:desk_suggest", kwargs={"slug": current.slug}) if current else "",
    ))
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@never_cache
def desk_suggest(request, slug: str):
    """POST starts an AI draft for this question; GET reports it.
    ``{"status": "pending" | "ready" | "error" | "none", "text"?, "error"?}``"""
    member = _team_or_404(request)
    question = get_object_or_404(Question, slug=slug, status=PUBLISHED)
    key = f"ask:suggest:{question.pk}"
    data = cache.get(key)
    if request.method == "POST":
        fresh = bool(request.POST.get("fresh"))
        if fresh or not data or data.get("status") == "error":
            if not ratelimit.consume("ask.desk.suggest", str(member.pk)).allowed:
                return JsonResponse({"status": "error", "error": "Too many drafts this hour."}, status=429)
            cache.set(key, {"status": "pending"}, 600)
            try:
                from .tasks import suggest_answer

                suggest_answer.delay(str(question.pk))
            except Exception:
                cache.set(key, {"status": "error", "error": "Couldn't start the AI job."}, 60)
            data = cache.get(key)
    return JsonResponse(data or {"status": "none"})
