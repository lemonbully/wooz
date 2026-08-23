"""Rate limiting.

The platform this replaces had none on any storefront endpoint. Its OTP send
endpoint, in particular, would mail a six-digit code to any address supplied by
any caller, unlimited times — a free email-bombing primitive attached to a
customer's brand.

Implementation is a fixed-window counter in the cache. Fixed windows allow a
burst of up to 2x the limit across a boundary; that is an acceptable trade for
a single atomic increment per check, and every limit here is sized with the
burst in mind. Limits are declared centrally in ``settings.RATE_LIMITS`` so
they can be reviewed in one place rather than discovered across the codebase.

Identity keys are hashed before use so the cache never holds a plaintext email
address or IP.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache

__all__ = ["RateLimitExceeded", "check", "consume", "peek", "reset"]


class RateLimitExceeded(Exception):
    """Raised by :func:`consume` when a caller is over its limit."""

    def __init__(self, name: str, retry_after: int):
        self.name = name
        self.retry_after = retry_after
        super().__init__(f"rate limit {name!r} exceeded; retry in {retry_after}s")


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    remaining: int
    retry_after: int


def _limit_for(name: str) -> tuple[int, int]:
    try:
        limit, window = settings.RATE_LIMITS[name]
    except KeyError as exc:
        raise KeyError(
            f"No rate limit declared for {name!r}. Add it to settings.RATE_LIMITS "
            f"rather than inventing a limit at the call site."
        ) from exc
    return int(limit), int(window)


def _bucket_key(name: str, identity: str, window: int) -> str:
    # Hash the identity so the cache holds no email addresses or IPs, and
    # include the window index so the key rotates on its own without a
    # separate expiry sweep.
    digest = hashlib.sha256(f"{name}:{identity}".encode()).hexdigest()[:32]
    window_index = int(time.time() // window)
    return f"rl:{name}:{digest}:{window_index}"


def peek(name: str, identity: str) -> Verdict:
    """Report the current state without consuming anything."""
    limit, window = _limit_for(name)
    used = cache.get(_bucket_key(name, identity, window), 0)
    remaining = max(0, limit - used)
    retry_after = window - int(time.time() % window)
    return Verdict(allowed=used < limit, remaining=remaining, retry_after=retry_after)


def consume(name: str, identity: str, *, cost: int = 1) -> Verdict:
    """Record an attempt and return the resulting verdict.

    Does not raise; callers decide how to present rejection. Use :func:`check`
    when raising is the desired behaviour.
    """
    limit, window = _limit_for(name)
    key = _bucket_key(name, identity, window)

    # ``add`` then ``incr`` rather than get/set: the increment is atomic in
    # Redis, so concurrent requests cannot both read the same count and
    # overwrite each other.
    cache.add(key, 0, timeout=window + 1)
    try:
        used = cache.incr(key, cost)
    except ValueError:
        # Key expired between add and incr. Treat as the first hit of a new
        # window rather than failing the request.
        cache.set(key, cost, timeout=window + 1)
        used = cost

    retry_after = window - int(time.time() % window)
    return Verdict(
        allowed=used <= limit,
        remaining=max(0, limit - used),
        retry_after=retry_after,
    )


def check(name: str, identity: str, *, cost: int = 1) -> None:
    """Consume one unit and raise :class:`RateLimitExceeded` if over limit."""
    verdict = consume(name, identity, cost=cost)
    if not verdict.allowed:
        raise RateLimitExceeded(name, verdict.retry_after)


def reset(name: str, identity: str) -> None:
    """Clear a counter.

    Called after a *successful* authentication so that a user who mistyped a
    password four times is not still one attempt from a lockout.
    """
    _, window = _limit_for(name)
    cache.delete(_bucket_key(name, identity, window))


def client_identity(request) -> str:
    """Best available identifier for an anonymous caller."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")
