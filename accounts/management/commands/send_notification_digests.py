"""Email a daily activity digest to owners who chose the daily cadence.

Run on a schedule (cron / Celery beat), e.g. once a day:

    python manage.py send_notification_digests

For each active owner whose notification cadence is *daily*, it gathers the last
day's notification-worthy store activity across their stores (respecting each
per-category switch) and sends a single summary email to their notification
address. Owners on *instant* or *off* are skipped  instant is delivered inline
as events happen, off is muted.
"""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts import notifications
from accounts.emails import _send
from accounts.models import User
from commerce.activity import describe
from core.models import AuditEvent
from tenancy.models import Site


class Command(BaseCommand):
    help = "Send the daily activity digest to owners on the daily notification cadence."

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours", type=int, default=24,
            help="How far back to include activity (default: 24).",
        )

    def handle(self, *args, **options):
        since = timezone.now() - timedelta(hours=options["hours"])
        actions = list(notifications.ACTION_CATEGORY)
        owners = User.objects.filter(
            is_active=True, notification_digest=User.Digest.DAILY, account_deleted_at__isnull=True
        )

        sent = 0
        for user in owners:
            sites = {
                str(s.id): s
                for s in Site.objects.filter(owner=user, is_deleted=False)
            }
            if not sites:
                continue

            events = AuditEvent.objects.filter(
                site_id_ref__in=list(sites),
                action__in=actions,
                created_at__gte=since,
            ).order_by("-created_at")

            grouped: dict[str, list[str]] = {}
            for event in events:
                category = notifications.category_for(event.action)
                if not category or not user.wants(category):
                    continue
                _icon, phrase = describe(event.action)
                line = f"{phrase}  {event.target_label}" if event.target_label else phrase
                grouped.setdefault(event.site_id_ref, []).append(line)

            groups = [
                {"site_name": sites[sid].name, "lines": lines}
                for sid, lines in grouped.items() if lines
            ]
            total = sum(len(g["lines"]) for g in groups)
            to = user.effective_notification_email
            if total == 0 or not to:
                continue

            ok = _send(
                to,
                f"Your OneWooz daily digest  {total} update{'s' if total != 1 else ''}",
                "notification/digest",
                {
                    "groups": groups,
                    "total": total,
                    "date": timezone.localtime(timezone.now()).strftime("%d %b %Y"),
                    "recipient": to,
                },
            )
            if ok:
                sent += 1

        self.stdout.write(self.style.SUCCESS(f"Daily digests sent: {sent}"))
