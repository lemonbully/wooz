"""Sign-in orchestration shared by every authentication method.

Keeping this in one place is what stops the four entry points (password,
TOTP, passkey, magic link) from drifting apart on the things that must be
identical between them: session fixation handling, activity recording,
second-factor gating, and where a user lands afterwards.
"""

from __future__ import annotations

import secrets
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.utils import timezone

from core import audit

from .models import LoginActivity, TrustedDevice

__all__ = [
    "record_attempt",
    "complete_login",
    "sign_out",
    "needs_second_factor",
    "begin_pending_login",
    "resolve_pending_login",
    "clear_pending_login",
    "remember_device",
    "safe_next_url",
    "is_signup_allowed",
    "begin_email_verification",
    "resolve_email_verification",
    "clear_email_verification",
    "remember_session_device",
    "touch_session_device",
]

PENDING_SESSION_KEY = "pending_login"
EMAIL_VERIFY_SESSION_KEY = "email_verify"
EMAIL_VERIFY_MAX_AGE = 600  # ten minutes to enter the emailed code
TRUSTED_DEVICE_COOKIE = "ow_td"
TRUSTED_DEVICE_DAYS = 30


# --------------------------------------------------------------------------
# Sign-up allowlist
# --------------------------------------------------------------------------


def is_signup_allowed(email: str) -> bool:
    """Whether ``email`` may create an account during the private beta.

    The allowlist lives in ``settings.SIGNUP_ALLOWLIST`` rather than being
    hardcoded here, so the set of test users is one config value to change and
    never requires editing a view.
    """
    allow = {a.strip().lower() for a in getattr(settings, "SIGNUP_ALLOWLIST", [])}
    return (email or "").strip().lower() in allow


# --------------------------------------------------------------------------
# Emailed-code verification (a step in every sign-in and sign-up)
# --------------------------------------------------------------------------


def begin_email_verification(request, *, flow: str, email: str, data: dict) -> None:
    """Park a half-finished sign-in/sign-up awaiting an emailed one-time code.

    Only what is needed to finish the flow is stored, server-side in the
    session: for a sign-in that is the user id; for a sign-up the profile fields
    and an already-hashed password, never a plaintext one. The code itself is
    never held here  only :mod:`accounts.otp` (server-side, hashed) knows it.
    """
    request.session[EMAIL_VERIFY_SESSION_KEY] = {
        "flow": flow,
        "email": email,
        "at": timezone.now().isoformat(),
        **data,
    }


def resolve_email_verification(request):
    """Return the parked verification state, or ``None`` if absent/expired."""
    state = request.session.get(EMAIL_VERIFY_SESSION_KEY)
    if not state:
        return None
    started = state.get("at")
    if started:
        try:
            age = (timezone.now() - timezone.datetime.fromisoformat(started)).total_seconds()
            if age > EMAIL_VERIFY_MAX_AGE:
                clear_email_verification(request)
                return None
        except (TypeError, ValueError):
            clear_email_verification(request)
            return None
    return state


def clear_email_verification(request) -> None:
    request.session.pop(EMAIL_VERIFY_SESSION_KEY, None)


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def record_attempt(request, *, method: str, outcome: str, user=None, email: str = ""):
    """Write one row of sign-in history.

    Recorded for failures as well as successes, and with the attempted address
    even when no such account exists  that is what makes an attack spread
    across many accounts from one address visible.
    """
    try:
        return LoginActivity.objects.create(
            user=user,
            email_attempted=(email or getattr(user, "email", "") or "")[:254],
            method=method,
            outcome=outcome,
            ip_address=_client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:1000],
            surface=str(getattr(request, "surface", "")),
            request_id=getattr(request, "request_id", ""),
        )
    except Exception:
        # History is valuable but never worth failing a sign-in over.
        return None


# --------------------------------------------------------------------------
# Second factor
# --------------------------------------------------------------------------


def needs_second_factor(request, user) -> bool:
    """Whether this sign-in must be challenged for a second factor.

    A passkey sign-in is already multi-factor by construction  possession of
    the authenticator plus, usually, a biometric or PIN  so it is never
    challenged again.
    """
    if not user.mfa_is_required():
        return False
    if not user.has_second_factor:
        # Required but not enrolled: the caller routes to enrolment.
        return True
    return TrustedDevice.lookup(user, request.COOKIES.get(TRUSTED_DEVICE_COOKIE, "")) is None


def begin_pending_login(request, user, *, method: str) -> None:
    """Park a half-authenticated user while the second factor is collected.

    Only the user id and a timestamp are stored  never the password, never a
    code. The parked state expires with the session and is cleared on
    completion or failure.
    """
    request.session[PENDING_SESSION_KEY] = {
        "user": str(user.pk),
        "method": method,
        "at": timezone.now().isoformat(),
    }


def resolve_pending_login(request):
    """Return the parked user, or ``None`` if there is no valid pending login."""
    from django.contrib.auth import get_user_model

    pending = request.session.get(PENDING_SESSION_KEY)
    if not pending:
        return None

    started = pending.get("at")
    if started:
        try:
            age = (timezone.now() - timezone.datetime.fromisoformat(started)).total_seconds()
            if age > 600:  # ten minutes to complete a challenge
                clear_pending_login(request)
                return None
        except (TypeError, ValueError):
            clear_pending_login(request)
            return None

    return get_user_model().objects.filter(pk=pending.get("user"), is_active=True).first()


