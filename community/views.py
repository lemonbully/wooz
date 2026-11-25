"""Ask OneWooz community pages (onewooz.com/ask/…).

Reading is open to everyone. Posting, voting, accepting and reporting need a
:class:`~community.models.Member` (email + one-time code, see :func:`join`).

Asking is AI first: the ask box creates a private ``prompts.AskQuestion`` and
shows its instant answer (:func:`instant`); from there the visitor can post it
to the community (:func:`instant_post`).
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction, IntegrityError
from django.db.models import Count, F, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from core import ratelimit
from core.queries import paginate_sorted, sorted_rows
from prompts.ask import render_html

from . import services
from .models import Answer, Member, Question, Reply, Report, Topic, Visibility

PER_PAGE = 20
TITLE_MIN, TITLE_MAX = 10, 200
BODY_MAX = 5000
ANSWER_MIN = 20
REPLY_MIN = 2
TABS = (
    ("top", "Top this week"),
    ("new", "New"),
    ("unanswered", "Unanswered"),
    ("team", "Team answered"),
)
_TAB_KEYS = {k for k, _ in TABS}
PUBLISHED = Visibility.PUBLISHED


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _plain(text: str, limit: int) -> str:
    text = " ".join((text or "").replace("**", "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:.") + "…"


def _published():
    return Question.objects.filter(status=PUBLISHED)


def _safe_next(request, value: str) -> str:
    value = (value or "").strip()
    if value.startswith("/") and url_has_allowed_host_and_scheme(
        value, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        return value
    return reverse("ask:ask_home")


def _join_url(request, next_url: str = "") -> str:
    return reverse("ask:join") + "?" + urlencode({"next": next_url or request.get_full_path()})


def _is_ajax(request) -> bool:
    return request.headers.get("x-requested-with") == "fetch" or "application/json" in request.headers.get("accept", "")


def _need_member(request, next_url: str = ""):
    """(member, None) when signed in and allowed to post, else (None, a
    response asking them to join, or saying why they can't post)."""
    member = services.current_member(request)
    if member is not None:
        block = services.posting_block(member)
        if not block:
            return member, None
        if _is_ajax(request):
            return None, JsonResponse({"error": block}, status=403)
        messages.error(request, block)
        return None, redirect(_safe_next(request, request.POST.get("next") or next_url))
    url = _join_url(request, next_url)
    if _is_ajax(request):
        return None, JsonResponse({"join": url, "error": "Confirm your email to take part."}, status=401)
    return None, redirect(url)


def _back(request, fallback: str):
    return redirect(_safe_next(request, request.POST.get("next") or fallback))


def _feed(qs, tab: str):
    if tab == "new":
        return qs.order_by("-created_at")
    if tab == "unanswered":
        return qs.filter(answer_count=0).order_by("-created_at")
    if tab == "team":
        return qs.filter(has_team_answer=True).order_by("-last_activity_at")
    week = qs.filter(created_at__gte=timezone.now() - timedelta(days=7))
    if week.exists():
        qs = week
    return qs.order_by("-score", "-answer_count", "-created_at")


def _search(qs, query: str):
    for term in query.split()[:6]:
        qs = qs.filter(Q(title__icontains=term) | Q(body__icontains=term))
    return qs.order_by("-score", "-created_at")


def _rows(questions: list, member) -> list[dict]:
    """Feed rows: each question with a preview of its best answer."""
    ids = [q.pk for q in questions]
    best: dict = {}
    if ids:
        for a in (
            Answer.objects.filter(question_id__in=ids, status=PUBLISHED)
            .order_by()
            .select_related("author")
            .only("id", "question_id", "body", "score", "is_accepted", "author", "author__display_name", "author__is_team")
        ):
            cur = best.get(a.question_id)
            if cur is None or (a.is_accepted, a.score) > (cur.is_accepted, cur.score):
                best[a.question_id] = a
    voted = services.voted_ids(member, questions=ids)
    rows = []
    for q in questions:
        a = best.get(q.pk)
        if a is not None:
            preview, by = _plain(a.body, 180), ("team" if a.author.is_team else a.author.display_name)
        elif q.ai_answer:
            preview, by = _plain(q.ai_answer, 180), "ai"
        else:
            preview, by = _plain(q.body, 180), ""
        rows.append({"q": q, "preview": preview, "by": by, "accepted": bool(a and a.is_accepted),
                     "voted": str(q.pk) in voted})
    return rows


def _topics_with_counts():
    return list(
        Topic.objects.filter(is_active=True).annotate(
            n=Count("questions", filter=Q(questions__status=PUBLISHED)),
        )
    )


def _base(request, **extra) -> dict:
    member = services.current_member(request)
    conf = services.settings_now()
    return {
        "member": member,
        "ask_settings": conf,
        "post_block": services.posting_block(member, conf),
        "join_url": _join_url(request),
        "tabs": TABS,
        **extra,
    }


def _feed_context(request, qs, *, tab: str, query: str = "") -> dict:
    member = services.current_member(request)
    source = _search(qs, query) if query else _feed(qs, tab)
    page = paginate_sorted(source, PER_PAGE, request.GET.get("page"))
    view = "cards" if request.GET.get("view") == "cards" else "list"
    return {"rows": _rows(list(page.object_list), member), "page": page, "tab": tab, "query": query,
            "view": view}


# ---------------------------------------------------------------------------
# Home feed (Ledger + live ask box + search)
# ---------------------------------------------------------------------------
def _home(request, *, ask_error: str = "", ask_draft: str = ""):
    from prompts.models import AskQuestion

    Topic.ensure_defaults()
    tab = request.GET.get("tab", "top")
    tab = tab if tab in _TAB_KEYS else "top"
    query = (request.GET.get("q") or "").strip()[:120]
    base = _published().select_related("author", "topic")

    week_ago = timezone.now() - timedelta(days=7)
    context = _base(
        request,
        **_feed_context(request, base, tab=tab, query=query),
        ticker=sorted_rows(_published().order_by("-created_at").only("id", "title", "slug", "created_at"), limit=3),
        topics=_topics_with_counts(),
        stats={
            "total": _published().count(),
            "unanswered": _published().filter(answer_count=0).count(),
            "answered_week": Answer.objects.filter(status=PUBLISHED, created_at__gte=week_ago)
            .values("question_id").distinct().count(),
        },
        ask_error=ask_error,
        ask_draft=ask_draft,
        max_length=AskQuestion.MAX_LENGTH,
    )
    if query:
        request.seo_extra = {"noindex": True, "title": f"Search: {query} | Ask OneWooz"}
    elif request.GET.get("page") or tab != "top":
        request.seo_extra = {"canonical_url": request.build_absolute_uri(reverse("ask:ask_home"))}
    return render(request, "community/home.html", context)


@never_cache
def home(request):
    return _home(request)


@never_cache
def search_json(request):
    """Typeahead under the ask box: questions already asked that match."""
    if not ratelimit.consume("ask.search", ratelimit.client_identity(request)).allowed:
        return JsonResponse({"results": []}, status=429)
    query = (request.GET.get("q") or "").strip()[:120]
    if len(query) < 3:
        return JsonResponse({"results": []})
    found = sorted_rows(
        _search(_published(), query).only("id", "title", "slug", "answer_count", "accepted_answer_id", "score"),
        limit=6,
    )
    return JsonResponse({"results": [
        {"title": q.title, "url": q.get_absolute_url(), "answers": q.answer_count,
         "accepted": bool(q.accepted_answer_id), "score": q.score}
        for q in found
    ]})


# ---------------------------------------------------------------------------
# Topics (rooms)
# ---------------------------------------------------------------------------
@never_cache
def topics(request):
    Topic.ensure_defaults()
    week_ago = timezone.now() - timedelta(days=7)
    rooms = []
    for topic in Topic.objects.filter(is_active=True).annotate(
        n=Count("questions", filter=Q(questions__status=PUBLISHED)),
        week=Count("questions", filter=Q(questions__status=PUBLISHED, questions__created_at__gte=week_ago)),
    ):
        recent = sorted_rows(
            _published().filter(topic=topic).select_related("author")
            .only("id", "title", "slug", "created_at", "author", "author__display_name", "author__handle",
                  "author__is_team", "author__avatar")
            .order_by("-created_at"),
            limit=4,
        )
        people, seen = [], set()
        for q in recent:
            if q.author_id not in seen:
                seen.add(q.author_id)
                people.append(q.author)
        rooms.append({"topic": topic, "newest": recent[0] if recent else None, "people": people[:3]})
    return render(request, "community/topics.html", _base(request, rooms=rooms))


@never_cache
def topic(request, slug: str):
    room = get_object_or_404(Topic, slug=slug, is_active=True)
    tab = request.GET.get("tab", "new")
    tab = tab if tab in _TAB_KEYS else "new"
    qs = _published().filter(topic=room).select_related("author", "topic")
    request.seo_extra = {
        "title": f"{room.name} questions | Ask OneWooz",
        "description": room.description or f"Questions and answers about {room.name.lower()} for online sellers.",
    }
    if request.GET.get("page") or tab != "new":
        request.seo_extra["canonical_url"] = request.build_absolute_uri(room.get_absolute_url())
    return render(request, "community/topic.html", _base(
        request, room=room, topics=_topics_with_counts(), **_feed_context(request, qs, tab=tab),
    ))


# ---------------------------------------------------------------------------
# Question thread
# ---------------------------------------------------------------------------
def _visible(qs, member):
    cond = Q(status=PUBLISHED)
    if member is not None:
        cond |= Q(status=Visibility.HELD, author_id=member.pk)
    return qs.filter(cond)


@never_cache
def question(request, slug: str):
    member = services.current_member(request)
    q = get_object_or_404(Question.objects.select_related("author", "topic"), slug=slug)
    if not services.visible_to(q, member):
        raise Http404("Unknown question.")

    answers = sorted_rows(
        _visible(Answer.objects.filter(question=q), member).select_related("author")
        .order_by("-is_accepted", "-score", "created_at"),
    )
    replies: dict = {}
    if answers:
        for r in sorted_rows(
            _visible(Reply.objects.filter(answer_id__in=[a.pk for a in answers]), member)
            .select_related("author").order_by("created_at"),
        ):
            replies.setdefault(r.answer_id, []).append(r)
    for a in answers:
        a.html = render_html(a.body)
        a.reply_list = replies.get(a.pk, [])

    # One view per visitor per question per 6 hours (a cache key on the hashed
    # IP, so anonymous readers never get a session row).
    from django.core.cache import cache

    viewer = salted_hmac("ask.view", ratelimit.client_identity(request)).hexdigest()[:20]
    try:
        first_view = cache.add(f"ask:viewed:{q.pk}:{viewer}", 1, 6 * 3600)
    except Exception:
        first_view = False
    if first_view:
        Question.objects.filter(pk=q.pk).update(view_count=F("view_count") + 1)

    voted = services.voted_ids(member, questions=[q.pk], answers=[a.pk for a in answers])
    related = []
    if q.topic_id:
        related = sorted_rows(
            _published().filter(topic_id=q.topic_id).exclude(pk=q.pk)
            .only("id", "title", "slug", "answer_count").order_by("-score", "-created_at"),
            limit=5,
        )

    published_answers = [a for a in answers if a.status == PUBLISHED]
    best = published_answers[0] if published_answers else None
    description = _plain(best.body if best else (q.body or q.ai_answer or q.title), 155)
    seo = {"title": f"{q.title} | Ask OneWooz", "description": description, "og_type": "article",
           "canonical_url": request.build_absolute_uri(q.get_absolute_url())}
    if q.status != PUBLISHED or not (published_answers or q.ai_answer):
        seo["noindex"] = True  # held, or nothing answered yet: keep thin pages out of search
    if q.status == PUBLISHED and published_answers:
        seo["custom_schema"] = [services.qa_page_schema(request, q, published_answers)]
    request.seo_extra = seo

    console_url = ""
    if member is not None and member.is_team:
        from .console import console_link

        console_url = console_link("ask_console:thread", request, pk=q.pk)
    return render(request, "community/question.html", _base(
        request,
        console_url=console_url,
        q=q,
        q_html=render_html(q.body),
        ai_html=render_html(q.ai_answer),
        answers=answers,
        voted=voted,
        related=related,
        can_accept=bool(member and (member.pk == q.author_id or member.is_team)),
        report_reasons=Report.Reason.choices,
        answer_min=ANSWER_MIN,
    ))


@require_POST
def answer_create(request, slug: str):
    q = get_object_or_404(Question, slug=slug, status=PUBLISHED)
    member, denied = _need_member(request, q.get_absolute_url() + "#answer")
    if denied:
        return denied
    body = (request.POST.get("body") or "").replace("\r\n", "\n").strip()
    if len(body) < ANSWER_MIN:
        messages.error(request, f"Write at least {ANSWER_MIN} characters so your answer helps.")
        return redirect(q.get_absolute_url() + "#answer")
    if len(body) > BODY_MAX:
        messages.error(request, f"Keep your answer under {BODY_MAX} characters.")
        return redirect(q.get_absolute_url() + "#answer")
    if not ratelimit.consume("ask.answer", str(member.pk)).allowed:
        messages.error(request, "You've posted a lot of answers in the last hour. Try again a little later.")
        return redirect(q.get_absolute_url())
    answer = services.post_answer(member, q, body)
    if answer.status == PUBLISHED:
        messages.success(request, "Answer posted.")
    else:
        messages.success(request, "Thanks. Your answer will appear after a quick review.")
    if request.POST.get("next"):
        return redirect(_safe_next(request, request.POST["next"]))
    return redirect(f"{q.get_absolute_url()}#a-{answer.pk}")


@require_POST
def reply_create(request, pk):
    answer = get_object_or_404(Answer.objects.select_related("question"), pk=pk, status=PUBLISHED)
    url = answer.question.get_absolute_url()
    member, denied = _need_member(request, f"{url}#a-{answer.pk}")
    if denied:
        return denied
    body = " ".join((request.POST.get("body") or "").split())
    if not (REPLY_MIN <= len(body) <= Reply.MAX_LENGTH):
        messages.error(request, f"Replies can be up to {Reply.MAX_LENGTH} characters.")
    elif not ratelimit.consume("ask.reply", str(member.pk)).allowed:
        messages.error(request, "You've replied a lot in the last hour. Try again a little later.")
    else:
        reply = services.post_reply(member, answer, body)
        if reply.status != PUBLISHED:
            messages.success(request, "Thanks. Your reply will appear after a quick review.")
    return redirect(f"{url}#a-{answer.pk}")


def _vote(request, *, question=None, answer=None, back: str):
    member, denied = _need_member(request, back)
    if denied:
        return denied
    target = question or answer
    if target.author_id == member.pk:
        msg = "You can't vote on your own post."
        if _is_ajax(request):
            return JsonResponse({"error": msg}, status=403)
        messages.error(request, msg)
        return redirect(back)
    if not ratelimit.consume("ask.vote", str(member.pk)).allowed:
        if _is_ajax(request):
            return JsonResponse({"error": "Slow down a little."}, status=429)
        return redirect(back)
    voted, score = services.toggle_vote(member, question=question, answer=answer)
    if _is_ajax(request):
        return JsonResponse({"voted": voted, "score": score})
    return redirect(back)


@require_POST
def question_vote(request, slug: str):
    q = get_object_or_404(Question, slug=slug, status=PUBLISHED)
    return _vote(request, question=q, back=_safe_next(request, request.POST.get("next") or q.get_absolute_url()))


@require_POST
def answer_vote(request, pk):
    a = get_object_or_404(Answer.objects.select_related("question", "author"), pk=pk, status=PUBLISHED)
    return _vote(request, answer=a, back=f"{a.question.get_absolute_url()}#a-{a.pk}")


@require_POST
def answer_accept(request, pk):
    a = get_object_or_404(Answer.objects.select_related("question", "author"), pk=pk, status=PUBLISHED)
    url = f"{a.question.get_absolute_url()}#a-{a.pk}"
    member, denied = _need_member(request, url)
    if denied:
        return denied
    if not (member.pk == a.question.author_id or member.is_team):
        raise Http404
    accepted = services.toggle_accept(a)
    messages.success(request, "Marked as the accepted answer." if accepted else "Accepted mark removed.")
    return redirect(url)


@require_POST
def report(request, kind: str, pk):
    model = {"question": Question, "answer": Answer, "reply": Reply}.get(kind)
    if model is None:
        raise Http404
    target = get_object_or_404(model, pk=pk, status=PUBLISHED)
    back = request.POST.get("next") or reverse("ask:ask_home")
    member, denied = _need_member(request, back)
    if denied:
        return denied
    if ratelimit.consume("ask.report", str(member.pk)).allowed and target.author_id != member.pk:
        services.report(member, target, request.POST.get("reason", ""))
    messages.success(request, "Thanks. The OneWooz team will take a look.")
    return _back(request, back)


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------
@never_cache
def profile(request, handle: str):
    person = get_object_or_404(Member, handle=handle)
    answers = sorted_rows(
        Answer.objects.filter(author=person, status=PUBLISHED, question__status=PUBLISHED)
        .select_related("question", "question__topic").order_by("-created_at"),
        limit=10,
    )
    questions = sorted_rows(
        _published().filter(author=person).select_related("topic")
        .only("id", "title", "slug", "answer_count", "score", "created_at", "topic", "topic__name")
        .order_by("-created_at"),
        limit=6,
    )
    strengths = list(
        Answer.objects.filter(author=person, status=PUBLISHED, question__topic__isnull=False)
        .values("question__topic__name").annotate(n=Count("id")).order_by("-n")[:5]
    )
    top = max([s["n"] for s in strengths] or [1])
    for s in strengths:
        s["pct"] = max(6, round(s["n"] * 100 / top))
    request.seo_extra = {
        "title": f"{person.display_name} on Ask OneWooz",
        "description": f"{person.display_name}: {person.answer_count} answers and {person.accepted_count} accepted on Ask OneWooz.",
        "noindex": not person.answer_count,
    }
    return render(request, "community/profile.html", _base(
        request, person=person, answers=answers, questions=questions, strengths=strengths,
        levels=[name for _floor, name in reversed(Member.LEVELS)],
        is_me=bool(services.current_member(request) and services.current_member(request).pk == person.pk),
    ))


def _profile_error(request, member, text: str, status: int = 400):
    """Shown inside the "Edit profile" popup, which keeps what was typed."""
    if _is_ajax(request):
        return JsonResponse({"error": text}, status=status)
    messages.error(request, text)
    return redirect(member.get_absolute_url() + "?edit=1")


@require_POST
def profile_update(request):
    member, denied = _need_member(request)
    if denied:
        return denied
    from . import profile

    if not ratelimit.consume("ask.profile.save", str(member.pk)).allowed:
        return _profile_error(request, member, "You've changed your profile a lot in the last hour. Try again a little later.", 429)
    name = services.clean_display_name(request.POST.get("display_name", ""))
    bio = " ".join((request.POST.get("bio") or "").split())[:160]
    extras = profile.ProfileExtrasForm(request.POST, request.FILES)
    if len(name) < 2:
        return _profile_error(request, member, "Your display name needs at least 2 characters.")
    if not extras.is_valid():
        return _profile_error(request, member, extras.error_text())
    Member.objects.filter(pk=member.pk).update(
        display_name=name, bio=bio, notify_answers=bool(request.POST.get("notify_answers")),
    )
    member.refresh_from_db()
    profile.save(member, extras)
    messages.success(request, "Profile saved.")
    if _is_ajax(request):
        return JsonResponse({"redirect": member.get_absolute_url()})
    return redirect(member.get_absolute_url())


# ---------------------------------------------------------------------------
# Joining: email + one-time code. Same response whether or not the email is
# already a member, so the form never discloses who has an account.
# ---------------------------------------------------------------------------
@never_cache
def join(request):
    from accounts import otp
    from accounts.emails import send_ask_code

    from . import profile

    next_url = _safe_next(request, request.POST.get("next") or request.GET.get("next") or "")
    if services.current_member(request) is not None and request.method == "GET":
        return redirect(next_url)
    request.seo_extra = {"noindex": True, "title": "Join Ask OneWooz"}
    ctx = _base(request, next=next_url, step="email", error="", email="", name="",
                profile_form=profile.ProfileExtrasForm())
    pending = request.session.get("ask_join") or {}

    if request.method == "POST" and request.POST.get("action") == "send":
        email = (request.POST.get("email") or "").strip().lower()
        name = services.clean_display_name(request.POST.get("display_name", ""))
        resend = bool(request.POST.get("resend"))
        # Text fields first (cheap). The photo is only decoded further down,
        # once the send rate limits allow this attempt.
        extras = profile.ProfileExtrasForm(request.POST)
        ctx.update(email=email, name=name, profile_form=extras)
        try:
            validate_email(email)
        except ValidationError:
            ctx["error"] = "Enter a valid email address."
            return render(request, "community/join.html", ctx)
        if len(name) < 2:
            ctx["error"] = "Choose a display name of at least 2 characters."
            return render(request, "community/join.html", ctx)
        if not resend and not extras.is_valid():
            ctx["error"] = extras.error_text()
            return render(request, "community/join.html", ctx)
        ip = ratelimit.client_identity(request)
        allowed = (ratelimit.consume("ask.join.send", email).allowed
                   and ratelimit.consume("ask.join.send.ip", ip).allowed)
        if resend and pending.get("email") == email:
            held = pending.get("extras") or {}  # "Send a new code" keeps what they chose
        elif allowed and not resend:
            if request.FILES.get("avatar"):
                extras = profile.ProfileExtrasForm(request.POST, request.FILES)
                if not extras.is_valid():
                    ctx.update(profile_form=extras, error=extras.error_text() + " Choose the photo again.")
                    return render(request, "community/join.html", ctx)
            held = profile.hold_for_join(extras)
        else:
            held = {}
        if allowed:
            code = otp.issue(services.OTP_PURPOSE, email)
            send_ask_code(email, code, minutes=otp.TTL_SECONDS // 60)
        request.session["ask_join"] = {"email": email, "name": name, "next": next_url, "extras": held}
        ctx.update(step="code")
        return render(request, "community/join.html", ctx)

    if request.method == "POST" and request.POST.get("action") == "verify" and pending.get("email"):
        email = pending["email"]
        ctx.update(step="code", email=email, name=pending.get("name", ""), next=pending.get("next") or next_url)
        if not ratelimit.consume("ask.join.verify", email).allowed:
            ctx["error"] = "Too many tries. Ask for a new code in a few minutes."
            return render(request, "community/join.html", ctx)
        result = otp.verify(services.OTP_PURPOSE, email, request.POST.get("code", ""))
        if not result.ok:
            ctx["error"] = {
                "expired": "That code has expired. Ask for a new one.",
                "too_many_attempts": "Too many wrong tries. Ask for a new code.",
            }.get(result.reason, "That code isn't right. Check the email and try again.")
            return render(request, "community/join.html", ctx)
        member = services.get_or_create_member(email, pending.get("name", ""))
        request.session.pop("ask_join", None)
        services.sign_in(request, member)
        if not member.is_banned:
            # Photo, website and brand chosen on the Join form; only fills
            # what is still blank, so signing back in never overwrites.
            profile.apply_after_join(member, pending.get("extras") or {})
        messages.success(request, f"Welcome, {member.display_name}.")
        return redirect(_safe_next(request, ctx["next"]))

    return render(request, "community/join.html", ctx)


@require_POST
def leave(request):
    from .bridge import SESSION_OFF

    services.sign_out(request)
    request.session[SESSION_OFF] = True  # don't link them straight back in
    messages.success(request, "Signed out of Ask OneWooz.")
    return redirect("ask:ask_home")


# ---------------------------------------------------------------------------
# AI first: ask → instant answer (private) → optionally post to the community
# ---------------------------------------------------------------------------
@never_cache
def ask_new(request):
    from prompts import ask as ask_ai
    from prompts.models import AskQuestion

    if request.method != "POST":
        return redirect("ask:ask_home")
    text = (request.POST.get("question") or "").replace("\r\n", "\n").strip()
    if (request.POST.get("website") or "").strip():
        return redirect("ask:ask_home")  # honeypot: a bot
    identity = ratelimit.client_identity(request)
    error = ""
    if len(text) < AskQuestion.MIN_LENGTH:
        error = "Write your question in a few more words."
    elif len(text) > AskQuestion.MAX_LENGTH:
        error = f"Keep your question under {AskQuestion.MAX_LENGTH} characters."
    elif not ratelimit.consume("ask.submit", identity).allowed:
        error = "You've asked a lot of questions in the last hour. Try again a little later."
    if error:
        return _home(request, ask_error=error, ask_draft=text)

    member = services.current_member(request)
    if member is not None and member.is_banned:
        return _home(request, ask_error=services.posting_block(member), ask_draft=text)
    conf = services.settings_now()
    use_ai = conf.ai_answers_enabled and ask_ai.ai_configured() and ask_ai.claim_ai_slot()
    question_ = AskQuestion.objects.create(
        question=text,
        email=member.email if member else "",
        status=AskQuestion.Status.PENDING if use_ai else AskQuestion.Status.WAITING,
        ip_hash=salted_hmac("ask.ip", identity).hexdigest(),
    )
    if use_ai:
        transaction.on_commit(lambda: ask_ai.queue(question_))
    # Signed in: the question goes straight to the community (waiting for the
    # team's approval in Django admin). A visitor without an account joins
    # first; the page posts it as soon as they come back (see instant).
    if member is not None:
        _post_from_ask(question_, member)
    return redirect("ask:instant", token=question_.public_token())


def _post_from_ask(aq, member):
    """Post an asked question to the community as ``member``, once.

    No title, topic or details to fill in: the title is the question's first
    line, the rest becomes the details. Every post waits for approval in
    Django admin before anyone else sees it (services.hold_reason). Returns
    the community Question, or None when this member may not post it.
    """
    existing = Question.objects.filter(instant=aq).first()
    if existing is not None:
        return existing
    # Only the person who asked: a forwarded private link must not post the
    # question under someone else's name.
    if aq.email and aq.email.lower() != (member.email or "").lower():
        return None
    if services.posting_block(member):
        return None
    if not ratelimit.consume("ask.post", str(member.pk)).allowed:
        return None
    title, details = _split_title(aq.question)
    try:
        question_ = services.post_question(
            member, title=title, body=details[:BODY_MAX], topic=None,
            ai_answer=(aq.ai_answer or "").strip(), instant=aq,
        )
    except IntegrityError:  # posted by a parallel request a moment ago
        return Question.objects.filter(instant=aq).first()
    if not aq.email and member.email:
        type(aq).objects.filter(pk=aq.pk).update(email=member.email)
        aq.email = member.email
    return question_


def _instant_or_404(token: str):
    from core import tokens
    from prompts.models import AskQuestion

    try:
        payload = tokens.unsign("ask.question", token)
    except tokens.TokenError:
        raise Http404("Unknown question.")
    found = AskQuestion.objects.filter(pk=payload.get("q")).first()
    if found is None:
        raise Http404("Unknown question.")
    return found


def _instant_body(aq) -> dict:
    email = aq.email or ""
    masked = ""
    if "@" in email:
        user, _, domain = email.partition("@")
        masked = f"{user[:2]}{'*' * max(1, len(user) - 2)}@{domain}"
    return {"aq": aq, "answer_html": render_html(aq.answer), "masked_email": masked}


def _split_title(text: str) -> tuple[str, str]:
    """A posted title + details from the free-text question."""
    first, _, rest = text.strip().partition("\n")
    first = " ".join(first.split())
    if len(first) <= TITLE_MAX:
        return first, rest.strip()
    cut = first[: TITLE_MAX - 1].rsplit(" ", 1)[0]
    return cut + "…", text.strip()


@never_cache
def instant(request, token: str):
    """After asking: a plain confirmation that the question was posted, who
    will answer, and a thank you. Nothing to fill in. A visitor who asked
    without an account is asked to join first; coming back here posts it."""
    aq = _instant_or_404(token)
    posted = Question.objects.filter(instant=aq).only("id", "slug", "title", "status").first()
    member = services.current_member(request)
    if posted is None and member is not None:
        posted = _post_from_ask(aq, member)
    request.seo_extra = {"noindex": True, "title": "Question posted | Ask OneWooz"}
    ctx = _base(
        request,
        **_instant_body(aq),
        posted=posted,
        blocked=services.posting_block(member) if member is not None and posted is None else "",
    )
    response = render(request, "community/instant.html", ctx)
    response["X-Robots-Tag"] = "noindex, nofollow"
    # same-origin, not no-referrer: the private link still never leaves
    # onewooz.com, but forms posted from this page (Sign out, Post to the
    # community) keep a real Origin header. no-referrer made Chrome send
    # "Origin: null", which the CSRF check rejects (403 on sign out).
    response["Referrer-Policy"] = "same-origin"
    return response


@never_cache
def instant_status(request, token: str):
    if not ratelimit.consume("ask.status", ratelimit.client_identity(request)).allowed:
        return JsonResponse({"status": "pending", "html": ""}, status=429)
    aq = _instant_or_404(token)
    html = render_to_string("community/_instant_body.html", _instant_body(aq), request=request)
    response = JsonResponse({"status": aq.status, "html": html})
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@require_POST
def instant_post(request, token: str):
    aq = _instant_or_404(token)
    back = reverse("ask:instant", kwargs={"token": token})
    existing = Question.objects.filter(instant=aq).first()
    if existing is not None:
        return redirect(existing.get_absolute_url())
    member, denied = _need_member(request, back + "#post")
    if denied:
        return denied
    title = " ".join((request.POST.get("title") or "").split())
    body = (request.POST.get("details") or "").replace("\r\n", "\n").strip()
    room = Topic.objects.filter(slug=request.POST.get("topic", ""), is_active=True).first()
    if not (TITLE_MIN <= len(title) <= TITLE_MAX):
        messages.error(request, f"Your title needs {TITLE_MIN} to {TITLE_MAX} characters.")
        return redirect(back + "#post")
    if len(body) > BODY_MAX:
        messages.error(request, f"Keep the details under {BODY_MAX} characters.")
        return redirect(back + "#post")
    if room is None:
        messages.error(request, "Pick a topic for your question.")
        return redirect(back + "#post")
    if not ratelimit.consume("ask.post", str(member.pk)).allowed:
        messages.error(request, "You've posted a lot of questions in the last hour. Try again a little later.")
        return redirect(back)
    q = services.post_question(member, title=title, body=body, topic=room,
                               ai_answer=(aq.ai_answer or "").strip(), instant=aq)
    if q.status == PUBLISHED:
        messages.success(request, "Posted. The community can answer it now.")
    else:
        messages.success(request, "Thanks. Your question will appear after a quick review.")
    return redirect(q.get_absolute_url())


# ---------------------------------------------------------------------------
# Live desk: what's being asked now, what's hot, what's still waiting
# ---------------------------------------------------------------------------
LIVE_REFRESH_SECONDS = 20
_LIVE_FIELDS = ("id", "title", "slug", "created_at", "score", "answer_count", "last_activity_at",
                "topic", "topic__name", "topic__slug")


def _live_columns() -> dict:
    now = timezone.now()
    today = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    pub = _published().select_related("topic").only(*_LIVE_FIELDS)
    fresh = sorted_rows(pub.filter(created_at__gte=now - timedelta(hours=24)).order_by("-created_at"), limit=8)
    if not fresh:
        fresh = sorted_rows(pub.order_by("-created_at"), limit=8)
    hot = sorted_rows(
        pub.filter(last_activity_at__gte=now - timedelta(hours=48))
        .order_by("-score", "-answer_count", "-last_activity_at"),
        limit=6,
    )
    waiting_qs = _published().filter(answer_count=0, created_at__gte=now - timedelta(days=30))
    waiting = sorted_rows(
        waiting_qs.select_related("topic").only(*_LIVE_FIELDS).order_by("created_at"), limit=8,
    )
    answers_today = Answer.objects.filter(status=PUBLISHED, created_at__gte=today)
    return {
        "fresh": fresh,
        "hot": hot,
        "waiting": waiting,
        "pulse": {
            "questions_today": _published().filter(created_at__gte=today).count(),
            "answers_today": answers_today.count(),
            "people_today": answers_today.values("author_id").distinct().count(),
            "waiting": waiting_qs.count(),
        },
        "updated_at": now,
    }


@never_cache
def live(request):
    Topic.ensure_defaults()
    from prompts.models import AskQuestion

    return render(request, "community/live.html", _base(
        request,
        **_live_columns(),
        refresh_url=reverse("ask:live_columns"),
        refresh_seconds=LIVE_REFRESH_SECONDS,
        max_length=AskQuestion.MAX_LENGTH,
    ))


@never_cache
def live_columns(request):
    """The three live columns as HTML, polled by the live page."""
    if not ratelimit.consume("ask.live", ratelimit.client_identity(request)).allowed:
        return JsonResponse({"html": ""}, status=429)
    html = render_to_string("community/_live_columns.html", _base(request, **_live_columns()), request=request)
    return JsonResponse({"html": html})
