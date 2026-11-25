"""Everything that changes community state: members, posting, moderation,
votes, accepting answers and the counters that feeds read.

Views stay thin and call these. Counters are recomputed from the rows that
count (published posts, existing votes) rather than nudged up and down, so a
moderation action can never leave a number drifting.
"""

from __future__ import annotations

import json
import re
import secrets

from django.db import IntegrityError, transaction
from django.db.models import Count, F, Max, Q, Sum
from django.utils import timezone
from django.utils.text import slugify

from .models import Answer, Member, Question, Reply, Report, Topic, Visibility, Vote

SESSION_KEY = "ask_member"
OTP_PURPOSE = "ask.join"

#: Reputation for each upvote received and each accepted answer.
REP_PER_VOTE = 1
REP_PER_ACCEPTED = 10

_URL_RE = re.compile(r"(https?://|www\.)\S+|\b[a-z0-9-]+\.(com|in|net|org|io|co|shop|store|xyz|info|biz)\b", re.I)
_SPAM_WORDS = (
    "whatsapp me", "call me on", "guaranteed income", "earn money fast", "crypto signal",
    "telegram @", "loan approval", "betting", "casino", "satta",
)


# ---------------------------------------------------------------------------
# Members and their session
# ---------------------------------------------------------------------------
def current_member(request) -> Member | None:
    """The member signed in to the community on this browser, or None."""
    if hasattr(request, "_ask_member"):
        return request._ask_member
    member = None
    member_id = request.session.get(SESSION_KEY) if hasattr(request, "session") else None
    if member_id:
        member = Member.objects.filter(pk=member_id).first()
        if member is None:
            request.session.pop(SESSION_KEY, None)
    request._ask_member = member
    return member


def sign_in(request, member: Member) -> None:
    # A fresh session key on sign-in, so a fixated pre-login session id is useless.
    request.session.cycle_key()
    request.session[SESSION_KEY] = str(member.pk)
    request._ask_member = member
    Member.objects.filter(pk=member.pk).update(last_seen_at=timezone.now())


def sign_out(request) -> None:
    request.session.pop(SESSION_KEY, None)
    request._ask_member = None


def _unique_handle(name: str) -> str:
    base = slugify(name)[:40] or "member"
    handle = base
    while Member.objects.filter(handle=handle).exists():
        handle = f"{base}-{secrets.token_hex(2)}"
    return handle


def clean_display_name(name: str) -> str:
    name = " ".join((name or "").split())[:40]
    return re.sub(r"[<>@]", "", name).strip()


def get_or_create_member(email: str, display_name: str) -> Member:
    """The member for a confirmed email, created on first sign-in."""
    email = email.strip().lower()
    member = Member.objects.filter(email=email).first()
    if member is None:
        name = clean_display_name(display_name) or email.split("@", 1)[0][:40]
        try:
            member = Member.objects.create(email=email, display_name=name, handle=_unique_handle(name))
        except IntegrityError:  # two tabs confirming at once
            member = Member.objects.get(email=email)
    link_account(member)
    return member


def link_account(member: Member) -> None:
    """Mark OneWooz staff as team and link the member's own store, if the
    email belongs to a OneWooz account. Never fails the sign-in."""
    try:
        from django.contrib.auth import get_user_model

        from core import links
        from tenancy.models import Site

        user = get_user_model().objects.filter(email__iexact=member.email).only("id", "is_staff").first()
        fields = []
        if user is not None and user.is_staff and not member.is_team:
            member.is_team = True
            fields.append("is_team")
        if user is not None:
            site = (
                Site.objects.filter(owner_id=user.pk, is_deleted=False)
                .only("id", "name", "slug", "custom_domain", "custom_domain_verified")
                .order_by()
                .first()
            )
            if site is not None:
                domain = (site.custom_domain or "").strip().lower()
                url = f"https://{domain}/" if domain and site.custom_domain_verified else (
                    links.storefront_origin(site.slug) + "/" if site.slug else ""
                )
                if url and (member.store_url != url or member.store_name != site.name):
                    member.store_url, member.store_name = url, (site.name or "")[:120]
                    fields += ["store_url", "store_name"]
        if fields:
            member.save(update_fields=fields + ["updated_at"])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Moderation
# ---------------------------------------------------------------------------
def settings_now():
    from .models import CommunitySettings

    return CommunitySettings.current()


def posting_block(member: Member | None, conf=None) -> str:
    """Why this member can't post right now, or "" when they can."""
    conf = conf or settings_now()
    if member is not None and member.is_banned:
        return "Your account can't post on Ask OneWooz. Contact the OneWooz team if you think this is a mistake."
    if not conf.posting_open:
        return "Ask OneWooz is read-only for now. Posting will be back soon."
    return ""


