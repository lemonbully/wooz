"""Identity across the marketplace and the dashboard.

One identity, two sessions:

* ``marketplace.onewooz.com`` has **no sign-in form**. "Sign in" sends the
  visitor to ``app.onewooz.com`` (password + TOTP, passkey, magic link / OTP,
  exactly as today), which mints a **single-use, 60-second, purpose-salted**
  hand-off token bound to the user, the dashboard session and the browser.
  The marketplace redeems it once and opens its **own** host-only session that
  can only browse and show the owner's library: it cannot read orders,
  customers or store content and cannot install anything by itself.
* Installing, buying and uninstalling happen on ``app.onewooz.com`` behind a
  **fresh re-authentication** (:func:`reauth_fresh`). Credentials and payments
  are therefore only ever entered on the dashboard origin.
* Signing out of the dashboard revokes every marketplace session it handed off
  (``user_logged_out`` receiver in :mod:`marketplace.signals`).
"""

from __future__ import annotations

import hashlib
import secrets
import time

from django.core.cache import cache

from core import tokens

HANDOFF_PURPOSE = "marketplace.handoff"
SESSION_KEY = "mkt_owner"
REAUTH_KEY = "mkt_reauth"
ABSOLUTE_SECONDS = 8 * 60 * 60
IDLE_SECONDS = 30 * 60


def _h(value: str) -> str:
    return hashlib.sha256((value or "").encode()).hexdigest()[:24]


def _ua(request) -> str:
    return _h(request.META.get("HTTP_USER_AGENT", "")[:400])


def dashboard_session_id(request) -> str:
    key = getattr(getattr(request, "session", None), "session_key", "") or ""
    return _h(key) if key else ""


# --------------------------------------------------------------------------
# Hand-off (minted on the app surface, redeemed on the marketplace surface)
# --------------------------------------------------------------------------
def mint_handoff(request) -> str:
    user = request.user
    if not request.session.session_key:
        request.session.save()
    return tokens.sign(
        HANDOFF_PURPOSE,
        {"u": str(user.pk), "j": secrets.token_urlsafe(12), "d": dashboard_session_id(request), "a": _ua(request)},
    )


class HandoffError(Exception):
    pass


def redeem_handoff(request, token: str) -> dict:
    from accounts.models import User

    try:
        payload = tokens.unsign(HANDOFF_PURPOSE, token)
    except tokens.TokenError as exc:
        raise HandoffError("expired") from exc
    jti = str(payload.get("j") or "")
    if not jti or not cache.add(f"mkt:jti:{jti}", 1, 300):
        raise HandoffError("used")
    if payload.get("a") != _ua(request):
        raise HandoffError("browser")
    dsid = str(payload.get("d") or "")
    if dsid and cache.get(f"mkt:revoked:{dsid}"):
        raise HandoffError("revoked")
    user = User.objects.filter(pk=payload.get("u"), is_active=True).first()
    if user is None:
        raise HandoffError("user")
    # A fresh marketplace session: never reuse whatever id the visitor held.
    request.session.cycle_key()
    now = int(time.time())
    request.session[SESSION_KEY] = {
        "ref": str(user.pk),
        "email": user.email,
        "name": user.display_name,
        "at": now,
        "seen": now,
        "d": dsid,
    }
    return request.session[SESSION_KEY]


def current_owner(request) -> dict | None:
    """The signed-in owner on the marketplace surface, or ``None``."""
    data = getattr(request, "session", {}).get(SESSION_KEY) if hasattr(request, "session") else None
    if not isinstance(data, dict):
        return None
    now = int(time.time())
    if now - int(data.get("at", 0)) > ABSOLUTE_SECONDS or now - int(data.get("seen", 0)) > IDLE_SECONDS:
        sign_out(request)
        return None
    if data.get("d") and cache.get(f"mkt:revoked:{data['d']}"):
        sign_out(request)
        return None
    if now - int(data.get("seen", 0)) > 60:
        data["seen"] = now
        request.session[SESSION_KEY] = data
    return data


def sign_out(request) -> None:
    try:
        request.session.pop(SESSION_KEY, None)
    except Exception:
        pass


def revoke_for_dashboard_session(session_key: str) -> None:
    if session_key:
        cache.set(f"mkt:revoked:{_h(session_key)}", 1, ABSOLUTE_SECONDS + 3600)


# --------------------------------------------------------------------------
# Re-authentication (app surface)
# --------------------------------------------------------------------------
def window_seconds() -> int:
    from .models import MarketplaceSettings

    try:
        return int(MarketplaceSettings.current().reauth_window_minutes) * 60
    except Exception:
        return 300


def mark_reauthed(request, method: str) -> None:
    request.session[REAUTH_KEY] = {"uid": str(request.user.pk), "at": int(time.time()), "m": method, "used": False}


def reauth_state(request) -> dict | None:
    data = request.session.get(REAUTH_KEY)
    if not isinstance(data, dict) or data.get("uid") != str(getattr(request.user, "pk", "")):
        return None
    age = int(time.time()) - int(data.get("at", 0))
    window = window_seconds()
    if window <= 0:
        # "Ask every time": valid for one action, within 5 minutes.
        if data.get("used") or age > 300:
            return None
    elif age > window:
        return None
    return data


def reauth_fresh(request) -> bool:
    return reauth_state(request) is not None


def reauth_method(request) -> str:
    data = reauth_state(request)
    return (data or {}).get("m", "")


def consume_reauth(request) -> None:
    data = request.session.get(REAUTH_KEY)
    if isinstance(data, dict):
        data["used"] = True
        request.session[REAUTH_KEY] = data
