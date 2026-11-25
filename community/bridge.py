"""Signed-in state across onewooz.com and app.onewooz.com.

Each surface has its own host-only session cookie (``__Host-ow_sid`` in
production), so onewooz.com cannot see that a visitor is signed in to their
dashboard. Two small, separate mechanisms bridge that, without ever sharing a
session or a credential:

1. **Signed-in hint** (navbar). :class:`SignedInHintMiddleware` runs on the
   app surface and keeps a cookie ``ow_signed_in=1`` in step with the real
   login: set while signed in, deleted once signed out or expired. It is
   scoped to the parent domain so onewooz.com can read it, and carries no
   identity at all: it only decides whether the marketing navbar says
   "Dashboard" or "Sign in". Forging it gains nothing; the Dashboard link
   still goes through the dashboard's own login.

2. **Ask OneWooz account link** (no separate "Join"). A signed-in OneWooz
   user who opens Ask is linked to their community member automatically:

   * :class:`AskAccountLinkMiddleware` (marketing surface, Ask pages, GET)
     sees the hint, stores a random ``state`` in the visitor's onewooz.com
     session and sends them once to ``app.onewooz.com/ask-link/``.
   * :func:`app_link` (app surface) mints a **single-use, 2-minute,
     purpose-bound** token (:mod:`core.tokens`, purpose ``ask.link``) holding
     the user id, that ``state``, a one-time id and a browser fingerprint,
     and sends them back to ``onewooz.com/ask/link/``. Signed out there? It
     just sends them back with no token.
   * :func:`redeem` (marketing surface) accepts the token only if the
     signature, age, one-time id, browser and ``state`` (bound to *this*
     browser's own session, so a token can't be planted in someone else's
     browser) all match, then signs the email's community member in.

   On localhost both surfaces share one session, so the user is linked
   directly without the round trip. Signing out of Ask stops the automatic
   link for the rest of that session.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import get_script_prefix, reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from core import tokens
from core.hosts import Surface
from core.links import surface_url

HINT_COOKIE = "ow_signed_in"
TOKEN_PURPOSE = "ask.link"

SESSION_STATE = "ask_link_state"
SESSION_TRIED = "ask_link_tried"
SESSION_OFF = "ask_link_off"
RETRY_SECONDS = 30 * 60

#: Ask pages that must never trigger the hop (the hop itself, sign out, and
#: the small JSON/poll endpoints that pages call in the background).
_SKIP_URL_NAMES = {"link", "leave", "search_json", "instant_status", "live_columns", "desk_suggest"}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _surface(request) -> str:
    return str(getattr(request, "surface", "") or "")


def _ua(request) -> str:
    return hashlib.sha256(request.META.get("HTTP_USER_AGENT", "")[:400].encode()).hexdigest()[:24]


def _path_on(urlconf: str, name: str) -> str:
    """A path for ``name`` in another surface's URLconf, without this
    request's dev-mode script prefix."""
    path = reverse(name, urlconf=urlconf)
    prefix = get_script_prefix()
    if prefix not in ("", "/") and path.startswith(prefix):
        path = "/" + path[len(prefix):]
    return path


def _safe_path(value: str) -> str:
    value = (value or "").strip()
    if value.startswith("/") and not value.startswith("//") and "\\" not in value and len(value) <= 500:
        return value
    return ""


def hint_domain() -> str | None:
    """The parent domain the hint is shared on (None = this host only)."""
    explicit = getattr(settings, "SIGNED_IN_HINT_DOMAIN", None)
    if explicit is not None:
        return explicit or None
    if settings.DEBUG:
        return None
    return settings.MARKETING_HOST  # e.g. "onewooz.com": readable by onewooz.com and app.onewooz.com


def signed_in(request) -> bool:
    """Whether the navbar should treat this visitor as signed in."""
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return True  # same-host session (local development)
    return request.COOKIES.get(HINT_COOKIE) == "1"


