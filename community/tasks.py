"""Celery jobs for the Ask OneWooz community."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger("onewooz.ask")

#: Local hour on Monday after which last week's digest goes out.
DIGEST_HOUR = 9
DIGEST_CHUNK = 100


@shared_task(ignore_result=True)
def send_weekly_digest() -> str:
    """Hourly. Between Monday 09:00 and Tuesday night (IST), send last week's
    digest once. A missed Monday (Beat down) still sends on Tuesday; later in
    the week it is skipped rather than arriving stale."""
    from django.db import IntegrityError
    from django.utils import timezone

    from . import digest
    from .emails import absolute
    from .models import CommunitySettings, DigestIssue, DigestSubscriber

    if not CommunitySettings.current().digest_enabled:
        return "paused"
    now = timezone.localtime()
    day = now.isoweekday()
    if day > 2 or (day == 1 and now.hour < DIGEST_HOUR):
        return "not now"
    year, week = digest.shift(*digest.current_week(), -1)
    try:
        issue = DigestIssue.objects.create(year=year, week=week)
    except IntegrityError:
        return "already sent"

    data = digest.build(year, week)
    if data["is_empty"]:
        return "empty week"
    context = digest.email_context(data, absolute)
    emails = list(
        DigestSubscriber.objects.filter(is_confirmed=True, unsubscribed_at__isnull=True)
        .order_by().values_list("email", flat=True)
    )
    for i in range(0, len(emails), DIGEST_CHUNK):
        send_digest_chunk.delay(emails[i:i + DIGEST_CHUNK], context)
    DigestIssue.objects.filter(pk=issue.pk).update(recipients=len(emails))
    return f"queued {len(emails)}"


@shared_task(ignore_result=True)
def send_digest_chunk(emails: list, context: dict) -> int:
    from .emails import send_digest

    sent = 0
    for email in emails:
        try:
            sent += bool(send_digest(email, context))
        except Exception:
            logger.exception("Ask OneWooz digest failed for one recipient")
    return sent


@shared_task(ignore_result=True)
def notify_answer(answer_id: str) -> str:
    """Tell an asker someone answered. At most one email per question an hour."""
    from django.core.cache import cache

    from .emails import send_answered
    from .models import Answer, Visibility

    answer = Answer.objects.select_related("question", "question__author", "author").filter(pk=answer_id).first()
    if answer is None or answer.status != Visibility.PUBLISHED:
        return "skip"
    question, asker = answer.question, answer.question.author
    if asker.pk == answer.author_id or not asker.notify_answers or question.status != Visibility.PUBLISHED:
        return "skip"
    try:
        if not cache.add(f"ask:notified:{question.pk}", 1, 3600):
            return "throttled"
    except Exception:
        pass
    send_answered(asker, question, answer)
    return "sent"


@shared_task(ignore_result=True)
def suggest_answer(question_id: str) -> str:
    """Draft an answer for the team desk with the active Ask prompt. The result
    waits in the cache for the desk page to pick up."""
    from django.core.cache import cache

    from prompts.ask import draft_answer

    from .models import Answer, Question, Visibility

    key = f"ask:suggest:{question_id}"
    question = Question.objects.filter(pk=question_id).first()
    if question is None:
        cache.delete(key)
        return "missing"
    others = list(
        Answer.objects.filter(question=question, status=Visibility.PUBLISHED)
        .order_by().values_list("body", flat=True)[:5]
    )
    try:
        text = draft_answer(question.title, question.body, others)
        cache.set(key, {"status": "ready", "text": text}, 3600)
        return "ready"
    except Exception as exc:
        cache.set(key, {"status": "error", "error": str(exc)[:300]}, 300)
        return "error"