def hold_reason(member: Member, *texts: str) -> str:
    """Why a new post should wait for review, or "" to publish it now."""
    conf = settings_now()
    if member.is_banned:
        return "Member is banned"
    # Every question, answer and reply from a member waits for the team to
    # approve it in Django admin (the Publish action) before anyone else sees
    # it; only OneWooz team posts go live at once. (approve_all is kept for
    # the console form but no longer needed.)
    if not member.is_team:
        return "Waiting for approval"
    blob = " ".join(t or "" for t in texts)
    if conf.hold_links and _URL_RE.search(blob) and not member.is_team:
        return "Contains a link"
    hit = blocked_term_in(blob, [*((w, "phrase") for w in _SPAM_WORDS), *conf.blocked_terms])
    if hit:
        term, kind = hit
        return f"Blocked {kind}: {term}"[:120]
    return ""


def _term_pattern(term: str) -> re.Pattern:
    """Whole-word match, case-insensitive. A phrase's words must appear in
    order, with any amount of space between them."""
    parts = term.split()
    words = [re.escape(w) for w in parts]
    # Only guard an edge that is a letter or digit: "telegram @" must still
    # catch "telegram @someone".
    start = r"(?<!\w)" if re.match(r"\w", parts[0]) else ""
    end = r"(?!\w)" if re.search(r"\w$", parts[-1]) else ""
    return re.compile(start + r"\s+".join(words) + end, re.IGNORECASE)


def blocked_term_in(text: str, terms) -> tuple[str, str] | None:
    """The first ``(term, kind)`` found in ``text``, or None.

    A keyword (one word) matches only as a whole word, so "bet" does not
    catch "better". A phrase (several words) matches those words together,
    in order, so "earn money fast" does not catch "learn money fastest".
    """
    text = text or ""
    for term, kind in terms:
        if term and _term_pattern(term).search(text):
            return term, kind
    return None


def status_for(member: Member, *texts: str) -> tuple[str, str]:
    reason = hold_reason(member, *texts)
    return (Visibility.HELD if reason else Visibility.PUBLISHED), reason


def visible_to(obj, member: Member | None) -> bool:
    """Published posts are public; a held post is visible to its author only."""
    if obj.status == Visibility.PUBLISHED:
        return True
    return obj.status == Visibility.HELD and member is not None and obj.author_id == member.pk


# ---------------------------------------------------------------------------
# Posting
# ---------------------------------------------------------------------------
def _unique_slug(title: str) -> str:
    base = slugify(title)[:200].strip("-") or "question"
    while True:
        slug = f"{base}-{secrets.token_hex(2)}"
        if not Question.objects.filter(slug=slug).exists():
            return slug


@transaction.atomic
def post_question(member: Member, *, title: str, body: str, topic: Topic | None,
                  ai_answer: str = "", instant=None) -> Question:
    status, reason = status_for(member, title, body)
    question = Question.objects.create(
        author=member, topic=topic, title=title, body=body, slug=_unique_slug(title),
        ai_answer=ai_answer, instant=instant, status=status, hold_reason=reason,
    )
    refresh_member(member)
    return question


@transaction.atomic
def post_answer(member: Member, question: Question, body: str) -> Answer:
    status, reason = status_for(member, body)
    answer = Answer.objects.create(
        question=question, author=member, body=body, status=status, hold_reason=reason,
    )
    refresh_question(question)
    refresh_member(member)
    if status == Visibility.PUBLISHED:
        queue_answer_notice(answer)
    return answer


def queue_answer_notice(answer: Answer) -> None:
    """Email the asker (after commit, in the background) that they got an answer."""

    def _queue():
        try:
            from .tasks import notify_answer

            notify_answer.delay(str(answer.pk))
        except Exception:
            pass

    transaction.on_commit(_queue)


def post_reply(member: Member, answer: Answer, body: str) -> Reply:
    status, reason = status_for(member, body)
    reply = Reply.objects.create(answer=answer, author=member, body=body, status=status, hold_reason=reason)
    if status == Visibility.PUBLISHED:
        Question.objects.filter(pk=answer.question_id).update(last_activity_at=timezone.now())
    return reply


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------
def refresh_question(question: Question) -> None:
    """Recount a question's answers, accepted answer and team flag."""
    published = Answer.objects.filter(question_id=question.pk, status=Visibility.PUBLISHED)
    stats = published.aggregate(
        n=Count("id"),
        team=Count("id", filter=Q(author__is_team=True)),
        latest=Max("created_at"),
    )
    accepted = published.filter(is_accepted=True).order_by().values_list("id", flat=True)[:1]
    Question.objects.filter(pk=question.pk).update(
        answer_count=stats["n"] or 0,
        has_team_answer=bool(stats["team"]),
        accepted_answer_id=next(iter(accepted), None),
        last_activity_at=max(stats["latest"] or question.created_at, question.created_at),
    )


