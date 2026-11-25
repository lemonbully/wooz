"""Universal auto-conversion of uploaded raster images to WebP.

Whatever format an image arrives in  JPEG, PNG, GIF, BMP, TIFF, HEIC/HEIF,
AVIF  it is transcoded to WebP *once*, at the moment it is first written, no
matter which model, form, view, admin or management command uploaded it. There
is a single enforcement point: one project-wide ``pre_save`` receiver
(:func:`convert_uploaded_images_to_webp`) connected for **every** model. A
model does not opt in and a caller does not opt out; if bytes land in a
``FileField``/``ImageField`` and they decode as a raster image, they leave as
WebP.

Why ``pre_save`` and not a custom field or storage backend:

* A ``pre_save`` signal fires before Django commits any file field to storage
  (``FileField.pre_save`` runs later, inside ``_save_table``), so we can swap
  the field's value and the *original bytes never touch disk*  no temporary
  non-WebP file is written and then replaced.
* It is genuinely universal without editing 20 models: ``ImageField`` is a
  subclass of ``FileField``, and the same store uses plain ``FileField`` for
  both images and videos (``commerce.MediaAsset``), so the decision must be made
  from the *bytes*, not the field type. We sniff, decode with Pillow, and skip
  anything that is not a raster image (videos, PDFs, SVG) or is already WebP.

Only *newly uploaded* files are touched: a ``FieldFile`` loaded from the
database is already committed (``_committed is True``) and is left alone, so
re-saving a row never re-transcodes or double-processes an existing image.

Formats Pillow cannot decode in this environment (an HEIC/AVIF with no codec
installed) are left exactly as uploaded rather than rejected  the file is still
valid, it simply is not converted. Installing ``pillow-heif`` (in
``requirements.txt``) makes HEIC/HEIF/AVIF convert too; it is registered below
when present.
"""

from __future__ import annotations

import io
import logging
import os

from django.conf import settings
from django.core.files.base import ContentFile
from django.db.models import FileField
from django.db.models.signals import pre_save

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Tunables (overridable in settings so they live in one reviewed place)
# --------------------------------------------------------------------------

# 0-100. 82 is a well-established default: visually lossless for photographs at
# a fraction of JPEG/PNG size.
WEBP_QUALITY = getattr(settings, "IMAGE_WEBP_QUALITY", 82)
# Encoder effort 0-6; 6 is slowest/smallest and fine for one-off uploads.
WEBP_METHOD = getattr(settings, "IMAGE_WEBP_METHOD", 6)
# Decompression-bomb ceiling shared with commerce.media_processing.
MAX_IMAGE_PIXELS = getattr(settings, "MEDIA_LIBRARY_MAX_IMAGE_PIXELS", 40_000_000)
# Longest side kept after conversion (0 = no cap). Phone photos arrive at
# 4000+ px; nothing on a storefront displays wider than ~2000 px, so the extra
# pixels only cost CDN transfer on every visit.
MAX_DIMENSION = int(getattr(settings, "IMAGE_MAX_DIMENSION", 2000) or 0)


def _cap(frame):
    """Downscale a still frame so its longest side is at most MAX_DIMENSION."""
    if MAX_DIMENSION and max(frame.size) > MAX_DIMENSION:
        from PIL import Image

        frame = frame.copy()
        frame.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.Resampling.LANCZOS)
    return frame

# Broaden Pillow's decoder set to HEIC/HEIF/AVIF when the optional plugin is
# installed, so those phone-camera formats convert instead of passing through.
try:  # pragma: no cover - depends on an optional wheel being present
    import pillow_heif

    pillow_heif.register_heif_opener()
    register_avif = getattr(pillow_heif, "register_avif_opener", None)
    if callable(register_avif):
        register_avif()
except Exception:
    pass


# --------------------------------------------------------------------------
# Byte sniffing  cheap gate so we never read a 256 MB video fully into memory
# --------------------------------------------------------------------------

def _looks_like_raster_image(head: bytes) -> bool:
    """True if a file's leading bytes match a raster-image signature.

    Deliberately local (not imported from ``commerce.media_processing``) so this
    low-level ``core`` utility carries no dependency on an app above it. WebP is
    included so it is read and recognised  then declined as already-WebP by the
    decode step  rather than mistaken for a video.
    """
    if len(head) < 12:
        return False
    if head[:3] == b"\xff\xd8\xff":                       # JPEG
        return True
    if head[:8] == b"\x89PNG\r\n\x1a\n":                  # PNG
        return True
    if head[:6] in (b"GIF87a", b"GIF89a"):               # GIF
        return True
    if head[:2] == b"BM":                                # BMP
        return True
    if head[:4] in (b"II*\x00", b"MM\x00*"):             # TIFF
        return True
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":    # WEBP
        return True
    if head[4:8] == b"ftyp":                             # ISO base media
        brand = head[8:12]
        return brand in (b"avif", b"avis", b"heic", b"heix", b"mif1", b"msf1", b"hevc")
    return False


# --------------------------------------------------------------------------
# Conversion
# --------------------------------------------------------------------------

