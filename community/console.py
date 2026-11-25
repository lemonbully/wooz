"""Ask OneWooz console (namespace ``ask_console``), mounted on the admin surface
at ``/ask/`` behind AdminAccessMiddleware, next to the Marketplace console.

One place to run the community: the moderation queue (held, reported, hidden),
every question as a thread with per-post controls, members (ban, unban, team,
hide everything, delete), topic rooms (add, edit, reorder, retire, remove),
instant AI questions, the weekly digest, site-wide settings and an activity log.

Every change is written to the audit trail as ``ask.console.<action>``.
Viewing needs a staff account; changing needs superuser or the
``community.change_question`` permission.
"""

from __future__ import annotations

import functools
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import get_script_prefix, reverse, set_script_prefix
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from core import audit, links
from core.hosts import Surface
from core.queries import paginate_sorted, sorted_rows

from . import services
from .models import (
    Answer, CommunitySettings, DigestIssue, DigestSubscriber, Member, Question, Reply, Report, Topic, Visibility,
)

NAV = (
    ("overview", "Overview"),
    ("queue", "Moderation"),
    ("questions", "Questions"),
    ("members", "Members"),
    ("topics", "Topic rooms"),
    ("instant", "Instant AI"),
    ("digest", "Weekly digest"),
    ("settings", "Settings"),
    ("activity", "Activity"),
)
PUBLISHED, HELD, HIDDEN = Visibility.PUBLISHED, Visibility.HELD, Visibility.HIDDEN
_KINDS = {"question": Question, "answer": Answer, "reply": Reply}


# --------------------------------------------------------------------------
# Access, links, logging
# --------------------------------------------------------------------------
def can_write(user) -> bool:
    return bool(user and user.is_active and user.is_staff
                and (user.is_superuser or user.has_perm("community.change_question")))


