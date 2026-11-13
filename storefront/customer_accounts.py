"""Storefront customer accounts  a light, email-OTP shopper session.

Storefront shoppers are **not** OneWooz accounts (see ``commerce.models.Customer``):
a customer is just an email within one store. So "signing in" here is deliberately
minimal  prove control of an email with a one-time code, then remember it in the
session as ``ow_customer_email``. From that we can show a shopper their own orders
and wishlist. There is no password, no cross-site identity, and nothing here can
reach the dashboard session (a different, public-suffix-separated origin).

The code is stored only as a salted hash in the visitor's own session, never in the
database, and expires quickly. Delivery, rate limiting and enumeration-resistance
mirror the owner auth: the send/verify responses never reveal whether an email has
placed an order.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone

from core import ratelimit
from core.mail import store_mail_connection

SESSION_CUSTOMER = "ow_customer_email"
_OTP_SESSION = "ow_customer_otp"
OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5


def current_customer_email(request) -> str:
    return (request.session.get(SESSION_CUSTOMER) or "").strip().lower()


def is_signed_in(request) -> bool:
    return bool(current_customer_email(request))


def sign_out(request) -> None:
    request.session.pop(SESSION_CUSTOMER, None)
    request.session.pop(_OTP_SESSION, None)
    # Signing out forgets the remembered checkout details too, so the next
    # person on this browser doesn't see this shopper's address.
    request.session.pop("ow_checkout_last", None)
    request.session.modified = True


def _hash_code(email: str, code: str) -> str:
    key = (settings.SECRET_KEY or "").encode()
    msg = f"{email.strip().lower()}:{code}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def _valid_email(email: str) -> bool:
    email = (email or "").strip()
    return "@" in email and "." in email.split("@")[-1] and len(email) <= 254


def start_challenge(request, site, email: str) -> bool:
    """Mint a one-time code, stash its hash in the session, and email it.

    Always returns ``True`` for a well-formed address regardless of delivery, so
    the caller cannot use the response to probe which emails exist. A malformed
    address returns ``False`` so the form can nudge the visitor.
    """
    email = (email or "").strip().lower()
    if not _valid_email(email):
        return False

    # Rate-limit by email and by client so a code can't be used to spam inboxes.
    if not ratelimit.consume("otp.send.email", email).allowed:
        return True
    if not ratelimit.consume("otp.send.ip", ratelimit.client_identity(request)).allowed:
        return True

    code = f"{secrets.randbelow(1_000_000):06d}"
    request.session[_OTP_SESSION] = {
        "email": email,
        "hash": _hash_code(email, code),
        "expires": (timezone.now() + timedelta(minutes=OTP_TTL_MINUTES)).isoformat(),
        "attempts": 0,
    }
    request.session.modified = True
    _send_code(site, email, code)
    return True


def verify_challenge(request, email: str, code: str) -> bool:
    """Check a submitted code against the session challenge; on success, sign in."""
    email = (email or "").strip().lower()
    code = (code or "").strip()
    if not ratelimit.consume("otp.verify", ratelimit.client_identity(request)).allowed:
        return False

    data = request.session.get(_OTP_SESSION) or {}
    if not data or data.get("email") != email:
        return False

    try:
        expires = datetime.fromisoformat(data["expires"])
    except (KeyError, ValueError):
        request.session.pop(_OTP_SESSION, None)
        return False
    if timezone.now() > expires:
        request.session.pop(_OTP_SESSION, None)
        request.session.modified = True
        return False

    attempts = int(data.get("attempts") or 0) + 1
    if attempts > OTP_MAX_ATTEMPTS:
        request.session.pop(_OTP_SESSION, None)
        request.session.modified = True
        return False
    data["attempts"] = attempts
    request.session[_OTP_SESSION] = data

    if not hmac.compare_digest(data.get("hash", ""), _hash_code(email, code)):
        request.session.modified = True
        return False

    # Success  promote to a signed-in customer session and burn the challenge.
    request.session.pop(_OTP_SESSION, None)
    request.session[SESSION_CUSTOMER] = email
    request.session.modified = True
    return True


def _send_code(site, email: str, code: str) -> None:
    store = site.name or "the store"
    subject = f"{code} is your {store} sign-in code"
    body = (
        f"Hi,\n\nYour one-time sign-in code for {store} is: {code}\n\n"
        f"It expires in {OTP_TTL_MINUTES} minutes. If you didn't request it, "
        f"you can ignore this email.\n\n {store}"
    )
    # The code comes from the store's own branded address on the shared
    # SES-verified domain (e.g. jackandjones@via.onewooz.com), so a shopper sees
    # it as "from the store", not from OneWooz. Replies reach the owner's
    # support inbox when one is set.
    reply_to = [site.support_email] if site.support_email else None
    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=body,
            from_email=site.customer_from_header,
            to=[email],
            reply_to=reply_to,
            connection=store_mail_connection(fail_silently=True),
        )
        # Branded HTML alternative in the shared OneWooz email design. Best-effort:
        # a render failure still leaves the plain-text code to send.
        try:
            from django.template.loader import render_to_string

            html = render_to_string(
                "email/store/login_code.html",
                {
                    "brand": store,
                    "code": code,
                    "minutes": OTP_TTL_MINUTES,
                    "reply_to": bool(site.support_email),
                    "powered_by": True,
                },
            )
            msg.attach_alternative(html, "text/html")
        except Exception:
            pass
        msg.send(fail_silently=True)
    except Exception:
        # Delivery is best-effort; a transport failure must never 500 the page
        # or reveal (through an error) that the address was accepted.
        pass


# --------------------------------------------------------------------------
# Action codes: confirm a sensitive account change with a one-time code
# --------------------------------------------------------------------------
# Separate from the sign-in challenge (its own session slot, purpose bound into
# the hash), so a sign-in code can never confirm an account deletion or an
# email change, and vice versa.
_ACTION_SESSION = "ow_customer_action_otp"
ACTION_EMAIL_CHANGE = "email-change"
ACTION_DELETE_ACCOUNT = "delete-account"
_ACTIONS = {ACTION_EMAIL_CHANGE, ACTION_DELETE_ACCOUNT}


def start_action_challenge(request, site, email: str, purpose: str) -> bool:
    """Email ``email`` a code that confirms ``purpose``. False only for a
    malformed address or unknown purpose; rate-limited like sign-in codes."""
    email = (email or "").strip().lower()
    if purpose not in _ACTIONS or not _valid_email(email):
        return False
    if not ratelimit.consume("otp.send.email", email).allowed:
        return True
    if not ratelimit.consume("otp.send.ip", ratelimit.client_identity(request)).allowed:
        return True
    code = f"{secrets.randbelow(1_000_000):06d}"
    request.session[_ACTION_SESSION] = {
        "purpose": purpose,
        "email": email,
        "hash": _hash_code(email, f"{purpose}:{code}"),
        "expires": (timezone.now() + timedelta(minutes=OTP_TTL_MINUTES)).isoformat(),
        "attempts": 0,
    }
    request.session.modified = True
    _send_action_code(site, email, code, purpose)
    return True


def verify_action_challenge(request, purpose: str, code: str) -> str:
    """The email the code was sent to when ``code`` confirms ``purpose``, else ""."""
    code = (code or "").strip()
    if not ratelimit.consume("otp.verify", ratelimit.client_identity(request)).allowed:
        return ""
    data = request.session.get(_ACTION_SESSION) or {}
    if not data or data.get("purpose") != purpose:
        return ""
    try:
        expires = datetime.fromisoformat(data["expires"])
    except (KeyError, ValueError):
        request.session.pop(_ACTION_SESSION, None)
        request.session.modified = True
        return ""
    if timezone.now() > expires:
        request.session.pop(_ACTION_SESSION, None)
        request.session.modified = True
        return ""
    attempts = int(data.get("attempts") or 0) + 1
    if attempts > OTP_MAX_ATTEMPTS:
        request.session.pop(_ACTION_SESSION, None)
        request.session.modified = True
        return ""
    data["attempts"] = attempts
    request.session[_ACTION_SESSION] = data
    request.session.modified = True
    email = data.get("email") or ""
    if not hmac.compare_digest(data.get("hash", ""), _hash_code(email, f"{purpose}:{code}")):
        return ""
    request.session.pop(_ACTION_SESSION, None)
    return email


def _send_action_code(site, email: str, code: str, purpose: str) -> None:
    store = site.name or "the store"
    if purpose == ACTION_DELETE_ACCOUNT:
        subject = f"{code} is your code to delete your {store} account"
        title = "Confirm account deletion"
        message = (
            f"Use this code to confirm that you want to delete your {store} account: {code}\n\n"
            f"It expires in {OTP_TTL_MINUTES} minutes. If you didn't ask to delete your "
            f"account, ignore this email and nothing will change."
        )
    else:
        subject = f"{code} is your {store} verification code"
        title = "Confirm your new email"
        message = (
            f"Use this code to confirm {email} as the email on your {store} account: {code}\n\n"
            f"It expires in {OTP_TTL_MINUTES} minutes. If you didn't request this, "
            f"you can ignore this email."
        )
    reply_to = [site.support_email] if site.support_email else None
    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=f"Hi,\n\n{message}\n\n{store}",
            from_email=site.customer_from_header,
            to=[email],
            reply_to=reply_to,
            connection=store_mail_connection(fail_silently=True),
        )
        try:
            from django.template.loader import render_to_string

            html = render_to_string(
                "email/store/message.html",
                {
                    "brand": store,
                    "title": title,
                    "message": message,
                    "link": "",
                    "reply_to": bool(site.support_email),
                    "powered_by": True,
                },
            )
            msg.attach_alternative(html, "text/html")
        except Exception:
            pass
        msg.send(fail_silently=True)
    except Exception:
        pass
