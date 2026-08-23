"""Sign-in, sign-up and credential management for site owners.

A rule applied throughout: **never disclose whether an email address has an
account.** Sign-in, magic link and password reset all report the same thing to
the caller whether or not the address exists. An endpoint that distinguishes
them is an account-enumeration oracle, and enumeration is the first step of
every credential-stuffing campaign.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.decorators import login_required
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

    if services.needs_second_factor(request, user):
        services.begin_pending_login(request, user, method=LoginActivity.Method.PASSWORD)
        services.record_attempt(
            request,
            method=LoginActivity.Method.PASSWORD,
            outcome=LoginActivity.Outcome.MFA_REQUIRED,
            user=user,
        )
        if not user.has_second_factor:
            return redirect("accounts:totp_enrol")
        return redirect(f"{reverse('accounts:mfa_challenge')}?next={services.safe_next_url(request, '')}")

    services.complete_login(request, user, method=LoginActivity.Method.PASSWORD)
    return redirect(services.safe_next_url(request, dashboard_url()))


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


@require_http_methods(["GET", "POST"])
def sign_up(request):
    # Public self-service sign-up is currently DISABLED. Accounts are created
    # by an operator with ``python manage.py createuser`` instead. The route and
    # view are kept so ``reverse('accounts:sign_up')`` and any ``{% url %}`` tag
    # still resolve; authenticated users go to the dashboard, everyone else is
    # sent to sign-in. To re-enable, remove this block and uncomment the
    # original implementation below.
    if request.user.is_authenticated:
        return redirect(dashboard_url())
    return redirect("accounts:sign_in")

    # --- Original public sign-up (disabled) -------------------------------
    # if request.user.is_authenticated:
    #     return redirect(dashboard_url())
    #
    # if request.method == "GET":
    #     return render(request, "accounts/sign_up.html")
    #
    # email = (request.POST.get("email") or "").strip().lower()
    # password = request.POST.get("password") or ""
    # full_name = (request.POST.get("full_name") or "").strip()[:150]
    #
    # if not ratelimit.consume("signup.ip", ratelimit.client_identity(request)).allowed:
    #     return render(
    #         request,
    #         "accounts/sign_up.html",
    #         {"error": "Too many sign-ups from this connection. Try again later."},
    #         status=429,
    #     )
    #
    # from django.contrib.auth.password_validation import validate_password
    # from django.core.exceptions import ValidationError
    #
    # errors = []
    # if not email or "@" not in email:
    #     errors.append("Enter a valid email address.")
    # if password:
    #     try:
    #         validate_password(password)
    #     except ValidationError as exc:
    #         errors.extend(exc.messages)
    #
    # if errors:
    #     return render(
    #         request,
    #         "accounts/sign_up.html",
    #         {"errors": errors, "email": email, "full_name": full_name},
    #         status=400,
    #     )
    #
    # existing = User.objects.filter(email=email).first()
    # if existing is not None:
    #     # Do not say the address is taken. Instead send a sign-in link, which
    #     # is useful to the real owner and tells an enumerator nothing.
    #     token = tokens.sign("auth.magic_link", {"u": str(existing.pk)})
    #     url = request.build_absolute_uri(
    #         reverse("accounts:magic_link_consume") + f"?t={token}"
    #     )
    #     emails.send_magic_link(email, url, minutes=tokens.ttl_for("auth.magic_link") // 60)
    #     return render(request, "accounts/magic_link_sent.html", {"email": email})
    #
    # with transaction.atomic():
    #     user = User.objects.create_user(
    #         email=email,
    #         password=password or None,
    #         full_name=full_name,
    #         signup_source=str(getattr(request, "surface", "")),
    #     )
    #
    # audit.record("account.created", request=request, actor=user)
    #
    # token = tokens.sign("auth.email_verify", {"u": str(user.pk)})
    # verify_url = request.build_absolute_uri(
    #     reverse("accounts:magic_link_consume") + f"?t={token}"
    # )
    # emails.send_magic_link(email, verify_url, minutes=tokens.ttl_for("auth.email_verify") // 60)
    #
    # services.complete_login(request, user, method=LoginActivity.Method.PASSWORD)
    # return redirect(dashboard_url())