def refresh_member(member: Member) -> None:
    """Recount a member's posts and reputation."""
    q_pub = Question.objects.filter(author_id=member.pk, status=Visibility.PUBLISHED)
    a_pub = Answer.objects.filter(author_id=member.pk, status=Visibility.PUBLISHED)
    q_score = q_pub.aggregate(s=Sum("score"))["s"] or 0
    a_stats = a_pub.aggregate(s=Sum("score"), n=Count("id"), acc=Count("id", filter=Q(is_accepted=True)))
    reputation = (q_score + (a_stats["s"] or 0)) * REP_PER_VOTE + (a_stats["acc"] or 0) * REP_PER_ACCEPTED
    Member.objects.filter(pk=member.pk).update(
        question_count=q_pub.count(),
        answer_count=a_stats["n"] or 0,
        accepted_count=a_stats["acc"] or 0,
        reputation=max(0, reputation),
    )


# ---------------------------------------------------------------------------
# Votes, accepting, reports
# ---------------------------------------------------------------------------
def toggle_vote(member: Member, *, question: Question | None = None, answer: Answer | None = None) -> tuple[bool, int]:
    """Add the member's upvote, or take it back. Returns (voted, new score)."""
    target = question or answer
    model = Question if question is not None else Answer
    lookup = {"question": question} if question is not None else {"answer": answer}
    with transaction.atomic():
        existing = Vote.objects.filter(member=member, **lookup).first()
        if existing:
            existing.delete()
            model.objects.filter(pk=target.pk).update(score=F("score") - 1)
            voted = False
        else:
            try:
                with transaction.atomic():
                    Vote.objects.create(member=member, **lookup)
            except IntegrityError:
                return True, model.objects.filter(pk=target.pk).values_list("score", flat=True).first() or 0
            model.objects.filter(pk=target.pk).update(score=F("score") + 1)
            voted = True
    refresh_member(target.author)
    return voted, model.objects.filter(pk=target.pk).values_list("score", flat=True).first() or 0


def voted_ids(member: Member | None, *, questions=(), answers=()) -> set:
    if member is None:
        return set()
    ids = set()
    if questions:
        ids |= {str(i) for i in Vote.objects.filter(member=member, question_id__in=list(questions)).values_list("question_id", flat=True)}
    if answers:
        ids |= {str(i) for i in Vote.objects.filter(member=member, answer_id__in=list(answers)).values_list("answer_id", flat=True)}
    return ids


@transaction.atomic
def toggle_accept(answer: Answer) -> bool:
    """Mark an answer as the accepted one (or un-mark it). One per question."""
    question = answer.question
    now_accepted = not answer.is_accepted
    Answer.objects.filter(question_id=question.pk, is_accepted=True).update(is_accepted=False)
    if now_accepted:
        Answer.objects.filter(pk=answer.pk).update(is_accepted=True)
    refresh_question(question)
    refresh_member(answer.author)
    return now_accepted


def report(member: Member, target, reason: str) -> None:
    """File one report per member per post; hold the post at the threshold."""
    kind = target.__class__.__name__.lower()
    if reason not in Report.Reason.values:
        reason = Report.Reason.OTHER
    try:
        with transaction.atomic():
            Report.objects.create(reporter=member, reason=reason, **{kind: target})
    except IntegrityError:
        return
    model = target.__class__
    model.objects.filter(pk=target.pk).update(report_count=F("report_count") + 1)
    target.refresh_from_db(fields=["report_count", "status"])
    threshold = settings_now().report_threshold or Report.AUTO_HOLD_AT
    if target.report_count >= threshold and target.status == Visibility.PUBLISHED:
        model.objects.filter(pk=target.pk).update(status=Visibility.HELD, hold_reason="Reported by readers")
        if isinstance(target, Answer):
            refresh_question(target.question)


def set_status(obj, status: str, reason: str = "", previous: str | None = None) -> None:
    """Moderation from admin: publish, hold or hide one post and recount."""
    was = previous if previous is not None else (
        obj.__class__.objects.filter(pk=obj.pk).values_list("status", flat=True).first()
    )
    obj.__class__.objects.filter(pk=obj.pk).update(status=status, hold_reason=reason)
    if isinstance(obj, Answer):
        refresh_question(obj.question)
        if status == Visibility.PUBLISHED and was == Visibility.HELD:
            queue_answer_notice(obj)
    if isinstance(obj, (Question, Answer)):
        refresh_member(obj.author)


# ---------------------------------------------------------------------------
# Console actions (admin): delete, ban, team, bulk hide
# ---------------------------------------------------------------------------
def delete_post(obj) -> None:
    """Permanently delete a question, answer or reply and fix every counter."""
    if isinstance(obj, Question):
        authors = set(Answer.objects.filter(question=obj).values_list("author_id", flat=True))
        authors.add(obj.author_id)
        obj.delete()
        for member in Member.objects.filter(pk__in=authors):
            refresh_member(member)
    elif isinstance(obj, Answer):
        question, author = obj.question, obj.author
        obj.delete()
        refresh_question(question)
        refresh_member(author)
    else:
        obj.delete()


