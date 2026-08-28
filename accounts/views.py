"""Sign-in, sign-up and credential management for site owners.

A rule applied throughout: **never disclose whether an email address has an
account.** Sign-in, magic link and password reset all report the same thing to
the caller whether or not the address exists. An endpoint that distinguishes
them is an account-enumeration oracle, and enumeration is the first step of
every credential-stuffing campaign.
"""

from __future__ import annotations

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
    each has its own sign-in route — the admin one accepts passkeys only. A
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

    Returns a non-``ok`` result — having parked nothing — when the address or
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

    # Every sign-in is confirmed with a one-time code emailed to the address on
    # the account — an emailed factor layered on the password, applied uniformly
    # whether or not a TOTP/passkey factor is also enrolled. Any enrolled second
    # factor is still challenged afterwards, in the email-code view. The password
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
        data={"user": str(user.pk), "next": services.safe_next_url(request, "")},
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

    # Identical response either way — sent, not sent, rate limited. The caller
    # learns nothing about whether the address is registered.
    return render(request, "accounts/magic_link_sent.html", {"email": email})


#: Both a sign-in link and an address-verification link land here. They are
#: separate purposes with separate lifetimes — a verification link is valid for
#: a day, a sign-in link for fifteen minutes — so each is tried against its own
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

    # A passkey is already two factors — the authenticator plus the gesture
    # that unlocked it — so no further challenge is raised.
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
# Account security page
# --------------------------------------------------------------------------


@login_required
@require_GET
def security(request):
    return render(
        request,
        "accounts/security.html",
        {
            "passkeys": request.user.passkeys.all(),
            "totp_devices": request.user.totp_devices.filter(confirmed_at__isnull=False),
            "trusted_devices": request.user.trusted_devices.filter(
                expires_at__gt=timezone.now()
            ),
            "recent_activity": request.user.login_activity.all()[:20],
        },
    )


# --------------------------------------------------------------------------
# Sign-up
# --------------------------------------------------------------------------


@ensure_csrf_cookie
@require_http_methods(["GET", "POST"])
def sign_up(request):
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
    # address is not on the list — that is the requested behaviour, and it says
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
