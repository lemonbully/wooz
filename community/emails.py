"""Ask OneWooz emails: digest sign-up confirmation, the weekly digest, and
"someone answered your question". Sent through ``accounts.emails._send`` so
they share the OneWooz email layout, sender and failure handling.

Links are built without a request (these run in Celery): reversed against the
marketing urlconf at the root prefix and put on the marketing origin.
"""

from __future__ import annotations

from django.urls import get_script_prefix, reverse, set_script_prefix

from core import links, tokens
from core.hosts import Surface


def absolute(path: str) -> str:
    return links.surface_url(Surface.MARKETING, path)


def url_for(name: str, **kwargs) -> str:
    previous = get_script_prefix()
    try:
        set_script_prefix("/")
        path = reverse(name, kwargs=kwargs or None, urlconf="onewooz.urls.marketing")
    finally:
        set_script_prefix(previous)
    return absolute(path)


def _send(*args, **kwargs) -> bool:
    from accounts.emails import _send as send

    return send(*args, **kwargs)


def send_digest_confirm(email: str) -> bool:
    token = tokens.sign("ask.digest.confirm", {"e": email})
    return _send(
        email,
        "Confirm your Ask OneWooz weekly digest",
        "ask_digest_confirm",
        {"url": url_for("ask:weekly_confirm", token=token)},
    )


def send_digest(email: str, context: dict) -> bool:
    token = tokens.sign("ask.digest.unsub", {"e": email})
    top = context.get("top") or {}
    subject = f"Ask OneWooz weekly: {top['title']}" if top else f"Ask OneWooz weekly, week {context['week']}"
    return _send(
        email,
        subject[:150],
        "ask_digest",
        {
            **context,
            "weekly_url": url_for("ask:weekly_issue", year=context["year"], week=context["week"]),
            "home_url": url_for("ask:ask_home"),
            "unsubscribe_url": url_for("ask:weekly_unsubscribe", token=token),
        },
    )


def send_answered(member, question, answer) -> bool:
    token = tokens.sign("ask.notify.off", {"m": str(member.pk)})
    preview = " ".join(answer.body.replace("**", "").split())
    if len(preview) > 280:
        preview = preview[:279].rsplit(" ", 1)[0] + "…"
    return _send(
        member.email,
        f"New answer: {question.title}"[:150],
        "ask_answered",
        {
            "name": member.display_name,
            "question": question.title,
            "by": answer.author.display_name,
            "by_team": answer.author.is_team,
            "preview": preview,
            "url": absolute(f"{question.get_absolute_url()}#a-{answer.pk}"),
            "off_url": url_for("ask:notify_off", token=token),
        },
    )