def console_view(write: bool = False):
    def deco(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            user = getattr(request, "user", None)
            if not (user and user.is_authenticated and user.is_staff):
                raise PermissionDenied
            if (write or request.method == "POST") and not can_write(user):
                messages.error(request, "Your account can view the Ask OneWooz console but not change it.")
                return redirect(_back(request, reverse("ask_console:overview")))
            return view(request, *args, **kwargs)

        return wrapper

    return deco


def _back(request, default: str) -> str:
    """A same-site path from ``next`` / Referer, else ``default`` (no open redirect)."""
    from urllib.parse import urlsplit

    for raw in (request.POST.get("next"), request.META.get("HTTP_REFERER")):
        raw = (raw or "").strip()
        if not raw:
            continue
        parts = urlsplit(raw)
        if parts.netloc and parts.netloc != request.get_host():
            continue
        path = parts.path or "/"
        if path.startswith("/") and not path.startswith("//"):
            return path + (f"?{parts.query}" if parts.query else "")
    return default


def _reverse_on(urlconf: str, name: str, **kwargs) -> str:
    previous = get_script_prefix()
    try:
        set_script_prefix("/")
        return reverse(name, kwargs=kwargs or None, urlconf=urlconf)
    finally:
        set_script_prefix(previous)


def console_link(name: str, request=None, **kwargs) -> str:
    """Absolute URL of a console page, for links from the public site."""
    return links.surface_url(Surface.ADMIN, _reverse_on("onewooz.urls.admin", name, **kwargs), request)


def site_link(request, name: str, **kwargs) -> str:
    """Absolute URL of a public Ask OneWooz page, for "view on site" links."""
    return links.surface_url(Surface.MARKETING, _reverse_on("onewooz.urls.marketing", name, **kwargs), request)


def _log(request, action: str, target=None, **context):
    audit.record(
        f"ask.console.{action}", request=request, actor_kind="admin", target=target,
        context={k: str(v)[:300] for k, v in context.items()},
    )


def _ctx(request, section: str, **extra) -> dict:
    ctx = {
        "nav_items": NAV,
        "section": section,
        "can_write": can_write(request.user),
        "admin_home": reverse("custom_admin:overview"),
        "db_url": reverse("admin:index"),
        "live_url": site_link(request, "ask:ask_home"),
        "held_count": (Question.objects.filter(status=HELD).count() + Answer.objects.filter(status=HELD).count()
                       + Reply.objects.filter(status=HELD).count()),
        "report_count": Report.objects.filter(is_resolved=False).count(),
        "conf": CommunitySettings.current(),
    }
    ctx.update(extra)
    return ctx


def _excerpt(text: str, n: int = 220) -> str:
    text = " ".join((text or "").replace("**", "").split())
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"


def _item(kind: str, obj) -> dict:
    """One post, normalised for the queue / member tables."""
    if kind == "question":
        question, text = obj, obj.title + (f" · {obj.body}" if obj.body else "")
    elif kind == "answer":
        question, text = obj.question, obj.body
    else:
        question, text = obj.answer.question, obj.body
    return {"kind": kind, "obj": obj, "text": _excerpt(text), "question": question,
            "author": obj.author, "status": obj.status, "reason": obj.hold_reason,
            "reports": getattr(obj, "report_count", 0), "created_at": obj.created_at,
            "anchor": "" if kind == "question" else f"#{kind[0]}-{obj.pk}"}


# --------------------------------------------------------------------------
# Overview
# --------------------------------------------------------------------------
@console_view()
def overview(request):
    from prompts import ask as ask_ai

    Topic.ensure_defaults()
    week = timezone.now() - timedelta(days=7)
    pub_q = Question.objects.filter(status=PUBLISHED)
    held = (Question.objects.filter(status=HELD).count() + Answer.objects.filter(status=HELD).count()
            + Reply.objects.filter(status=HELD).count())
    open_reports = Report.objects.filter(is_resolved=False).count()
    kpis = [
        {"label": "Questions", "value": pub_q.count(), "sub": f"{pub_q.filter(created_at__gte=week).count()} this week"},
        {"label": "Answers", "value": Answer.objects.filter(status=PUBLISHED).count(),
         "sub": f"{Answer.objects.filter(status=PUBLISHED, created_at__gte=week).count()} this week"},
        {"label": "Members", "value": Member.objects.count(),
         "sub": f"{Member.objects.filter(created_at__gte=week).count()} joined this week"},
        {"label": "Unanswered", "value": pub_q.filter(answer_count=0).count(), "sub": "published, no answer yet"},
        {"label": "Held for review", "value": held, "sub": "waiting for you", "warn": bool(held)},
        {"label": "Open reports", "value": open_reports, "sub": "from readers", "warn": bool(open_reports)},
        {"label": "Banned", "value": Member.objects.filter(is_banned=True).count(), "sub": "members"},
        {"label": "Digest subscribers",
         "value": DigestSubscriber.objects.filter(is_confirmed=True, unsubscribed_at__isnull=True).count(),
         "sub": "get the Monday email"},
    ]
    held_items = (
        [_item("question", q) for q in sorted_rows(Question.objects.filter(status=HELD).select_related("author").order_by("-created_at"), limit=6)]
        + [_item("answer", a) for a in sorted_rows(Answer.objects.filter(status=HELD).select_related("author", "question").order_by("-created_at"), limit=6)]
    )
    held_items.sort(key=lambda i: i["created_at"], reverse=True)
    recent = sorted_rows(Question.objects.select_related("author", "topic").order_by("-created_at"), limit=8)
    from core.models import AuditEvent

    events = sorted_rows(AuditEvent.objects.filter(action__startswith="ask.console.").order_by("-created_at"), limit=8)
    return render(request, "community/console/overview.html", _ctx(
        request, "overview", kpis=kpis, held_items=held_items[:8], recent=recent, events=events,
        ai_prompt=ask_ai.active_prompt(), ai_ready=ask_ai.ai_configured(),
        prompt_admin=reverse("admin:prompts_prompt_changelist"),
    ))


# --------------------------------------------------------------------------
# Moderation queue
# --------------------------------------------------------------------------
QUEUE_TABS = (("held", "Held for review"), ("reported", "Reported"), ("hidden", "Hidden"))


@console_view()
def queue(request):
    tab = request.GET.get("tab", "held")
    tab = tab if tab in dict(QUEUE_TABS) else "held"
    items: list[dict] = []
    if tab in ("held", "hidden"):
        status = HELD if tab == "held" else HIDDEN
        items += [_item("question", o) for o in sorted_rows(
            Question.objects.filter(status=status).select_related("author").order_by("-created_at"), limit=60)]
        items += [_item("answer", o) for o in sorted_rows(
            Answer.objects.filter(status=status).select_related("author", "question").order_by("-created_at"), limit=60)]
        items += [_item("reply", o) for o in sorted_rows(
            Reply.objects.filter(status=status).select_related("author", "answer__question").order_by("-created_at"), limit=60)]
        items.sort(key=lambda i: i["created_at"], reverse=True)
    else:
        grouped: dict = {}
        for rep in sorted_rows(
            Report.objects.filter(is_resolved=False)
            .select_related("question__author", "answer__author", "answer__question", "reply__author", "reply__answer__question")
            .order_by("-created_at"),
            limit=400,
        ):
            target = rep.target
            if target is None:
                continue
            kind = target.__class__.__name__.lower()
            key = (kind, target.pk)
            if key not in grouped:
                grouped[key] = {**_item(kind, target), "reasons": {}, "count": 0, "last": rep.created_at}
            grouped[key]["count"] += 1
            label = rep.get_reason_display()
            grouped[key]["reasons"][label] = grouped[key]["reasons"].get(label, 0) + 1
        items = sorted(grouped.values(), key=lambda i: (i["count"], i["last"]), reverse=True)
    return render(request, "community/console/queue.html", _ctx(
        request, "queue", tabs=QUEUE_TABS, tab=tab, items=items,
    ))


@console_view(write=True)
@require_POST
def post_action(request, kind: str, pk):
    """Approve, hold, hide, delete, accept, resolve reports, or ban the author
    of one question, answer or reply."""
    model = _KINDS.get(kind)
    if model is None:
        raise Http404
    obj = get_object_or_404(model, pk=pk)
    action = request.POST.get("action", "")
    reason = (request.POST.get("reason") or "").strip()[:120]
    back = _back(request, reverse("ask_console:queue"))
    label = str(obj)[:80]

    def resolve():
        Report.objects.filter(is_resolved=False, **{kind: obj}).update(is_resolved=True)

    if action == "approve":
        services.set_status(obj, PUBLISHED)
        resolve()
        messages.success(request, f"Published: {label}")
    elif action == "hold":
        services.set_status(obj, HELD, reason or "Held by the team")
        messages.success(request, f"Held for review: {label}")
    elif action == "hide":
        services.set_status(obj, HIDDEN, reason or "Hidden by the team")
        resolve()
        messages.success(request, f"Hidden: {label}")
    elif action == "delete":
        _log(request, f"{kind}.delete", obj, text=label, reason=reason)
        if kind == "question":
            back = reverse("ask_console:questions")
        services.delete_post(obj)
        messages.success(request, f"Deleted for good: {label}")
        return redirect(back)
    elif action == "resolve":
        resolve()
        Model = obj.__class__
        Model.objects.filter(pk=obj.pk).update(report_count=0)
        if obj.status == HELD and obj.hold_reason == "Reported by readers":
            services.set_status(obj, PUBLISHED)
        messages.success(request, "Reports cleared. The post stays as it is.")
    elif action == "ban_author":
        services.ban(obj.author, reason or f"Posted: {label}")
        services.set_status(obj, HIDDEN, "Author banned")
        resolve()
        messages.success(request, f"{obj.author.display_name} is banned and this post is hidden.")
    elif action == "accept" and kind == "answer":
        accepted = services.toggle_accept(obj)
        messages.success(request, "Marked as the accepted answer." if accepted else "Accepted mark removed.")
    else:
        messages.error(request, "Unknown action.")
        return redirect(back)
    _log(request, f"{kind}.{action}", obj, reason=reason)
    return redirect(back)


# --------------------------------------------------------------------------
# Questions and threads
# --------------------------------------------------------------------------
@console_view()
def questions(request):
    qs = Question.objects.select_related("author", "topic")
    q = (request.GET.get("q") or "").strip()[:120]
    status = request.GET.get("status", "")
    topic = request.GET.get("topic", "")
    flag = request.GET.get("flag", "")
    if q:
        for term in q.split()[:6]:
            qs = qs.filter(Q(title__icontains=term) | Q(body__icontains=term) | Q(author__display_name__icontains=term))
    if status in (PUBLISHED, HELD, HIDDEN):
        qs = qs.filter(status=status)
    if topic == "none":
        qs = qs.filter(topic__isnull=True)
    elif topic:
        qs = qs.filter(topic__slug=topic)
    if flag == "reported":
        qs = qs.filter(report_count__gt=0)
    elif flag == "unanswered":
        qs = qs.filter(answer_count=0)
    page = paginate_sorted(qs.order_by("-created_at"), 40, request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "community/console/questions.html", _ctx(
        request, "questions", page=page, q=q, status=status, topic=topic, flag=flag,
        topics=Topic.objects.all(), statuses=Visibility.choices, qs=params.urlencode(),
    ))


@console_view()
def thread(request, pk):
    from . import authoring

    q = get_object_or_404(Question.objects.select_related("author", "topic", "instant"), pk=pk)
    if request.method == "POST":
        form = authoring.question_edit_form(q, request.user, data=request.POST)
        if form.is_valid():
            authoring.save_question(request, q, form)
            return redirect("ask_console:thread", pk=q.pk)
        messages.error(request, "The question was not saved. Check the highlighted fields.")
        return render_thread(request, q, form=form)
    return render_thread(request, q)


def render_thread(request, q, *, form=None, new_answer_form=None, open_answer=None):
    """The thread page. Bound forms passed in (after a failed save) are shown
    open with their errors; every other post gets a fresh edit form."""
    from . import authoring

    can = can_write(request.user)
    if form is None and can:
        form = authoring.question_edit_form(q, request.user)
    answers = sorted_rows(
        Answer.objects.filter(question=q).select_related("author").order_by("-is_accepted", "-score", "created_at"),
    )
    replies: dict = {}
    for r in sorted_rows(
        Reply.objects.filter(answer_id__in=[a.pk for a in answers]).select_related("author").order_by("created_at"),
    ):
        replies.setdefault(r.answer_id, []).append(r)
    for a in answers:
        a.reply_list = replies.get(a.pk, [])
    open_reports = Report.objects.filter(is_resolved=False).filter(
        Q(question=q) | Q(answer__question=q) | Q(reply__answer__question=q)
    ).count()
    from prompts.ask import render_html

    for a in answers:
        a.html = render_html(a.body)
        if open_answer is not None and a.pk == open_answer.pk:
            a.edit_form, a.edit_open = open_answer.edit_form, True
    if can:
        authoring.answer_forms_for(answers, request.user)
        new_answer_form = new_answer_form or authoring.new_answer_form(request.user)
    return render(request, "community/console/thread.html", _ctx(
        request, "questions", q=q, form=form, answers=answers, open_reports=open_reports,
        new_answer_form=new_answer_form,
        edit_open=bool(form is not None and form.is_bound),
        new_open=bool(new_answer_form is not None and new_answer_form.is_bound),
        q_html=render_html(q.body), ai_html=render_html(q.ai_answer),
        site_url=site_link(request, "ask:question", slug=q.slug),
        instant_admin=(reverse("admin:prompts_askquestion_change", args=[q.instant_id]) if q.instant_id else ""),
    ))


def _team_member_for(user) -> Member:
    """The community member who posts for this admin (created on first use,
    always labelled OneWooz team)."""
    name = (getattr(user, "get_full_name", lambda: "")() or "").strip() or "OneWooz team"
    member = services.get_or_create_member(user.email, name)
    if not member.is_team:
        Member.objects.filter(pk=member.pk).update(is_team=True)
        member.is_team = True
    return member


@console_view(write=True)
@require_POST
def thread_answer(request, pk):
    """Add an answer, shown as yourself or any name, dated and voted as set."""
    from . import authoring

    q = get_object_or_404(Question.objects.select_related("author", "topic", "instant"), pk=pk)
    return authoring.add_answer(request, q)


# --------------------------------------------------------------------------
# Members
# --------------------------------------------------------------------------
MEMBER_FILTERS = (("", "Everyone"), ("banned", "Banned"), ("team", "OneWooz team"), ("new", "Joined this week"),
                  ("stores", "Store owners"))


@console_view()
def members(request):
    qs = Member.objects.all()
    q = (request.GET.get("q") or "").strip()[:120]
    flag = request.GET.get("flag", "")
    if q:
        qs = qs.filter(Q(display_name__icontains=q) | Q(email__icontains=q) | Q(handle__icontains=q))
    if flag == "banned":
        qs = qs.filter(is_banned=True)
    elif flag == "team":
        qs = qs.filter(is_team=True)
    elif flag == "new":
        qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=7))
    elif flag == "stores":
        qs = qs.exclude(store_url="")
    sort = request.GET.get("sort", "new")
    order = {"rep": ("-reputation", "-created_at"), "answers": ("-answer_count", "-created_at")}.get(sort, ("-created_at",))
    page = paginate_sorted(qs.order_by(*order), 50, request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "community/console/members.html", _ctx(
        request, "members", page=page, q=q, flag=flag, sort=sort, filters=MEMBER_FILTERS, qs=params.urlencode(),
    ))


