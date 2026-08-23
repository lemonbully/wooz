"""Purpose-bound signed tokens.

Any URL that grants a capability without a session — an invoice download link,
a cart recovery email, a magic sign-in link, an unsubscribe link, an order
action from a confirmation email — carries one of these instead of a raw
identifier.

The property that matters is **purpose binding**. Each purpose gets its own
signer salt, so a token minted to download an invoice cannot be replayed
against the order-cancellation endpoint, even though both encode the same
account. Django's :class:`~django.core.signing.TimestampSigner` provides the
signature and the age check; the purpose salt and the registry of maximum
lifetimes are what make it safe to hand out.

Nothing here is encryption. The payload is signed, not hidden: it is readable
by anyone holding the token. Never put a secret in one.

Usage::

    token = tokens.sign("billing.invoice", {"bill": str(bill.pk)})
    ...
    payload = tokens.unsign("billing.invoice", token)   # -> {"bill": "..."}
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core import signing

__all__ = ["sign", "unsign", "TokenError", "TokenExpired", "TokenInvalid"]

_SALT_PREFIX = "onewooz.token."

# Ceiling applied when a purpose has no explicit entry in settings.TOKEN_TTL.
# Short on purpose: a purpose that forgets to declare a lifetime gets the
# strictest one, not the loosest.
_DEFAULT_TTL = 900


class TokenError(Exception):
    """Base class for token failures."""


class TokenExpired(TokenError):
    """The signature was valid but the token is older than its purpose allows."""


class TokenInvalid(TokenError):
    """The token was tampered with, truncated, or minted for another purpose."""


def _salt(purpose: str) -> str:
    if not purpose or " " in purpose:
        raise ValueError(f"invalid token purpose: {purpose!r}")
    return f"{_SALT_PREFIX}{purpose}"


def ttl_for(purpose: str) -> int:
    return int(getattr(settings, "TOKEN_TTL", {}).get(purpose, _DEFAULT_TTL))


def sign(purpose: str, payload: dict[str, Any]) -> str:
    """Mint a token for ``purpose`` carrying ``payload``.

    The purpose is embedded in the payload as well as in the signer salt. The
    salt alone already prevents cross-purpose use; the embedded copy means a
    decoded token is self-describing in logs and support tooling.
    """
    if not isinstance(payload, dict):
        raise TypeError("token payload must be a dict")
    body = dict(payload)
    body["_p"] = purpose
    return signing.dumps(body, salt=_salt(purpose), compress=True)


def unsign(purpose: str, token: str) -> dict[str, Any]:
    """Verify a token and return its payload.

    Raises :class:`TokenExpired` or :class:`TokenInvalid`. Callers should treat
    both as "this link does not work" and must not leak which one occurred to
    an unauthenticated visitor.
    """
    if not token:
        raise TokenInvalid("empty token")

    try:
        body = signing.loads(token, salt=_salt(purpose), max_age=ttl_for(purpose))
    except signing.SignatureExpired as exc:
        raise TokenExpired(str(exc)) from exc
    except signing.BadSignature as exc:
        raise TokenInvalid(str(exc)) from exc

    if not isinstance(body, dict):
        raise TokenInvalid("token payload is not an object")

    # Defence in depth: the salt already made a cross-purpose signature fail.
    # This catches the case where two purposes were accidentally given the same
    # salt string.
    if body.pop("_p", None) != purpose:
        raise TokenInvalid("token purpose mismatch")

    return body
