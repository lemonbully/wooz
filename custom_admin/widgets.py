"""A TinyMCE rich-text widget for the Django admin.

Reuses the same free (GPL) TinyMCE 6 build the owner dashboard's content editor
loads from jsDelivr, so authoring core-page and blog content in the admin has the
same toolbar and features. The admin surface's CSP admits jsDelivr (see
``core.middleware``  the editor hosts are allowed on the APP and ADMIN
surfaces). The init script is served same-origin from our static.
"""

from __future__ import annotations

from django import forms


class TinyMCEWidget(forms.Textarea):
    """A textarea that TinyMCE mounts over (class ``admin-richtext``)."""

    def __init__(self, attrs=None):
        attrs = dict(attrs or {})
        classes = (attrs.get("class", "") + " admin-richtext").strip()
        attrs["class"] = classes
        super().__init__(attrs)

    class Media:
        js = (
            "https://cdn.jsdelivr.net/npm/tinymce@6/tinymce.min.js",
            "js/admin/tinymce_admin_init.js",
        )
