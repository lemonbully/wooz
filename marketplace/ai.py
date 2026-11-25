"""AI credentials for marketplace apps.

An app that calls an AI model (Magic SEO today) reads its key and model IDs
from its own App row (Django admin > Apps > the app > "AI provider"), so each
app can run on its own OpenAI key and models, changed without a deploy. Any
value left blank there falls back to the server settings
(``OPENAI_API_KEY``, ``OPENAI_TEXT_MODEL``, ``OPENAI_IMAGE_MODEL``).

The key is stored encrypted (``core.fields.EncryptedTextField``), is never
rendered back into a form, and is redacted from catalogue revisions
(:func:`fingerprint`).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger("onewooz.marketplace")

#: App fields that hold secrets: never rendered, logged or snapshotted.
SECRET_FIELDS = ("ai_api_key",)


@dataclass(frozen=True)
class Credentials:
    api_key: str
    base_url: str
    #: The app's own model IDs ('' when the admin left them blank).
    text_model: str
    image_model: str
    #: "app" when the key comes from the App row, "server" from settings.
    key_source: str

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def text(self, *fallbacks: str) -> str:
        """The text model: the app's, else the first non-blank fallback (a
        prompt's own model, say), else the server default."""
        for m in (self.text_model, *fallbacks):
            if (m or "").strip():
                return m.strip()
        return getattr(settings, "OPENAI_TEXT_MODEL", "")

    def image(self) -> str:
        return self.image_model or getattr(settings, "OPENAI_IMAGE_MODEL", "") or "gpt-image-1"


def credentials(slug: str) -> Credentials:
    """The AI credentials for the app with ``slug``, with server fallbacks."""
    key = text_model = image_model = ""
    try:
        from .models import App

        row = (App.objects.filter(slug=slug).order_by()
               .values_list("ai_api_key", "ai_text_model", "ai_image_model").first())
        if row:
            key, text_model, image_model = ((v or "").strip() for v in row)
    except Exception:
        # A key that cannot be decrypted (rotated FIELD_ENCRYPTION_KEY) must
        # not take the app down: fall back to the server key, loudly.
        logger.exception("could not read AI credentials for app %s", slug)
    server_key = getattr(settings, "OPENAI_API_KEY", "") or ""
    return Credentials(
        api_key=key or server_key,
        base_url=(getattr(settings, "OPENAI_BASE_URL", "") or "https://api.openai.com/v1").rstrip("/"),
        text_model=text_model,
        image_model=image_model,
        key_source="app" if key else "server",
    )


def mask(key: str) -> str:
    """``sk-proj…a1B2``: enough to recognise a key, useless to anyone else."""
    key = (key or "").strip()
    if not key:
        return ""
    if len(key) <= 12:
        return "•" * 8
    head = key[:7] if key.startswith("sk-") else key[:3]
    return f"{head}…{key[-4:]}"


def fingerprint(key: str) -> str:
    """What a revision records instead of the key: changes stay visible."""
    key = (key or "").strip()
    return f"set:{hashlib.sha256(key.encode()).hexdigest()[:10]}" if key else ""
