"""The store media library.

A :class:`MediaAsset` is one uploaded file  an image or a video  that belongs
to exactly one :class:`~tenancy.models.Site`. The library is the single place an
owner uploads and curates media; every other surface (product galleries,
category images, hero banners and section content) picks from it through one
shared modal rather than re-uploading.

The reference tool scattered image columns across many models and re-uploaded
the same file for every use. Here a file is uploaded once, validated once
(``commerce.media_processing``), and described once  name, alt text,
description and a placeholder caption  then reused by reference.
"""

from __future__ import annotations

from django.db import models

from core.models import TenantScopedModel

__all__ = ["MediaAsset", "media_upload_to"]


def media_upload_to(instance: "MediaAsset", filename: str) -> str:
    """Storage path for a library file.

    Namespaced by site and by the asset's own (unguessable UUIDv7) id, so two
    stores never share a directory and a file name can never be guessed from
    another asset's URL.
    """
    return f"sites/{instance.site_id}/library/{instance.id}/{filename}"


class MediaAsset(TenantScopedModel):
    """One uploaded image or video in a store's media library."""

    class Kind(models.TextChoices):
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"

    site = models.ForeignKey(
        "tenancy.Site", on_delete=models.CASCADE, related_name="media_assets"
    )

    file = models.FileField(upload_to=media_upload_to, max_length=500)
    kind = models.CharField(max_length=8, choices=Kind.choices, db_index=True)

    # Owner-editable descriptors  the four fields the edit dialog exposes.
    name = models.CharField(max_length=255, blank=True, default="")
    alt_text = models.CharField(max_length=255, blank=True, default="")
    description = models.TextField(blank=True, default="")
    # A short placeholder / caption string an owner can attach (e.g. the text a
    # field shows before an image loads, or a caption to surface with it).
    placeholder = models.CharField(max_length=255, blank=True, default="")

    # Probe metadata, captured once at upload from the validated bytes. Never
    # trusted from the client  see commerce.media_processing.
    original_filename = models.CharField(max_length=255, blank=True, default="")
    mime_type = models.CharField(max_length=100, blank=True, default="")
    byte_size = models.PositiveBigIntegerField(default=0)
    # Pixel dimensions for images; null for video (probing video needs ffmpeg,
    # which is out of scope here).
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("site", "kind", "-created_at")),
            models.Index(fields=("site", "-created_at")),
        ]

    def __str__(self) -> str:
        return self.name or self.original_filename or str(self.pk)

    @property
    def url(self) -> str:
        try:
            return self.file.url
        except ValueError:
            return ""

    @property
    def display_name(self) -> str:
        return self.name or self.original_filename or "Untitled"

    @property
    def is_image(self) -> bool:
        return self.kind == self.Kind.IMAGE

    @property
    def is_video(self) -> bool:
        return self.kind == self.Kind.VIDEO

    def as_dict(self) -> dict:
        """The shape the media modal / picker JS consumes."""
        return {
            "id": str(self.pk),
            "url": self.url,
            "kind": self.kind,
            "name": self.display_name,
            "alt": self.alt_text,
            "description": self.description,
            "placeholder": self.placeholder,
            "mime": self.mime_type,
            "width": self.width,
            "height": self.height,
            "size": self.byte_size,
        }

    def delete(self, *args, **kwargs):
        """Remove the stored file alongside the row.

        Product and category selections copy the file into their own storage
        (see ``commerce.media_processing.clone_asset_file``), so deleting a
        library asset never breaks a saved product image; only direct URL
        references in storefront content (which were always arbitrary URLs)
        would 404, exactly as they would today.
        """
        stored = self.file
        result = super().delete(*args, **kwargs)
        try:
            if stored:
                stored.delete(save=False)
        except Exception:
            # A missing file must not turn a delete into a 500.
            pass
        return result
