"""Sign-in, sign-up and credential management for site owners.

A rule applied throughout: **never disclose whether an email address has an
account.** Sign-in, magic link and password reset all report the same thing to
the caller whether or not the address exists. An endpoint that distinguishes
them is an account-enumeration oracle, and enumeration is the first step of
every credential-stuffing campaign.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import NamedTuple

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from core import audit, ratelimit, tokens
from core.hosts import Surface
from custom_admin import middleware as admin_middleware

from . import emails, otp, passkeys, services, totp
from .models import LoginActivity, TOTPDevice, WebAuthnCredential

User = get_user_model()


def dashboard_url() -> str:
    """Where a completed sign-in lands.

    Resolved per request rather than held as a constant: ``reverse`` applies
    the script prefix, so this is ``/dashboard/`` on app.onewooz.com and
    ``/user/dashboard/`` under single-host local development, with no branch
    here for either.
    """
    return reverse("ud:dashboard")


def sign_in_url(request=None) -> str:
    """The sign-in page for the surface this request is on.

    The sign-out view is mounted on both the app and the admin URLconfs, and
    each has its own sign-in route  the admin one accepts passkeys only. A
    single hardcoded path would send a signed-out administrator to a page that
    does not exist on their surface.
    """
    if getattr(request, "surface", None) is Surface.ADMIN:
        return reverse("custom_admin:sign_in")
    return reverse("accounts:sign_in")


GENERIC_SIGN_IN_ERROR = "That email address and password do not match an account."
GENERIC_SENT_MESSAGE = (
    "If that address has a OneWooz account, a sign-in link is on its way. "
    "Check your inbox."
)


# --------------------------------------------------------------------------
# Emailed one-time code, required in every sign-in and sign-up
# --------------------------------------------------------------------------


class _CodeResult(NamedTuple):
    """Outcome of :func:`_email_code_and_park`.

    ``ok`` means the code was mailed and the flow is parked; otherwise ``error``
    and ``status`` are the message and HTTP status the caller should render.
    """

    ok: bool
    error: str = ""
    status: int = 200


def _email_code_and_park(request, *, flow: str, email: str, purpose: str, data: dict) -> _CodeResult:
    """Issue an emailed code, send it, and park the pending flow in the session.

    Returns a non-``ok`` result  having parked nothing  when the address or
    this connection has hit its send limit, or when the mail could not be
    delivered (e.g. the provider refused it). ``purpose`` namespaces the code in
    :mod:`accounts.otp` so a sign-up code and a sign-in code for the same address
    never satisfy one another.
    """
    ip = ratelimit.client_identity(request)
    address_ok = ratelimit.consume("otp.send.email", email).allowed
    ip_ok = ratelimit.consume("otp.send.ip", ip).allowed
    if not (address_ok and ip_ok):
        return _CodeResult(
            False, "Too many codes requested. Wait a few minutes and try again.", 429
        )

    services.begin_email_verification(request, flow=flow, email=email, data=data)
    code = otp.issue(purpose, email)
    if not emails.send_login_code(email, code, minutes=otp.TTL_SECONDS // 60):
        # Delivery failed: unpark the pending flow so the visitor is not stranded
        # on the code-entry screen waiting for a code that will never arrive.
        services.clear_email_verification(request)
        return _CodeResult(
            False,
            "We couldn't send your verification code right now. "
            "Please try again in a moment.",
            503,
        )
    return _CodeResult(True)


@ensure_csrf_cookie
@require_http_methods(["GET", "POST"])
def email_otp(request):
    """Collect and verify the one-time code emailed during sign-in or sign-up.

    Both flows converge here: a sign-in resumes into the second-factor gate (so
    staff still face their authenticator), a sign-up creates the account. There
    is no way to reach a session from this view without first having proved
    control of the address that the code was mailed to.
    """
    state = services.resolve_email_verification(request)
    if state is None:
        return redirect("accounts:sign_in")

    email = state["email"]
    purpose = "signup" if state.get("flow") == "signup" else "login"

    if request.method == "GET":
        return render(request, "accounts/email_otp.html", {"email": email})

    if not ratelimit.consume("otp.verify", email).allowed:
        services.clear_email_verification(request)
        return render(
            request,
            "accounts/email_otp.html",
            {"email": email, "error": "Too many attempts. Please start again."},
            status=429,
        )

    result = otp.verify(purpose, email, request.POST.get("code", ""))
    if not result.ok:
        services.record_attempt(
            request,
            method=LoginActivity.Method.EMAIL_OTP,
            outcome=LoginActivity.Outcome.MFA_FAILED,
            email=email,
        )
        return render(
            request,
            "accounts/email_otp.html",
            {"email": email, "error": "That code is not right or has expired."},
            status=401,
        )

    ratelimit.reset("otp.verify", email)

    if state.get("flow") == "signup":
        return _finish_signup(request, state)

    # Sign-in flow: the emailed code passed. Resume into any enrolled second
    # factor exactly as the password path used to, then complete.
    user = User.objects.filter(pk=state.get("user"), is_active=True).first()
    services.clear_email_verification(request)
    if user is None:
        return redirect("accounts:sign_in")

    next_url = state.get("next") or ""
    if services.needs_second_factor(request, user):
        services.begin_pending_login(request, user, method=LoginActivity.Method.EMAIL_OTP)
        if not user.has_second_factor:
            return redirect("accounts:totp_enrol")
        return redirect(f"{reverse('accounts:mfa_challenge')}?next={next_url}")

    services.complete_login(request, user, method=LoginActivity.Method.EMAIL_OTP)
    return redirect(next_url or dashboard_url())


def _finish_signup(request, state: dict):
    """Create the account for a sign-up whose emailed code has been verified."""
    email = state["email"]
    next_url = state.get("next") or ""
    services.clear_email_verification(request)

    user = User.objects.filter(email=email).first()
    if user is None:
        with transaction.atomic():
            user = User.objects.create_user(
                email=email,
                password=None,
                full_name=state.get("full_name", ""),
                signup_source=str(getattr(request, "surface", "")),
            )
            # The password was validated and hashed at sign-up time and carried
            # through the session as a hash, never as plaintext. Assign it
            # directly rather than re-hashing.
            hashed = state.get("password") or ""
            if hashed:
                user.password = hashed
                user.password_changed_at = timezone.now()
            # Entering the emailed code proves control of the mailbox, so the
            # address is verified in the same step.
            user.email_verified_at = timezone.now()
            user.save(update_fields=["password", "password_changed_at",
                                     "email_verified_at", "updated_at"])
        audit.record("account.created", request=request, actor=user)

    services.complete_login(request, user, method=LoginActivity.Method.EMAIL_OTP)
    return redirect(next_url or dashboard_url())


# --------------------------------------------------------------------------
# Password sign-in
# --------------------------------------------------------------------------


@ensure_csrf_cookie
@require_http_methods(["GET", "POST"])
def sign_in(request):
    if request.user.is_authenticated:
        return redirect(services.safe_next_url(request, dashboard_url()))

    if request.method == "GET":
        return render(request, "accounts/sign_in.html", {"next": services.safe_next_url(request, "")})

    email = (request.POST.get("email") or "").strip().lower()
    password = request.POST.get("password") or ""
    client_ip = ratelimit.client_identity(request)

    # Two independent limits. The per-IP one stops one source hammering many
    # accounts; the per-identifier one stops many sources hammering one
    # account. Either alone leaves the other attack shape open.
    ip_verdict = ratelimit.consume("login.ip", client_ip)
    id_verdict = ratelimit.consume("login.identifier", email) if email else ip_verdict

    if not ip_verdict.allowed or not id_verdict.allowed:
        services.record_attempt(
            request,
            method=LoginActivity.Method.PASSWORD,
            outcome=LoginActivity.Outcome.RATE_LIMITED,
            email=email,
        )
        return render(
            request,
            "accounts/sign_in.html",
            {
                "error": "Too many attempts. Please wait a few minutes and try again.",
                "email": email,
            },
            status=429,
        )

    user = authenticate(request, username=email, password=password)

    if user is None:
        # `authenticate` already spent time hashing even when no account
        # matched, so this branch is not distinguishable by timing.
        exists = User.objects.filter(email=email).exists()
        services.record_attempt(
            request,
            method=LoginActivity.Method.PASSWORD,
            outcome=(
                LoginActivity.Outcome.BAD_PASSWORD
                if exists
                else LoginActivity.Outcome.NO_ACCOUNT
            ),
            email=email,
        )
        return render(
            request,
            "accounts/sign_in.html",
            {"error": GENERIC_SIGN_IN_ERROR, "email": email},
            status=401,
        )

    ratelimit.reset("login.identifier", email)

    next_url = services.safe_next_url(request, "")

    # When the account has an authenticator app enrolled, that is its second
    # factor: after the password we ask for the app's rotating code directly and
    # send no email at all. Only accounts *without* an authenticator fall back to
    # the emailed one-time code below. A browser the user has chosen to trust
    # still short-circuits the challenge, exactly as it did before.
    if user.has_totp:
        services.record_attempt(
            request,
            method=LoginActivity.Method.PASSWORD,
            outcome=LoginActivity.Outcome.MFA_REQUIRED,
            user=user,
        )
        if services.needs_second_factor(request, user):
            services.begin_pending_login(
                request, user, method=LoginActivity.Method.PASSWORD
            )
            return redirect(f"{reverse('accounts:mfa_challenge')}?next={next_url}")
        # Trusted browser  the authenticator step was already cleared here.
        services.complete_login(request, user, method=LoginActivity.Method.PASSWORD)
        return redirect(next_url or dashboard_url())

    # No authenticator: confirm with a one-time code emailed to the address on
    # the account  an emailed factor layered on the password. The password
    # having matched, this send only ever goes to the account's own address.
    services.record_attempt(
        request,
        method=LoginActivity.Method.PASSWORD,
        outcome=LoginActivity.Outcome.MFA_REQUIRED,
        user=user,
    )
    sent = _email_code_and_park(
        request,
        flow="login",
        email=user.email,
        purpose="login",
        data={"user": str(user.pk), "next": next_url},
    )
    if not sent.ok:
        return render(
            request,
            "accounts/sign_in.html",
            {"error": sent.error, "email": email},
            status=sent.status,
        )
    return redirect("accounts:email_otp")


@require_POST
def sign_out(request):
    services.sign_out(request)
    return redirect(sign_in_url(request))


# --------------------------------------------------------------------------
# Second-factor challenge
# --------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def mfa_challenge(request):
    user = services.resolve_pending_login(request)
    if user is None:
        return redirect("accounts:sign_in")

    context = {"has_passkey": user.has_passkey, "has_totp": user.has_totp}

    if request.method == "GET":
        return render(request, "accounts/mfa_challenge.html", context)

    verdict = ratelimit.consume("otp.verify", str(user.pk))
    if not verdict.allowed:
        services.clear_pending_login(request)
        return render(
            request,
            "accounts/mfa_challenge.html",
            {**context, "error": "Too many attempts. Please sign in again."},
            status=429,
        )

    device = user.totp_devices.filter(confirmed_at__isnull=False).first()
    if device is None:
        return redirect("accounts:totp_enrol")

    result = totp.verify(device, request.POST.get("code", ""))
    if not result.ok:
        services.record_attempt(
            request,
            method=LoginActivity.Method.TOTP,
            outcome=LoginActivity.Outcome.MFA_FAILED,
            user=user,
        )
        return render(
            request,
            "accounts/mfa_challenge.html",
            {**context, "error": "That code is not right. Try the current one."},
            status=401,
        )

    ratelimit.reset("otp.verify", str(user.pk))
    services.complete_login(request, user, method=LoginActivity.Method.TOTP)

    response = redirect(services.safe_next_url(request, dashboard_url()))
    if request.POST.get("remember_device"):
        services.remember_device(request, response, user)
    return response


# --------------------------------------------------------------------------
# Magic link
# --------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def magic_link_request(request):
    if request.method == "GET":
        return render(request, "accounts/magic_link_request.html")

    email = (request.POST.get("email") or "").strip().lower()
    client_ip = ratelimit.client_identity(request)

    # Both limits are needed: per-address so one mailbox cannot be flooded,
    # per-IP so one caller cannot flood many mailboxes. The platform this
    # replaces had neither, which made its equivalent endpoint a usable
    # email-bombing tool.
    address_ok = ratelimit.consume("magiclink.send", email).allowed if email else False
    ip_ok = ratelimit.consume("otp.send.ip", client_ip).allowed

    if address_ok and ip_ok:
        user = User.objects.filter(email=email, is_active=True).first()
        if user is not None:
            token = tokens.sign("auth.magic_link", {"u": str(user.pk)})
            url = request.build_absolute_uri(
                reverse("accounts:magic_link_consume") + f"?t={token}"
            )
            emails.send_magic_link(
                email, url, minutes=tokens.ttl_for("auth.magic_link") // 60
            )

    # Identical response either way  sent, not sent, rate limited. The caller
    # learns nothing about whether the address is registered.
    return render(request, "accounts/magic_link_sent.html", {"email": email})


#: Both a sign-in link and an address-verification link land here. They are
#: separate purposes with separate lifetimes  a verification link is valid for
#: a day, a sign-in link for fifteen minutes  so each is tried against its own
#: signer rather than being collapsed into one long-lived token.
_SIGN_IN_LINK_PURPOSES = ("auth.magic_link", "auth.email_verify")


@require_GET
def magic_link_consume(request):
    raw = request.GET.get("t", "")

    payload = None
    for purpose in _SIGN_IN_LINK_PURPOSES:
        try:
            payload = tokens.unsign(purpose, raw)
            break
        except tokens.TokenError:
            continue

    if payload is None:
        services.record_attempt(
            request,
            method=LoginActivity.Method.MAGIC_LINK,
            outcome=LoginActivity.Outcome.TOKEN_INVALID,
        )
        return render(request, "accounts/magic_link_invalid.html", status=400)

    user = User.objects.filter(pk=payload.get("u"), is_active=True).first()
    if user is None:
        return render(request, "accounts/magic_link_invalid.html", status=400)

    # Following the link proves control of the mailbox, so it also verifies
    # the address.
    if user.email_verified_at is None:
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified_at", "updated_at"])

    if services.needs_second_factor(request, user):
        services.begin_pending_login(request, user, method=LoginActivity.Method.MAGIC_LINK)
        return redirect("accounts:mfa_challenge")

    services.complete_login(request, user, method=LoginActivity.Method.MAGIC_LINK)
    return redirect(dashboard_url())


# --------------------------------------------------------------------------
# Password reset ("forgot password")
# --------------------------------------------------------------------------
#
# Two steps. First the visitor gives an email and we mail a reset link to it;
# the response is identical whether or not that address has an account, so the
# endpoint never becomes an account-enumeration oracle. Following the link opens
# a form to choose a new password.
#
# The link carries a purpose-bound signed token (``auth.password_reset``, a
# short lifetime in settings.TOKEN_TTL). No reset state is stored server-side;
# single use is achieved by binding a fingerprint of the current password hash
# into the token  once the password is changed the fingerprint no longer
# matches, so a used (or stale) link stops working.

_PASSWORD_RESET_PURPOSE = "auth.password_reset"


def _reset_fingerprint(user) -> str:
    """A short, opaque fingerprint of the account's current password secret.

    Embedded in a reset token and re-checked on confirmation. Because
    :meth:`User.set_password` changes the stored hash, completing one reset (or
    any later password change) alters this value and invalidates every
    outstanding link  the token becomes effectively single-use without any
    server-side reset record.
    """
    basis = f"{user.pk}:{user.password}".encode()
    return hashlib.sha256(basis).hexdigest()[:16]


@ensure_csrf_cookie
@require_http_methods(["GET", "POST"])
def password_reset_request(request):
    """Step 1  collect the email and send a reset link to it."""
    if request.user.is_authenticated:
        return redirect(dashboard_url())

    if request.method == "GET":
        return render(request, "accounts/password_reset_request.html")

    email = (request.POST.get("email") or "").strip().lower()
    client_ip = ratelimit.client_identity(request)

    # Per-address and per-IP limits, as elsewhere: the first stops one mailbox
    # being flooded with reset mail, the second stops one caller spraying many.
    address_ok = ratelimit.consume("password_reset.send", email).allowed if email else False
    ip_ok = ratelimit.consume("otp.send.ip", client_ip).allowed

    if address_ok and ip_ok:
        user = User.objects.filter(email=email, is_active=True).first()
        # Only a real, password-capable account is mailed a link. A passwordless
        # account (magic-link only) has nothing to reset; it is treated exactly
        # like a non-existent one, so the response still discloses nothing.
        if user is not None and user.has_usable_password():
            token = tokens.sign(
                _PASSWORD_RESET_PURPOSE,
                {"u": str(user.pk), "fp": _reset_fingerprint(user)},
            )
            url = request.build_absolute_uri(
                reverse("accounts:password_reset_confirm") + f"?t={token}"
            )
            emails.send_password_reset(
                email, url, minutes=tokens.ttl_for(_PASSWORD_RESET_PURPOSE) // 60
            )

    # Identical response either way  sent, not sent, rate limited.
    return render(request, "accounts/password_reset_sent.html", {"email": email})


def _resolve_reset_token(raw: str):
    """Return the account a valid reset token names, or ``None``."""
    if not raw:
        return None
    try:
        payload = tokens.unsign(_PASSWORD_RESET_PURPOSE, raw)
    except tokens.TokenError:
        return None
    user = User.objects.filter(pk=payload.get("u"), is_active=True).first()
    if user is None or not user.has_usable_password():
        return None
    if payload.get("fp") != _reset_fingerprint(user):
        return None
    return user


@ensure_csrf_cookie
@require_http_methods(["GET", "POST"])
def password_reset_confirm(request):
    """Step 2  validate the link and set the new password."""
    raw = request.GET.get("t") or request.POST.get("t") or ""
    user = _resolve_reset_token(raw)
    if user is None:
        return render(request, "accounts/password_reset_invalid.html", status=400)

    if request.method == "GET":
        return render(request, "accounts/password_reset_confirm.html", {"token": raw})

    new = request.POST.get("new_password") or ""
    confirm = request.POST.get("confirm_password") or ""

    errors = []
    if new != confirm:
        errors.append("The two passwords don't match.")
    try:
        validate_password(new, user=user)
    except ValidationError as exc:
        errors.extend(exc.messages)
    if errors:
        return render(
            request,
            "accounts/password_reset_confirm.html",
            {"token": raw, "errors": errors},
            status=400,
        )

    user.set_password(new)
    user.save(update_fields=["password", "password_changed_at", "updated_at"])
    audit.record("account.password.reset", request=request, actor=user, actor_kind="user")

    from . import notifications

    notifications.send_security_alert(
        user,
        subject="Your OneWooz password was reset",
        headline="Your account password was just reset.",
        detail="If you didn't reset it, contact us immediately  someone else may "
               "have access to your inbox.",
    )
    # The auth pages don't render the Django messages framework, so signal the
    # outcome with a query flag the sign-in page reads, rather than leaving an
    # unshown message to surface on a later page.
    return redirect(f"{reverse('accounts:sign_in')}?reset=done")


# --------------------------------------------------------------------------
# Email one-time code (alternative to the link, for clients that mangle URLs)
# --------------------------------------------------------------------------


@require_POST
def login_code_request(request):
    email = (request.POST.get("email") or "").strip().lower()
    client_ip = ratelimit.client_identity(request)

    address_ok = ratelimit.consume("otp.send.email", email).allowed if email else False
    ip_ok = ratelimit.consume("otp.send.ip", client_ip).allowed

    if address_ok and ip_ok and User.objects.filter(email=email, is_active=True).exists():
        code = otp.issue("login", email)
        emails.send_login_code(email, code, minutes=otp.TTL_SECONDS // 60)

    return render(request, "accounts/login_code_verify.html", {"email": email})


@require_POST
def login_code_verify(request):
    email = (request.POST.get("email") or "").strip().lower()
    submitted = request.POST.get("code") or ""

    if not ratelimit.consume("otp.verify", email).allowed:
        return render(
            request,
            "accounts/login_code_verify.html",
            {"email": email, "error": "Too many attempts. Request a new code."},
            status=429,
        )

    result = otp.verify("login", email, submitted)
    if not result.ok:
        services.record_attempt(
            request,
            method=LoginActivity.Method.EMAIL_OTP,
            outcome=LoginActivity.Outcome.MFA_FAILED,
            email=email,
        )
        return render(
            request,
            "accounts/login_code_verify.html",
            {"email": email, "error": "That code is not right or has expired."},
            status=401,
        )

    user = User.objects.filter(email=email, is_active=True).first()
    if user is None:
        return redirect("accounts:sign_in")

    ratelimit.reset("otp.verify", email)

    if services.needs_second_factor(request, user):
        services.begin_pending_login(request, user, method=LoginActivity.Method.EMAIL_OTP)
        return redirect("accounts:mfa_challenge")

    services.complete_login(request, user, method=LoginActivity.Method.EMAIL_OTP)
    return redirect(dashboard_url())


# --------------------------------------------------------------------------
# Passkeys
# --------------------------------------------------------------------------


@login_required
@require_POST
def passkey_register_begin(request):
    return JsonResponse(
        {"options": passkeys.begin_registration(request, request.user)}, safe=False
    )


@login_required
@require_POST
def passkey_register_finish(request):
    # The body is the raw WebAuthn credential JSON, so the label travels as a
    # query parameter rather than a form field.
    label = (request.GET.get("label") or "").strip()[:60]
    try:
        credential = passkeys.finish_registration(
            request, request.user, request.body.decode("utf-8"), label=label
        )
    except passkeys.PasskeyError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    audit.record(
        "auth.passkey.register",
        request=request,
        actor=request.user,
        target=credential,
        severity=audit.AuditEvent.Severity.NOTICE,
    )
    return JsonResponse({"ok": True, "id": str(credential.pk), "label": credential.label})


@require_POST
def passkey_auth_begin(request):
    """Start a discoverable assertion.

    No email is required and none is accepted: asking for one and then tailoring
    the response would re-introduce the account-enumeration oracle that the
    rest of this module is careful to avoid.
    """
    return JsonResponse({"options": passkeys.begin_authentication(request)}, safe=False)


@require_POST
def passkey_auth_finish(request):
    credential_id = request.headers.get("X-Credential-Id", "")
    try:
        user, credential = passkeys.finish_authentication(
            request, request.body.decode("utf-8"), credential_id=credential_id
        )
    except passkeys.PasskeyError as exc:
        services.record_attempt(
            request,
            method=LoginActivity.Method.PASSKEY,
            outcome=LoginActivity.Outcome.MFA_FAILED,
        )
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    if not user.is_active:
        return JsonResponse({"ok": False, "error": "This account is disabled."}, status=403)

    # The admin surface accepts passkey sessions and nothing else. Only this
    # path may set the flag, so a session established by password, TOTP or
    # magic link can never be carried onto admin.onewooz.com.
    if getattr(request, "surface", None) == Surface.ADMIN and not user.is_staff:
        return JsonResponse({"ok": False, "error": "Not permitted."}, status=403)

    # A passkey is already two factors  the authenticator plus the gesture
    # that unlocked it  so no further challenge is raised.
    services.complete_login(request, user, method=LoginActivity.Method.PASSKEY)
    request.session[admin_middleware.SESSION_PASSKEY_FLAG] = True

    return JsonResponse({"ok": True, "redirect": services.safe_next_url(request, dashboard_url())})


@login_required
@require_POST
def passkey_delete(request, credential_id):
    credential = request.user.passkeys.filter(pk=credential_id).first()
    if credential is None:
        return JsonResponse({"ok": False}, status=404)

    # Staff reach the admin surface with a passkey and nothing else. Removing
    # the last one would lock them out of it entirely.
    if request.user.is_staff and request.user.passkeys.count() <= 1:
        return JsonResponse(
            {"ok": False, "error": "Staff accounts must keep at least one passkey."},
            status=400,
        )

    audit.record(
        "auth.passkey.delete",
        request=request,
        actor=request.user,
        target=credential,
        severity=audit.AuditEvent.Severity.WARNING,
    )
    credential.delete()
    return JsonResponse({"ok": True})


# --------------------------------------------------------------------------
# TOTP enrolment
# --------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def totp_enrol(request):
    """Enrol an authenticator app.

    Reachable both by a signed-in user adding a factor and by a user parked
    mid-sign-in because a factor is required but none is enrolled.
    """
    user = request.user if request.user.is_authenticated else services.resolve_pending_login(request)
    if user is None:
        return redirect("accounts:sign_in")

    if request.method == "GET":
        # The secret is created unconfirmed. It only becomes usable once the
        # user proves their app produces matching codes, so a half-finished
        # enrolment cannot lock anyone out.
        device = user.totp_devices.filter(confirmed_at__isnull=True).first()
        if device is None:
            device = TOTPDevice.objects.create(user=user, secret=totp.new_secret())
        return render(
            request,
            "accounts/totp_enrol.html",
            {
                "secret": device.secret,
                "uri": totp.provisioning_uri(device.secret, user.email),
            },
        )

    device = user.totp_devices.filter(confirmed_at__isnull=True).first()
    if device is None:
        return redirect("accounts:totp_enrol")

    if not totp.verify(device, request.POST.get("code", "")).ok:
        return render(
            request,
            "accounts/totp_enrol.html",
            {
                "secret": device.secret,
                "uri": totp.provisioning_uri(device.secret, user.email),
                "error": "That code is not right. Make sure you scanned the current code.",
            },
            status=400,
        )

    audit.record(
        "auth.totp.enrol",
        request=request,
        actor=user,
        severity=audit.AuditEvent.Severity.NOTICE,
    )

    if request.user.is_authenticated:
        messages.success(request, "Authenticator app added.")
        return redirect("accounts:security")

    services.complete_login(request, user, method=LoginActivity.Method.TOTP)
    return redirect(dashboard_url())


# --------------------------------------------------------------------------
# Authenticator enrolment from the security page (modal, JSON)
# --------------------------------------------------------------------------
#
# The signed-in owner sets up an authenticator without leaving the security
# page: a modal shows the QR code (with a "show secret key" fallback), and these
# two JSON endpoints back it. They complement the full-page ``totp_enrol`` above,
# which stays for the mid-sign-in case (a user who *must* enrol before they can
# finish signing in). Both require an authenticated session.


@login_required
@require_POST
def totp_setup_begin(request):
    """Start (or resume) authenticator enrolment for the signed-in user.

    Mints an unconfirmed device  or reuses a pending one so reopening the modal
    doesn't pile up secrets  and returns the shared secret, the ``otpauth`` URI
    and an inline QR image. The device is not usable until a code confirms it via
    :func:`totp_setup_verify`.
    """
    if request.user.has_totp:
        return JsonResponse(
            {"ok": False, "error": "An authenticator app is already set up."}, status=400
        )

    device = request.user.totp_devices.filter(confirmed_at__isnull=True).first()
    if device is None:
        device = TOTPDevice.objects.create(user=request.user, secret=totp.new_secret())

    uri = totp.provisioning_uri(device.secret, request.user.email)
    return JsonResponse(
        {"ok": True, "secret": device.secret, "uri": uri, "qr": totp.qr_data_uri(uri)}
    )


@login_required
@require_POST
def totp_setup_verify(request):
    """Confirm an authenticator enrolment with a code from the app."""
    if not ratelimit.consume("otp.verify", str(request.user.pk)).allowed:
        return JsonResponse(
            {"ok": False, "error": "Too many attempts. Please wait a moment and try again."},
            status=429,
        )

    device = request.user.totp_devices.filter(confirmed_at__isnull=True).first()
    if device is None:
        return JsonResponse(
            {"ok": False, "error": "Start the setup again  the session expired."}, status=400
        )

    if not totp.verify(device, request.POST.get("code", "")).ok:
        return JsonResponse(
            {"ok": False, "error": "That code is not right. Make sure you entered the current one."},
            status=400,
        )

    ratelimit.reset("otp.verify", str(request.user.pk))
    audit.record(
        "auth.totp.enrol",
        request=request,
        actor=request.user,
        severity=audit.AuditEvent.Severity.NOTICE,
    )
    return JsonResponse({"ok": True})


@login_required
@require_POST
def two_factor_use_email(request):
    """Switch two-step verification back to emailed one-time codes.

    Removes every authenticator device (confirmed or a half-finished setup), so
    the account's second factor becomes the emailed code again. This is the
    explicit counterpart to setting up an authenticator: a method is active only
    when it is fully turned on, never because setup was merely started.
    """
    removed = request.user.totp_devices.all().delete()[0]
    if removed:
        audit.record(
            "auth.totp.removed",
            request=request,
            actor=request.user,
            severity=audit.AuditEvent.Severity.NOTICE,
        )
        messages.success(
            request,
            "Two-step verification now uses an emailed code. Your authenticator app was removed.",
        )
    else:
        messages.info(request, "You’re already using emailed codes for two-step verification.")
    return redirect(f"{reverse('accounts:security')}#methods")


# --------------------------------------------------------------------------
# Account security page
# --------------------------------------------------------------------------


@login_required
@require_GET
def security(request):
    from . import geo, sessions as session_registry, useragent

    # Sign-in history, each row decorated with a friendly device + location so
    # the template shows "Chrome on Windows · Mumbai, India" rather than a raw
    # user-agent string and a bare IP.
    activity = list(request.user.login_activity.all()[:30])
    for entry in activity:
        entry.device_friendly = useragent.friendly(entry.user_agent)
        entry.location = geo.describe(entry.ip_address or "", entry.country_code)

    live = session_registry.list_for_user(
        request.user, request.session.session_key or ""
    )
    # Never print a raw session key into the page. Each endable session carries a
    # short-lived, purpose-bound, owner-bound token instead; the terminate
    # endpoint unsigns it (and still verifies ownership before deleting).
    live_sessions = [
        {
            "device": s.device,
            "location": s.location,
            "ip": s.ip,
            "is_current": s.is_current,
            "signed_in_at": s.signed_in_at,
            "last_seen_at": s.last_seen_at,
            "token": ""
            if s.is_current
            else tokens.sign(
                _SESSION_END_PURPOSE, {"key": s.session_key, "u": str(request.user.pk)}
            ),
        }
        for s in live
    ]

    return render(
        request,
        "accounts/security.html",
        {
            "nav_active": "security",
            "passkeys": request.user.passkeys.all(),
            "totp_devices": request.user.totp_devices.filter(confirmed_at__isnull=False),
            "trusted_devices": request.user.trusted_devices.filter(
                expires_at__gt=timezone.now()
            ),
            "recent_activity": activity,
            "live_sessions": live_sessions,
            "live_session_count": len(live_sessions),
            "other_session_count": sum(1 for s in live if not s.is_current),
        },
    )


#: Purpose salt for the DOM-facing session-end token (see the security view).
_SESSION_END_PURPOSE = "accounts.session_end"


# --------------------------------------------------------------------------
# Live sessions  list on the security page, end any one instantly
# --------------------------------------------------------------------------


@login_required
@require_GET
def session_ping(request):
    """Heartbeat the dashboard polls so a *remotely ended* session logs out fast.

    Returns ``{"authenticated": true}`` while the caller's session is valid. When
    the owner ends this device's session from another one, its row is deleted, so
    the next ping arrives anonymous and this returns ``{"authenticated": false}``
     the page then redirects itself to sign-in within one poll interval.

    It is intentionally *not* ``login_required``: an ended session must get a
    clean negative answer, not a redirect to the sign-in page.
    """
    if not request.user.is_authenticated:
        return JsonResponse({"authenticated": False}, status=200)
    services.touch_session_device(request)
    return JsonResponse({"authenticated": True}, status=200)


@login_required
@require_POST
def session_terminate(request):
    """End one of the owner's live sessions (block / sign out that device).

    The device is named by a signed, owner-bound token minted on the security
    page  never a raw session key from the browser. Ownership is still
    re-verified against the decoded key before anything is deleted.
    """
    from . import sessions as session_registry

    try:
        payload = tokens.unsign(_SESSION_END_PURPOSE, request.POST.get("token") or "")
    except tokens.TokenError:
        return JsonResponse(
            {"ok": False, "error": "This action expired. Reload the page and try again."},
            status=400,
        )
    if str(payload.get("u") or "") != str(request.user.pk):
        return JsonResponse({"ok": False, "error": "Not permitted."}, status=403)

    key = payload.get("key") or ""
    is_current = key == (request.session.session_key or "")
    ended = session_registry.end_session(request.user, key)
    if not ended:
        return JsonResponse(
            {"ok": False, "error": "That session is no longer active."}, status=404
        )

    audit.record(
        "auth.session.ended",
        request=request,
        actor=request.user,
        actor_kind="user",
        context={"self": is_current},
    )
    # Ending your own current session is just a sign-out  send the caller on.
    if is_current:
        return JsonResponse({"ok": True, "redirect": sign_in_url(request)})
    return JsonResponse({"ok": True})


@login_required
@require_POST
def sessions_terminate_others(request):
    """End every other session  "sign out everywhere else" in one action."""
    from . import sessions as session_registry

    count = session_registry.end_others(request.user, request.session.session_key or "")
    if count:
        audit.record(
            "auth.session.ended_others",
            request=request,
            actor=request.user,
            actor_kind="user",
            context={"count": count},
        )
    return JsonResponse({"ok": True, "count": count})


@login_required
@require_POST
def trusted_device_remove(request, device_id):
    """Revoke a trusted browser so it faces the second factor again."""
    device = request.user.trusted_devices.filter(pk=device_id).first()
    if device is None:
        return JsonResponse({"ok": False}, status=404)
    device.delete()
    audit.record(
        "auth.trusted_device.removed",
        request=request,
        actor=request.user,
        actor_kind="user",
    )
    return JsonResponse({"ok": True})


# Common IANA zones offered in the profile timezone picker. Kept short and
# India-first (OneWooz's primary market) rather than an exhaustive list.
_TIMEZONES = [
    "Asia/Kolkata", "Asia/Dubai", "Asia/Singapore", "Asia/Tokyo",
    "Europe/London", "Europe/Berlin", "America/New_York", "America/Los_Angeles",
    "Australia/Sydney", "UTC",
]


def _profile_snapshot(user) -> dict:
    """Read-only account overview shown alongside the editable profile."""
    from tenancy.models import Site

    last_success = (
        user.login_activity.filter(outcome=LoginActivity.Outcome.SUCCESS)
        .order_by("-created_at")
        .first()
    )
    return {
        "member_since": user.created_at,
        "email_verified": user.is_email_verified,
        "has_passkey": user.has_passkey,
        "has_totp": user.has_totp,
        "has_second_factor": user.has_second_factor,
        "passkey_count": user.passkeys.count(),
        "trusted_count": user.trusted_devices.filter(expires_at__gt=timezone.now()).count(),
        "store_count": Site.objects.filter(owner=user, is_deleted=False).count(),
        "has_password": user.has_usable_password(),
        "last_login_at": getattr(last_success, "created_at", None),
        "last_login_ip": getattr(last_success, "ip_address", None),
    }


def _subscription_overview(user) -> dict:
    """The owner's plan standing and how each store sits under it.

    A OneWooz subscription is held at the *account* level (keyed on the owner's
    email  see ``billing.subscriptions`` / ``commerce.features``), so every store
    the owner runs operates under the one plan. This assembles exactly that for
    the profile page: the current plan, and the list of stores it covers, so the
    owner can see which store is running on which subscription basis.
    """
    from billing.subscriptions import current_for_owner
    from commerce.context import NAV_GROUPS
    from commerce.features import allowed_features_for_owner
    from tenancy.models import Site

    sub = current_for_owner(user)

    sites = list(
        Site.objects.filter(owner=user, is_deleted=False).order_by("-created_at")
    )

    # For a gated free plan, the dashboard areas that are locked (shown so the
    # owner sees exactly what upgrading unlocks). ``None`` = nothing gated.
    allowed = allowed_features_for_owner(user)
    locked_labels = []
    if allowed is not None:
        for group in NAV_GROUPS:
            for item in group.items:
                if item.key not in allowed:
                    locked_labels.append(item.label)

    # The subscription bill (latest paid order), for the "View bill" dialog.
    # On localhost (DEBUG) an account without a paid order still gets a sample
    # bill behind a "Preview bill" button, so the layout and the PDF download can
    # be checked without making a purchase. Never offered in production.
    from django.conf import settings as dj_settings

    from billing.bill import build_bill, build_sample_bill

    # The bill is for the very order the card above describes (sub.order), so
    # the two always agree; any settled order qualifies, ₹0 grants included.
    bill = build_bill(user, order=sub.order)
    bill_preview = build_sample_bill(user) if (bill is None and dj_settings.DEBUG) else None

    return {
        "subscription": sub,
        "subscription_stores": sites,
        "stores_used": len(sites),
        "stores_allowed": sub.number_of_stores,
        "locked_feature_labels": locked_labels,
        "subscription_bill": bill,
        "subscription_bill_preview": bill_preview,
    }


def _save_profile(request, user):
    user.full_name = (request.POST.get("full_name") or "").strip()[:150]
    user.phone = (request.POST.get("phone") or "").strip()[:20]
    tz = (request.POST.get("timezone") or "").strip()
    if tz in _TIMEZONES:
        user.timezone = tz
    user.save(update_fields=["full_name", "phone", "timezone", "updated_at"])
    messages.success(request, "Profile updated.")


def _save_notifications(request, user):
    from django.core.validators import validate_email

    raw = (request.POST.get("notification_email") or "").strip().lower()
    if raw:
        try:
            validate_email(raw)
        except ValidationError:
            messages.error(request, "Enter a valid notification email, or leave it blank to use your sign-in email.")
            return
    user.notification_email = raw
    digest = request.POST.get("notification_digest")
    if digest in User.Digest.values:
        user.notification_digest = digest
    user.notify_security = bool(request.POST.get("notify_security"))
    user.notify_orders = bool(request.POST.get("notify_orders"))
    user.notify_store_activity = bool(request.POST.get("notify_store_activity"))
    user.notify_billing = bool(request.POST.get("notify_billing"))
    user.notify_product_news = bool(request.POST.get("notify_product_news"))
    user.save(update_fields=[
        "notification_email", "notification_digest", "notify_security",
        "notify_orders", "notify_store_activity", "notify_billing",
        "notify_product_news", "updated_at",
    ])
    messages.success(request, f"Notification preferences saved. Alerts go to {user.effective_notification_email}.")


def _change_password(request, user):
    from django.contrib.auth import update_session_auth_hash

    has_pw = user.has_usable_password()
    current = request.POST.get("current_password") or ""
    new = request.POST.get("new_password") or ""
    confirm = request.POST.get("confirm_password") or ""

    if has_pw and not user.check_password(current):
        messages.error(request, "Your current password isn't right.")
        return
    if new != confirm:
        messages.error(request, "The new passwords don't match.")
        return
    try:
        validate_password(new, user=user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return

    user.set_password(new)
    user.save(update_fields=["password", "password_changed_at", "updated_at"])
    update_session_auth_hash(request, user)  # keep the current session signed in
    audit.record("account.password.changed", request=request, actor=user, actor_kind="user")
    from . import notifications

    notifications.send_security_alert(
        user,
        subject="Your OneWooz password was changed",
        headline="Your account password was just changed.",
        detail="If you made this change you can ignore this message.",
    )
    messages.success(request, "Password updated." if has_pw else "Password set  you can now sign in with it.")


@login_required
@require_http_methods(["GET", "POST"])
def profile(request):
    user = request.user
    if request.method == "POST":
        intent = request.POST.get("intent")
        if intent == "profile":
            _save_profile(request, user)
        elif intent == "notifications":
            _save_notifications(request, user)
        # Browser push: save the per-type pop-up switches / remove one browser.
        # BROWSER PUSH: DISABLED (commented out, not removed). Uncomment to re-enable.
        # elif intent == "push_prefs":
        #     _save_push_prefs(request, user)
        # elif intent == "push_remove":
        #     _remove_push_device(request, user)
        elif intent == "password":
            _change_password(request, user)
        elif intent == "test_notification":
            from . import notifications

            if notifications.send_test(user):
                messages.success(request, f"Test notification sent to {user.effective_notification_email}.")
            else:
                messages.error(request, "Couldn't send the test notification. Check the address and try again.")
        return redirect("accounts:profile")

    context = {
        "snapshot": _profile_snapshot(user),
        "timezones": _TIMEZONES,
        "digest_choices": User.Digest.choices,
        **_subscription_overview(user),
        # Browser push: the Profile "Browser notifications" card's switches
        # and the list of browsers receiving pop-ups.
        # BROWSER PUSH: DISABLED (commented out, not removed). Uncomment to re-enable.
        # **_push_overview(user),
        "deletion": _deletion_overview(user),
    }
    return render(request, "accounts/profile.html", context)


# --------------------------------------------------------------------------
# Delete My Account (Profile page, bottom)
# --------------------------------------------------------------------------
#
# Same shape as deleting a store (ud.views.delete_site_*): the owner ticks the
# acknowledgement, we email a one-time code bound to this account, and only a
# correct code performs the delete. Nothing is erased: accounts.deletion marks
# the account deleted, soft-deletes its stores and ends every session. Signing
# in again within the recovery window restores it (after renewing a lapsed paid
# plan); see accounts.deletion for the full lifecycle.


def _deletion_overview(user) -> dict:
    from tenancy.models import Site

    from . import deletion

    return {
        "retention_days": deletion.retention_days(),
        "pending": bool(user.account_deleted_at),
        "recover_until": deletion.recover_until(user.account_deleted_at),
        "store_count": Site.objects.filter(owner=user, is_deleted=False).count(),
    }


def _account_delete_purpose(user) -> str:
    # Salted with the account id so a code minted for one account can never be
    # replayed against another.
    return f"account_delete:{user.pk}"


def _account_delete_refusal(request):
    """A JSON refusal when this request may not delete the account, else None."""
    if getattr(request, "impersonation", None):
        return JsonResponse(
            {"ok": False, "error": "An admin viewing this account cannot delete it."}, status=403
        )
    if request.user.is_staff or request.user.is_superuser:
        return JsonResponse(
            {"ok": False, "error": "Staff accounts cannot be deleted from here."}, status=403
        )
    if request.user.account_deleted_at:
        return JsonResponse(
            {"ok": False, "error": "This account is already scheduled for deletion."}, status=400
        )
    return None


@require_POST
@login_required
def account_delete_send_code(request):
    """Step 1: email the owner a one-time code to confirm deleting the account."""
    refusal = _account_delete_refusal(request)
    if refusal is not None:
        return refusal
    if request.POST.get("acknowledge") != "1":
        return JsonResponse(
            {"ok": False, "error": "Tick the box to confirm you understand what happens."},
            status=400,
        )
    email = (request.user.email or "").strip()
    if not email:
        return JsonResponse({"ok": False, "error": "Your account has no email to send a code to."}, status=400)

    ip = ratelimit.client_identity(request)
    if not (
        ratelimit.consume("account.delete", str(request.user.pk)).allowed
        and ratelimit.consume("otp.send.email", email).allowed
        and ratelimit.consume("otp.send.ip", ip).allowed
    ):
        return JsonResponse(
            {"ok": False, "error": "Too many requests. Please wait a few minutes and try again."},
            status=429,
        )

    from . import deletion

    code = otp.issue(_account_delete_purpose(request.user), email)
    if not emails.send_account_delete_code(
        email, code, minutes=otp.TTL_SECONDS // 60, retention_days=deletion.retention_days()
    ):
        return JsonResponse(
            {"ok": False, "error": "We couldn't send the code right now. Please try again."},
            status=502,
        )
    local, _, domain = email.partition("@")
    return JsonResponse(
        {"ok": True, "email": f"{local[:1]}•••@{domain}", "expires_in": otp.TTL_SECONDS}
    )


@require_POST
@login_required
def account_delete_confirm(request):
    """Step 2: verify the emailed code, delete the account, sign out."""
    refusal = _account_delete_refusal(request)
    if refusal is not None:
        return refusal
    user = request.user
    email = (user.email or "").strip()

    if not ratelimit.consume("otp.verify", email).allowed:
        return JsonResponse({"ok": False, "error": "Too many attempts. Please wait and try again."}, status=429)

    code = (request.POST.get("code") or "").strip()
    if not code:
        return JsonResponse({"ok": False, "error": "Enter the code we emailed you."}, status=400)

    result = otp.verify(_account_delete_purpose(user), email, code)
    if not result.ok:
        message = {
            "expired": "That code has expired. Send a new one.",
            "too_many_attempts": "Too many incorrect attempts. Send a new code.",
            "incorrect": "That code isn't right. Check the email and try again.",
        }.get(result.reason, "That code isn't right. Please try again.")
        return JsonResponse({"ok": False, "error": message}, status=400)
    ratelimit.reset("otp.verify", email)

    from . import deletion

    deletion.delete_account(user, request=request, reason=request.POST.get("reason", ""))
    until = deletion.recover_until(user.account_deleted_at)
    services.sign_out(request)
    # The goodbye page reads the date from a signed value, never a raw one.
    token = tokens.sign("account.deleted", {"until": until.date().isoformat() if until else ""})
    return JsonResponse({"ok": True, "redirect": f"{reverse('accounts:account_deleted')}?t={token}"})


@require_GET
def account_deleted(request):
    """Shown right after an account is deleted: what happens and how to restore it."""
    from . import deletion

    until = ""
    try:
        until = tokens.unsign("account.deleted", request.GET.get("t", "")).get("until") or ""
    except tokens.TokenError:
        until = ""
    try:
        until_date = timezone.datetime.fromisoformat(until).date() if until else None
    except ValueError:
        until_date = None
    return render(
        request,
        "accounts/account_deleted.html",
        {"recover_until": until_date, "retention_days": deletion.retention_days()},
    )


def _push_overview(user) -> dict:
    """Browser push: the per-kind switches and the account's subscribed browsers."""
    from . import webpush
    from .models import PushSubscription

    muted = user.push_muted
    kinds = [
        {"key": key, "label": label, "help": help_text, "on": key not in muted}
        for key, label, help_text in webpush.PUSH_KINDS
    ]
    devices = list(
        PushSubscription.objects.filter(user=user)
        .only("id", "device_label", "created_at", "last_success_at")
        .order_by("-created_at")[: webpush.MAX_PER_USER]
    )
    return {"push_kinds": kinds, "push_devices": devices}