@console_view()
def member_detail(request, pk):
    person = get_object_or_404(Member, pk=pk)
    items = (
        [_item("question", o) for o in sorted_rows(Question.objects.filter(author=person).select_related("author").order_by("-created_at"), limit=40)]
        + [_item("answer", o) for o in sorted_rows(Answer.objects.filter(author=person).select_related("author", "question").order_by("-created_at"), limit=40)]
        + [_item("reply", o) for o in sorted_rows(Reply.objects.filter(author=person).select_related("author", "answer__question").order_by("-created_at"), limit=40)]
    )
    items.sort(key=lambda i: i["created_at"], reverse=True)
    reports_against = Report.objects.filter(
        Q(question__author=person) | Q(answer__author=person) | Q(reply__author=person)
    ).count()
    return render(request, "community/console/member.html", _ctx(
        request, "members", person=person, items=items, reports_against=reports_against,
        profile_url=site_link(request, "ask:profile", handle=person.handle),
        hidden_count=sum(1 for i in items if i["status"] == HIDDEN),
    ))


@console_view(write=True)
@require_POST
def member_action(request, pk):
    person = get_object_or_404(Member, pk=pk)
    action = request.POST.get("action", "")
    reason = (request.POST.get("reason") or "").strip()[:300]
    back = _back(request, reverse("ask_console:member", kwargs={"pk": person.pk}))
    if action == "ban":
        services.ban(person, reason)
        if request.POST.get("hide_all"):
            services.hide_everything(person)
        messages.success(request, f"{person.display_name} is banned. They can read but not ask, answer, reply or vote.")
    elif action == "unban":
        services.unban(person)
        messages.success(request, f"{person.display_name} can post again.")
    elif action in ("team_on", "team_off"):
        Member.objects.filter(pk=person.pk).update(is_team=(action == "team_on"))
        messages.success(request, f"{person.display_name} is {'now' if action == 'team_on' else 'no longer'} labelled OneWooz team.")
    elif action == "hide_all":
        n = services.hide_everything(person)
        messages.success(request, f"Hid {n} post{'s' if n != 1 else ''} by {person.display_name}.")
    elif action == "publish_all":
        n = 0
        for model in (Question, Answer, Reply):
            for obj in model.objects.filter(author=person, status=HIDDEN):
                services.set_status(obj, PUBLISHED)
                n += 1
        messages.success(request, f"Restored {n} hidden post{'s' if n != 1 else ''}.")
    elif action == "rename":
        name = services.clean_display_name(request.POST.get("display_name", ""))
        if len(name) < 2:
            messages.error(request, "A display name needs at least 2 characters.")
            return redirect(back)
        Member.objects.filter(pk=person.pk).update(display_name=name, bio=(request.POST.get("bio") or "")[:160])
        messages.success(request, "Member updated.")
    elif action == "delete":
        _log(request, "member.delete", person, email=person.email, reason=reason)
        services.delete_member(person)
        messages.success(request, f"Deleted {person.display_name} and everything they posted.")
        return redirect("ask_console:members")
    else:
        messages.error(request, "Unknown action.")
        return redirect(back)
    _log(request, f"member.{action}", person, reason=reason)
    return redirect(back)


