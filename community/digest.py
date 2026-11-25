"""The Ask OneWooz weekly digest: one ISO week (Monday to Sunday, IST) of the
community, as shown on /ask/weekly/ and mailed every Monday.

:func:`build` returns model objects for the page; :func:`email_context` turns
that into plain strings and absolute URLs a Celery job can pass around.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.db.models import Count
from django.utils import timezone

from core.queries import sorted_rows

from .models import Answer, Member, Question, Visibility

PUBLISHED = Visibility.PUBLISHED


def current_week() -> tuple[int, int]:
    year, week, _ = timezone.localdate().isocalendar()
    return year, week


def shift(year: int, week: int, weeks: int) -> tuple[int, int]:
    d = date.fromisocalendar(year, week, 1) + timedelta(weeks=weeks)
    y, w, _ = d.isocalendar()
    return y, w


def bounds(year: int, week: int) -> tuple[datetime, datetime]:
    start = timezone.make_aware(datetime.combine(date.fromisocalendar(year, week, 1), time.min))
    return start, start + timedelta(days=7)


def valid(year: int, week: int) -> bool:
    try:
        date.fromisocalendar(year, week, 1)
    except ValueError:
        return False
    return (year, week) <= current_week() and year >= 2024


def _plain(text: str, limit: int) -> str:
    text = " ".join((text or "").replace("**", "").split())
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def build(year: int, week: int) -> dict:
    start, end = bounds(year, week)
    questions = Question.objects.filter(status=PUBLISHED, created_at__gte=start, created_at__lt=end)
    answers = Answer.objects.filter(
        status=PUBLISHED, question__status=PUBLISHED, created_at__gte=start, created_at__lt=end,
    )

    top = sorted_rows(
        questions.select_related("author", "topic").order_by("-score", "-answer_count", "-created_at"),
        limit=1,
    )
    top_q = top[0] if top else None
    top_preview = ""
    if top_q is not None:
        best = sorted_rows(
            Answer.objects.filter(question=top_q, status=PUBLISHED).order_by("-is_accepted", "-score"),
            limit=1,
        )
        top_preview = _plain(best[0].body if best else (top_q.ai_answer or top_q.body), 260)

    helpful = sorted_rows(
        answers.select_related("question", "author").order_by("-score", "-created_at"),
        limit=4,
    )

    counts = list(answers.values("author_id").annotate(n=Count("id")).order_by("-n")[:5])
    people = {m.pk: m for m in Member.objects.filter(pk__in=[c["author_id"] for c in counts])}
    contributors = [{"member": people[c["author_id"]], "n": c["n"]} for c in counts if c["author_id"] in people]

    busiest = list(
        questions.exclude(topic__isnull=True).values("topic_id", "topic__name")
        .annotate(n=Count("id")).order_by("-n")[:1]
    )
    busy_topic, busy_questions = None, []
    if busiest:
        busy_topic = busiest[0]["topic__name"]
        busy_questions = sorted_rows(
            questions.filter(topic_id=busiest[0]["topic_id"]).only("id", "title", "slug", "answer_count")
            .order_by("-score", "-created_at"),
            limit=3,
        )

    waiting = sorted_rows(
        Question.objects.filter(
            status=PUBLISHED, answer_count=0,
            created_at__lt=end - timedelta(hours=48), created_at__gte=end - timedelta(days=30),
        ).only("id", "title", "slug", "created_at").order_by("created_at"),
        limit=4,
    )

    stats = {"questions": questions.count(), "answers": answers.count()}
    return {
        "year": year,
        "week": week,
        "start": start,
        "last_day": end - timedelta(days=1),
        "is_current": (year, week) == current_week(),
        "stats": stats,
        "top": top_q,
        "top_preview": top_preview,
        "helpful": helpful,
        "contributors": contributors,
        "busy_topic": busy_topic,
        "busy_questions": busy_questions,
        "waiting": waiting,
        "is_empty": not (stats["questions"] or stats["answers"]),
    }


def email_context(data: dict, absolute) -> dict:
    """Strings only, for the email job. ``absolute(path)`` makes a full URL."""

    def link(q):
        return {"title": q.title, "url": absolute(q.get_absolute_url())}

    top = data["top"]
    return {
        "year": data["year"],
        "week": data["week"],
        "range": f"{data['start'].day} {data['start']:%b} to {data['last_day'].day} {data['last_day']:%b %Y}",
        "stats": data["stats"],
        "top": ({**link(top), "preview": data["top_preview"], "score": top.score, "answers": top.answer_count}
                if top else None),
        "helpful": [
            {"title": a.question.title, "url": absolute(f"{a.question.get_absolute_url()}#a-{a.pk}"),
             "by": a.author.display_name, "score": a.score}
            for a in data["helpful"]
        ],
        "contributors": [{"name": c["member"].display_name, "n": c["n"]} for c in data["contributors"]],
        "busy_topic": data["busy_topic"],
        "busy_questions": [link(q) for q in data["busy_questions"]],
        "waiting": [link(q) for q in data["waiting"]],
    }
