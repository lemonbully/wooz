"""A B2B store's certificates, in the one shape every surface reads.

The owner adds certificates in the content editor's Certificates tab
(``site.content["certificates"]``): a name, a preview image and the
certificate document itself (a PDF, or an image of the certificate). The
public /certificates/ page, the SEO tab's page list and the page's JSON-LD all
read them through :func:`public_certificates`, so they agree on what exists.

Older content (the reference platform) stored ``{"name", "file"}`` with no
separate image; both shapes are accepted.
"""

from __future__ import annotations

from urllib.parse import urlparse

_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".avif")


def is_image_url(url) -> bool:
    try:
        return urlparse(str(url or "").strip()).path.lower().endswith(_IMAGE_EXT)
    except ValueError:
        return False


def is_pdf_url(url) -> bool:
    try:
        return urlparse(str(url or "").strip()).path.lower().endswith(".pdf")
    except ValueError:
        return False


def _s(value) -> str:
    return str(value).strip() if value not in (None, "") else ""


def public_certificates(content) -> list[dict]:
    """``[{title, image, file, thumb, file_type, document}]`` in the owner's order.

    * ``thumb``  what the card shows: the preview image, else the file when the
      file itself is an image ("" → the page draws a document icon).
    * ``file``  what opens on click: the document, else the image.
    * ``file_type``  ``"image"`` or ``"pdf"``, which the preview modal uses to
      pick an ``<img>`` or an ``<iframe>``.
    * ``document``  the uploaded document URL when it is not just the image.

    A row with neither an image nor a file is skipped (an empty card is noise).
    """
    items = (content or {}).get("certificates") if isinstance(content, dict) else None
    out = []
    for raw in items if isinstance(items, list) else []:
        if not isinstance(raw, dict):
            continue
        image = _s(raw.get("image"))
        file = _s(raw.get("file"))
        if not (image or file):
            continue
        target = file or image
        out.append({
            "title": _s(raw.get("title")) or _s(raw.get("name")) or "Certificate",
            "image": image,
            "file": target,
            "thumb": image or (file if is_image_url(file) else ""),
            "file_type": "image" if is_image_url(target) else "pdf",
            "document": file if (file and file != image) else "",
        })
    return out
