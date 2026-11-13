"""Post-payment onboarding  create the account and capture the tax profile.

A subscription purchase is anonymous up to the moment it settles: the checkout
(pricing page or invite) only ever collects an email + phone and takes payment.
The account itself does not exist yet. This module is the step that follows 
reached once an order is **paid or granted free**  where the buyer:

* creates their OneWooz account (email already proven by the checkout OTP, plus
  the password they set here), and
* records the business identity an invoice needs: entity type (company /
  individual / GST), the matching tax id (PAN or GSTIN, both validated), the
  registered name, and a state / city / address.

The page lives on the **app** surface (that is where a session cookie can be
set and where the dashboard is), while the checkout that precedes it lives on
the marketing surface. The handoff is a purpose-bound signed token carrying the
settled order's id  :func:`onboarding_url` mints it on the marketing side and
:func:`onboarding` consumes it here, creating the account and signing the buyer
straight into their dashboard.

Everything is validated server-side; the client-side hints in the template only
make the same rules visible as the buyer types.
"""

from __future__ import annotations

import re

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from core import audit, tokens
from core.links import surface_url
from core.ratelimit import RateLimitExceeded, check, client_identity

from .models import OnboardingProfile, SubscriptionOrder

# Ties a settled order to its onboarding page across the marketing→app hop.
_ONBOARD_PURPOSE = "billing.onboarding"

# --------------------------------------------------------------------------
# Tax identity validation
# --------------------------------------------------------------------------
#
# PAN    5 letters, 4 digits, 1 letter (e.g. ABCDE1234F).
# GSTIN  2-digit state code, the holder's 10-char PAN, 1 entity digit, the
#         letter Z, and 1 checksum char (e.g. 22ABCDE1234F1Z5). We validate the
#         shape strictly; the embedded PAN is what makes it self-consistent.
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")


def normalise_pan(raw: str) -> str:
    return re.sub(r"\s+", "", (raw or "")).upper()


def normalise_gstin(raw: str) -> str:
    return re.sub(r"\s+", "", (raw or "")).upper()


def pan_error(raw: str) -> str:
    """"" if ``raw`` is a well-formed PAN, else a short reason it is not."""
    value = normalise_pan(raw)
    if not value:
        return "Enter your PAN."
    if len(value) != 10 or not PAN_RE.match(value):
        return "That doesn’t look like a valid PAN (e.g. ABCDE1234F)."
    return ""


def gstin_error(raw: str) -> str:
    """"" if ``raw`` is a well-formed GSTIN, else a short reason it is not."""
    value = normalise_gstin(raw)
    if not value:
        return "Enter your GSTIN."
    if len(value) != 15 or not GSTIN_RE.match(value):
        return "That doesn’t look like a valid 15-character GSTIN."
    return ""


# --------------------------------------------------------------------------
# Indian states / union territories  the state dropdown's source of truth
# --------------------------------------------------------------------------
INDIAN_STATES: tuple[str, ...] = (
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
    "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
    "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Andaman and Nicobar Islands", "Chandigarh",
    "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Jammu and Kashmir",
    "Ladakh", "Lakshadweep", "Puducherry",
)
_STATES_LOWER = {s.lower(): s for s in INDIAN_STATES}


def canonical_state(raw: str) -> str:
    """Return the canonical state name for ``raw`` (case-insensitive), or ""."""
    return _STATES_LOWER.get((raw or "").strip().lower(), "")


# --------------------------------------------------------------------------
# Password strength
# --------------------------------------------------------------------------
#
# The rules shown to the buyer, in the order they are displayed. Each is checked
# here as well as by Django's own validators (min length, common-password,
# not-all-numeric, not-too-similar-to-email) so the message the buyer sees and
# the rule that is enforced never drift apart.
PASSWORD_RULES: tuple[tuple[str, str], ...] = (
    ("length", "At least 12 characters"),
    ("upper", "An uppercase letter (A–Z)"),
    ("lower", "A lowercase letter (a–z)"),
    ("digit", "A number (0–9)"),
    ("symbol", "A symbol (e.g. ! @ # $ %)"),
)

_SYMBOL_RE = re.compile(r"[^A-Za-z0-9]")


