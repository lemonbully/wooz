"""Ask OneWooz weekly: the digest page (/ask/weekly/), sign-up, confirmation,
unsubscribe, and the "stop emailing me new answers" switch.

Sign-up never discloses whether an address is already subscribed: a visitor
always sees "check your inbox". Unsubscribe and opt-out links land on a page
with a button (POST), so mail scanners that prefetch links change nothing.
"""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from core import ratelimit, tokens

from . import digest, services
from .models import DigestSubscriber, Member
from .views import _base


def _subscribed(member) -> bool:
    if member is None:
        return False
    return DigestSubscriber.objects.filter(
        email=member.email, is_confirmed=True, unsubscribed_at__isnull=True,
    ).exists()


@never_cache
def weekly(request, year: int | None = None, week: int | None = None):
    if year is None:
        year, week = digest.current_week()
    elif (year, week) == digest.current_week():
        return redirect("ask:weekly")
    elif not digest.valid(year, week):
        raise Http404("No such week.")

    data = digest.build(year, week)
    prev_year, prev_week = digest.shift(year, week, -1)
    nxt = None if data["is_current"] else digest.shift(year, week, 1)
    request.seo_extra = {
        "title": ("This week on Ask OneWooz" if data["is_current"]
                  else f"Ask OneWooz weekly: week {week}, {year}"),
        "description": (f"{data['stats']['questions']} questions and {data['stats']['answers']} answers "
                        f"from online sellers in week {week} of {year}."),
        "noindex": data["is_empty"],
    }
    member = services.current_member(request)
    return render(request, "community/weekly.html", _base(
        request,
        d=data,
        prev_url=reverse("ask:weekly_issue", kwargs={"year": prev_year, "week": prev_week}),
        next_url=(None if nxt is None else (
            reverse("ask:weekly") if nxt == digest.current_week()
            else reverse("ask:weekly_issue", kwargs={"year": nxt[0], "week": nxt[1]})
        )),
        subscribed=_subscribed(member),
    ))


@require_POST
def weekly_subscribe(request):
    from .emails import send_digest_confirm

    back = reverse("ask:weekly") + "#subscribe"
    if (request.POST.get("website") or "").strip():
        return redirect(back)  # honeypot
    member = services.current_member(request)
    now = timezone.now()
    if member is not None:
        # The member's email is already confirmed by their join code.
        sub, _ = DigestSubscriber.objects.get_or_create(email=member.email, defaults={"member": member})
        DigestSubscriber.objects.filter(pk=sub.pk).update(
            member=member, is_confirmed=True, confirmed_at=sub.confirmed_at or now, unsubscribed_at=None,
        )
        messages.success(request, "You're subscribed. The digest arrives every Monday morning.")
        return redirect(back)

    email = (request.POST.get("email") or "").strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        messages.error(request, "Enter a valid email address.")
        return redirect(back)
    if (ratelimit.consume("ask.digest.subscribe", ratelimit.client_identity(request)).allowed
            and ratelimit.consume("ask.digest.subscribe.email", email).allowed):
        sub, _ = DigestSubscriber.objects.get_or_create(email=email)
        if not sub.is_active:
            send_digest_confirm(email)
    messages.success(request, "Check your inbox and confirm. The digest arrives every Monday morning.")
    return redirect(back)


def _status(request, *, title: str, text: str, form_action: str = "", button: str = ""):
    request.seo_extra = {"noindex": True, "title": f"{title} | Ask OneWooz"}
    response = render(request, "community/notice.html", _base(
        request, title=title, text=text, form_action=form_action, button=button,
    ))
    response["X-Robots-Tag"] = "noindex, nofollow"
    # same-origin, not no-referrer: this page has forms (and the header's Sign
    # out); no-referrer makes Chrome send "Origin: null", failing CSRF.
    response["Referrer-Policy"] = "same-origin"
    return response


def _email_from(purpose: str, token: str) -> str:
    try:
        return (tokens.unsign(purpose, token).get("e") or "").strip().lower()
    except tokens.TokenError:
        return ""


@never_cache
def weekly_confirm(request, token: str):
    email = _email_from("ask.digest.confirm", token)
    if not email:
        return _status(request, title="That link has expired",
                       text="Sign up again on the weekly page and use the newest email.")
    sub, _ = DigestSubscriber.objects.get_or_create(email=email)
    member = Member.objects.filter(email=email).first()
    DigestSubscriber.objects.filter(pk=sub.pk).update(
        is_confirmed=True, confirmed_at=sub.confirmed_at or timezone.now(), unsubscribed_at=None,
        member=member or sub.member,
    )
    return _status(request, title="You're subscribed",
                   text="Ask OneWooz weekly arrives every Monday morning: the question of the week, "
                        "the most helpful answers and what still needs an answer.")


@never_cache
def weekly_unsubscribe(request, token: str):
    email = _email_from("ask.digest.unsub", token)
    if not email:
        return _status(request, title="That link has expired",
                       text="Use the unsubscribe link in the newest digest email.")
    if request.method == "POST":
        DigestSubscriber.objects.filter(email=email).update(unsubscribed_at=timezone.now())
        return _status(request, title="You're unsubscribed",
                       text="You won't get the weekly digest any more. You can sign up again any time.")
    return _status(request, title="Unsubscribe from the weekly digest?",
                   text=f"Stop sending Ask OneWooz weekly to {email}.",
                   form_action=request.path, button="Unsubscribe")


@never_cache
def notify_off(request, token: str):
    try:
        member_id = tokens.unsign("ask.notify.off", token).get("m")
    except tokens.TokenError:
        member_id = None
    member = Member.objects.filter(pk=member_id).first() if member_id else None
    if member is None:
        return _status(request, title="That link has expired",
                       text="You can switch answer emails off on your profile instead.")
    if request.method == "POST":
        Member.objects.filter(pk=member.pk).update(notify_answers=False)
        return _status(request, title="Answer emails are off",
                       text="We won't email you when someone answers your questions. "
                            "You can switch them back on from your profile.")
    return _status(request, title="Stop answer emails?",
                   text="We email you when someone answers a question you asked, at most once an hour per question.",
                   form_action=request.path, button="Stop these emails")
