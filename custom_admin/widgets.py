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


class HtmlContentWidget(forms.Textarea):
    """Paste-HTML editor for onewooz.com core pages and blog posts.

    A plain code textarea (the stored value is exactly what you paste, cleaned
    on save by web.content_html) enhanced by js/admin/html_editor.js into:
    write / preview / side-by-side modes, a live preview rendered with the real
    site stylesheets, notes on what cleaning will change, an image uploader
    that returns ready-to-paste URLs, and a formatting guide. Replaces TinyMCE
    for this content, so no third-party editor script is loaded.
    """

    template_name = "django/forms/widgets/textarea.html"

    def __init__(self, attrs=None):
        attrs = dict(attrs or {})
        attrs.setdefault("rows", 22)
        attrs["class"] = (attrs.get("class", "") + " ow-html-src").strip()
        attrs.setdefault("spellcheck", "false")
        attrs.setdefault("autocomplete", "off")
        super().__init__(attrs)

    def get_context(self, name, value, attrs):
        from django.templatetags.static import static
        from django.urls import reverse

        context = super().get_context(name, value, attrs)
        context["widget"]["attrs"].update(editor_attrs(reverse, static))
        return context

    class Media:
        css = {"all": ("css/admin/html-editor.css",)}
        js = ("js/admin/html_editor.js",)


def editor_attrs(reverse, static) -> dict[str, str]:
    """The data attributes html_editor.js reads (shared with the tree editor)."""
    return {
        "data-ow-html": "1",
        "data-preview-url": reverse("admin:web_content_preview"),
        "data-upload-url": reverse("admin:web_content_upload_image"),
        "data-css": ",".join(
            static(p) for p in ("css/onewooz-pal.css", "css/onewooz-core-pages.css")
        ),
    }