# --------------------------------------------------------------------------
# Topic rooms
# --------------------------------------------------------------------------
class TopicForm(forms.ModelForm):
    class Meta:
        model = Topic
        fields = ("name", "slug", "description", "position", "is_active")
        help_texts = {"slug": "The web address part, like shipping in /ask/topics/shipping/. Left empty, it is made from the name."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False

    def clean_slug(self):
        slug = slugify(self.cleaned_data.get("slug") or self.cleaned_data.get("name") or "")[:70]
        if not slug:
            raise forms.ValidationError("Give the room a name.")
        clash = Topic.objects.filter(slug=slug).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("Another room already uses this address.")
        return slug


@console_view()
def topics(request):
    Topic.ensure_defaults()
    week = timezone.now() - timedelta(days=7)
    rooms = list(Topic.objects.annotate(
        published=Count("questions", filter=Q(questions__status=PUBLISHED)),
        total=Count("questions"),
        week=Count("questions", filter=Q(questions__created_at__gte=week)),
    ))
    new_form = TopicForm(prefix="new", initial={"position": (max([r.position for r in rooms] or [0]) + 10)})
    if request.method == "POST":
        if not can_write(request.user):
            raise PermissionDenied
        new_form = TopicForm(request.POST, prefix="new")
        if new_form.is_valid():
            room = new_form.save()
            _log(request, "topic.create", room)
            messages.success(request, f"Room “{room.name}” added.")
            return redirect("ask_console:topics")
    return render(request, "community/console/topics.html", _ctx(
        request, "topics", rooms=rooms, new_form=new_form,
        untopiced=Question.objects.filter(topic__isnull=True).count(),
    ))


@console_view(write=True)
@require_POST
def topic_save(request, pk):
    room = get_object_or_404(Topic, pk=pk)
    form = TopicForm(request.POST, instance=room)
    if form.is_valid():
        form.save()
        _log(request, "topic.edit", room, fields=",".join(form.changed_data))
        messages.success(request, f"Room “{room.name}” saved.")
    else:
        errors = "; ".join(f"{k}: {' '.join(v)}" for k, v in form.errors.items())
        messages.error(request, f"Not saved. {errors}")
    return redirect("ask_console:topics")


@console_view(write=True)
@require_POST
def topic_delete(request, pk):
    room = get_object_or_404(Topic, pk=pk)
    move_to = request.POST.get("move_to", "")
    target = Topic.objects.filter(pk=move_to).exclude(pk=room.pk).first() if move_to else None
    moved = Question.objects.filter(topic=room).update(topic=target)
    name = room.name
    _log(request, "topic.delete", room, moved=moved, moved_to=target.name if target else "no topic")
    room.delete()
    where = f"“{target.name}”" if target else "no topic"
    messages.success(request, f"Room “{name}” removed. {moved} question{'s' if moved != 1 else ''} moved to {where}.")
    return redirect("ask_console:topics")


# --------------------------------------------------------------------------
# Instant AI questions (prompts.AskQuestion)
# --------------------------------------------------------------------------
@console_view()
def instant(request):
    from prompts import ask as ask_ai
    from prompts.models import AskQuestion

    qs = AskQuestion.objects.all()
    status = request.GET.get("status", "")
    q = (request.GET.get("q") or "").strip()[:120]
    if status in AskQuestion.Status.values:
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(question__icontains=q)
    page = paginate_sorted(qs.order_by("-created_at"), 40, request.GET.get("page"))
    posted = set(Question.objects.filter(instant_id__in=[a.pk for a in page.object_list]).values_list("instant_id", flat=True))
    for a in page.object_list:
        a.was_posted = a.pk in posted
        a.admin_url = reverse("admin:prompts_askquestion_change", args=[a.pk])
    today = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "community/console/instant.html", _ctx(
        request, "instant", page=page, status=status, q=q, statuses=AskQuestion.Status.choices,
        ai_prompt=ask_ai.active_prompt(), ai_ready=ask_ai.ai_configured(),
        prompt_admin=reverse("admin:prompts_prompt_changelist"),
        today_count=AskQuestion.objects.filter(created_at__gte=today).count(),
        waiting_count=AskQuestion.objects.filter(status=AskQuestion.Status.WAITING).count(),
        qs=params.urlencode(),
    ))


