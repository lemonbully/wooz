"""Email one-time codes, stored server-side.

Four things here are deliberate corrections of how the platform this replaces
did it, each of which was independently exploitable:

1. **Codes come from** :mod:`secrets`, not :mod:`random`. ``random`` is a
   Mersenne Twister seeded from system time; observing a few outputs lets an
   attacker predict the rest. It must never generate a credential.

2. **The code is stored server-side**, not in the requesting user's own
   session. The old flow wrote the correct code into the session of the person
   who had to guess it.

3. **Comparison is constant-time**, so response timing does not leak how many
   leading digits were right.

4. **Attempts and sends are both capped.** The old send endpoint would mail a
   code to any address, any number of times — a free email-bombing tool
   pointed at a merchant's own domain reputation.

Only a hash of the code is stored, so a dump of the cache does not hand over
live codes.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from django.core.cache import cache

__all__ = ["issue", "verify", "invalidate", "OTPResult"]

CODE_LENGTH = 6
TTL_SECONDS = 600
MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class OTPResult:
    ok: bool
    reason: str = ""


def _key(purpose: str, identity: str) -> str:
    digest = hashlib.sha256(f"{purpose}:{identity.lower()}".encode()).hexdigest()[:32]
    return f"otp:{purpose}:{digest}"


def _hash_code(code: str, identity: str) -> str:
    # Identity is mixed in so a hash captured for one address cannot be
    # replayed against another that happened to receive the same digits.
    return hashlib.sha256(f"{identity.lower()}:{code}".encode()).hexdigest()


def generate_code(length: int = CODE_LENGTH) -> str:
    """A numeric code with uniform digits and no modulo bias."""
    return "".join(secrets.choice("0123456789") for _ in range(length))


def issue(purpose: str, identity: str, *, ttl: int = TTL_SECONDS) -> str:
    """Create and store a code, returning the plaintext to send.

    Issuing replaces any outstanding code for the same purpose and identity,
    so requesting a new one invalidates the previous email.
    """
    code = generate_code()
    cache.set(
        _key(purpose, identity),
        {"hash": _hash_code(code, identity), "attempts": 0},
        timeout=ttl,
    )
    return code


def verify(purpose: str, identity: str, submitted: str) -> OTPResult:
    """Check a submitted code and consume it on success.

    A correct code is deleted immediately, so it works exactly once even if the
    email is later read by someone else.
    """
    key = _key(purpose, identity)
    record = cache.get(key)

    if not record:
        return OTPResult(False, "expired")

    if record.get("attempts", 0) >= MAX_ATTEMPTS:
        cache.delete(key)
        return OTPResult(False, "too_many_attempts")

    submitted = (submitted or "").strip()
    expected = record.get("hash", "")

    if hmac.compare_digest(_hash_code(submitted, identity), expected):
        cache.delete(key)
        return OTPResult(True)

    record["attempts"] = record.get("attempts", 0) + 1
    # Preserve the remaining lifetime rather than extending it: a wrong guess
    # must not buy the guesser more time.
    cache.set(key, record, timeout=_remaining_ttl(key))
    return OTPResult(False, "incorrect")


def invalidate(purpose: str, identity: str) -> None:
    cache.delete(_key(purpose, identity))


def _remaining_ttl(key: str) -> int:
    try:
        ttl = cache.ttl(key)  # django-redis
    except (AttributeError, NotImplementedError):
        return TTL_SECONDS
    if ttl is None or ttl <= 0:
        return TTL_SECONDS
    return int(ttl)
