"""Admin endpoints behind the paste-HTML editor (onewooz.com core pages + blog).

Two JSON endpoints, mounted on the core-page admin (see
``MarketingCorePageAdmin.get_urls``) and used by both the core-page tree editor
and the blog post form:

* ``preview``  returns pasted HTML exactly as it will be saved (cleaned by
  web.content_html, headings anchored by web.core_tree), plus notes on what the
  cleaning changed and reading stats, so the live preview never differs from
  the published page.
* ``upload_image``  stores one image for use inside content and returns its
  public URL, ready to paste into the HTML (or into the prompt you give an AI
  tool). Raster photos are converted to WebP; the file lands under
  ``marketing/content/<yyyy>/<mm>/`` in the default storage (S3 + CDN in
  production), and the upload is written to the audit trail.

Both sit behind ``admin_site.admin_view`` (staff, passkey session, IP
allowlist) and additionally require change permission on core pages or blog
posts.
"""

from __future__ import annotations

import io
import json
import uuid
from pathlib import PurePath

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.http import JsonResponse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_PREVIEW_CHARS = 400_000

_FORMAT_EXT = {"JPEG": "jpg", "PNG": "png", "GIF": "gif", "WEBP": "webp", "BMP": "bmp", "TIFF": "tif"}


def _can_edit(user) -> bool:
    return bool(
        user
        and user.is_active
        and user.is_staff
        and (user.has_perm("web.change_marketingcorepage") or user.has_perm("web.change_marketingblog"))
    )


def _forbidden():
    return JsonResponse({"ok": False, "error": "You do not have permission to edit content."}, status=403)


@require_POST
def preview(request):
    """``{"html": "..."}`` → the cleaned, anchored HTML plus notes and stats."""
    if not _can_edit(request.user):
        return _forbidden()
    try:
        payload = json.loads(request.body or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid request."}, status=400)
    raw = str(payload.get("html") or "")[:MAX_PREVIEW_CHARS]

    from web.content_html import clean_report
    from web.core_tree import analyse

    result = clean_report(raw)
    info = analyse(result.html)
    return JsonResponse({
        "ok": True,
        "html": info.body,
        "notes": result.notes,
        "stats": {
            "words": info.words,
            "minutes": info.minutes,
            "headings": len(info.toc),
            "images": result.html.count("<img"),
            "faqs": len(info.faqs),
            "steps": len(info.steps),
        },
    })


@require_POST
def upload_image(request):
    """Multipart ``image`` → ``{"url", "width", "height", "name"}``."""
    if not _can_edit(request.user):
        return _forbidden()
    upload = request.FILES.get("image")
    if upload is None:
        return JsonResponse({"ok": False, "error": "Choose an image to upload."}, status=400)
    if upload.size > MAX_IMAGE_BYTES:
        return JsonResponse({"ok": False, "error": "Images must be 10 MB or smaller."}, status=400)

    from core.image_webp import _looks_like_raster_image, convert_to_webp

    raw = upload.read()
    if not _looks_like_raster_image(raw[:32]):
        return JsonResponse(
            {"ok": False, "error": "That file is not a supported image (JPG, PNG, WebP, GIF, HEIC or AVIF)."},
            status=400,
        )

    converted = convert_to_webp(raw)
    if converted is not None:
        data, width, height = converted
        ext = "webp"
    else:
        # Already WebP, or a format Pillow cannot re-encode here: keep the
        # original bytes, but only if Pillow can at least read it as an image.
        try:
            from PIL import Image

            with Image.open(io.BytesIO(raw)) as img:
                width, height = img.size
                ext = _FORMAT_EXT.get((img.format or "").upper())
        except Exception:
            ext = None
        if not ext:
            return JsonResponse({"ok": False, "error": "That image could not be read."}, status=400)
        data = raw

    stem = slugify(PurePath(upload.name or "image").stem)[:60] or "image"
    now = timezone.now()
    name = f"marketing/content/{now:%Y/%m}/{stem}-{uuid.uuid4().hex[:8]}.{ext}"
    saved = default_storage.save(name, ContentFile(data))
    url = default_storage.url(saved)
    if url.startswith("/"):
        url = request.build_absolute_uri(url)

    from core import audit

    audit.record(
        "admin.marketing.image_upload",
        request=request,
        actor=request.user,
        actor_kind="admin",
        target_kind="MarketingContentImage",
        target_label=saved,
        context={"bytes": len(data), "width": width, "height": height},
    )
    alt = stem.replace("-", " ").strip().capitalize()
    return JsonResponse({
        "ok": True,
        "url": url,
        "width": width,
        "height": height,
        "name": PurePath(saved).name,
        "alt": alt,
    })
