"""List and end an owner's *live* sign-in sessions.

A OneWooz session is a row in Django's session table (the ``db`` backend). To
show an owner every device they are currently signed in on  and let them end
any one instantly  we need two things the bare table doesn't give directly:

* **which sessions are this owner's**  found by decoding each unexpired session
  and matching ``_auth_user_id``; and
* **what device each one is**  recorded into the session itself at sign-in
  (:func:`accounts.services.remember_session_device`), so no separate table (and
  no migration) is needed.

Ending a session simply deletes its row: the next request from that device
arrives with a cookie pointing at nothing, so it is anonymous and the dashboard
bounces it to sign-in. The heartbeat (:func:`accounts.views.session_ping`) makes
that happen within seconds rather than on the device's next navigation.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.utils import timezone

from . import geo, useragent

__all__ = ["LiveSession", "list_for_user", "end_session", "end_others", "enforce_limit"]

#: The key our own device metadata lives under inside each session.
DEVICE_KEY = "ow_device"


@dataclass(frozen=True)
class LiveSession:
    """One live session, resolved for display."""

    session_key: str
    is_current: bool
    device: str  # friendly, e.g. "Chrome on Windows"
    location: str  # friendly, e.g. "Mumbai, India"
    ip: str
    signed_in_at: object | None  # datetime | None
    last_seen_at: object | None  # datetime | None


def _parse_dt(value):
    if not value:
        return None
    try:
        return timezone.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _decoded_sessions_for(user_pk: str):
    """Yield ``(session_key, decoded_dict)`` for the user's unexpired sessions."""
    now = timezone.now()
    rows = Session.objects.filter(expire_date__gte=now).only("session_key", "session_data")
    for row in rows.iterator():
        try:
            data = row.get_decoded()
        except Exception:
            continue
        if str(data.get("_auth_user_id") or "") == str(user_pk):
            yield row.session_key, data


def list_for_user(user, current_key: str = "") -> list[LiveSession]:
    """Every live session for ``user``, current one first, newest next."""
    if not user or not getattr(user, "is_authenticated", False):
        return []

    out: list[LiveSession] = []
    for key, data in _decoded_sessions_for(user.pk):
        meta = data.get(DEVICE_KEY) or {}
        ua = meta.get("ua") or ""
        ip = meta.get("ip") or ""
        out.append(
            LiveSession(
                session_key=key,
                is_current=bool(current_key) and key == current_key,
                device=useragent.friendly(ua),
                location=geo.describe(ip, meta.get("country") or ""),
                ip=ip,
                signed_in_at=_parse_dt(meta.get("at")),
                last_seen_at=_parse_dt(meta.get("seen")) or _parse_dt(meta.get("at")),
            )
        )

    out.sort(
        key=lambda s: (
            0 if s.is_current else 1,
            -(s.last_seen_at.timestamp() if s.last_seen_at else 0),
        )
    )
    return out


def _owned_by(session_key: str, user_pk) -> bool:
    """True only when ``session_key`` exists and belongs to ``user_pk``.

    Checked before deletion so an owner can only ever end their *own* sessions,
    never guess another account's key.
    """
    try:
        data = SessionStore().decode(Session.objects.get(session_key=session_key).session_data)
    except Session.DoesNotExist:
        return False
    except Exception:
        return False
    return str(data.get("_auth_user_id") or "") == str(user_pk)


def end_session(user, session_key: str) -> bool:
    """End one of ``user``'s sessions. Returns True if a session was removed."""
    if not session_key or not _owned_by(session_key, user.pk):
        return False
    Session.objects.filter(session_key=session_key).delete()
    return True


def end_others(user, current_key: str) -> int:
    """End every session for ``user`` except ``current_key``. Returns the count."""
    keys = [
        key
        for key, _ in _decoded_sessions_for(user.pk)
        if key and key != current_key
    ]
    if not keys:
        return 0
    Session.objects.filter(session_key__in=keys).delete()
    return len(keys)


def _recency(session: "LiveSession") -> float:
    """A sortable recency for a session (last seen, else sign-in, else 0)."""
    when = session.last_seen_at or session.signed_in_at
    try:
        return when.timestamp() if when else 0.0
    except (AttributeError, ValueError, OverflowError):
        return 0.0


def enforce_limit(user, current_key: str, limit: int) -> int:
    """Trim ``user``'s live sessions to a ``limit`` of devices (0 = unlimited).

    Called at sign-in (:func:`accounts.services.complete_login`). Keeps the
    session that just signed in plus the most-recently-active other sessions, up
    to the cap, and ends the rest by deleting their session rows  so the account
    can be actively managed on at most ``limit`` devices at once. An ended device
    falls back to anonymous on its next request (the security-page heartbeat
    makes that near-immediate), and is bounced to sign-in by the dashboard.

    Returns how many sessions were ended.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return 0
    if not limit or limit <= 0:
        return 0

    sessions = list_for_user(user, current_key)
    if len(sessions) <= limit:
        return 0

    # Always keep the current device; fill the remaining slots with the
    # most-recently-active other sessions, and end whatever is left over.
    others = [s for s in sessions if s.session_key and s.session_key != current_key]
    others.sort(key=_recency, reverse=True)
    keep = {current_key} | {s.session_key for s in others[: max(0, limit - 1)]}
    to_end = [s.session_key for s in sessions if s.session_key and s.session_key not in keep]
    if to_end:
        Session.objects.filter(session_key__in=to_end).delete()
    return len(to_end)
