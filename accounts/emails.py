"""Transactional email for authentication flows."""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from core import links
from core.hosts import Surface

__all__ = [
    "send_magic_link",
    "send_login_code",
    "send_password_reset",
    "send_new_device_notice",
    "send_site_delete_code",
    "send_account_delete_code",
    "send_business_hub_post",
    "send_ask_answer",
    "send_ask_code",
]

logger = logging.getLogger(__name__)


def _send(to: str, subject: str, template: str, context: dict) -> bool:
    # Email is rendered without a request  often from a Celery worker  so
    # the origin comes from settings rather than the live host. In development
    # that means links carry DEV_PORT; if you run runserver on another port,
    # set ONEWOOZ_DEV_PORT to match or the links in your inbox will not open.
    context = {
        **context,
        "app_origin": links.surface_origin(Surface.APP),
        "marketing_origin": links.surface_origin(Surface.MARKETING),
        "brand": "OneWooz",
    }
    text_body = render_to_string(f"accounts/email/{template}.txt", context)
    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to],
    )
    try:
        html_body = render_to_string(f"accounts/email/{template}.html", context)
    except Exception:
        html_body = ""
    if html_body:
        message.attach_alternative(html_body, "text/html")

    # A transport failure (SMTP refusal, an unreachable provider, or  in an
    # SES sandbox  an unverified recipient) must not surface as a 500. It is
    # logged with its cause and reported to the caller as a boolean, so an
    # authentication view can tell the visitor "we couldn't send that" rather
    # than leaking the recipient address through an unhandled stack trace.
    try:
        message.send(fail_silently=False)
    except Exception:
        logger.exception("Could not deliver %r email", template)
        return False
    return True


def send_magic_link(email: str, url: str, *, minutes: int) -> bool:
    return _send(
        email,
        "Your OneWooz sign-in link",
        "magic_link",
        {"url": url, "minutes": minutes, "email": email},
    )


def send_password_reset(email: str, url: str, *, minutes: int) -> bool:
    """Email a password-reset link to ``email``.

    The link carries a purpose-bound signed token (``auth.password_reset``) and
    works once, within its short window. Sent identically whether or not the
    address has an account  the caller only ever reaches this for a real
    account, and the view above it responds the same either way, so nothing here
    discloses account existence.
    """
    return _send(
        email,
        "Reset your OneWooz password",
        "password_reset",
        {"url": url, "minutes": minutes, "email": email},
    )


def _echo_otp_to_console(email: str, code: str, minutes: int) -> None:
    """Print a one-time code to the terminal instead of emailing it.

    The local-development path: no SMTP, no inbox  the code needed to finish a
    sign-in or sign-up is right in the runserver output. Uses a plain, greppable
    line (``[OTP]``) so it is easy to spot and search for.
    """
    bar = "─" * 60
    print(
        f"\n{bar}\n  [OTP] {email} → {code}   (valid for {minutes} min)\n{bar}\n",
        flush=True,
    )


def send_login_code(email: str, code: str, *, minutes: int) -> bool:
    # On localhost (OTP_CONSOLE_ECHO) log the code to the terminal instead of
    # sending anything. Every sign-in and sign-up code passes through here, so
    # this single branch covers them all. Returns True: the code was delivered
    # to where dev expects it (the terminal), so callers proceed as on a send.
    if getattr(settings, "OTP_CONSOLE_ECHO", False):
        _echo_otp_to_console(email, code, minutes)
        return True
    return _send(
        email,
        f"{code} is your OneWooz sign-in code",
        "login_code",
        {"code": code, "minutes": minutes, "email": email},
    )


def send_site_delete_code(email: str, code: str, *, site_name: str, minutes: int) -> bool:
    """Email the one-time code that confirms deleting a store.

    Deleting a store is destructive and, past the recovery window, final, so it
    is gated behind an emailed code exactly like a sign-in. In local development
    the code is echoed to the console (same branch as sign-in codes) so the flow
    is testable without SMTP.
    """
    if getattr(settings, "OTP_CONSOLE_ECHO", False):
        _echo_otp_to_console(email, code, minutes)
        return True
    return _send(
        email,
        f"{code} is your OneWooz store-deletion code",
        "site_delete_code",
        {
            "code": code, "minutes": minutes, "email": email, "site_name": site_name,
            "retention_days": getattr(settings, "ACCOUNT_DELETION_RETENTION_DAYS", 60),
            "note": (
                "Deleting a store takes it off your dashboard and its storefront offline. "
                "Its data stays on our servers for "
                f"{getattr(settings, 'ACCOUNT_DELETION_RETENTION_DAYS', 60)} days; to get it "
                "back within that time, contact OneWooz support. If you didn't ask to delete "
                "this store, do NOT share this code. Ignore this email and the store stays "
                "exactly as it is."
            ),
        },
    )


def send_account_delete_code(email: str, code: str, *, minutes: int, retention_days: int) -> bool:
    """Email the one-time code that confirms deleting the whole OneWooz account."""
    if getattr(settings, "OTP_CONSOLE_ECHO", False):
        _echo_otp_to_console(email, code, minutes)
        return True
    return _send(
        email,
        f"{code} is your OneWooz account-deletion code",
        "account_delete_code",
        {
            "code": code, "minutes": minutes, "email": email, "retention_days": retention_days,
            "note": (
                f"Your account and store data stay on our servers for {retention_days} days. "
                "Sign in again within that time to restore everything (renew your plan first "
                "if it has expired). If you didn't ask to delete your account, do NOT share "
                "this code. Ignore this email and nothing changes."
            ),
        },
    )


def send_new_device_notice(email: str, *, ip: str, user_agent: str, when) -> bool:
    """Tell the owner about a sign-in from somewhere new.

    Sent after the fact rather than as a challenge: the value is that a
    compromise the user did not initiate becomes visible to them quickly.
    """
    return _send(
        email,
        "New sign-in to your OneWooz account",
        "new_device",
        {"ip": ip, "user_agent": user_agent, "when": when, "email": email},
    )


def send_business_hub_post(
    email: str, *, kind_label: str, kind: str, title: str, preview: str, url: str, name: str = "",
    has_video: bool = False,
) -> bool:
    """A new Business Hub post (News / Help / Important) for this owner."""
    subject = f"{kind_label}: {title}" if kind != "important" else f"Important: {title}"
    return _send(
        email,
        subject[:150],
        "business_hub_post",
        {
            "kind_label": kind_label,
            "kind": kind,
            "title": title,
            "preview": preview,
            "url": url,
            "name": name,
            "has_video": has_video,
        },
    )


def send_ask_code(email: str, code: str, *, minutes: int) -> bool:
    """One-time code that confirms an email to join the Ask OneWooz community."""
    if getattr(settings, "OTP_CONSOLE_ECHO", False):
        _echo_otp_to_console(email, code, minutes)
        return True
    return _send(
        email,
        f"{code} is your Ask OneWooz code",
        "ask_code",
        {"code": code, "minutes": minutes, "email": email},
    )


def send_ask_answer(email: str, *, question: str, preview: str, url: str) -> bool:
    """The OneWooz team answered a question this visitor asked on /ask."""
    return _send(
        email,
        "We answered your question on OneWooz",
        "ask_answer",
        {"question": question, "preview": preview, "url": url},
    )