def _save_push_prefs(request, user):
    from . import webpush

    valid = [k for k, _label, _help in webpush.PUSH_KINDS]
    muted = [k for k in valid if not request.POST.get(f"push_{k}")]
    user.push_muted_kinds = ",".join(muted)
    user.save(update_fields=["push_muted_kinds", "updated_at"])
    messages.success(request, "Browser notification preferences saved.")


def _remove_push_device(request, user):
    from .models import PushSubscription

    try:
        device_id = uuid.UUID(str(request.POST.get("device_id") or ""))
    except ValueError:
        return
    removed, _ = PushSubscription.objects.filter(user=user, pk=device_id).delete()
    if removed:
        messages.success(request, "That browser will no longer get notifications.")


# --------------------------------------------------------------------------
# Sign-up
# --------------------------------------------------------------------------


def sign_up(request):
    """Self-service sign-up is currently DISABLED.

    OneWooz is not accepting new self-service accounts right now, so there is no
    way to sign up. This route keeps its name (``accounts:sign_up``) so existing
    links and ``{% url %}`` references  including the "/sign-up/" call-to-action
    buttons across the marketing site  still resolve; it simply redirects to
    the sign-in page instead of showing a sign-up form.

    The full original implementation is preserved below as
    :func:`_sign_up_disabled` (not wired to any URL). To turn sign-up back on,
    point the ``sign-up/`` route in ``accounts/urls.py`` at it again.
    """
    return redirect("accounts:sign_in")


