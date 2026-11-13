"""Forms for the owner dashboard."""

from __future__ import annotations

import os

from django import forms

from core.queries import without_heavy_fields

from .models import (
    SUPPORT_ATTACHMENT_EXTENSIONS,
    SUPPORT_ATTACHMENT_MAX_BYTES,
    SupportTicket,
)

# The first bytes of each accepted binary type. A renamed executable (or any
# file whose content doesn't match its extension) is refused, so an attachment
# is always what it claims to be. Text formats (txt, csv) have no signature.
_SIGNATURES = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
    "gif": (b"GIF87a", b"GIF89a"),
    "webp": (b"RIFF",),
    "pdf": (b"%PDF",),
    # Office Open XML (docx / xlsx) are zip archives; legacy doc / xls are OLE2.
    "docx": (b"PK\x03\x04",),
    "xlsx": (b"PK\x03\x04",),
    "doc": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
    "xls": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
}

ATTACHMENT_ACCEPT = ",".join("." + e for e in SUPPORT_ATTACHMENT_EXTENSIONS)


class SupportTicketForm(forms.ModelForm):
    """What an owner fills in to raise a ticket from the Business Hub."""

    class Meta:
        model = SupportTicket
        fields = ["subject", "category", "priority", "site", "message", "attachment"]
        labels = {
            "subject": "Subject",
            "category": "Topic",
            "priority": "Priority",
            "site": "Store",
            "message": "Describe the issue",
            "attachment": "Attachment (optional)",
        }
        widgets = {
            "subject": forms.TextInput(
                attrs={"class": "pal-input", "maxlength": 160, "placeholder": "e.g. Payment not showing on an order"}
            ),
            "category": forms.Select(attrs={"class": "pal-select"}),
            "priority": forms.Select(attrs={"class": "pal-select"}),
            "site": forms.Select(attrs={"class": "pal-select"}),
            "message": forms.Textarea(
                attrs={
                    "class": "pal-textarea",
                    "rows": 5,
                    "maxlength": 5000,
                    "placeholder": "What happened, what you expected, and any order number or page link that helps.",
                }
            ),
            "attachment": forms.ClearableFileInput(attrs={"accept": ATTACHMENT_ACCEPT}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # The dropdown shows only each store's name. Leaving the unbounded
        # ``content`` JSON out keeps MySQL's ORDER BY name (no index serves it)
        # inside the sort buffer; it once failed here with error 1038.
        sites = (
            without_heavy_fields(user.sites.filter(is_deleted=False).order_by("name"))
            if user is not None
            else None
        )
        site_field = self.fields["site"]
        if sites is not None:
            site_field.queryset = sites
        site_field.required = False
        site_field.empty_label = "Not about a specific store"
        self.fields["subject"].required = True
        self.fields["message"].required = True
        self.fields["attachment"].required = False

    def clean_subject(self):
        return " ".join((self.cleaned_data.get("subject") or "").split())

    def clean_message(self):
        message = (self.cleaned_data.get("message") or "").strip()
        if len(message) < 10:
            raise forms.ValidationError("Please describe the issue in a little more detail.")
        return message[:5000]

    def clean_attachment(self):
        return clean_ticket_attachment(self.cleaned_data.get("attachment"))


class SupportReplyForm(forms.Form):
    """An owner's reply on one of their tickets (raised as a follow-up ticket)."""

    message = forms.CharField(
        label="Your reply",
        max_length=5000,
        widget=forms.Textarea(
            attrs={
                "class": "pal-textarea",
                "rows": 3,
                "maxlength": 5000,
                "placeholder": "Ask a follow-up question or add more detail…",
            }
        ),
    )
    attachment = forms.FileField(
        label="Attachment (optional)",
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": ATTACHMENT_ACCEPT}),
    )

    def clean_message(self):
        message = (self.cleaned_data.get("message") or "").strip()
        if len(message) < 2:
            raise forms.ValidationError("Please write your reply.")
        return message[:5000]

    def clean_attachment(self):
        return clean_ticket_attachment(self.cleaned_data.get("attachment"))


def clean_ticket_attachment(upload):
    """Size, extension and magic-byte checks shared by both ticket forms."""
    if not upload:
        return upload
    if upload.size > SUPPORT_ATTACHMENT_MAX_BYTES:
        raise forms.ValidationError(
            f"Attachments can be up to {SUPPORT_ATTACHMENT_MAX_BYTES // (1024 * 1024)} MB."
        )
    ext = os.path.splitext(upload.name or "")[1].lower().lstrip(".")
    if ext not in SUPPORT_ATTACHMENT_EXTENSIONS:
        raise forms.ValidationError(
            "That file type isn't supported. Attach an image, PDF, text, CSV, Word or Excel file."
        )
    signatures = _SIGNATURES.get(ext)
    if signatures:
        head = upload.read(16)
        upload.seek(0)
        if not any(head.startswith(sig) for sig in signatures):
            raise forms.ValidationError("That file doesn't look like a valid ." + ext + " file.")
    return upload