def password_rule_failures(password: str) -> list[str]:
    """The keys from :data:`PASSWORD_RULES` this password fails."""
    pw = password or ""
    failed = []
    if len(pw) < 12:
        failed.append("length")
    if not re.search(r"[A-Z]", pw):
        failed.append("upper")
    if not re.search(r"[a-z]", pw):
        failed.append("lower")
    if not re.search(r"[0-9]", pw):
        failed.append("digit")
    if not _SYMBOL_RE.search(pw):
        failed.append("symbol")
    return failed


def password_problems(password: str, *, user=None) -> list[str]:
    """Every reason ``password`` is unacceptable, as human messages.

    Combines the explicit strength rules with Django's configured validators
    (which add the common-password and email-similarity checks), de-duplicated
    and safe to show the buyer.
    """
    messages: list[str] = []
    rule_labels = dict(PASSWORD_RULES)
    for key in password_rule_failures(password):
        messages.append(rule_labels[key])

    try:
        validate_password(password or "", user=user)
    except ValidationError as exc:
        for msg in exc.messages:
            # Django repeats the length message; the strength rules already
            # cover it, so keep only what they don't.
            low = msg.lower()
            if "too short" in low or "at least 12" in low:
                continue
            messages.append(msg)

    # Preserve order, drop duplicates.
    seen, unique = set(), []
    for m in messages:
        if m not in seen:
            seen.add(m)
            unique.append(m)
    return unique


# --------------------------------------------------------------------------
# The marketing→app handoff link
# --------------------------------------------------------------------------
def onboarding_url(order: SubscriptionOrder, request=None) -> str:
    """Absolute app-surface onboarding URL for a settled ``order``.

    The route lives only on the app urlconf, so it is reversed against that
    urlconf explicitly  the caller (payment return, free settle) runs on the
    marketing surface, whose active urlconf has no such route.
    """
    token = tokens.sign(_ONBOARD_PURPOSE, {"order": str(order.id)})
    path = reverse("onboarding", args=[token], urlconf="onewooz.urls.app")
    return surface_url("app", path, request)


def _load_order(token: str) -> SubscriptionOrder | None:
    """Resolve an onboarding token to its settled order, or ``None``.

    ``None`` for a tampered/expired token and for an order that is not paid 
    onboarding is only reachable once money (or a free grant) has settled.
    """
    try:
        payload = tokens.unsign(_ONBOARD_PURPOSE, token or "")
    except tokens.TokenError:
        return None
    order = (
        SubscriptionOrder.objects.filter(pk=payload.get("order"))
        .select_related("plan")
        .first()
    )
    if order is None or not order.is_paid:
        return None
    return order


# --------------------------------------------------------------------------
# The onboarding page (app surface)
# --------------------------------------------------------------------------
def _render(request, order, token, *, form=None, errors=None, status=200):
    """Render the onboarding form, prefilled and with any field errors.

    ``errors`` is a field→message map; a ``__all__`` entry surfaces as the
    form-wide banner (the shell renders ``error`` for that), everything else is
    shown inline against its field via ``field_errors``.
    """
    form = form or {}
    errors = errors or {}
    return render(
        request,
        "billing/onboarding.html",
        {
            "page_title": "Complete your account",
            "token": token,
            "order": order,
            "email": order.customer_email,
            "states": INDIAN_STATES,
            "password_rules": PASSWORD_RULES,
            "entity_choices": OnboardingProfile.Entity.choices,
            "form": form,
            "field_errors": errors,
            "error": errors.get("__all__", ""),
        },
        status=status,
    )


