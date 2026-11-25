"""Questions and answers the team writes from the Ask OneWooz console.

The console can:

* write a new question (and any number of answers to it) in one go:
  ``/ask/questions/new/``;
* add an answer to any thread, and edit any question or answer;
* set what the site shows for each post: who posted it (yourself with the
  OneWooz team label, an existing member, or any name you type), the date it
  was asked or answered, its vote count and, for questions, its view count.

A typed name becomes a *persona* member: a :class:`~community.models.Member`
whose email is on :data:`~community.models.PERSONA_DOMAIN`. It has a public
profile like anyone else, but it can never receive mail or a sign-in code, and
it never gets "new answer" emails. Typing the same name again reuses it.

Team-written posts skip the automatic hold rules (links, blocked phrases,
approve-all): the status is whatever you pick. Counters that come from other
rows (answer count, accepted answer, team flag, reputation) are still
recomputed by :mod:`community.services`, so they never drift.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.db import transaction
from django.forms import formset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from . import services
from .console import _ctx, _log, _team_member_for, console_view
from .models import PERSONA_DOMAIN, Answer, Member, Question, Topic, Visibility

DATETIME_FORMAT = "%Y-%m-%dT%H:%M"
MAX_ANSWERS = 20


# --------------------------------------------------------------------------
# Who a post is shown as
# --------------------------------------------------------------------------
def persona_for(name: str, *, team: bool = False, store_url: str = "") -> Member:
    """The persona member shown as ``name``, created on first use.

    The same name (any capitals) reuses the same persona, so one name keeps
    one profile and one reputation. Team label and store link are updated to
    what was typed this time.
    """
    name = services.clean_display_name(name)
    member = (
        Member.objects.filter(email__endswith="@" + PERSONA_DOMAIN, display_name__iexact=name)
        .order_by()
        .first()
    )
    if member is None:
        return Member.objects.create(
            email=f"{secrets.token_hex(8)}@{PERSONA_DOMAIN}",
            display_name=name,
            handle=services._unique_handle(name),
            is_team=team,
            store_url=store_url,
            notify_answers=False,
        )
    changes = {}
    if member.is_team != team:
        changes["is_team"] = team
    if store_url and member.store_url != store_url:
        changes["store_url"] = store_url
    if changes:
        Member.objects.filter(pk=member.pk).update(**changes)
        for key, value in changes.items():
            setattr(member, key, value)
    return member


def _personas() -> list[Member]:
    rows = list(
        Member.objects.filter(email__endswith="@" + PERSONA_DOMAIN)
        .only("id", "display_name", "is_team")
        .order_by()[:500]
    )
    rows.sort(key=lambda m: m.display_name.lower())
    return rows


def _is_me(member: Member | None, user) -> bool:
    return bool(member is not None and user is not None and user.email
                and member.email == user.email.strip().lower())


def _backdate(member: Member, when) -> None:
    """A persona's "member since" never comes after its first post."""
    if member.is_persona and when:
        Member.objects.filter(pk=member.pk, created_at__gt=when).update(created_at=when)


def _local(dt) -> str:
    return timezone.localtime(dt).strftime(DATETIME_FORMAT) if dt else ""


