"""Email-safe image URLs for marketing emails.

Every image a store uploads is transcoded to WebP (``core.image_webp``). WebP is
fine on the storefront, but classic Outlook for Windows (and a few older mail
apps) cannot display it, so a product grid or hero would arrive as empty boxes.

:func:`email_safe_url` hands the email a JPEG instead: the first time a WebP
from the store's media storage is used in an email, a JPEG rendition is written
next to the uploads (``email-renditions/<hash>.jpg``, at most 1200px wide) and
its URL is cached; every later send reuses it. Anything that is not a WebP in
our own storage (a JPEG/PNG, an external URL) is returned as an absolute URL
unchanged. Any failure falls back to the original URL: an image is never worth
failing a send over.
"""

from __future__ import annotations

import hashlib
import io
import logging

logger = logging.getLogger(__name__)

_MAX_WIDTH = 1200
_CACHE_TTL = 60 * 60 * 24 * 30


def absolute(url: str) -> str:
    """Make a same-origin path (dev media/static) absolute for a sent email."""
    if not url or not url.startswith("/") or url.startswith("//"):
        return url or ""
    try:
        from core.hosts import Surface
        from core.links import surface_url

        return surface_url(Surface.APP, url)
    except Exception:
        return url


def _storage_name(url: str) -> str:
    """The media-storage name behind ``url``, or "" when it is not ours."""
    from django.conf import settings
    from django.core.files.storage import default_storage

    prefixes = []
    try:
        base = default_storage.url("")
        if base:
            prefixes.append(base)
    except Exception:
        pass
    media = getattr(settings, "MEDIA_URL", "") or ""
    if media:
        prefixes.append(media)
        prefixes.append(absolute(media))
    for prefix in prefixes:
        if prefix and url.startswith(prefix):
            name = url[len(prefix):].split("?", 1)[0].lstrip("/")
            return name
    return ""


def _rendition(name: str) -> str:
    from django.core.cache import cache
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage
    from PIL import Image

    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:24]
    key = f"ow:emailimg:{digest}"
    cached = cache.get(key)
    if cached:
        return cached

    target = f"email-renditions/{digest}.jpg"
    if not default_storage.exists(target):
        with default_storage.open(name, "rb") as fh:
            img = Image.open(fh)
            img.load()
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            flat = Image.new("RGB", img.size, (255, 255, 255))
            flat.paste(img, mask=img.split()[-1])
            img = flat
        else:
            img = img.convert("RGB")
        if img.width > _MAX_WIDTH:
            ratio = _MAX_WIDTH / float(img.width)
            img = img.resize((_MAX_WIDTH, max(1, int(img.height * ratio))), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85, optimize=True, progressive=True)
        target = default_storage.save(target, ContentFile(buf.getvalue()))
    url = absolute(default_storage.url(target))
    cache.set(key, url, _CACHE_TTL)
    return url


def email_safe_url(url: str) -> str:
    """An absolute URL every mail client can display (see module docstring)."""
    url = (url or "").strip()
    if not url:
        return ""
    try:
        name = _storage_name(url)
        if name and name.lower().endswith(".webp"):
            return _rendition(name)
    except Exception:
        logger.warning("email image rendition failed for %s", url, exc_info=True)
    return absolute(url)
