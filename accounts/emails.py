"""Transactional email for authentication flows."""

from __future__ import annotations

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from core import links
from core.hosts import Surface

__all__ = ["send_magic_link", "send_login_code", "send_new_device_notice"]


def _send(to: str, subject: str, template: str, context: dict) -> None:
    # Email is rendered without a request — often from a Celery worker — so
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
    message.send(fail_silently=False)


def send_magic_link(email: str, url: str, *, minutes: int) -> None:
    _send(
        email,
        "Your OneWooz sign-in link",
        "magic_link",
        {"url": url, "minutes": minutes, "email": email},
    )


def send_login_code(email: str, code: str, *, minutes: int) -> None:
    _send(
        email,
        f"{code} is your OneWooz sign-in code",
        "login_code",
        {"code": code, "minutes": minutes, "email": email},
    )


def send_new_device_notice(email: str, *, ip: str, user_agent: str, when) -> None:
    """Tell the owner about a sign-in from somewhere new.

    Sent after the fact rather than as a challenge: the value is that a
    compromise the user did not initiate becomes visible to them quickly.
    """
    _send(
        email,
        "New sign-in to your OneWooz account",
        "new_device",
        {"ip": ip, "user_agent": user_agent, "when": when, "email": email},
    )