# ---------------------------------------------------------------------------
# Original self-service sign-up  RETAINED but NOT routed while sign-up is off.
# Kept intact (rather than deleted) so re-enabling is a one-line URL change; see
# :func:`sign_up` above. Nothing reaches this view in the current wiring.
# ---------------------------------------------------------------------------
@ensure_csrf_cookie
@require_http_methods(["GET", "POST"])
def _sign_up_disabled(request):
    """Self-service sign-up, limited to allowlisted test users during the beta.

    An approved address does not get an account on submit: it gets an emailed
    one-time code, and the account is created only once that code is entered on
    :func:`email_otp`. So even here, control of the mailbox is proven before any
    account exists.
    """
    if request.user.is_authenticated:
        return redirect(dashboard_url())

    if request.method == "GET":
        return render(request, "accounts/sign_up.html")

    email = (request.POST.get("email") or "").strip().lower()
    password = request.POST.get("password") or ""
    full_name = (request.POST.get("full_name") or "").strip()[:150]
    form = {"email": email, "full_name": full_name}

    if not ratelimit.consume("signup.ip", ratelimit.client_identity(request)).allowed:
        return render(
            request,
            "accounts/sign_up.html",
            {**form, "error": "Too many sign-ups from this connection. Try again later."},
            status=429,
        )

    # Private beta: only approved test addresses may register. Everyone else is
    # turned away with a plain message. (This intentionally does reveal that the
    # address is not on the list  that is the requested behaviour, and it says
    # nothing about whether any account exists.)
    if not services.is_signup_allowed(email):
        services.record_attempt(
            request,
            method=LoginActivity.Method.PASSWORD,
            outcome=LoginActivity.Outcome.NO_ACCOUNT,
            email=email,
        )
        return render(
            request,
            "accounts/sign_up.html",
            {
                **form,
                "error": "You're not a test user. Sign-up is limited to approved "
                         "test accounts during the OneWooz private beta.",
            },
            status=403,
        )

    errors = []
    if not email or "@" not in email:
        errors.append("Enter a valid email address.")
    try:
        validate_password(password)
    except ValidationError as exc:
        errors.extend(exc.messages)
    if errors:
        return render(
            request,
            "accounts/sign_up.html",
            {**form, "errors": errors},
            status=400,
        )

    existing = User.objects.filter(email=email).first()
    if existing is not None:
        # Already registered: don't make a second account. Send a sign-in code
        # to the same address instead, so an allowlisted returning user still
        # lands in the dashboard.
        sent = _email_code_and_park(
            request, flow="login", email=email, purpose="login",
            data={"user": str(existing.pk), "next": ""},
        )
        if not sent.ok:
            return render(
                request,
                "accounts/sign_up.html",
                {**form, "error": sent.error},
                status=sent.status,
            )
        return redirect("accounts:email_otp")

    # New account: hold the validated details (password as a hash, never
    # plaintext) until the emailed code confirms the address.
    sent = _email_code_and_park(
        request,
        flow="signup",
        email=email,
        purpose="signup",
        data={"full_name": full_name, "password": make_password(password), "next": ""},
    )
    if not sent.ok:
        return render(
            request,
            "accounts/sign_up.html",
            {**form, "error": sent.error},
            status=sent.status,
        )
    return redirect("accounts:email_otp")
