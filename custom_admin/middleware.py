"""Gatekeeping for admin.onewooz.com.

Four independent barriers, deliberately layered so that defeating any one of
them is not enough:

1. **Network.** Requests from outside the staff allowlist are refused before
   authentication is even attempted, so a stolen credential is useless from an
   arbitrary address. An empty allowlist is treated as a misconfiguration and
   refuses everything — failing closed, because an admin surface that silently
   opens to the internet on a missing environment variable is worse than one
   that is down.

2. **Staff flag.** Only ``is_staff`` accounts may hold an admin session.

3. **Passkey.** The session must have been established by a WebAuthn
   assertion. A password, a TOTP code and a magic link are all phishable or
   relayable; a passkey assertion is bound by the browser to the origin that
   requested it.

4. **Idle timeout.** A short window of inactivity ends the session, so an
   unattended screen is not a standing grant.

Everything reaching this surface is written to the audit trail.
"""

from __future__ import annotations

import ipaddress
import logging

from django.conf import settings
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from core.hosts import Surface

logger = logging.getLogger("onewooz.audit")

SESSION_PASSKEY_FLAG = "admin_passkey_verified"
SESSION_LAST_SEEN = "admin_last_seen"

#: Reachable without an established admin session, or nobody could ever sign
#: in. Compared against ``path_info``, which is the path *after* any
#: development prefix has been moved into SCRIPT_NAME — so these stay correct
#: whether the surface is on admin.onewooz.com or under /admin locally.
PUBLIC_PATHS = ("/sign-in/", "/passkeys/", "/health/")


class AdminAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if getattr(request, "surface", None) is not Surface.ADMIN:
            return self.get_response(request)

        if not self._ip_allowed(request):
            logger.warning(
                "admin surface refused: ip=%s path=%s",
                request.META.get("REMOTE_ADDR"),
                request.path,
            )
            # A blank 403 rather than a helpful message: an attacker probing
            # for the admin host learns only that something refused them.
            return HttpResponseForbidden("Forbidden")

        if any(request.path_info.startswith(prefix) for prefix in PUBLIC_PATHS):
            return self.get_response(request)

        sign_in = reverse("custom_admin:sign_in")

        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated or not user.is_staff:
            return redirect(sign_in)

        if not request.session.get(SESSION_PASSKEY_FLAG):
            # Signed in, but not with a passkey. Password and TOTP sessions do
            # not carry over to this surface.
            return redirect(sign_in)

        if self._is_idle(request):
            request.session.flush()
            return redirect(f"{sign_in}?timeout=1")

        request.session[SESSION_LAST_SEEN] = timezone.now().isoformat()
        return self.get_response(request)

    # -- barriers ----------------------------------------------------------

    def _ip_allowed(self, request) -> bool:
        allowlist = getattr(settings, "ADMIN_IP_ALLOWLIST", None)

        # Absent entirely (development) means the barrier is not configured and
        # is skipped. Present but empty in production means somebody removed
        # the entries, which must not silently open the door.
        if allowlist is None:
            return settings.DEBUG
        if not allowlist:
            logger.error("ADMIN_IP_ALLOWLIST is empty; refusing all admin traffic")
            return False

        client = self._client_ip(request)
        if not client:
            return False

        try:
            address = ipaddress.ip_address(client)
        except ValueError:
            return False

        for entry in allowlist:
            try:
                if address in ipaddress.ip_network(entry, strict=False):
                    return True
            except ValueError:
                logger.error("ADMIN_IP_ALLOWLIST contains an invalid entry: %r", entry)
        return False

    @staticmethod
    def _client_ip(request) -> str:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "")

    @staticmethod
    def _is_idle(request) -> bool:
        last_seen = request.session.get(SESSION_LAST_SEEN)
        if not last_seen:
            return False
        try:
            elapsed = (timezone.now() - timezone.datetime.fromisoformat(last_seen)).total_seconds()
        except (TypeError, ValueError):
            return True
        return elapsed > settings.ADMIN_SESSION_IDLE_TIMEOUT
