"""Field-level encryption for third-party credentials.

Protects secrets that OneWooz must be able to *use*, not merely verify:
payment gateway keys, courier API credentials, OAuth tokens, warehouse contact
details. Passwords are never encrypted  they are hashed with Argon2id and
never recoverable.

Three deliberate differences from the implementation this replaces, each of
which was a real defect there:

1. **A dedicated key.** The old code derived its encryption key from
   ``SECRET_KEY`` with a bare SHA-256 and no salt. That couples credential
   encryption to a value that gets rotated for unrelated reasons  session
   invalidation, a leaked signing key  and rotating it silently destroyed
   every stored credential. Here the key comes from ``FIELD_ENCRYPTION_KEY``
   and nothing else uses it.

2. **A real KDF.** HKDF-SHA256 with a fixed salt and a purpose-specific info
   string, so the same master key yields independent subkeys per purpose.

3. **Fail loud.** The old ``decrypt_value`` returned the *ciphertext* when
   decryption failed. Callers could not distinguish "here is your API key" from
   "here is an opaque blob that will 401 against the gateway". This raises.

Ciphertexts are prefixed with a key version (``v1:``) so a future rotation can
decrypt old values with the retired key while writing new ones with the current
key. See ``ONEWOOZ_FIELD_ENCRYPTION_KEY_RETIRED``.
"""

from __future__ import annotations

import base64
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings

__all__ = ["encrypt", "decrypt", "mask", "DecryptionError"]

CURRENT_VERSION = "v1"
_SEPARATOR = ":"

# A fixed, non-secret salt. HKDF's security does not depend on the salt being
# secret; it must merely be stable, or every restart would derive a different
# key and no stored ciphertext would decrypt.
_HKDF_SALT = b"onewooz.field-encryption.v1"


class DecryptionError(Exception):
    """Raised when a stored ciphertext cannot be decrypted.

    Always a real problem: a truncated column, a ciphertext written under a key
    that is no longer configured, or tampering. Never swallowed.
    """


@lru_cache(maxsize=8)
def _fernet_for(master_key: str, purpose: str) -> Fernet:
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=f"onewooz:{purpose}".encode(),
    ).derive(master_key.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(derived))


def _master_keys() -> dict[str, str]:
    """Map key version -> master key.

    The current key always comes from settings. Retired keys may be supplied as
    ``ONEWOOZ_FIELD_ENCRYPTION_KEY_RETIRED`` in ``version=key,version=key``
    form so a rotation can proceed without downtime.
    """
    keys = {CURRENT_VERSION: settings.FIELD_ENCRYPTION_KEY}
    retired = os.environ.get("ONEWOOZ_FIELD_ENCRYPTION_KEY_RETIRED", "")
    for entry in retired.split(","):
        entry = entry.strip()
        if not entry or "=" not in entry:
            continue
        version, _, key = entry.partition("=")
        version = version.strip()
        if version and version != CURRENT_VERSION:
            keys[version] = key.strip()
    return keys


def encrypt(plaintext: str, *, purpose: str = "credential") -> str:
    """Encrypt a value for storage. Returns ``"v1:<token>"``.

    ``purpose`` binds the ciphertext to a use: a value encrypted for
    ``"credential"`` cannot be decrypted as ``"pii"``, so a bug that reads a
    courier token through the wrong accessor fails instead of leaking.
    """
    if plaintext is None:
        return ""
    if not isinstance(plaintext, str):
        plaintext = str(plaintext)
    if plaintext == "":
        return ""

    fernet = _fernet_for(settings.FIELD_ENCRYPTION_KEY, purpose)
    token = fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
    return f"{CURRENT_VERSION}{_SEPARATOR}{token}"


def decrypt(stored: str, *, purpose: str = "credential") -> str:
    """Decrypt a stored value.

    Raises :class:`DecryptionError` on any failure. A caller that genuinely
    wants a tolerant read should catch it explicitly and decide what to do 
    the decision is never made silently here.
    """
    if not stored:
        return ""

    version, separator, token = stored.partition(_SEPARATOR)
    if not separator:
        raise DecryptionError(
            "Stored value has no key-version prefix. It was written by an "
            "incompatible implementation and cannot be read."
        )

    keys = _master_keys()
    master = keys.get(version)
    if not master:
        raise DecryptionError(
            f"No key configured for version {version!r}. "
            "Set ONEWOOZ_FIELD_ENCRYPTION_KEY_RETIRED to decrypt values "
            "written before the last rotation."
        )

    try:
        return _fernet_for(master, purpose).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise DecryptionError(
            f"Could not decrypt a {purpose!r} value written under key {version!r}. "
            "The column may be truncated or the key may be wrong."
        ) from exc


def mask(secret: str | None, *, visible_tail: int = 4) -> str:
    """Render a secret for display: ``••••••••3f2a``.

    Used wherever a stored credential is shown back to its owner so they can
    confirm which key is configured without the value being readable from a
    screenshot or a support session.
    """
    if not secret:
        return ""
    text = str(secret)
    if len(text) <= visible_tail:
        return "•" * 8
    return "•" * 8 + text[-visible_tail:]