# --------------------------------------------------------------------------
# Weekly digest
# --------------------------------------------------------------------------
@console_view()
def digest_view(request):
    subs = DigestSubscriber.objects.all()
    q = (request.GET.get("q") or "").strip()[:120]
    if q:
        subs = subs.filter(email__icontains=q)
    page = paginate_sorted(subs.select_related("member").order_by("-created_at"), 50, request.GET.get("page"))
    counts = {
        "active": DigestSubscriber.objects.filter(is_confirmed=True, unsubscribed_at__isnull=True).count(),
        "pending": DigestSubscriber.objects.filter(is_confirmed=False, unsubscribed_at__isnull=True).count(),
        "left": DigestSubscriber.objects.filter(unsubscribed_at__isnull=False).count(),
    }
    issues = sorted_rows(DigestIssue.objects.order_by("-year", "-week"), limit=12)
    return render(request, "community/console/digest.html", _ctx(
        request, "digest", page=page, q=q, counts=counts, issues=issues,
        weekly_url=site_link(request, "ask:weekly"),
    ))


@console_view(write=True)
@require_POST
def subscriber_action(request, pk):
    sub = get_object_or_404(DigestSubscriber, pk=pk)
    action = request.POST.get("action", "")
    if action == "unsubscribe":
        DigestSubscriber.objects.filter(pk=sub.pk).update(unsubscribed_at=timezone.now())
        messages.success(request, f"{sub.email} unsubscribed.")
    elif action == "confirm":
        DigestSubscriber.objects.filter(pk=sub.pk).update(
            is_confirmed=True, confirmed_at=sub.confirmed_at or timezone.now(), unsubscribed_at=None)
        messages.success(request, f"{sub.email} subscribed.")
    elif action == "delete":
        sub.delete()
        messages.success(request, "Subscriber removed.")
    _log(request, f"digest.{action}", None, email=sub.email)
    return redirect(_back(request, reverse("ask_console:digest")))


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
class SettingsForm(forms.ModelForm):
    class Meta:
        model = CommunitySettings
        fields = ("posting_open", "approve_all", "hold_links", "ai_answers_enabled", "digest_enabled",
                  "report_threshold", "blocked_phrases", "notice")
        widgets = {"blocked_phrases": forms.Textarea(attrs={"rows": 4, "data-chip-source": ""})}
        labels = {"blocked_phrases": "Blocked keywords and phrases"}
        help_texts = {
            "blocked_phrases": (
                "Separate entries with commas. One word is a keyword and matches whole "
                "words only (\"bet\" won't catch \"better\"). Several words are a phrase "
                "and match those words together, in order. Capitals don't matter. A post "
                "containing any of them is held for review."
            ),
        }

    MAX_TERMS = 500

    def clean_blocked_phrases(self):
        from .models import split_terms

        terms = split_terms(self.cleaned_data.get("blocked_phrases", ""))
        if len(terms) > self.MAX_TERMS:
            raise forms.ValidationError(f"Keep it to {self.MAX_TERMS} keywords and phrases.")
        # Stored one per line, so a phrase never needs quoting.
        return "\n".join(terms)


