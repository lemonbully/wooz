"""Admin "impersonate a store owner"  cross-surface, session-overlay based.

A verified admin, from the admin surface, can step into any store owner's
dashboard and operate it exactly as that owner would  seeing and doing
everything the owner can  while a banner keeps the impersonation obvious and one
click ends it.

Each surface has its own host-scoped session cookie (``SESSION_COOKIE_DOMAIN`` is
``None``; the production cookie is ``__Host-`` prefixed), so the admin cannot
simply flip ``request.user`` across domains the way the single-host reference
did. Instead:

1. From the admin surface the admin mints a short-lived, purpose-bound signed
   token (:mod:`core.tokens`) naming the target owner, and is redirected to the
   app surface's ``/impersonate/enter/`` carrying it.
2. :func:`enter` validates the token and records the impersonation in the
   *app-surface* session  deliberately WITHOUT a Django login, so nothing about
   the admin's own session is disturbed (this matters in single-host
   development, where both surfaces share one cookie).
3. :class:`ImpersonationMiddleware` (app surface only) overlays ``request.user``
   with the target owner for the life of that session, so every gate, view and
   template downstream sees the owner, never the admin.

The overlay is confined to the app surface, so impersonation ever only grants
owner access  never admin access, even though the session may also hold the
admin's own login in development.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import get_user
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core import audit, tokens
from core.hosts import Surface
from core.links import surface_url

from .models import User

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

#: Purpose salt for the hand-off token. Its TTL lives in ``settings.TOKEN_TTL``.
TOKEN_PURPOSE = "admin.impersonate"

#: Session keys written on the app surface once impersonation is under way.
SESSION_USER_ID = "impersonate_user_id"
SESSION_ADMIN_ID = "impersonate_admin_id"
SESSION_ADMIN_LABEL = "impersonate_admin_label"
SESSION_STARTED_AT = "impersonate_started_at"
SESSION_RETURN_URL = "impersonate_return_url"

_SESSION_KEYS = (
    SESSION_USER_ID,
    SESSION_ADMIN_ID,
    SESSION_ADMIN_LABEL,
    SESSION_STARTED_AT,
    SESSION_RETURN_URL,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def mint_token(admin_user, target_user, return_url: str = "") -> str:
    """A one-way, short-lived token the admin surface hands to the app surface.

    ``return_url`` is the absolute admin-surface URL "Stop impersonating" sends
    the admin back to (the owners list they started from).
    """
    return tokens.sign(
        TOKEN_PURPOSE,
        {
            "u": str(target_user.pk),
            "a": str(admin_user.pk),
            "ae": admin_user.email,
            "r": return_url or "",
        },
    )


def _admin_return_url(request, candidate) -> str:
    """Where "Stop impersonating" lands: ``candidate`` if it is on the admin
    surface, else the admin home. Never an arbitrary URL, so the exit can't be
    turned into an open redirect."""
    admin_home = surface_url(Surface.ADMIN, "/", request=request)
    if isinstance(candidate, str) and candidate.startswith(admin_home):
        return candidate
    return admin_home


def _clear(session) -> None:
    for key in _SESSION_KEYS:
        session.pop(key, None)
    session.modified = True


def _impersonatable(pk):
    """The active, non-staff owner ``pk`` names, or ``None``.

    Only store owners (``is_staff=False``) can be impersonated: an admin can
    never step into another admin/operator account through this path, and the
    overlay never applies on the admin surface anyway.
    """
    if not pk:
        return None
    return User.objects.filter(pk=pk, is_active=True, is_staff=False).first()


# --------------------------------------------------------------------------
# Middleware  the overlay
# --------------------------------------------------------------------------

class ImpersonationMiddleware:
    """Overlay ``request.user`` with the impersonated owner on the app surface.

    A no-op on every other surface, so the impersonation session can never take
    effect on the admin surface (where the admin's own login lives) or leak into
    a storefront. Runs after authentication and before the dashboard's own gates
    (renewal / staff / plan), so those all evaluate against the owner  the admin
    sees exactly the dashboard that owner would.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.impersonation = None
        if getattr(request, "surface", None) is Surface.APP:
            target = _impersonatable(request.session.get(SESSION_USER_ID))
            if target is not None:
                request.user = target
                request.impersonation = {
                    "target": target,
                    "admin_label": request.session.get(SESSION_ADMIN_LABEL, "an admin"),
                    "started_at": request.session.get(SESSION_STARTED_AT, ""),
                }
            elif request.session.get(SESSION_USER_ID):
                # Stale (owner deactivated, deleted, or made staff): drop it.
                _clear(request.session)
        return self.get_response(request)


# --------------------------------------------------------------------------
# App-surface views  entry + exit
# --------------------------------------------------------------------------

def enter(request):
    """Validate the hand-off token and begin impersonation in this session.

    Reached only via the signed link the admin surface builds; needs no login of
    its own (in production the app surface has no admin session at all). On
    success the session carries the impersonation and the middleware takes over.
    """
    if getattr(request, "surface", None) is not Surface.APP:
        raise Http404

    try:
        payload = tokens.unsign(TOKEN_PURPOSE, request.GET.get("t") or "")
    except tokens.TokenError:
        messages.error(request, "That impersonation link is invalid or has expired.")
        return redirect(surface_url(Surface.ADMIN, "/", request=request))

    target = _impersonatable(payload.get("u"))
    if target is None:
        messages.error(request, "That account can no longer be impersonated.")
        return redirect(surface_url(Surface.ADMIN, "/", request=request))

    admin = User.objects.filter(pk=payload.get("a"), is_staff=True).first()

    request.session[SESSION_USER_ID] = str(target.pk)
    request.session[SESSION_ADMIN_ID] = str(payload.get("a") or "")
    request.session[SESSION_ADMIN_LABEL] = payload.get("ae") or (admin.email if admin else "an admin")
    request.session[SESSION_STARTED_AT] = timezone.now().isoformat()
    request.session[SESSION_RETURN_URL] = _admin_return_url(request, payload.get("r"))
    request.session.modified = True

    audit.record(
        "admin.impersonation.started",
        request=request,
        actor=admin,
        actor_kind="admin",
        target=target,
        target_kind="User",
        target_label=target.email,
        severity=audit.AuditEvent.Severity.NOTICE,
        context={"admin": request.session[SESSION_ADMIN_LABEL]},
    )
    return redirect("ud:dashboard")


@require_POST
def stop(request):
    """End impersonation and send the admin back to the admin surface.

    Not a redirect: the admin surface is another origin, and the CSP's
    ``form-action 'self'`` makes the browser silently refuse a form POST that
    302s cross-origin  which is why the button used to appear to do nothing.
    Instead the session is cleared here and a small page navigates onward
    (see ``accounts/impersonation_ended.html``).
    """
    imp = getattr(request, "impersonation", None)
    admin_url = _admin_return_url(request, request.session.get(SESSION_RETURN_URL))
    target_label = ""
    if imp is not None:
        target = imp["target"]
        target_label = target.full_name or target.email
        admin = User.objects.filter(pk=request.session.get(SESSION_ADMIN_ID) or None, is_staff=True).first()
        audit.record(
            "admin.impersonation.ended",
            request=request,
            actor=admin,
            actor_kind="admin",
            target=target,
            target_kind="User",
            target_label=target.email,
            severity=audit.AuditEvent.Severity.NOTICE,
            context={"admin": imp.get("admin_label")},
        )
    _clear(request.session)
    # The overlay is over for the rest of this request too, so nothing rendered
    # below (the banner, the shell) still treats the admin as the owner.
    request.impersonation = None
    request.user = get_user(request)

    response = render(
        request,
        "accounts/impersonation_ended.html",
        {"admin_url": admin_url, "target_label": target_label},
    )
    response["Cache-Control"] = "no-store"
    return response
