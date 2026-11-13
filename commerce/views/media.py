"""The media library  upload, browse, describe and delete store media.

Every route is scoped to one owned store. Uploads are validated and probed by
``commerce.media_processing`` before a row is written; browsing, editing the
four descriptor fields, and deletion each have a small endpoint the shared
media modal (``static/js/app/media-library.js``) and the library page both use.
"""

from __future__ import annotations

import json
import os
import uuid

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core import ratelimit
from commerce.context import get_owned_site, store_context
from commerce.media_processing import validate_and_probe
from commerce.models import MediaAsset

# 4 columns × 5 rows  the library grid, and the modal, before the pager.
PER_PAGE = 20


def _filtered(site, request):
    qs = MediaAsset.objects.for_site(site)
    kind = (request.GET.get("kind") or "").strip()
    if kind in (MediaAsset.Kind.IMAGE, MediaAsset.Kind.VIDEO):
        qs = qs.filter(kind=kind)
    q = (request.GET.get("q") or "").strip()
    if q:
        from django.db.models import Q

        qs = qs.filter(
            Q(name__icontains=q)
            | Q(original_filename__icontains=q)
            | Q(alt_text__icontains=q)
        )
    return qs, kind, q


# --------------------------------------------------------------------------
# Library page
# --------------------------------------------------------------------------
@login_required
def library(request, site_id):
    site = get_owned_site(request, site_id)
    qs, kind, q = _filtered(site, request)
    paginator = Paginator(qs, PER_PAGE)
    page = paginator.get_page(request.GET.get("page") or 1)

    context = store_context(request, site, "media")
    context.update(
        {
            "assets": page,
            "active_kind": kind,
            "q": q,
            # Reuse the paginator's cached COUNT rather than issuing a second one.
            "total": paginator.count,
        }
    )
    return render(request, "store/media_library.html", context)


# --------------------------------------------------------------------------
# JSON: paginated list for the modal
# --------------------------------------------------------------------------
@login_required
def media_list(request, site_id):
    site = get_owned_site(request, site_id)
    qs, kind, q = _filtered(site, request)
    page = Paginator(qs, PER_PAGE).get_page(request.GET.get("page") or 1)
    return JsonResponse(
        {
            "ok": True,
            "assets": [a.as_dict() for a in page.object_list],
            "page": page.number,
            "num_pages": page.paginator.num_pages,
            "has_next": page.has_next(),
            "has_prev": page.has_previous(),
            "total": page.paginator.count,
            "kind": kind,
            "q": q,
        }
    )


# --------------------------------------------------------------------------
# JSON: upload (create)
# --------------------------------------------------------------------------
@require_POST
@login_required
def media_upload(request, site_id):
    site = get_owned_site(request, site_id)

    verdict = ratelimit.consume("media.upload", f"user:{request.user.pk}")
    if not verdict.allowed:
        return JsonResponse(
            {"ok": False, "error": "Too many uploads. Please wait a moment and try again."},
            status=429,
        )

    upload = request.FILES.get("file")
    if upload is None:
        return JsonResponse({"ok": False, "error": "No file was received."}, status=400)

    try:
        probe = validate_and_probe(upload)
    except ValidationError as exc:
        return JsonResponse({"ok": False, "error": " ".join(exc.messages)}, status=400)

    original = os.path.basename(upload.name or "")
    stem = original.rsplit(".", 1)[0] if "." in original else original
    name = (request.POST.get("name") or stem or "Untitled").strip()[:255]

    asset = MediaAsset(
        site=site,
        kind=probe.kind,
        name=name,
        alt_text=(request.POST.get("alt_text") or "").strip()[:255],
        original_filename=original[:255],
        mime_type=probe.mime,
        byte_size=probe.size,
        width=probe.width,
        height=probe.height,
    )
    asset.file = upload
    # Brand-tab uploads (logo, favicon, …) ask to keep their original format
    # rather than being transcoded to WebP  favicons in particular need a real
    # PNG/ICO. The universal pre_save converter honours this per-instance flag.
    if str(request.POST.get("no_webp") or "").strip().lower() in ("1", "true", "yes", "on"):
        asset._skip_webp_conversion = True
    asset.save()

    return JsonResponse({"ok": True, "asset": asset.as_dict()})


# --------------------------------------------------------------------------
# JSON: edit descriptors (name, alt text, description, placeholder)
# --------------------------------------------------------------------------
@require_POST
@login_required
def media_update(request, site_id, media_id):
    site = get_owned_site(request, site_id)
    asset = get_object_or_404(MediaAsset.objects.for_site(site), pk=media_id)

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON.")

    asset.name = (payload.get("name") or "").strip()[:255]
    asset.alt_text = (payload.get("alt_text") or "").strip()[:255]
    asset.description = (payload.get("description") or "").strip()
    asset.placeholder = (payload.get("placeholder") or "").strip()[:255]
    asset.save(update_fields=["name", "alt_text", "description", "placeholder", "updated_at"])

    return JsonResponse({"ok": True, "asset": asset.as_dict()})


# --------------------------------------------------------------------------
# Delete (AJAX JSON or plain form post from the library page)
# --------------------------------------------------------------------------
@require_POST
@login_required
def media_delete(request, site_id, media_id):
    site = get_owned_site(request, site_id)
    asset = get_object_or_404(MediaAsset.objects.for_site(site), pk=media_id)
    asset.delete()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": True, "id": str(media_id)})

    from django.contrib import messages

    messages.success(request, "Media removed.")
    return redirect("store:media", site_id=site.id)


# --------------------------------------------------------------------------
# JSON: delete several assets at once (multi-select management)
# --------------------------------------------------------------------------
@require_POST
@login_required
def media_delete_bulk(request, site_id):
    """Delete a batch of the store's assets in one request.

    The multi-select management mode in the library page and modal posts the
    chosen ids here. Each row is deleted through the model's own ``delete()``
    (never a queryset ``delete``) so the stored file is removed with it. Ids
    are validated as UUIDs and scoped to this store, so an id from another
    tenant  or junk  is silently ignored rather than trusted.
    """
    site = get_owned_site(request, site_id)

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON.")

    raw_ids = payload.get("ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        return JsonResponse({"ok": False, "error": "No media selected."}, status=400)

    valid = []
    for x in raw_ids[:500]:
        try:
            valid.append(str(uuid.UUID(str(x))))
        except (ValueError, AttributeError, TypeError):
            continue

    deleted = []
    if valid:
        assets = list(MediaAsset.objects.for_site(site).filter(pk__in=valid))
        for asset in assets:
            aid = str(asset.pk)
            asset.delete()
            deleted.append(aid)

    return JsonResponse({"ok": True, "deleted": deleted})