def convert_to_webp(raw: bytes) -> tuple[bytes, int, int] | None:
    """Transcode raster-image bytes to WebP.

    Returns ``(webp_bytes, width, height)``, or ``None`` meaning "leave the
    upload untouched": the bytes are not a raster image this Pillow can decode
    (a video, PDF, SVG, or a codec-less HEIC/AVIF), or they are already WebP.

    Transparency (PNG/GIF/palette alpha) is preserved; animated GIF/PNG are
    re-encoded as animated WebP with their frame timing; EXIF orientation on a
    still photo is baked in so the WebP displays upright.
    """
    try:
        from PIL import Image, ImageOps, ImageSequence
    except Exception:
        return None

    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS or None

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        # Not a decodable raster image (or a decompression bomb): don't convert.
        return None

    is_animated = getattr(img, "is_animated", False) and getattr(img, "n_frames", 1) > 1
    if (img.format or "").upper() == "WEBP" and (
        is_animated or not MAX_DIMENSION or max(img.size) <= MAX_DIMENSION
    ):
        return None  # already WebP at a sensible size; nothing to gain

    try:
        out = io.BytesIO()
        if is_animated:
            # Animated images keep their size: resizing every frame is costly and
            # they are small banners/stickers in practice.
            frames, durations = [], []
            for frame in ImageSequence.Iterator(img):
                frames.append(frame.convert("RGBA"))
                durations.append(frame.info.get("duration", img.info.get("duration", 100)))
            first, rest = frames[0], frames[1:]
            first.save(
                out, format="WEBP", save_all=True, append_images=rest,
                duration=durations, loop=img.info.get("loop", 0),
                quality=WEBP_QUALITY, method=WEBP_METHOD,
            )
            width, height = first.size
        else:
            frame = ImageOps.exif_transpose(img) or img
            if frame.mode in ("RGBA", "LA") or (
                frame.mode == "P" and "transparency" in frame.info
            ):
                frame = frame.convert("RGBA")
            else:
                frame = frame.convert("RGB")
            # Cap after the mode conversion so the resize runs in full colour
            # (Pillow would fall back to nearest-neighbour on palette images).
            frame = _cap(frame)
            frame.save(out, format="WEBP", quality=WEBP_QUALITY, method=WEBP_METHOD)
            width, height = frame.size
        return out.getvalue(), width, height
    except Exception:
        # A real image we failed to re-encode: keep the original rather than
        # turning the user's save into a 500 or losing their upload.
        logger.warning(
            "WebP conversion failed for a %s image; keeping the original.",
            (img.format or "?"), exc_info=True,
        )
        return None


# --------------------------------------------------------------------------
# The universal pre_save receiver
# --------------------------------------------------------------------------

# {model_class: (filefield_name, ...)}  computed once per model so the global
# receiver, which runs on every save in the project, stays close to free for
# the vast majority of models that have no file fields.
_FILE_FIELDS_CACHE: dict[type, tuple[str, ...]] = {}


def _file_field_names(model: type) -> tuple[str, ...]:
    names = _FILE_FIELDS_CACHE.get(model)
    if names is None:
        names = tuple(
            f.name for f in model._meta.concrete_fields
            if isinstance(f, FileField)
        )
        _FILE_FIELDS_CACHE[model] = names
    return names


def _webp_name(current_name: str) -> str:
    """A ``.webp`` filename derived from the upload's own basename."""
    base = os.path.basename(current_name or "") or "image"
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return f"{stem or 'image'}.webp"


def convert_uploaded_images_to_webp(sender, instance, **kwargs):
    """``pre_save`` receiver: swap any newly uploaded image field to WebP.

    Connected for all models (no ``sender``). For each file field holding an
    *uncommitted* (freshly assigned) file, sniff and decode it; if it is a
    convertible raster image, replace the field with WebP bytes under a
    ``.webp`` name before Django writes it to storage.

    A caller may opt a single save out of conversion by setting
    ``instance._skip_webp_conversion = True`` before saving (used by the storefront
    editor's Brand tab, which keeps logos and favicons in their original format).
    """
    if getattr(instance, "_skip_webp_conversion", False):
        return
    for name in _file_field_names(sender):
        fieldfile = getattr(instance, name, None)
        # Empty field, or a file already in storage (loaded from the DB or left
        # unchanged): nothing to convert.
        if not fieldfile or getattr(fieldfile, "_committed", True):
            continue

        try:
            fieldfile.open("rb")
            fieldfile.seek(0)
            head = fieldfile.read(32)
            if not _looks_like_raster_image(head):
                fieldfile.seek(0)
                continue  # video / PDF / other: skip without reading it all
            fieldfile.seek(0)
            raw = fieldfile.read()
            fieldfile.seek(0)
        except Exception:
            # Unreadable here for any reason: let the normal save proceed.
            continue

        if not raw:
            continue

        result = convert_to_webp(raw)
        if result is None:
            continue  # not convertible / already WebP  leave the upload as-is

        webp, width, height = result
        setattr(instance, name, ContentFile(webp, name=_webp_name(fieldfile.name)))

        # Keep probe-metadata models (e.g. commerce.MediaAsset) consistent with
        # the bytes we just swapped in. Presence of ``mime_type`` marks such a
        # model, which makes updating its sibling descriptors collision-safe.
        if str(getattr(instance, "mime_type", "") or "").startswith("image/"):
            instance.mime_type = "image/webp"
            if hasattr(instance, "byte_size"):
                instance.byte_size = len(webp)
            if hasattr(instance, "width") and width:
                instance.width = width
            if hasattr(instance, "height") and height:
                instance.height = height


def connect() -> None:
    """Wire the universal receiver. Idempotent via ``dispatch_uid``."""
    pre_save.connect(
        convert_uploaded_images_to_webp,
        dispatch_uid="core.image_webp.convert_uploaded_images_to_webp",
    )