# --------------------------------------------------------------------------
# Forms
# --------------------------------------------------------------------------
class PostedForm(forms.Form):
    """Who a post is shown as, when, and with how many votes."""

    poster = forms.ChoiceField(
        label="Posted by",
        help_text="Pick a name you have used before, or 'Someone new' to type any name.",
    )
    poster_name = forms.CharField(
        label="Name shown", max_length=40, required=False,
        help_text="For someone new. The same name later reuses the same profile.",
    )
    poster_team = forms.BooleanField(label="Show the OneWooz team label", required=False)
    poster_store_url = forms.URLField(
        label="Their store link (optional)", required=False, assume_scheme="https",
        help_text="Shows 'Runs a store on OneWooz' next to the name.",
    )
    posted_on = forms.DateTimeField(
        input_formats=[DATETIME_FORMAT, "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format=DATETIME_FORMAT),
    )
    votes = forms.IntegerField(
        label="Votes", min_value=0, max_value=1_000_000, initial=0,
        help_text="The vote count shown. Readers' upvotes add to it from here.",
    )

    when_label = "Posted on"

    def __init__(self, *args, user=None, current: Member | None = None, mode: str = "new", **kwargs):
        super().__init__(*args, **kwargs)
        self.user, self.current, self.mode = user, current, mode
        self.fields["posted_on"].label = self.when_label
        me = "Me, with the OneWooz team label" + (f" ({user.email})" if user and user.email else "")
        choices: list = [("me", me)]
        if current is not None and not current.is_persona and not _is_me(current, user):
            choices.append((str(current.pk), f"Keep {current.display_name} (signed-up member)"))
        personas = _personas()
        if current is not None and current.is_persona and all(p.pk != current.pk for p in personas):
            personas.insert(0, current)  # never let a save silently change who posted it
        if personas:
            choices.append(("Names you have used", [
                (str(p.pk), p.display_name + (" (team label)" if p.is_team else "")) for p in personas
            ]))
        choices.append(("new", "Someone new (type a name)"))
        self.fields["poster"].choices = choices
        if not self.is_bound and not self.initial.get("posted_on"):
            self.initial["posted_on"] = _local(timezone.now())

    #: The post's own text; everything else is "what the site shows".
    CONTENT_FIELDS = ("title", "topic", "body", "ai_answer")

    def content_fields(self):
        return [self[name] for name in self.fields if name in self.CONTENT_FIELDS]

    def shown_fields(self):
        return [self[name] for name in self.fields if name not in self.CONTENT_FIELDS]

    def clean_posted_on(self):
        when = self.cleaned_data.get("posted_on")
        if when and when > timezone.now() + timedelta(minutes=5):
            raise forms.ValidationError("This can't be in the future.")
        return when

    def clean(self):
        data = super().clean()
        if data.get("poster") == "new":
            name = services.clean_display_name(data.get("poster_name") or "")
            if len(name) < 2:
                self.add_error("poster_name", "Type the name to show (at least 2 characters).")
            data["poster_name"] = name
        return data

    def poster_member(self) -> Member:
        """The member this post should be shown as (created when new)."""
        choice = self.cleaned_data["poster"]
        if choice == "me":
            return _team_member_for(self.user)
        if choice == "new":
            return persona_for(
                self.cleaned_data["poster_name"],
                team=bool(self.cleaned_data.get("poster_team")),
                store_url=self.cleaned_data.get("poster_store_url") or "",
            )
        if self.current is not None and choice == str(self.current.pk):
            return self.current
        return Member.objects.get(pk=choice)

    @staticmethod
    def poster_initial(member: Member | None, user) -> str:
        if member is None or _is_me(member, user):
            return "me"
        return str(member.pk)


class QuestionForm(PostedForm):
    title = forms.CharField(max_length=200, widget=forms.TextInput(attrs={"maxlength": 200}))
    topic = forms.ModelChoiceField(queryset=Topic.objects.none(), required=False, empty_label="No room",
                                   label="Topic room")
    body = forms.CharField(
        label="Details", required=False, max_length=5000, widget=forms.Textarea(attrs={"rows": 5}),
        help_text="Optional. Blank line for a new paragraph, \"- \" for a list, **bold**.",
    )
    ai_answer = forms.CharField(
        label="Instant AI answer (optional)", required=False, max_length=8000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Shown folded under the question as the AI answer the asker saw. Leave empty for none.",
    )
    views = forms.IntegerField(label="Views", min_value=0, max_value=10_000_000, initial=0,
                               help_text="The view count shown. Real visits add to it from here.")
    status = forms.ChoiceField(choices=Visibility.choices, initial=Visibility.PUBLISHED,
                               help_text="Team posts skip the automatic hold rules.")

    when_label = "Asked on"
    field_order = ("title", "topic", "body", "ai_answer", "poster", "poster_name", "poster_team",
                   "poster_store_url", "posted_on", "votes", "views", "status")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["topic"].queryset = Topic.objects.all()
        if self.mode == "edit":
            del self.fields["status"]  # the moderation buttons own the status

    @classmethod
    def initial_for(cls, q: Question, user) -> dict:
        return {
            "title": q.title, "topic": q.topic_id, "body": q.body, "ai_answer": q.ai_answer,
            "poster": cls.poster_initial(q.author, user), "posted_on": _local(q.created_at),
            "votes": max(0, q.score), "views": q.view_count,
        }


class AnswerForm(PostedForm):
    body = forms.CharField(
        label="Answer", max_length=5000, widget=forms.Textarea(attrs={"rows": 6}),
        help_text="Blank line for a new paragraph, \"- \" for a list, **bold**.",
    )
    accepted = forms.BooleanField(label="Mark as the accepted answer", required=False)
    status = forms.ChoiceField(choices=Visibility.choices, initial=Visibility.PUBLISHED)

    when_label = "Answered on"
    field_order = ("body", "poster", "poster_name", "poster_team", "poster_store_url",
                   "posted_on", "votes", "accepted", "status")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.mode == "edit":
            del self.fields["status"]

    def has_changed(self) -> bool:
        # A blank answer card on the new-question page is simply skipped,
        # whatever its date and name boxes hold.
        return bool((self.data.get(self.add_prefix("body")) or "").strip()) if self.is_bound else False

    @classmethod
    def initial_for(cls, a: Answer, user) -> dict:
        return {
            "body": a.body, "poster": cls.poster_initial(a.author, user), "posted_on": _local(a.created_at),
            "votes": max(0, a.score), "accepted": a.is_accepted,
        }


AnswerFormSet = formset_factory(AnswerForm, extra=1, max_num=MAX_ANSWERS, validate_max=True)


def _new_initial(user) -> dict:
    return {"poster": "me", "posted_on": _local(timezone.now()), "votes": 0}


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------
def _create_answer(question: Question, form: AnswerForm) -> tuple[Answer, set]:
    """Create one answer from a valid form. Returns it and the members whose
    counters need refreshing."""
    data = form.cleaned_data
    member = form.poster_member()
    status = data.get("status") or Visibility.PUBLISHED
    answer = Answer.objects.create(
        question=question, author=member, body=data["body"].replace("\r\n", "\n").strip(),
        status=status, score=data["votes"], created_at=data["posted_on"],
    )
    touched = {member.pk}
    if data.get("accepted"):
        touched |= set(Answer.objects.filter(question=question, is_accepted=True).values_list("author_id", flat=True))
        Answer.objects.filter(question=question, is_accepted=True).update(is_accepted=False)
        Answer.objects.filter(pk=answer.pk).update(is_accepted=True)
        answer.is_accepted = True
    _backdate(member, data["posted_on"])
    return answer, touched


def _refresh(question: Question, member_ids: set) -> None:
    question.refresh_from_db(fields=["created_at"])
    services.refresh_question(question)
    for member in Member.objects.filter(pk__in=member_ids):
        services.refresh_member(member)


def _check_answer_dates(forms_, asked_on) -> bool:
    ok = True
    for f in forms_:
        when = f.cleaned_data.get("posted_on")
        if when and asked_on and when < asked_on:
            f.add_error("posted_on", "An answer can't be dated before its question was asked.")
            ok = False
    return ok


@console_view()
def question_new(request):
    """Write a question and its answers, with every shown detail set by hand."""
    Topic.ensure_defaults()
    user = request.user
    if request.method == "POST":
        qform = QuestionForm(request.POST, prefix="q", user=user, mode="new")
        aset = AnswerFormSet(request.POST, prefix="a", form_kwargs={"user": user, "mode": "new"})
        if qform.is_valid() and aset.is_valid():
            filled = [f for f in aset.forms if f.has_changed() and f.cleaned_data]
            ok = _check_answer_dates(filled, qform.cleaned_data["posted_on"])
            if sum(1 for f in filled if f.cleaned_data.get("accepted")) > 1:
                aset._non_form_errors = aset.error_class(["Only one answer can be the accepted one."])
                ok = False
            if ok:
                with transaction.atomic():
                    data = qform.cleaned_data
                    asker = qform.poster_member()
                    q = Question.objects.create(
                        author=asker, topic=data.get("topic"), title=data["title"].strip(),
                        slug=services._unique_slug(data["title"]),
                        body=(data.get("body") or "").replace("\r\n", "\n").strip(),
                        ai_answer=(data.get("ai_answer") or "").replace("\r\n", "\n").strip(),
                        status=data["status"], score=data["votes"], view_count=data["views"],
                        created_at=data["posted_on"], last_activity_at=data["posted_on"],
                    )
                    _backdate(asker, data["posted_on"])
                    touched = {asker.pk}
                    for f in filled:
                        _, members = _create_answer(q, f)
                        touched |= members
                    _refresh(q, touched)
                _log(request, "question.write", q, answers=len(filled), shown_as=asker.display_name)
                messages.success(
                    request,
                    f"Question added with {len(filled)} answer{'s' if len(filled) != 1 else ''}."
                    + ("" if data["status"] == Visibility.PUBLISHED else " It is not public until you approve it."),
                )
                return redirect("ask_console:thread", pk=q.pk)
    else:
        initial = {**_new_initial(user), "views": 0, "status": Visibility.PUBLISHED}
        qform = QuestionForm(prefix="q", user=user, mode="new", initial=initial)
        aset = AnswerFormSet(prefix="a", form_kwargs={"user": user, "mode": "new"},
                             initial=[{**_new_initial(user), "status": Visibility.PUBLISHED}])
    return render(request, "community/console/question_new.html", _ctx(
        request, "questions", qform=qform, aset=aset, max_answers=MAX_ANSWERS,
    ))


def save_question(request, q: Question, form: QuestionForm) -> None:
    """Apply a valid edit form to a question (text and what the site shows)."""
    data = form.cleaned_data
    old_author_id = q.author_id
    member = form.poster_member()
    first_answer = (
        Answer.objects.filter(question=q).order_by("created_at").values_list("created_at", flat=True)[:1]
    )
    first_answer = next(iter(first_answer), None)
    with transaction.atomic():
        Question.objects.filter(pk=q.pk).update(
            title=data["title"].strip(), topic=data.get("topic"),
            body=(data.get("body") or "").replace("\r\n", "\n").strip(),
            ai_answer=(data.get("ai_answer") or "").replace("\r\n", "\n").strip(),
            author=member, score=data["votes"], view_count=data["views"], created_at=data["posted_on"],
        )
        _backdate(member, data["posted_on"])
        _refresh(q, {member.pk, old_author_id})
    _log(request, "question.edit", q, fields=",".join(form.changed_data), shown_as=member.display_name)
    if first_answer and data["posted_on"] > first_answer:
        messages.warning(request, "Saved. Note: the question is now dated after its first answer.")
    else:
        messages.success(request, "Question saved.")


def answer_forms_for(answers, user) -> None:
    """Attach an unbound edit form to each answer, for the thread page."""
    for a in answers:
        if not hasattr(a, "edit_form"):
            a.edit_form = AnswerForm(prefix=f"ans-{a.pk}", user=user, current=a.author, mode="edit",
                                     initial=AnswerForm.initial_for(a, user))


@console_view(write=True)
def answer_edit(request, pk):
    """Edit one answer: text, who it is shown as, date, votes, accepted."""
    answer = get_object_or_404(Answer.objects.select_related("author", "question"), pk=pk)
    q = answer.question
    if request.method != "POST":
        return redirect(reverse("ask_console:thread", kwargs={"pk": q.pk}) + f"#a-{answer.pk}")
    form = AnswerForm(request.POST, prefix=f"ans-{answer.pk}", user=request.user, current=answer.author, mode="edit")
    if form.is_valid() and _check_answer_dates([form], q.created_at):
        data = form.cleaned_data
        old_author_id = answer.author_id
        member = form.poster_member()
        touched = {member.pk, old_author_id}
        with transaction.atomic():
            Answer.objects.filter(pk=answer.pk).update(
                body=data["body"].replace("\r\n", "\n").strip(), author=member,
                score=data["votes"], created_at=data["posted_on"],
            )
            if bool(data.get("accepted")) != answer.is_accepted:
                touched |= set(Answer.objects.filter(question=q, is_accepted=True).values_list("author_id", flat=True))
                Answer.objects.filter(question=q, is_accepted=True).update(is_accepted=False)
                if data.get("accepted"):
                    Answer.objects.filter(pk=answer.pk).update(is_accepted=True)
            _backdate(member, data["posted_on"])
            _refresh(q, touched)
        _log(request, "answer.edit", answer, fields=",".join(form.changed_data), shown_as=member.display_name)
        messages.success(request, "Answer saved.")
        return redirect(reverse("ask_console:thread", kwargs={"pk": q.pk}) + f"#a-{answer.pk}")
    messages.error(request, "The answer was not saved. Check the highlighted fields.")
    from .console import render_thread

    answer.edit_form = form
    return render_thread(request, q, open_answer=answer)


def add_answer(request, q: Question):
    """Add an answer to a thread from the console. Called by
    ``console.thread_answer``, which has already checked write access."""
    form = AnswerForm(request.POST, prefix="new", user=request.user, mode="new")
    if form.is_valid() and _check_answer_dates([form], q.created_at):
        with transaction.atomic():
            answer, touched = _create_answer(q, form)
            _refresh(q, touched)
        if answer.status == Visibility.PUBLISHED:
            services.queue_answer_notice(answer)
        _log(request, "answer.write", answer, question=q.title, shown_as=answer.author.display_name)
        messages.success(request, f"Answer added as {answer.author.display_name}.")
        return redirect(reverse("ask_console:thread", kwargs={"pk": q.pk}) + f"#a-{answer.pk}")
    messages.error(request, "The answer was not added. Check the highlighted fields.")
    from .console import render_thread

    return render_thread(request, q, new_answer_form=form)


def new_answer_form(user) -> AnswerForm:
    return AnswerForm(prefix="new", user=user, mode="new",
                      initial={**_new_initial(user), "status": Visibility.PUBLISHED})


def question_edit_form(q: Question, user, data=None) -> QuestionForm:
    if data is not None:
        return QuestionForm(data, prefix="q", user=user, current=q.author, mode="edit")
    return QuestionForm(prefix="q", user=user, current=q.author, mode="edit",
                        initial=QuestionForm.initial_for(q, user))


__all__ = [
    "AnswerForm", "QuestionForm", "add_answer", "answer_edit", "answer_forms_for", "new_answer_form",
    "persona_for", "question_edit_form", "question_new", "save_question",
]
