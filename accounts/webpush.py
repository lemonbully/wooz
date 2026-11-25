"""Browser push notifications for the owner dashboard (Web Push).

STATUS: BROWSER PUSH IS CURRENTLY DISABLED. This module is kept intact but
nothing calls it: the signal import (accounts/apps.py), the routes
(accounts/urls.py), the context (ud/context_processors.py), the Profile card,
the top-bar include and the admin listing are commented out. To turn it back
on, uncomment every spot marked "BROWSER PUSH: DISABLED" (grep for it).

Every bell :class:`~accounts.models.Notification` is also delivered to each
browser the owner allowed notifications in (:class:`~accounts.models.PushSubscription`),
so it pops up on their desktop or phone even when no OneWooz tab is open. A
browser that is closed gets it the next time it starts, for as long as the
message TTL lasts.

The protocol is implemented here directly on ``cryptography`` + ``requests``
(both already dependencies) rather than through a wrapper library:

* **RFC 8291**  the payload is encrypted (ECDH P-256 + HKDF + AES-128-GCM,
  ``aes128gcm`` content coding) to the browser's own key, so the push service
  relays ciphertext it cannot read.
* **RFC 8292**  every request carries a VAPID JWT signed with the platform's
  key (:class:`~accounts.models.WebPushKey`, generated automatically on first
  use), which is how the push service knows the message is from OneWooz.

Delivery never runs in a web request: ``accounts.signals`` queues
:func:`accounts.tasks.send_web_push` after the notification commits.

Security notes:

* Endpoints are restricted to the real browser push services
  (:data:`PUSH_HOST_SUFFIXES`) over HTTPS with redirects disabled, so a
  signed-in user cannot point the worker at an internal address (SSRF).
* A subscription belongs to one login session and is removed with it (see
  ``accounts.signals``), so signing out stops the alerts on that browser.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import struct
import time
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

#: Browser push services a subscription endpoint may point at. Chrome, Brave,
#: Opera and Samsung Internet use FCM; Firefox uses Mozilla autopush; Edge uses
#: Windows Push Notification Services; Safari (macOS and iOS home-screen apps)
#: uses Apple's web push service.
PUSH_HOST_SUFFIXES = (
    "fcm.googleapis.com",
    "android.googleapis.com",
    "push.services.mozilla.com",
    "notify.windows.com",
    "push.apple.com",
)

#: Kinds an owner can mute for push, in the order the profile page lists them.
#: Mirrors ``Notification.Kind``.
PUSH_KINDS = (
    ("order", "Orders, payments & customer requests", "New orders, payments, enquiries and cancel or return requests."),
    ("shipment", "Shipment updates", "Courier status changes for orders on their way."),
    ("stock", "Low stock", "A product is running out or has sold out."),
    ("system", "Store & billing", "Bills, failed payments, domain, reviews, collaborations and wallet warnings."),
    ("admin", "Messages from OneWooz", "News, important notices and replies to your support tickets."),
)

#: How long the push service keeps a message for a browser that is offline.
MESSAGE_TTL = 3 * 24 * 60 * 60
#: A tab polling the bell while visible within this many seconds counts as the
#: owner looking at the dashboard on that browser: its bell, toast and chime
#: already announce the notification, so no system popup is sent there.
PRESENCE_SECONDS = 15
#: Consecutive failures after which a subscription is considered dead.
MAX_FAILURES = 8
#: Subscriptions kept per account (oldest dropped beyond this).
MAX_PER_USER = 20

_RECORD_SIZE = 4096
_private_key_cache = None


# --------------------------------------------------------------------------
# Encoding helpers
# --------------------------------------------------------------------------

def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_decode(value: str) -> bytes:
    value = (value or "").strip()
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def session_hash(session_key: str) -> str:
    """SHA-256 of a session key (the key itself is never stored)."""
    return hashlib.sha256((session_key or "").encode("utf-8")).hexdigest()


def endpoint_hash(endpoint: str) -> str:
    return hashlib.sha256((endpoint or "").encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# VAPID key
# --------------------------------------------------------------------------

def _generate_raw_key() -> str:
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    return _b64u(key.private_numbers().private_value.to_bytes(32, "big"))


def _load_private_key(raw_b64: str):
    """A P-256 private key from base64url raw scalar, or a PEM string."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    raw_b64 = (raw_b64 or "").strip()
    if raw_b64.startswith("-----BEGIN"):
        key = serialization.load_pem_private_key(raw_b64.encode("ascii"), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise ValueError("VAPID key must be an EC P-256 key.")
        return key
    value = int.from_bytes(_b64u_decode(raw_b64), "big")
    return ec.derive_private_key(value, ec.SECP256R1())


def _stored_raw_key() -> str:
    """The platform key from the database, creating it on first use."""
    from .models import WebPushKey

    row = WebPushKey.objects.filter(name="vapid").first()
    if row is not None and row.private_key:
        return row.private_key
    try:
        with transaction.atomic():
            row = WebPushKey.objects.create(name="vapid", private_key=_generate_raw_key())
        return row.private_key
    except IntegrityError:
        # Another instance created it first: use theirs, so every subscription
        # shares one key.
        return WebPushKey.objects.get(name="vapid").private_key


def private_key():
    global _private_key_cache
    if _private_key_cache is None:
        configured = getattr(settings, "WEBPUSH_VAPID_PRIVATE_KEY", "") or ""
        _private_key_cache = _load_private_key(configured or _stored_raw_key())
    return _private_key_cache


def public_key() -> str:
    """The VAPID public key (base64url, uncompressed point) browsers subscribe with."""
    from cryptography.hazmat.primitives import serialization

    pub = private_key().public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return _b64u(pub)


def enabled() -> bool:
    return bool(getattr(settings, "WEBPUSH_ENABLED", True))


# --------------------------------------------------------------------------
# Subscriptions
# --------------------------------------------------------------------------

def _allowed_endpoint(endpoint: str) -> bool:
    try:
        parts = urlsplit(endpoint)
    except ValueError:
        return False
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return False
    if parts.port not in (None, 443):
        return False
    host = parts.hostname.lower().rstrip(".")
    return any(host == s or host.endswith("." + s) for s in PUSH_HOST_SUFFIXES)


def validate(data) -> tuple[str, str, str]:
    """``(endpoint, p256dh, auth)`` from a browser ``PushSubscription.toJSON()``.

    Raises ``ValueError`` for anything that is not a genuine browser push
    subscription to a known push service.
    """
    if not isinstance(data, dict):
        raise ValueError("Bad subscription.")
    endpoint = str(data.get("endpoint") or "").strip()
    keys = data.get("keys") if isinstance(data.get("keys"), dict) else {}
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    if not endpoint or len(endpoint) > 1024 or not _allowed_endpoint(endpoint):
        raise ValueError("Unsupported push service.")
    try:
        if len(_b64u_decode(p256dh)) != 65 or len(_b64u_decode(auth)) != 16:
            raise ValueError
    except Exception:
        raise ValueError("Bad subscription keys.") from None
    return endpoint, p256dh, auth


def _device_label(user_agent: str) -> str:
    try:
        from user_agents import parse

        ua = parse(user_agent or "")
        browser = (ua.browser.family or "").replace("Mobile ", "").strip()
        os_name = (ua.os.family or "").strip()
        if browser == "Other":
            browser = ""
        if os_name == "Other":
            os_name = ""
        label = " on ".join(p for p in (browser or "Browser", os_name) if p)
        return label[:120]
    except Exception:
        return "Browser"


def save(request, data):
    """Create or re-bind the subscription for this browser + login session."""
    from .models import PushSubscription

    endpoint, p256dh, auth = validate(data)
    if not request.session.session_key:
        request.session.save()
    ua = (request.META.get("HTTP_USER_AGENT") or "")[:300]
    fields = {
        "user": request.user,
        "endpoint": endpoint,
        "p256dh": p256dh,
        "auth": auth,
        "session_key_hash": session_hash(request.session.session_key),
        "session_expires_at": request.session.get_expiry_date(),
        "user_agent": ua,
        "device_label": _device_label(ua),
        "failure_count": 0,
    }
    key = endpoint_hash(endpoint)
    sub = PushSubscription.objects.filter(endpoint_hash=key).first()
    if sub is None:
        try:
            with transaction.atomic():
                sub = PushSubscription.objects.create(endpoint_hash=key, **fields)
        except IntegrityError:
            sub = PushSubscription.objects.get(endpoint_hash=key)
    # Re-binding: the browser is the same, the session (or even the account)
    # may be new. The endpoint is the browser's, so it follows whoever is
    # signed in on it now.
    for name, value in fields.items():
        setattr(sub, name, value)
    sub.save()

    # Keep the list bounded: drop the account's oldest subscriptions.
    ids = list(
        PushSubscription.objects.filter(user=request.user)
        .order_by("-created_at")
        .values_list("pk", flat=True)[MAX_PER_USER:]
    )
    if ids:
        PushSubscription.objects.filter(pk__in=ids).delete()
    return sub


def bound_here(request) -> bool:
    """True when this login session already has a push subscription."""
    from .models import PushSubscription

    key = getattr(request.session, "session_key", None)
    if not key:
        return False
    return PushSubscription.objects.filter(
        user=request.user, session_key_hash=session_hash(key)
    ).exists()


def remove_for_session(session_key: str) -> int:
    from .models import PushSubscription

    if not session_key:
        return 0
    deleted, _ = PushSubscription.objects.filter(session_key_hash=session_hash(session_key)).delete()
    return deleted


# --------------------------------------------------------------------------
# Presence: skip the popup on a browser the owner is looking at
# --------------------------------------------------------------------------

def _presence_key(user_id, sub_id) -> str:
    return f"push:seen:{user_id}:{sub_id}"


def mark_seen(user_id, sub_id) -> None:
    """Called by the bell poll from a visible dashboard tab."""
    try:
        cache.set(_presence_key(user_id, sub_id), 1, PRESENCE_SECONDS)
    except Exception:
        pass


def _recently_seen(user_id, sub_id) -> bool:
    try:
        return bool(cache.get(_presence_key(user_id, sub_id)))
    except Exception:
        return False


# --------------------------------------------------------------------------
# Payload
# --------------------------------------------------------------------------

def _app_path(url: str) -> str:
    """An app-surface link as a path the service worker resolves against its
    own scope (so it works on any host and on the dev ``/user`` prefix).
    Links to other hosts stay absolute."""
    url = (url or "").strip()
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme not in ("", "http", "https"):
        return ""
    if parts.netloc and (parts.hostname or "").lower() != str(settings.APP_HOST).lower():
        return url
    path = parts.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    if parts.query:
        path += "?" + parts.query
    if parts.fragment:
        path += "#" + parts.fragment
    return path


def _dashboard_path() -> str:
    from django.urls import reverse

    try:
        return reverse("ud:dashboard", urlconf="onewooz.urls.app")
    except Exception:
        return "/dashboard/"


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def payload_for(n) -> dict:
    url = _app_path(n.action_url)
    if not url:
        # Nowhere specific to go: open the dashboard on this notification's
        # detail popup (the top bar opens ``#notif-<id>``).
        url = f"{_dashboard_path()}#notif-{n.pk}"
    body = " · ".join(p for p in (n.site_name, _clip(n.description, 220)) if p)
    important = n.kind == "order" or n.level == "warning"
    return {
        "id": str(n.pk),
        "title": _clip(n.headline, 120) or "OneWooz",
        "body": body,
        "url": url,
        "kind": n.kind,
        "level": n.level,
        # Orders and warnings stay on screen until the owner acts on them.
        "sticky": important,
        "ts": int(n.created_at.timestamp() * 1000),
    }


# --------------------------------------------------------------------------
# RFC 8291 encryption + RFC 8292 VAPID
# --------------------------------------------------------------------------

def encrypt(plaintext: bytes, p256dh: str, auth: str) -> bytes:
    """``aes128gcm`` Web Push message body for one browser."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    ua_public = _b64u_decode(p256dh)
    auth_secret = _b64u_decode(auth)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)

    as_private = ec.generate_private_key(ec.SECP256R1())
    as_public = as_private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    ecdh_secret = as_private.exchange(ec.ECDH(), ua_key)

    ikm = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=auth_secret,
        info=b"WebPush: info\x00" + ua_public + as_public,
    ).derive(ecdh_secret)
    salt = os.urandom(16)
    cek = HKDF(
        algorithm=hashes.SHA256(), length=16, salt=salt,
        info=b"Content-Encoding: aes128gcm\x00",
    ).derive(ikm)
    nonce = HKDF(
        algorithm=hashes.SHA256(), length=12, salt=salt,
        info=b"Content-Encoding: nonce\x00",
    ).derive(ikm)

    # One record: the plaintext, then the 0x02 "last record" delimiter.
    if len(plaintext) + 1 + 16 > _RECORD_SIZE:
        raise ValueError("Push payload too large.")
    ciphertext = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    header = salt + struct.pack("!IB", _RECORD_SIZE, len(as_public)) + as_public
    return header + ciphertext


def _vapid_authorization(endpoint: str) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    parts = urlsplit(endpoint)
    claims = {
        "aud": f"{parts.scheme}://{parts.netloc}",
        "exp": int(time.time()) + 12 * 60 * 60,
        "sub": getattr(settings, "WEBPUSH_CONTACT", "") or f"https://{settings.MARKETING_HOST}",
    }
    header = {"typ": "JWT", "alg": "ES256"}
    signing_input = (
        _b64u(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64u(json.dumps(claims, separators=(",", ":")).encode())
    )
    der = private_key().sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    token = signing_input + "." + _b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={token}, k={public_key()}"


class Gone(Exception):
    """The browser unsubscribed (404/410): drop the subscription."""


def post(sub, payload: dict, *, urgency: str = "normal", ttl: int = MESSAGE_TTL) -> int:
    """Send one encrypted message to one subscription. Returns the HTTP status.

    Raises :class:`Gone` when the push service says the subscription no
    longer exists, and ``requests`` errors on network failure.
    """
    import requests

    if not _allowed_endpoint(sub.endpoint):
        raise Gone("endpoint no longer allowed")
    body = encrypt(json.dumps(payload, separators=(",", ":")).encode("utf-8"), sub.p256dh, sub.auth)
    response = requests.post(
        sub.endpoint,
        data=body,
        headers={
            "Authorization": _vapid_authorization(sub.endpoint),
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(int(ttl)),
            "Urgency": urgency,
        },
        timeout=10,
        allow_redirects=False,
    )
    if response.status_code in (404, 410):
        raise Gone(str(response.status_code))
    return response.status_code


def _record(sub, ok: bool) -> None:
    now = timezone.now()
    if ok:
        type(sub).objects.filter(pk=sub.pk).update(
            last_success_at=now, failure_count=0, updated_at=now
        )
        return
    failures = (sub.failure_count or 0) + 1
    if failures >= MAX_FAILURES:
        type(sub).objects.filter(pk=sub.pk).delete()
    else:
        type(sub).objects.filter(pk=sub.pk).update(
            last_failure_at=now, failure_count=failures, updated_at=now
        )


def deliver(sub, payload: dict, *, urgency: str = "normal") -> str:
    """Send to one subscription and book-keep it. Returns ``sent``, ``gone``
    or ``failed`` (never raises)."""
    try:
        status = post(sub, payload, urgency=urgency)
    except Gone:
        type(sub).objects.filter(pk=sub.pk).delete()
        return "gone"
    except Exception:
        logger.warning("web push to subscription %s failed", sub.pk, exc_info=True)
        _record(sub, False)
        return "failed"
    if 200 <= status < 300:
        _record(sub, True)
        return "sent"
    logger.warning("web push to subscription %s answered HTTP %s", sub.pk, status)
    if status != 413:
        _record(sub, False)
    return "failed"


def send_notification(notification_id) -> int:
    """Push one bell notification to every eligible browser of its owner.
    Returns how many browsers it was sent to."""
    from .models import Notification, PushSubscription

    if not enabled():
        return 0
    n = Notification.objects.select_related("user").filter(pk=notification_id).first()
    if n is None or n.user is None or not n.user.is_active:
        return 0
    if not n.user.wants_push(n.kind):
        return 0
    # A notification that is days old (a retried or backlogged task) is no
    # longer worth popping up.
    if timezone.now() - n.created_at > timedelta(days=2):
        return 0

    now = timezone.now()
    subs = list(PushSubscription.objects.filter(user=n.user))
    if not subs:
        return 0
    payload = payload_for(n)
    urgency = "high" if n.kind == "order" else "normal"
    sent = 0
    for sub in subs:
        if sub.session_expires_at and sub.session_expires_at < now:
            # The login on that browser has expired: stop until they sign in.
            PushSubscription.objects.filter(pk=sub.pk).delete()
            continue
        if _recently_seen(n.user_id, sub.pk):
            continue  # they are looking at the dashboard on this browser
        if deliver(sub, payload, urgency=urgency) == "sent":
            sent += 1
    return sent


def send_test(sub) -> str:
    """A test popup to one browser, sent right away (not via the queue)."""
    payload = {
        "id": f"test-{int(time.time())}",
        "title": "Notifications are on",
        "body": "This is how new orders and updates will reach you, even when OneWooz is closed.",
        "url": _dashboard_path(),
        "kind": "system",
        "level": "success",
        "sticky": False,
        "ts": int(time.time() * 1000),
    }
    return deliver(sub, payload, urgency="high")
