"""TOTP second factor.

Wraps :mod:`pyotp` with the two properties a bare library call does not give
you: a one-step-either-side drift window, and single-use enforcement.

Single use matters more than it first appears. A TOTP code is valid for a
30-second step, and with drift tolerance it is accepted across a 90-second
span. Without recording which step was used, a code phished or shoulder-surfed
mid-window can be replayed by an attacker for the remainder of it. Storing the
highest accepted step and refusing anything at or below closes that.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import quote

import pyotp
from django.conf import settings
from django.utils import timezone

__all__ = ["new_secret", "provisioning_uri", "verify", "TOTPResult"]

#: Steps of tolerance either side of now, for clock drift between the phone
#: and the server. One step (30s) is the usual recommendation: enough for real
#: drift, small enough not to widen the attack window meaningfully.
DRIFT_STEPS = 1
STEP_SECONDS = 30


@dataclass(frozen=True)
class TOTPResult:
    ok: bool
    reason: str = ""


def new_secret() -> str:
    """A fresh base32 shared secret (160 bits)."""
    return pyotp.random_base32(length=32)


def provisioning_uri(secret: str, account_email: str) -> str:
    """The ``otpauth://`` URI an authenticator app scans."""
    issuer = quote(getattr(settings, "WEBAUTHN_RP_NAME", "OneWooz"))
    return pyotp.TOTP(secret).provisioning_uri(name=account_email, issuer_name=issuer)


def _current_step(at: float | None = None) -> int:
    return int((at if at is not None else time.time()) // STEP_SECONDS)


def verify(device, submitted: str, *, commit: bool = True) -> TOTPResult:
    """Verify a code against a :class:`~accounts.models.TOTPDevice`.

    On success the device's ``last_used_step`` is advanced so the same code
    cannot be presented again.
    """
    submitted = (submitted or "").strip().replace(" ", "")
    if not submitted.isdigit() or len(submitted) != 6:
        return TOTPResult(False, "malformed")

    totp = pyotp.TOTP(device.secret)
    now = time.time()

    for offset in range(-DRIFT_STEPS, DRIFT_STEPS + 1):
        candidate_time = now + (offset * STEP_SECONDS)
        step = _current_step(candidate_time)

        if step <= device.last_used_step:
            # Already spent. Keep checking later steps rather than returning,
            # so a legitimate code from the *next* step still works.
            continue

        # pyotp compares in constant time internally.
        if totp.verify(submitted, for_time=candidate_time, valid_window=0):
            if commit:
                device.last_used_step = step
                fields = ["last_used_step", "updated_at"]
                if device.confirmed_at is None:
                    device.confirmed_at = timezone.now()
                    fields.append("confirmed_at")
                device.save(update_fields=fields)
            return TOTPResult(True)

    return TOTPResult(False, "incorrect")
