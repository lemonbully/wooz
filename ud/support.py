"""Support tickets raised from the Business Hub.

The owner side (raise + list) lives in :func:`ud.views.business_hub`; staff
work tickets in the admin (``custom_admin.admin.SupportTicketAdmin``). This
module holds the side effects: telling the OneWooz team a ticket arrived, and
telling the owner when staff reply. Everything here is best-effort  a mail or
bell failure never loses a ticket.
"""

from __future__ import annotations

import logging
from email.utils import parseaddr

from django.conf import settings

logger = logging.getLogger("onewooz.audit")


def team_address() -> str:
    """Where new tickets are emailed: ``SUPPORT_TICKETS_EMAIL`` if set, else the
    platform's own sending address."""
    configured = (getattr(settings, "SUPPORT_TICKETS_EMAIL", "") or "").strip()
    if configured:
        return configured
    return parseaddr(getattr(settings, "DEFAULT_FROM_EMAIL", "") or "")[1]


def hub_support_url(ticket=None) -> str:
    """Absolute link to the Business Hub with the "Your tickets" popup open,
    on ``ticket``'s conversation when one is given."""
    from .hub import hub_url

    url = hub_url() + "?tickets=1"
    if ticket is not None:
        url += f"&ticket={ticket.thread_root.pk}"
    return url


def email_team(ticket_id) -> bool:
    """Email the OneWooz team about a new ticket (attachment included)."""
    from django.core.mail import EmailMessage

    from .models import SupportTicket

    ticket = (
        SupportTicket.objects.select_related("user", "site", "parent")
        .filter(pk=ticket_id)
        .first()
    )
    to = team_address()
    if ticket is None or not to:
        return False
    lines = [
        f"New support ticket {ticket.reference}",
        *(
            [f"Follow-up on {ticket.parent.reference}: {ticket.parent.subject}"]
            if ticket.parent_id
            else []
        ),
        "",
        f"From: {ticket.user.full_name or ticket.user.email} <{ticket.user.email}>",
        f"Store: {ticket.site.name if ticket.site else 'Not about a specific store'}",
        f"Topic: {ticket.get_category_display()}",
        f"Priority: {ticket.get_priority_display()}",
        f"Subject: {ticket.subject}",
        "",
        ticket.message,
        "",
        "Reply to the owner from the admin (Support tickets); they will see it on their Business Hub.",
    ]
    msg = EmailMessage(
        subject=f"[{ticket.get_priority_display()}] {ticket.reference}: {ticket.subject}"[:200],
        body="\n".join(lines),
        to=[to],
        reply_to=[ticket.user.email] if ticket.user.email else None,
    )
    if ticket.attachment:
        try:
            ticket.attachment.open("rb")
            try:
                msg.attach(
                    ticket.attachment_name or ticket.attachment.name.rsplit("/", 1)[-1],
                    ticket.attachment.read(),
                )
            finally:
                ticket.attachment.close()
        except Exception:
            logger.exception("support ticket attachment unreadable ticket=%s", ticket.pk)
    msg.send(fail_silently=False)
    return True


def queue_team_email(ticket_id) -> None:
    """Send :func:`email_team` in the background (inline if no worker)."""
    try:
        from .tasks import email_support_ticket

        email_support_ticket.delay(str(ticket_id))
    except Exception:
        try:
            email_team(ticket_id)
        except Exception:
            logger.exception("support ticket email failed ticket=%s", ticket_id)


def notify_owner_of_reply(ticket) -> None:
    """Bell notification for the owner when staff reply to (or close) a ticket."""
    try:
        from accounts import notify

        notify.push(
            ticket.user,
            headline=f"Reply on your ticket {ticket.reference}",
            description=(ticket.reply or "")[:280] or f"Status: {ticket.get_status_display()}",
            kind="admin",
            level="info",
            action_url=hub_support_url(ticket),
            site=ticket.site,
        )
    except Exception:
        logger.exception("support ticket reply notification failed ticket=%s", ticket.pk)
