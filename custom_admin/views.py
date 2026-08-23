from __future__ import annotations

from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie


# This page renders no form, so nothing would set the CSRF cookie — and the
# passkey ceremony that follows is a POST that needs the token. Without this
# the only way into the admin surface returns 403.
@ensure_csrf_cookie
def sign_in(request):
    """Passkey-only sign-in for staff.

    Deliberately offers no password field and no email field: the assertion is
    discoverable, so the authenticator presents whichever staff credential it
    holds for this origin.
    """
    return render(request, "custom_admin/sign_in.html", {"timed_out": "timeout" in request.GET})


def overview(request):
    return render(request, "custom_admin/overview.html")
