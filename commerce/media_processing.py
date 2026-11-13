"""Secure validation and probing for media-library uploads.

An owner can upload "any image or video format", but the file is never trusted
on the strength of its name or the ``Content-Type`` the browser claims. Every
upload passes through :func:`validate_and_probe`, which enforces, in order:

1. **Size**  a hard cap per kind, so one upload cannot exhaust disk or memory.
2. **Extension allowlist**  a known image/video extension, nothing executable.
3. **Magic-byte sniff**  the file's actual leading bytes must match a known
   image/video signature. This is what stops a renamed ``.exe`` (or a polyglot
   crafted to look like an image to the browser) from being stored as media.
4. **Declared vs. actual agreement**  the sniffed kind must match the
   extension's kind; a ``.png`` that is really an MP4 is rejected.
5. **Decode verification (images)**  the bytes are handed to Pillow, which must
   parse and verify them as a real raster image within a pixel-count ceiling
   (a decompression-bomb guard). SVG and other non-raster "images" are refused.

This is defence-in-depth, not an antivirus engine: combined with the endpoint's
``login_required`` owner scoping, CSRF protection and the ``media.upload`` rate
limit, it is the practical, enforceable barrier against hostile or automated
uploads. A dedicated AV scanner (e.g. ClamAV) can later be slotted in at the
marked hook without touching callers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError

# --------------------------------------------------------------------------
# Limits (defaults; overridable in settings so they live in one reviewed place)
# --------------------------------------------------------------------------

MAX_IMAGE_BYTES = getattr(settings, "MEDIA_LIBRARY_MAX_IMAGE_BYTES", 15 * 1024 * 1024)
MAX_VIDEO_BYTES = getattr(settings, "MEDIA_LIBRARY_MAX_VIDEO_BYTES", 256 * 1024 * 1024)
# Decompression-bomb ceiling for raster images (~40 megapixels).
MAX_IMAGE_PIXELS = getattr(settings, "MEDIA_LIBRARY_MAX_IMAGE_PIXELS", 40_000_000)

# Extension -> canonical MIME, grouped by kind. The allowlist is deliberately
# explicit: an extension not listed here is refused, whatever its bytes say.
IMAGE_EXTENSIONS = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
    "tif": "image/tiff", "tiff": "image/tiff", "avif": "image/avif",
    "heic": "image/heic", "heif": "image/heif",
}
VIDEO_EXTENSIONS = {
    "mp4": "video/mp4", "m4v": "video/x-m4v", "mov": "video/quicktime",
    "webm": "video/webm", "ogv": "video/ogg", "mkv": "video/x-matroska",
    "avi": "video/x-msvideo", "3gp": "video/3gpp",
}


@dataclass(frozen=True)
class Probe:
    kind: str  # "image" | "video"
    mime: str
    width: int | None
    height: int | None
    size: int
    extension: str


# --------------------------------------------------------------------------
# Magic-byte sniffing
# --------------------------------------------------------------------------

def _sniff_kind(head: bytes) -> str | None:
    """Return ``"image"``, ``"video"`` or ``None`` from a file's leading bytes."""
    if len(head) < 12:
        return None

    # --- Images ---
    if head[:3] == b"\xff\xd8\xff":
        return "image"                                   # JPEG
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image"                                   # PNG
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image"                                   # GIF
    if head[:2] == b"BM":
        return "image"                                   # BMP
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image"                                   # TIFF
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image"                                   # WEBP

    # --- Video ---
    if head[:4] == b"\x1aE\xdf\xa3":
        return "video"                                   # Matroska / WebM
    if head[:4] == b"OggS":
        return "video"                                   # Ogg (treated as video)
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "video"                                   # AVI

    # --- ISO base-media (ftyp): mp4/mov/m4v/3gp AND avif/heic ---
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"avif", b"avis", b"heic", b"heix", b"mif1", b"msf1", b"hevc"):
            return "image"                               # HEIF / AVIF family
        return "video"                                   # mp4 / mov / 3gp / m4v

    return None


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def _human(nbytes: int) -> str:
    mb = nbytes / (1024 * 1024)
    return f"{mb:.0f} MB" if mb >= 1 else f"{nbytes / 1024:.0f} KB"