def _chips(raw: str) -> list[dict]:
    from .models import split_terms, term_kind

    return [{"text": t, "kind": term_kind(t)} for t in split_terms(raw)]


@console_view()
def settings_view(request):
    conf = CommunitySettings.current()
    form = SettingsForm(instance=conf)
    if request.method == "POST":
        if not can_write(request.user):
            raise PermissionDenied
        form = SettingsForm(request.POST, instance=conf)
        if form.is_valid():
            form.save()
            _log(request, "settings.save", conf, fields=",".join(form.changed_data))
            messages.success(request, "Settings saved. They apply on the next page load.")
            return redirect("ask_console:settings")
    chips = _chips(form["blocked_phrases"].value() or "")
    return render(request, "community/console/settings.html", _ctx(
        request, "settings", form=form, chips=chips,
        keyword_count=sum(1 for c in chips if c["kind"] == "keyword"),
        phrase_count=sum(1 for c in chips if c["kind"] == "phrase"),
    ))


# --------------------------------------------------------------------------
# Activity
# --------------------------------------------------------------------------
@console_view()
def activity(request):
    from core.models import AuditEvent

    page = paginate_sorted(
        AuditEvent.objects.filter(action__startswith="ask.console.").order_by("-created_at"),
        50, request.GET.get("page"),
    )
    for e in page.object_list:
        e.short = e.action.removeprefix("ask.console.")
    return render(request, "community/console/activity.html", _ctx(request, "activity", page=page))
