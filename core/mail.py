"""Amazon SES email backend and the store→customer mail connection.

OneWooz sends two very different classes of email, and they must not share a
sender:

* **OneWooz's own transactional mail**  account sign-in codes, billing
  receipts  leaves from one fixed address (``DEFAULT_FROM_EMAIL``) over
  whatever SMTP provider is configured (Hostinger / Gmail today). This module
  does not touch it.
* **Store→customer mail**  order updates, store credit, owner notices, and the
  storefront's own shopper sign-in codes  leaves from a *per-store* branded
  address on a single shared, SES-verified sending domain, e.g.
  ``jackandjones@via.onewooz.com`` (see :mod:`tenancy.email`). A conventional
  SMTP relay rejects a From it does not own, so this whole class of mail is sent
  through Amazon SES instead: verifying the domain once authorises every local
  part beneath it, so each store gets its own address for free.

:class:`SESEmailBackend` is a minimal boto3 SESv2 backend. It renders each
Django message to a MIME blob and hands it to ``send_email`` as raw content, so
HTML alternatives, attachments and Reply-To pass through untouched. It uses the
ambient AWS credential chain  the ECS task role in production  so there is no
SMTP password to store anywhere.

:func:`store_mail_connection` opens a connection to whatever backend
``settings.STORE_EMAIL_BACKEND`` names. In development that stays the console
backend, so store mail prints to the runserver output; production points it at
:class:`SESEmailBackend`. Call sites never change between the two.
"""

from __future__ import annotations

import logging
import threading

from django.conf import settings
from django.core.mail import get_connection
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)

__all__ = ["SESEmailBackend", "store_mail_connection"]

_DEFAULT_STORE_BACKEND = "django.core.mail.backends.console.EmailBackend"


def store_mail_connection(*, fail_silently: bool = False):
    """Return a connection for store→customer mail.

    When a dedicated store SMTP relay is configured (``STORE_SMTP_HOST``, e.g.
    Cloudflare's ``smtp.mx.cloudflare.net``), store mail is sent through it: the
    relay accepts any branded From on the verified sending domain, and its
    credentials are kept separate from OneWooz's own transactional SMTP. With no
    such relay set, the backend is whatever ``settings.STORE_EMAIL_BACKEND``
    names  the console backend in dev, :class:`SESEmailBackend` in production 
    so the customer-email call sites are identical across environments.

    ``fail_silently`` is set on the connection itself because a caller that
    passes an explicit ``connection=`` to :meth:`EmailMessage.send` has its own
    ``fail_silently`` argument ignored  the connection's value wins. Customer
    mail is best-effort (a bounce must never 500 a checkout or an owner action),
    so those callers pass ``fail_silently=True``.
    """
    host = (getattr(settings, "STORE_SMTP_HOST", "") or "").strip()
    if host:
        use_ssl = bool(getattr(settings, "STORE_SMTP_USE_SSL", True))
        return get_connection(
            "django.core.mail.backends.smtp.EmailBackend",
            host=host,
            port=getattr(settings, "STORE_SMTP_PORT", 465),
            username=(getattr(settings, "STORE_SMTP_USER", "") or "").strip() or None,
            password=(getattr(settings, "STORE_SMTP_PASSWORD", "") or "").strip() or None,
            use_ssl=use_ssl,        # port 465 → implicit TLS (SMTPS)
            use_tls=not use_ssl,    # port 587 → STARTTLS instead
            fail_silently=fail_silently,
        )
    backend = getattr(settings, "STORE_EMAIL_BACKEND", _DEFAULT_STORE_BACKEND)
    return get_connection(backend, fail_silently=fail_silently)


class SESEmailBackend(BaseEmailBackend):
    """Send Django email messages through Amazon SES (SESv2, raw content).

    The SES client is created lazily and reused. Any local part is accepted as
    long as the sending *domain* is a verified SES identity, which is exactly
    what lets one shared domain serve every store's branded sender address.
    """

    def __init__(self, *, fail_silently: bool = False, **kwargs):
        super().__init__(fail_silently=fail_silently)
        self._client = None
        self._lock = threading.Lock()
        # Fall back to the S3 region so a single ONEWOOZ_AWS_REGION configures
        # both, but allow SES to live in its own region if ever needed.
        self._region = getattr(settings, "STORE_EMAIL_SES_REGION", "") or getattr(
            settings, "AWS_S3_REGION_NAME", ""
        )
        # An optional SES configuration set turns on open/click/bounce event
        # publishing for this mail stream without changing any call site.
        self._configuration_set = getattr(
            settings, "STORE_EMAIL_SES_CONFIGURATION_SET", ""
        )

    def _get_client(self):
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is None:
                try:
                    import boto3
                except ImportError as exc:  # pragma: no cover - dependency present
                    raise ImportError(
                        "boto3 is required to use core.mail.SESEmailBackend"
                    ) from exc
                client_kwargs = {}
                if self._region:
                    client_kwargs["region_name"] = self._region
                self._client = boto3.client("sesv2", **client_kwargs)
        return self._client

    def send_messages(self, email_messages) -> int:
        if not email_messages:
            return 0
        try:
            client = self._get_client()
        except Exception:
            if not self.fail_silently:
                raise
            logger.exception("Could not create the SES client")
            return 0

        sent = 0
        for message in email_messages:
            if self._send(client, message):
                sent += 1
        return sent

    def _send(self, client, message) -> bool:
        recipients = message.recipients()
        if not recipients:
            return False

        try:
            # Render the whole MIME message (headers, HTML alternative,
            # attachments, Reply-To) and let SES relay it verbatim as raw
            # content. CRLF line endings are what SMTP/SES expect.
            raw = message.message().as_bytes(linesep="\r\n")
        except Exception:
            if not self.fail_silently:
                raise
            logger.exception("Could not serialise a message for SES")
            return False

        params = {
            "Content": {"Raw": {"Data": raw}},
            "Destination": {"ToAddresses": recipients},
        }
        if message.from_email:
            # Accepts a bare address or a "Name <addr>" header value; SES checks
            # only the address part against the verified sending domain.
            params["FromEmailAddress"] = message.from_email
        if message.reply_to:
            params["ReplyToAddresses"] = list(message.reply_to)
        if self._configuration_set:
            params["ConfigurationSetName"] = self._configuration_set

        try:
            client.send_email(**params)
        except Exception:
            if not self.fail_silently:
                raise
            logger.exception("SES rejected a store→customer message")
            return False
        return True