def validate_and_probe(upload) -> Probe:
    """Validate an uploaded file and return probed metadata, or raise.

    ``upload`` is a Django ``UploadedFile``. On success the file's read cursor
    is reset to the start so the caller can save it unchanged. On failure a
    :class:`django.core.exceptions.ValidationError` is raised with a message
    safe to show the owner.
    """
    name = os.path.basename(upload.name or "")
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""

    if ext in IMAGE_EXTENSIONS:
        declared_kind, mime, max_bytes = "image", IMAGE_EXTENSIONS[ext], MAX_IMAGE_BYTES
    elif ext in VIDEO_EXTENSIONS:
        declared_kind, mime, max_bytes = "video", VIDEO_EXTENSIONS[ext], MAX_VIDEO_BYTES
    else:
        raise ValidationError(
            "That file type isn’t supported. Upload an image "
            "(JPG, PNG, GIF, WEBP, BMP, TIFF, AVIF, HEIC) or a video "
            "(MP4, MOV, WEBM, MKV, AVI, OGV, 3GP)."
        )

    size = int(getattr(upload, "size", 0) or 0)
    if size <= 0:
        raise ValidationError("That file is empty.")
    if size > max_bytes:
        raise ValidationError(
            f"That {declared_kind} is too large ({_human(size)}). "
            f"The limit is {_human(max_bytes)}."
        )

    # Read the header for sniffing, then rewind.
    try:
        upload.seek(0)
        head = upload.read(32)
        upload.seek(0)
    except Exception:
        raise ValidationError("That file could not be read.")

    sniffed = _sniff_kind(head)
    if sniffed is None:
        raise ValidationError(
            "That file doesn’t look like a real image or video. "
            "It may be corrupt or disguised."
        )
    if sniffed != declared_kind:
        raise ValidationError(
            "That file’s contents don’t match its extension. "
            "Rename it to its true format and try again."
        )

    width = height = None
    if declared_kind == "image":
        width, height = _verify_image(upload, ext)

    try:
        upload.seek(0)
    except Exception:
        pass

    return Probe(
        kind=declared_kind, mime=mime, width=width, height=height,
        size=size, extension=ext,
    )


def _verify_image(upload, ext: str) -> tuple[int | None, int | None]:
    """Decode-verify a raster image with Pillow and return its dimensions.

    HEIC/AVIF need a Pillow plugin that may be absent; for those we accept the
    magic-byte verification already done and skip Pillow rather than falsely
    rejecting a valid file.
    """
    try:
        from PIL import Image, UnidentifiedImageError
    except Exception:
        return None, None

    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

    if ext in ("heic", "heif", "avif"):
        # Format support is plugin-dependent; the signature check stands in.
        return None, None

    try:
        upload.seek(0)
        with Image.open(upload) as probe:
            probe.verify()  # integrity check; consumes the object
        upload.seek(0)
        with Image.open(upload) as probe2:
            width, height = probe2.size
    except Image.DecompressionBombError:
        raise ValidationError("That image is too large to process safely.")
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise ValidationError(
            "That image couldn’t be read. It may be corrupt or not a real image."
        )
    finally:
        try:
            upload.seek(0)
        except Exception:
            pass

    if MAX_IMAGE_PIXELS and width and height and width * height > MAX_IMAGE_PIXELS:
        raise ValidationError("That image’s resolution is too high.")
    return width, height


def clone_asset_file(asset, prefix: str, max_length: int = 100) -> str:
    """Copy a library asset's file into ``prefix`` and return the new path.

    Product galleries and category images own their files rather than sharing
    the library's, so an owner deleting a library asset can never blank a saved
    product image. The copy is written through the configured default storage,
    which resolves any name collision. ``max_length`` is the receiving
    FileField's column length (Django's default 100); the storage trims the
    filename to fit, so a long library filename can't overflow the column.
    """
    from django.core.files.storage import default_storage

    base = os.path.basename(asset.file.name)
    target = f"{prefix.rstrip('/')}/{base}"
    with asset.file.open("rb") as fh:
        return default_storage.save(target, fh, max_length=max_length)
