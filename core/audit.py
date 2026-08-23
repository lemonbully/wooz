"""Writing to the audit trail.

Call :func:`record` for anything consequential. It never raises: an audit write
failing must not take down the action it was describing, so failures are logged
and swallowed. That is the one place in this codebase where swallowing an
exception is the right call, and it is why the logger is separate.
"""

from __future__ import annotations

import logging
from typing import Any

from .models import AuditEvent

logger = logging.getLogger("onewooz.audit")

__all__ = ["record", "redact"]

# Keys whose values must never reach the audit table. Matched case-insensitively
# as substrings, so "razorpay_key_secret" and "SECRET_KEY" are both caught.
_SENSITIVE = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "private",
    "credential",
    "authorization",
    "cvv",
    "card_number",
    "otp",
    "signature",
    "session",
)

_REDACTED = "[redacted]"


def redact(data: Any, _depth: int = 0) -> Any:
    """Recursively replace sensitive values with a marker.

    Applied to every payload before it is written. The audit trail records that
    a credential changed, never what it changed to.
    """
    if _depth > 6:
        return "[too deep]"
    if isinstance(data, dict):
        cleaned = {}
        for key, value in data.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in _SENSITIVE):
                cleaned[key] = _REDACTED
            else:
                cleaned[key] = redact(value, _depth + 1)
        return cleaned
    if isinstance(data, (list, tuple)):
        return [redact(item, _depth + 1) for item in data]
    return data


def _client_ip(request) -> str | None:
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        # Leftmost entry is the original client. Trustworthy here because the
        # only thing in front of the application is our own Caddy/NLB pair.
        return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def record(
    action: str,
    *,
    request=None,
    actor=None,
    actor_kind: str | None = None,
    target=None,
    target_kind: str = "",
    target_label: str = "",
    site=None,
    severity: str = AuditEvent.Severity.INFO,
    changes: dict | None = None,
    context: dict | None = None,
) -> AuditEvent | None:
    """Append one event to the audit trail.

    Returns the created row, or ``None`` if the write failed.
    """
    try:
        if actor is None and request is not None:
            candidate = getattr(request, "user", None)
            if candidate is not None and getattr(candidate, "is_authenticated", False):
                actor = candidate

        event = AuditEvent(
            action=action,
            severity=severity,
            actor_id=str(getattr(actor, "pk", "") or ""),
            actor_label=str(actor) if actor is not None else "",
            actor_kind=actor_kind or ("user" if actor is not None else "system"),
            target_kind=target_kind or (target.__class__.__name__ if target else ""),
            target_id=str(getattr(target, "pk", "") or ""),
            target_label=target_label or (str(target) if target is not None else ""),
            site_id_ref=str(getattr(site, "pk", "") or ""),
            ip_address=_client_ip(request),
            user_agent=(request.META.get("HTTP_USER_AGENT", "")[:1000] if request else ""),
            request_id=getattr(request, "request_id", "") if request else "",
            surface=getattr(request, "surface", "") if request else "",
            changes=redact(changes or {}),
            context=redact(context or {}),
        )
        event.save()
        return event
    except Exception:
        # An audit write must never break the operation it describes.
        logger.exception("failed to write audit event action=%s", action)
        return None
