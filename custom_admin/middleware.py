"""Gatekeeping for arcane.onewooz.com.

Three independent barriers, deliberately layered so that defeating any one of
them is not enough:

1. **Staff flag.** Only ``is_staff`` accounts may hold an admin session.

2. **Verified admin session.** The session must have been established through
   the admin's own email + password + emailed-OTP flow (see
   ``custom_admin.views``); an ordinary app login does not carry over here.

3. **Idle timeout.** A short window of inactivity ends the session, so an
   unattended screen is not a standing grant.

Everything reaching this surface is written to the audit trail.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from core.hosts import Surface

logger = logging.getLogger("onewooz.audit")

SESSION_PASSKEY_FLAG = "admin_passkey_verified"
# Set once an admin has completed email + password + emailed-OTP sign-in.
SESSION_ADMIN_VERIFIED = "admin_verified"
SESSION_LAST_SEEN = "admin_last_seen"

#: Reachable without an established admin session, or nobody could ever sign
#: in. Compared against ``path_info``, which is the path *after* any
#: development prefix has been moved into SCRIPT_NAME  so these stay correct
#: whether the surface is on arcane.onewooz.com or under /admin locally.
PUBLIC_PATHS = ("/sign-in/", "/passkeys/", "/health/")


class AdminAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if getattr(request, "surface", None) is not Surface.ADMIN:
            return self.get_response(request)

        if any(request.path_info.startswith(prefix) for prefix in PUBLIC_PATHS):
            return self.get_response(request)

        # Resolve against this request's admin URLconf explicitly: middleware runs
        # before Django activates ``request.urlconf`` for bare ``reverse()``, so
        # without this the lookup falls back to ROOT_URLCONF (the marketing
        # surface), where the ``custom_admin`` namespace doesn't exist.
        sign_in = reverse("custom_admin:sign_in", urlconf=getattr(request, "urlconf", None))

        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated or not user.is_staff:
            return redirect(sign_in)

        if not request.session.get(SESSION_ADMIN_VERIFIED):
            # Signed in on the app, but not through the admin's own email +
            # password + emailed-OTP flow  that verification does not carry
            # over to this surface.
            return redirect(sign_in)

        if self._is_idle(request):
            request.session.flush()
            return redirect(f"{sign_in}?timeout=1")

        request.session[SESSION_LAST_SEEN] = timezone.now().isoformat()
        return self.get_response(request)

    # -- barriers ----------------------------------------------------------

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
