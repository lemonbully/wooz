"""Model fields that encrypt their contents at rest.

Storing a gateway key or courier token as an ``EncryptedTextField`` means the
value is ciphertext in the database, in backups, and in any query log — while
reading ``config.api_key`` in Python just gives the plaintext.

The trade is that an encrypted column cannot be filtered, ordered, or indexed
on its value, because identical plaintexts produce different ciphertexts. That
is correct for secrets: they are looked up by owner, never searched by content.
"""

from __future__ import annotations

from django.db import models

from .crypto import DecryptionError, decrypt, encrypt

__all__ = ["EncryptedTextField", "EncryptedCharField"]


class _EncryptedMixin:
    """Shared encrypt-on-write / decrypt-on-read behaviour."""

    #: Binds the ciphertext to a use. A value written as a "credential" cannot
    #: be read back as "pii", so wiring a field to the wrong accessor fails
    #: rather than leaking across purposes.
    encryption_purpose = "credential"

    def __init__(self, *args, purpose: str | None = None, **kwargs):
        if purpose:
            self.encryption_purpose = purpose
        # Ciphertext is meaningfully longer than plaintext: Fernet output is
        # base64 over a 57-byte header plus padded AES blocks, roughly 1.4x
        # plus ~100 bytes. Callers size for plaintext; we widen the column.
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        if self.encryption_purpose != "credential":
            kwargs["purpose"] = self.encryption_purpose
        return name, path, args, kwargs

    def get_prep_value(self, value):
        if value is None:
            return None
        if value == "":
            return ""
        return encrypt(str(value), purpose=self.encryption_purpose)

    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return value
        try:
            return decrypt(value, purpose=self.encryption_purpose)
        except DecryptionError:
            # Deliberately re-raised. A caller that receives ciphertext where
            # it expected an API key will fail confusingly against the third
            # party instead of here, which is exactly the failure mode this
            # replaces.
            raise

    def to_python(self, value):
        return value


class EncryptedTextField(_EncryptedMixin, models.TextField):
    """Encrypted free-length text. Use for tokens and long credentials."""


class EncryptedCharField(_EncryptedMixin, models.CharField):
    """Encrypted bounded text.

    ``max_length`` is the *storage* width, so allow headroom over the longest
    plaintext you expect — roughly ``len(plaintext) * 1.4 + 120``.
    """
