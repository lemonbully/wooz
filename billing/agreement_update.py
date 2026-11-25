"""Re-acceptance of an updated user agreement, inside the owner dashboard.

When staff publish a new :class:`~billing.models.UserAgreement` version (say
version 2), every owner who bought a plan under an earlier version must accept
the new one. The first dashboard page they open after that shows an
un-dismissable popup ("We have updated our user agreement") carrying the full
agreement. The Accept button stays disabled until they have scrolled to the end;
accepting emails a one-time code to their account address, and entering that
code writes a new :class:`~billing.models.AgreementAcceptance` row for the new
version. Their earlier acceptance rows are never touched, so the admin shows
"v1 accepted on ..." and "v2 accepted on ..." side by side.

Who is asked
    An authenticated owner on the app surface who has at least one **paid**
    subscription order on their email (any plan, free grants included, since
    those also went through the agreement step) and no acceptance on record for
    the agreement currently in force. Someone who never bought a plan accepts the
    agreement at checkout like everyone else, so they are not asked here.

Never asked
    An admin impersonating an owner (only the owner can accept for themselves),
    and every non-app surface.

Cost
    Once an owner is cleared for the active agreement the session remembers that
    agreement's id, so a cleared page load costs a single small query (the id of
    the active agreement).
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from accounts import otp
from core import audit
from core.ratelimit import RateLimitExceeded, check

from . import emails
from .models import AgreementAcceptance, SubscriptionOrder, UserAgreement

__all__ = ["pending_agreement", "send_code", "verify_code"]

#: Session key holding the id of the agreement this owner is cleared for.
_SESSION_KEY = "ow_agreement_cleared"
#: Namespaces the code in the shared OTP store (see accounts.otp).
_OTP_PURPOSE = "billing.agreement.update"
_OTP_MINUTES = 10

_SENTINEL = object()


def _owner_email(user) -> str:
    return (getattr(user, "email", "") or "").strip()[:254]


def _active_ids():
    """The id of the agreement in force, without loading its body."""
    return (
        UserAgreement.objects.filter(is_active=True)
        .order_by("-created_at")
        .values_list("id", flat=True)
        .first()
    )


def _is_owner_with_plan(email: str) -> bool:
    return SubscriptionOrder.objects.filter(
        customer_email__iexact=email, status=SubscriptionOrder.Status.PAID
    ).exists()


def pending_agreement(request) -> "UserAgreement | None":
    """The agreement this signed-in owner still has to accept, or ``None``.

    Request-memoised; session-cached once cleared.
    """
    cached = getattr(request, "_ow_pending_agreement", _SENTINEL)
    if cached is not _SENTINEL:
        return cached

    result = None
    try:
        result = _compute(request)
    except Exception:  # A broken check must never take the dashboard down.
        result = None
    try:
        request._ow_pending_agreement = result
    except Exception:
        pass
    return result


def _compute(request):
    if str(getattr(request, "surface", "") or "") != "app":
        return None
    if getattr(request, "impersonation", None):
        return None
    user = getattr(request, "user", None)
    if not (user and getattr(user, "is_authenticated", False)):
        return None
    email = _owner_email(user)
    if not email:
        return None

    active_id = _active_ids()
    if active_id is None:
        return None

    session = getattr(request, "session", None)
    if session is not None and session.get(_SESSION_KEY) == str(active_id):
        return None

    agreement = UserAgreement.objects.filter(pk=active_id).first()
    if agreement is None:
        return None

    if AgreementAcceptance.has_accepted(email, agreement) or not _is_owner_with_plan(email):
        _mark_cleared(request, agreement)
        return None
    return agreement


def _mark_cleared(request, agreement) -> None:
    session = getattr(request, "session", None)
    if session is not None and agreement is not None:
        session[_SESSION_KEY] = str(agreement.id)


def _agreement_matches(request):
    """The pending agreement when the posted id still names it, else ``None``."""
    agreement = pending_agreement(request)
    posted = (request.POST.get("agreement") or "").strip()
    if agreement is None or (posted and posted != str(agreement.id)):
        return None
    return agreement


# --------------------------------------------------------------------------
# Endpoints (app surface, same-origin with the dashboard)
# --------------------------------------------------------------------------
@login_required
@require_POST
def send_code(request):
    """Email the owner a one-time code confirming their acceptance."""
    agreement = _agreement_matches(request)
    if agreement is None:
        # Already accepted (another tab), or a newer version replaced it.
        return JsonResponse({"ok": False, "reload": True, "error": "This agreement has changed. Reloading…"}, status=409)

    email = _owner_email(request.user)
    try:
        check("subscription.otp.send", email.lower())
    except RateLimitExceeded as exc:
        return JsonResponse(
            {"ok": False, "error": "Too many code requests. Please wait a moment and try again."},
            status=429, headers={"Retry-After": str(exc.retry_after)},
        )

    code = otp.issue(_OTP_PURPOSE, email, ttl=_OTP_MINUTES * 60)
    if not emails.send_agreement_otp(email, code, minutes=_OTP_MINUTES):
        otp.invalidate(_OTP_PURPOSE, email)
        return JsonResponse(
            {"ok": False, "error": "We couldn't send the code right now. Please try again."},
            status=502,
        )
    return JsonResponse({"ok": True, "email": _mask(email), "minutes": _OTP_MINUTES})


@login_required
@require_POST
def verify_code(request):
    """Check the emailed code and record acceptance of the new version."""
    agreement = _agreement_matches(request)
    if agreement is None:
        return JsonResponse({"ok": False, "reload": True, "error": "This agreement has changed. Reloading…"}, status=409)

    email = _owner_email(request.user)
    code = (request.POST.get("otp") or request.POST.get("code") or "").strip()
    try:
        check("subscription.otp.verify", email.lower())
    except RateLimitExceeded as exc:
        return JsonResponse(
            {"ok": False, "error": "Too many attempts. Please wait a moment and try again."},
            status=429, headers={"Retry-After": str(exc.retry_after)},
        )

    result = otp.verify(_OTP_PURPOSE, email, code)
    if not result.ok:
        msg = {
            "expired": "That code has expired. Please request a new one.",
            "too_many_attempts": "Too many wrong attempts. Please request a new code.",
        }.get(result.reason, "That code is incorrect. Please try again.")
        return JsonResponse({"ok": False, "error": msg}, status=400)

    acceptance = AgreementAcceptance.objects.create(
        agreement=agreement,
        email=email,
        user=request.user,
        source=AgreementAcceptance.Source.UPDATE,
        otp_verified=True,
        ip_address=request.META.get("REMOTE_ADDR") or None,
        user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:400],
    )
    _mark_cleared(request, agreement)
    audit.record(
        "billing.agreement.reaccepted",
        request=request,
        context={
            "email": email,
            "agreement": str(agreement.id),
            "version": acceptance.agreement_version,
        },
    )
    return JsonResponse({"ok": True, "version": acceptance.agreement_version})


def _mask(email: str) -> str:
    """a****z@domain.com  enough to recognise, not enough to harvest."""
    local, _, domain = email.partition("@")
    if len(local) <= 2:
        shown = local[:1] + "*"
    else:
        shown = local[0] + "*" * min(len(local) - 2, 6) + local[-1]
    return f"{shown}@{domain}" if domain else shown