@ensure_csrf_cookie
@require_http_methods(["GET", "POST"])
def onboarding(request, token):
    """Create the account + capture the tax profile for a settled order.

    GET   show the form, email locked to the order, phone prefilled.
    POST  validate everything, create the account (or route an existing one to
           sign-in), store the profile, sign the buyer in, land on the dashboard.
    """
    User = get_user_model()

    order = _load_order(token)
    if order is None:
        return render(
            request,
            "billing/onboarding.html",
            {"page_title": "Onboarding", "onboard_error": True},
            status=410,
        )

    email = (order.customer_email or "").strip().lower()

    # An account may already exist for this address  the buyer purchased with an
    # email they had already registered, or they have onboarded before and come
    # back to the link. Never create a second account: send them to sign in,
    # where their (email-bound) subscription is already waiting.
    existing = User.objects.filter(email=email).first()
    if existing is not None:
        return render(
            request,
            "billing/onboarding.html",
            {
                "page_title": "Sign in",
                "order": order,
                "email": email,
                "already_registered": True,
                "sign_in_url": reverse("accounts:sign_in"),
            },
        )

    if request.method == "GET":
        return _render(request, order, token, form={"phone": order.customer_phone})

    # ---- POST: validate -------------------------------------------------
    try:
        check("onboarding.submit", client_identity(request))
    except RateLimitExceeded:
        return _render(
            request, order, token,
            form=request.POST,
            errors={"__all__": "Too many attempts. Please wait a moment and try again."},
            status=429,
        )

    entity = (request.POST.get("entity_type") or "").strip()
    company_name = (request.POST.get("company_name") or "").strip()[:200]
    pan_raw = request.POST.get("pan") or ""
    gstin_raw = request.POST.get("gstin") or ""
    state_raw = request.POST.get("state") or ""
    city = (request.POST.get("city") or "").strip()[:80]
    address = (request.POST.get("address") or "").strip()[:1000]
    phone = re.sub(r"\D+", "", request.POST.get("phone") or "")
    password = request.POST.get("password") or ""
    confirm = request.POST.get("confirm_password") or ""

    valid_entities = {c[0] for c in OnboardingProfile.Entity.choices}
    errors: dict[str, str] = {}

    if entity not in valid_entities:
        errors["entity_type"] = "Choose how you're registering."
        entity = OnboardingProfile.Entity.INDIVIDUAL

    # Only a company is asked for a tax id (its GSTIN). An individual is not
    # asked for one at all  PAN is not collected at onboarding for now.
    pan = gstin = ""
    if entity != OnboardingProfile.Entity.INDIVIDUAL:  # company / gst
        msg = gstin_error(gstin_raw)
        if msg:
            errors["gstin"] = msg
        else:
            gstin = normalise_gstin(gstin_raw)

    if not company_name:
        errors["company_name"] = (
            "Enter your name." if entity == OnboardingProfile.Entity.INDIVIDUAL
            else "Enter your company name."
        )

    state = canonical_state(state_raw)
    if not state:
        errors["state"] = "Choose your state."
    if not city:
        errors["city"] = "Enter your city."
    if not address:
        errors["address"] = "Enter your address."
    if not (10 <= len(phone) <= 15):
        errors["phone"] = "Enter a valid phone number."

    pw_problems = password_problems(password, user=User(email=email, full_name=company_name))
    if pw_problems:
        errors["password"] = pw_problems[0] if len(pw_problems) == 1 else \
            "Your password needs: " + "; ".join(pw_problems) + "."
    elif confirm != password:
        errors["confirm_password"] = "The two passwords don’t match."

    if errors:
        return _render(request, order, token, form=request.POST, errors=errors, status=400)

    # ---- Create the account + profile, then sign in ---------------------
    from accounts import services  # local import: avoid app-load ordering issues
    from accounts.models import LoginActivity

    # Re-check for a race: another tab/window may have created the account
    # between the GET and this POST.
    if User.objects.filter(email=email).exists():
        return render(
            request,
            "billing/onboarding.html",
            {
                "page_title": "Sign in",
                "order": order,
                "email": email,
                "already_registered": True,
                "sign_in_url": reverse("accounts:sign_in"),
            },
        )

    with transaction.atomic():
        user = User.objects.create_user(
            email=email,
            password=password,
            full_name=company_name[:150],
            phone=phone[:20],
            signup_source="billing.onboarding",
        )
        # The checkout OTP already proved control of this mailbox, so the
        # address is verified in the same step the account is created.
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified_at", "updated_at"])

        OnboardingProfile.objects.create(
            user=user,
            order=order,
            entity_type=entity,
            company_name=company_name,
            pan=pan,
            gstin=gstin,
            phone=phone[:20],
            state=state,
            city=city,
            address=address,
        )

    audit.record(
        "billing.onboarding.completed",
        request=request,
        actor=user,
        target=order,
        target_kind="SubscriptionOrder",
        target_label=order.number,
        context={"plan": order.plan_name, "entity": entity, "email": email},
    )

    services.complete_login(request, user, method=LoginActivity.Method.EMAIL_OTP)
    return redirect(reverse("ud:dashboard"))
