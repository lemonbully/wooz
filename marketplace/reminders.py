"""Renewal reminders: email + bell 7 days and 1 day before a licence lapses."""

from __future__ import annotations

import logging
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger("onewooz.marketplace")

WINDOWS = (7, 1)


def send_due() -> int:
    from .models import Licence

    now = timezone.now()
    sent = 0
    for days in WINDOWS:
        start = now + timedelta(days=days - 1)
        end = now + timedelta(days=days)
        for lic in Licence.objects.filter(
            status=Licence.Status.ACTIVE, expires_at__gt=start, expires_at__lte=end
        ).select_related("app")[:1000]:
            key = f"mkt:remind:{lic.pk}:{days}"
            if not cache.add(key, 1, 60 * 60 * 24 * 3):
                continue
            if _remind(lic, days):
                sent += 1
    return sent


def _remind(lic, days: int) -> bool:
    from django.conf import settings
    from django.core.mail import send_mail

    from core.links import surface_url

    url = surface_url("marketplace", f"/apps/{lic.app.slug}/")
    when = "tomorrow" if days == 1 else f"in {days} days"
    subject = f"Your {lic.name_snapshot} licence ends {when}"
    body = (
        f"Hello,\n\nYour OneWooz licence for {lic.name_snapshot} ends {when} "
        f"({lic.expires_at:%d %b %Y}). Renew it any time from {url} to keep using it. "
        "If it lapses, your store moves back to a design or set-up you still have, and nothing is lost.\n\n"
        "OneWooz"
    )
    try:
        send_mail(subject, body, getattr(settings, "DEFAULT_FROM_EMAIL", None), [lic.owner_email])
    except Exception:
        logger.exception("renewal reminder email failed")
    try:
        from accounts import notify
        from accounts.models import User

        user = User.objects.filter(pk=lic.owner_ref).first()
        notify.push(
            user, headline=subject, description=f"Renew from OneWooz Apps to keep using {lic.name_snapshot}.",
            kind="system", level="warning", action_url=url,
        )
    except Exception:
        logger.debug("bell notification skipped", exc_info=True)
    return True