def ban(member: Member, reason: str = "") -> None:
    Member.objects.filter(pk=member.pk).update(
        is_banned=True, ban_reason=(reason or "")[:300], banned_at=timezone.now(),
    )


def unban(member: Member) -> None:
    Member.objects.filter(pk=member.pk).update(is_banned=False, ban_reason="", banned_at=None)


def hide_everything(member: Member) -> int:
    """Hide every question, answer and reply by one member. Returns how many."""
    n = 0
    question_ids = set(Answer.objects.filter(author=member).values_list("question_id", flat=True))
    n += Question.objects.filter(author=member).exclude(status=Visibility.HIDDEN).update(
        status=Visibility.HIDDEN, hold_reason="Hidden with the member's other posts")
    n += Answer.objects.filter(author=member).exclude(status=Visibility.HIDDEN).update(
        status=Visibility.HIDDEN, hold_reason="Hidden with the member's other posts")
    n += Reply.objects.filter(author=member).exclude(status=Visibility.HIDDEN).update(
        status=Visibility.HIDDEN, hold_reason="Hidden with the member's other posts")
    for q in Question.objects.filter(pk__in=question_ids).only("id", "created_at"):
        refresh_question(q)
    refresh_member(member)
    return n


def delete_member(member: Member) -> None:
    """Delete a member and everything they posted, then fix the counters on
    the threads they took part in and the people they answered."""
    question_ids = set(Answer.objects.filter(author=member).values_list("question_id", flat=True))
    question_ids |= set(
        Answer.objects.filter(question__author=member).values_list("question_id", flat=True)
    )
    other_authors = set(
        Answer.objects.filter(question__author=member).exclude(author=member).values_list("author_id", flat=True)
    )
    voted_answers = list(Vote.objects.filter(member=member, answer__isnull=False).values_list("answer_id", flat=True))
    voted_questions = list(Vote.objects.filter(member=member, question__isnull=False).values_list("question_id", flat=True))
    voted_on = set(Answer.objects.filter(pk__in=voted_answers).values_list("author_id", flat=True))
    voted_on |= set(Question.objects.filter(pk__in=voted_questions).values_list("author_id", flat=True))
    if member.avatar:
        try:
            member.avatar.delete(save=False)
        except Exception:
            pass  # never block deleting a member over a stray file
    member.delete()
    # Their upvotes are gone with them: take one off each post they voted on
    # (one vote per member per post). Subtracting, rather than recounting from
    # the Vote rows, keeps a vote count the team set by hand in the console.
    Answer.objects.filter(pk__in=voted_answers, score__gt=0).update(score=F("score") - 1)
    Question.objects.filter(pk__in=voted_questions, score__gt=0).update(score=F("score") - 1)
    for q in Question.objects.filter(pk__in=question_ids).only("id", "created_at"):
        refresh_question(q)
    for m in Member.objects.filter(pk__in=(other_authors | voted_on)):
        refresh_member(m)


def recount_votes(obj) -> None:
    """Rebuild a post's score from its votes (after deleting a voter)."""
    obj.__class__.objects.filter(pk=obj.pk).update(score=obj.votes.count())


# ---------------------------------------------------------------------------
# Search engines: schema.org QAPage
# ---------------------------------------------------------------------------
def qa_page_schema(request, question: Question, answers: list) -> str:
    """A QAPage JSON-LD block for one published question and its answers."""

    def person(member):
        return {"@type": "Person", "name": member.display_name,
                "url": request.build_absolute_uri(member.get_absolute_url())}

    url = request.build_absolute_uri(question.get_absolute_url())

    def answer_node(a):
        return {
            "@type": "Answer",
            "text": a.body[:2000],
            "upvoteCount": max(0, a.score),
            "datePublished": a.created_at.isoformat(),
            "url": f"{url}#a-{a.pk}",
            "author": person(a.author),
        }

    main = {
        "@type": "Question",
        "name": question.title,
        "text": (question.body or question.title)[:2000],
        "answerCount": len(answers),
        "upvoteCount": max(0, question.score),
        "datePublished": question.created_at.isoformat(),
        "author": person(question.author),
    }
    accepted = [a for a in answers if a.is_accepted]
    others = [a for a in answers if not a.is_accepted]
    if accepted:
        main["acceptedAnswer"] = answer_node(accepted[0])
    if others:
        main["suggestedAnswer"] = [answer_node(a) for a in others[:10]]
    data = {"@context": "https://schema.org", "@type": "QAPage", "mainEntity": main}
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
