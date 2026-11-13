"""Store settings  identity, currency, contact and store-wide toggles."""

from __future__ import annotations

import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from commerce.context import get_owned_site, store_context
from commerce.forms import SiteForm

# Formats the invoice PDF can always draw (commerce.label_pdf reads the
# signature with Pillow); the library also holds AVIF/HEIC, which may not.
_SIGNATURE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_SIGNATURE_MIMES = {"image/png", "image/jpeg", "image/webp"}


def _picked_signature(site, asset_id):
    """``(asset, error)`` for the media-library image picked as the signature."""
    from commerce.models import MediaAsset

    try:
        asset = (
            MediaAsset.objects.for_site(site)
            .filter(pk=asset_id, kind=MediaAsset.Kind.IMAGE)
            .first()
        )
    except Exception:  # malformed id
        asset = None
    if asset is None or not asset.file:
        return None, "That image is no longer in your media library. Please choose it again."
    ext = os.path.splitext(asset.file.name or "")[1].lower()
    mime = (getattr(asset, "mime_type", "") or "").lower()
    if ext not in _SIGNATURE_EXTS and mime not in _SIGNATURE_MIMES:
        return None, "Choose a PNG, JPG or WEBP image for the signature."
    return asset, ""


@login_required
def settings(request, site_id):
    site = get_owned_site(request, site_id)
    # Read before binding: an invalid POST mutates the instance, and the page
    # must keep previewing the *saved* signature.
    signature_url = ""
    if site.authorized_signature:
        try:
            signature_url = site.authorized_signature.url
        except Exception:
            signature_url = ""
    signature_error = ""

    if request.method == "POST":
        old_signature = site.authorized_signature.name if site.authorized_signature else ""
        form = SiteForm(request.POST, instance=site)

        # The signature comes from the media library modal: the picked asset's
        # id (copied into the store's own signatures/ folder, so deleting it
        # from the library later never blanks the invoice), or a removal.
        asset_id = (request.POST.get("media_signature") or "").strip()
        remove_signature = request.POST.get("remove_signature") == "1"
        asset = None
        if asset_id:
            asset, signature_error = _picked_signature(site, asset_id)

        if form.is_valid() and not signature_error:
            new_signature = old_signature
            if asset is not None:
                from commerce.media_processing import clone_asset_file

                try:
                    new_signature = clone_asset_file(asset, f"signatures/{site.id}")
                except Exception:
                    signature_error = "Couldn’t use that image. Please try again or upload it again."
            elif remove_signature:
                new_signature = ""

            if not signature_error:
                site = form.save(commit=False)
                site.authorized_signature = new_signature
                site.save()
                # A replaced or removed signature leaves no orphan file behind.
                # (Only the store's own copy; the library asset is untouched.)
                if old_signature and old_signature != new_signature:
                    try:
                        site.authorized_signature.storage.delete(old_signature)
                    except Exception:
                        pass
                messages.success(request, "Store settings saved.")
                return redirect("store:settings", site_id=site.id)
    else:
        form = SiteForm(instance=site)

    from billing import gst

    context = store_context(request, site, "settings")
    context.update({
        "form": form,
        "gstin_state": gst.state_from_gstin(site.gstin),
        "signature_url": signature_url,
        "signature_error": signature_error,
    })
    return render(request, "store/settings.html", context)