def clear_pending_login(request) -> None:
    request.session.pop(PENDING_SESSION_KEY, None)


def remember_device(request, response, user) -> None:
    """Issue a trusted-device cookie so this browser skips the next challenge.

    The cookie holds a random value; only its hash is stored. HttpOnly so
    script cannot read it, and host-only so it never travels to another
    surface or another tenant's domain.
    """
    raw = secrets.token_urlsafe(32)
    TrustedDevice.issue(user, raw_token=raw, ttl_days=TRUSTED_DEVICE_DAYS, request=request)
    response.set_cookie(
        TRUSTED_DEVICE_COOKIE,
        raw,
        max_age=TRUSTED_DEVICE_DAYS * 86400,
        httponly=True,
        secure=not settings.DEBUG,
        samesite="Lax",
        path="/",
    )


# --------------------------------------------------------------------------
# Completion
# --------------------------------------------------------------------------


#: Key under which each session records the device it belongs to, so the
#: security page can list live sessions and end any one. Kept in sync with
#: ``accounts.sessions.DEVICE_KEY``.
SESSION_DEVICE_KEY = "ow_device"


def remember_session_device(request) -> None:
    """Stamp the current session with the device that established it.

    Written into the session itself (not a separate table) so
    ``accounts.sessions`` can present every live session  its device, rough
    location and when it began  and end any one. Best-effort: a session that
    can't be written must never break a sign-in.
    """
    try:
        request.session[SESSION_DEVICE_KEY] = {
            "ua": request.META.get("HTTP_USER_AGENT", "")[:400],
            "ip": _client_ip(request),
            "at": timezone.now().isoformat(),
            "seen": timezone.now().isoformat(),
        }
    except Exception:
        pass


def touch_session_device(request) -> None:
    """Refresh this session's ``last seen`` stamp (called by the heartbeat)."""
    try:
        meta = request.session.get(SESSION_DEVICE_KEY)
        if isinstance(meta, dict):
            meta["seen"] = timezone.now().isoformat()
            request.session[SESSION_DEVICE_KEY] = meta
    except Exception:
        pass


def _enforce_session_limit(request, user) -> None:
    """Trim the owner's live sessions to their plan's device cap. Best-effort.

    Staff and superusers are exempt (the admin surface manages its own access),
    and a missing/zero cap means unlimited. Never raises  a session-accounting
    hiccup must not block a sign-in.
    """
    try:
        if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
            return
        from billing.entitlements import concurrent_sessions_for_user

        limit = concurrent_sessions_for_user(user)
        if not limit or limit <= 0:
            return
        key = request.session.session_key or ""
        if not key:
            return
        from . import sessions as session_registry

        session_registry.enforce_limit(user, key, limit)
    except Exception:
        pass


def complete_login(request, user, *, method: str):
    """Establish the authenticated session.

    ``django_login`` cycles the session key, which is what defends against
    session fixation: any session identifier the visitor arrived holding 
    including one an attacker planted  is discarded and replaced.
    """
    clear_pending_login(request)

    # A deleted account (Profile -> Delete My Account) comes back by signing in
    # within its recovery window; past the window it is closed and the sign-in
    # stops here. Callers redirect to the dashboard, whose login_required sends
    # the visitor back to sign-in, which then answers like any unknown account.
    from . import deletion

    if deletion.on_sign_in(request, user) == deletion.CLOSED:
        record_attempt(
            request, method=method, outcome=LoginActivity.Outcome.INACTIVE, user=user
        )
        return None

    django_login(request, user, backend="accounts.backends.EmailPasswordBackend")

    # Record which device this (freshly-keyed) session belongs to, so the owner
    # can see and end it from the security page.
    remember_session_device(request)

    # Enforce the subscription's concurrent-device cap: signing in on a new
    # device ends the oldest sessions beyond the plan's allowance, so an account
    # is only ever managed on the permitted number of devices at once.
    _enforce_session_limit(request, user)

    user.last_seen_at = timezone.now()
    user.save(update_fields=["last_seen_at", "updated_at"])

    record_attempt(
        request, method=method, outcome=LoginActivity.Outcome.SUCCESS, user=user
    )
    audit.record(
        "auth.login",
        request=request,
        actor=user,
        context={"method": method},
    )
    return user


def sign_out(request):
    user = request.user if request.user.is_authenticated else None
    if user is not None:
        audit.record("auth.logout", request=request, actor=user)
    django_logout(request)


# --------------------------------------------------------------------------
# Redirects
# --------------------------------------------------------------------------


def safe_next_url(request, default: str = "/") -> str:
    """Validate a ``?next=`` parameter before redirecting to it.

    Only same-host, path-only destinations are accepted. An unchecked ``next``
    is an open redirect, which turns a legitimate sign-in link into a
    convincing phishing vector  the victim really does authenticate on the
    real site, and only then gets bounced to the attacker's page.
    """
    candidate = request.POST.get("next") or request.GET.get("next") or ""
    if not candidate:
        return default

    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        return default
    if not candidate.startswith("/") or candidate.startswith("//"):
        return default
    return candidate