# --------------------------------------------------------------------------
# 1. Signed-in hint (set on the app surface)
# --------------------------------------------------------------------------
class SignedInHintMiddleware:
    """Keeps ``ow_signed_in`` in step with the dashboard login. App surface only."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if _surface(request) == Surface.MARKETING and response.get("Content-Type", "").startswith("text/html"):
            # The navbar now depends on the hint cookie: never let a shared
            # cache hand one visitor's "Dashboard" page to another (the edge
            # guard in core.middleware turns Vary: Cookie into private).
            from django.utils.cache import patch_vary_headers

            patch_vary_headers(response, ("Cookie",))
            return response
        if _surface(request) != Surface.APP:
            return response
        user = getattr(request, "user", None)
        authed = bool(user is not None and user.is_authenticated)
        has = request.COOKIES.get(HINT_COOKIE) == "1"
        domain = hint_domain()
        if authed and not has:
            response.set_cookie(
                HINT_COOKIE, "1", max_age=settings.SESSION_COOKIE_AGE, domain=domain, path="/",
                secure=not settings.DEBUG, httponly=True, samesite="Lax",
            )
        elif not authed and has:
            response.delete_cookie(HINT_COOKIE, domain=domain, path="/", samesite="Lax")
        return response


# --------------------------------------------------------------------------
# 2. Ask OneWooz account link
# --------------------------------------------------------------------------
def _link_member(request, user) -> None:
    from . import services

    if not user.is_active or not user.email:
        return
    # accounts.User has a ``full_name`` field, not Django's get_full_name().
    name = (getattr(user, "full_name", "") or "").strip() or user.email.split("@", 1)[0]
    member = services.get_or_create_member(user.email, name)
    services.sign_in(request, member)


class AskAccountLinkMiddleware:
    """On Ask pages, links a signed-in OneWooz user to their community member
    (directly on one host, or with one hop through the dashboard)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = getattr(request, "resolver_match", None)
        if (match is None or match.namespace != "ask" or match.url_name in _SKIP_URL_NAMES
                or request.method != "GET" or _surface(request) != Surface.MARKETING):
            return None
        if "application/json" in request.headers.get("accept", "") or request.headers.get("x-requested-with"):
            return None
        session = getattr(request, "session", None)
        if session is None or session.get(SESSION_OFF):
            return None
        from . import services

        if services.current_member(request) is not None:
            return None
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            _link_member(request, user)  # same session (local development)
            return None
        if request.COOKIES.get(HINT_COOKIE) != "1":
            return None
        tried = session.get(SESSION_TRIED) or 0
        # Opening Join is an explicit "let me in", so it always retries.
        if match.url_name != "join" and tried and time.time() - float(tried) < RETRY_SECONDS:
            return None
        state = secrets.token_urlsafe(24)
        session[SESSION_STATE] = state
        session[SESSION_TRIED] = time.time()
        query = urlencode({"state": state, "next": request.get_full_path()})
        return HttpResponseRedirect(surface_url(Surface.APP, _path_on("onewooz.urls.app", "ask_link"), request) + "?" + query)


@never_cache
@require_GET
def app_link(request):
    """App surface: hand a signed-in user back to Ask with a one-time token."""
    from core import ratelimit

    state = (request.GET.get("state") or "")[:64]
    nxt = _safe_path(request.GET.get("next") or "")
    params = {"state": state, **({"next": nxt} if nxt else {})}
    user = request.user
    if (user.is_authenticated and user.is_active and state
            and ratelimit.consume("ask.link", str(user.pk)).allowed):
        if not request.session.session_key:
            request.session.save()
        params["t"] = tokens.sign(TOKEN_PURPOSE, {
            "u": str(user.pk), "s": state, "j": secrets.token_urlsafe(12), "a": _ua(request),
        })
    back = surface_url(Surface.MARKETING, _path_on("onewooz.urls.marketing", "ask:link"), request)
    response = HttpResponseRedirect(back + "?" + urlencode(params))
    response["Referrer-Policy"] = "no-referrer"
    return response


@never_cache
@require_GET
def redeem(request):
    """Marketing surface: accept the token and sign the member in."""
    from accounts.models import User

    from .views import _safe_next

    nxt = _safe_next(request, request.GET.get("next") or "")
    expected = request.session.pop(SESSION_STATE, None)
    token = request.GET.get("t") or ""
    if token and expected and secrets.compare_digest(str(expected), str(request.GET.get("state") or "")):
        try:
            payload = tokens.unsign(TOKEN_PURPOSE, token)
        except tokens.TokenError:
            payload = None
        if (payload and payload.get("s") == expected and payload.get("a") == _ua(request)
                and payload.get("j") and cache.add(f"ask:link:jti:{payload['j']}", 1, 600)):
            user = User.objects.filter(pk=payload.get("u"), is_active=True).first()
            if user is not None:
                _link_member(request, user)
    response = redirect(nxt)
    response["Referrer-Policy"] = "no-referrer"
    return response


# --------------------------------------------------------------------------
# Template context
# --------------------------------------------------------------------------
def nav_context(request) -> dict:
    """``ow_signed_in`` for the marketing navbar (Dashboard vs Sign in)."""
    return {"ow_signed_in": signed_in(request)}
