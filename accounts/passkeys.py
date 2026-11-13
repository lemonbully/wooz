"""Passkey (WebAuthn) registration and authentication.

Why passkeys are the primary recommendation rather than TOTP: the browser
signs a challenge together with the origin it is talking to. A convincing
lookalike domain cannot obtain a usable signature, because the signature it
would receive is bound to the wrong origin. A TOTP code, by contrast, is six
digits that work anywhere for thirty seconds  a real-time relay page collects
one and replays it.

Challenges live in the cache keyed by session, never in a hidden form field: a
challenge the client can choose is not a challenge.
"""

from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from .models import WebAuthnCredential

__all__ = [
    "begin_registration",
    "finish_registration",
    "begin_authentication",
    "finish_authentication",
    "PasskeyError",
]


class PasskeyError(Exception):
    """Registration or assertion failed. Message is safe to show the user."""


@dataclass(frozen=True)
class _Ceremony:
    key: str
    challenge: bytes


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _origin(request) -> str:
    scheme = "https" if request.is_secure() else "http"
    return f"{scheme}://{request.get_host()}"


def _store_challenge(request, kind: str, challenge: bytes) -> None:
    # Bound to the session key so one browser's challenge cannot be completed
    # by another, and short-lived so a captured challenge is not reusable.
    if not request.session.session_key:
        request.session.save()
    cache.set(
        f"webauthn:{kind}:{request.session.session_key}",
        _b64(challenge),
        timeout=settings.WEBAUTHN_CHALLENGE_TTL,
    )


def _pop_challenge(request, kind: str) -> bytes:
    key = f"webauthn:{kind}:{request.session.session_key}"
    stored = cache.get(key)
    if not stored:
        raise PasskeyError("This request expired. Please try again.")
    cache.delete(key)  # single use
    return base64url_to_bytes(stored)


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def begin_registration(request, user) -> str:
    """Return registration options as JSON for ``navigator.credentials.create``."""
    existing = [
        PublicKeyCredentialDescriptor(id=base64url_to_bytes(cred.credential_id))
        for cred in user.passkeys.all()
    ]

    options = generate_registration_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        rp_name=settings.WEBAUTHN_RP_NAME,
        # A stable, opaque handle. Deliberately not the email address: the user
        # handle is stored on the authenticator and syncs to the user's cloud
        # keychain, so it should not carry personal data.
        user_id=str(user.pk).encode(),
        user_name=user.email,
        user_display_name=user.display_name,
        # Ask the authenticator to store the credential so it can later be used
        # for passwordless sign-in without the user first naming their account.
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        # Prevents registering the same authenticator twice on one account.
        exclude_credentials=existing,
    )

    _store_challenge(request, "register", options.challenge)
    return options_to_json(options)


def finish_registration(request, user, credential_json: str, *, label: str = "") -> WebAuthnCredential:
    challenge = _pop_challenge(request, "register")

    try:
        verification = verify_registration_response(
            credential=credential_json,
            expected_challenge=challenge,
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=_origin(request),
            require_user_verification=False,
        )
    except Exception as exc:
        raise PasskeyError("That passkey could not be registered.") from exc

    credential_id = _b64(verification.credential_id)
    if WebAuthnCredential.objects.filter(credential_id=credential_id).exists():
        raise PasskeyError("That passkey is already registered.")

    return WebAuthnCredential.objects.create(
        user=user,
        label=label or "Passkey",
        credential_id=credential_id,
        public_key=_b64(verification.credential_public_key),
        sign_count=verification.sign_count,
        aaguid=str(getattr(verification, "aaguid", "") or ""),
        is_backed_up=bool(getattr(verification, "credential_backed_up", False)),
    )


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------


def begin_authentication(request, user=None) -> str:
    """Return assertion options for ``navigator.credentials.get``.

    With no ``user``, the ceremony is discoverable: the authenticator offers
    whichever passkey it holds for this site and we identify the account from
    the response. That is the passwordless path, and it is also why this
    function must not reveal whether a given email has a passkey.
    """
    allow = []
    if user is not None:
        allow = [
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(cred.credential_id))
            for cred in user.passkeys.all()
        ]

    options = generate_authentication_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    _store_challenge(request, "authenticate", options.challenge)
    return options_to_json(options)


def finish_authentication(request, credential_json: str, *, credential_id: str):
    """Verify an assertion. Returns ``(user, credential)``."""
    challenge = _pop_challenge(request, "authenticate")

    credential = (
        WebAuthnCredential.objects.select_related("user")
        .filter(credential_id=credential_id)
        .first()
    )
    if credential is None:
        raise PasskeyError("That passkey is not recognised.")

    try:
        verification = verify_authentication_response(
            credential=credential_json,
            expected_challenge=challenge,
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=_origin(request),
            credential_public_key=base64url_to_bytes(credential.public_key),
            credential_current_sign_count=credential.sign_count,
            require_user_verification=False,
        )
    except Exception as exc:
        raise PasskeyError("That passkey could not be verified.") from exc

    # A counter that fails to advance suggests a cloned authenticator. Devices
    # that always report zero are permitted by the spec and skip the check.
    if verification.new_sign_count and verification.new_sign_count <= credential.sign_count:
        raise PasskeyError("That passkey could not be verified.")

    credential.sign_count = verification.new_sign_count
    credential.last_used_at = timezone.now()
    credential.save(update_fields=["sign_count", "last_used_at", "updated_at"])

    return credential.user, credential


def random_label() -> str:
    return f"Passkey {secrets.token_hex(2)}"
