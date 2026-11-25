"""The owner settings form, built from the fields staff define on an App.

Every field type in :data:`marketplace.blocks.FIELD_TYPES` is cleaned here,
server-side. Images are re-encoded (WebP via ``core.image_webp``), size-capped
and stored under the store's own folder. Secrets are encrypted and never
re-shown.
"""

from __future__ import annotations

from decimal import Decimal

from django import forms
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from .sanitize import clean_color, clean_html, clean_https_url, clean_identifier

MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _secret_box():
    from core import crypto

    return crypto


class AppSettingsForm(forms.Form):
    enabled = forms.BooleanField(required=False, label="Show this app on my store")

    def __init__(self, *args, app, site, config, placements, **kwargs):
        super().__init__(*args, **kwargs)
        self.app = app
        self.site = site
        self.config = config
        self.placements = placements
        self.app_fields = list(app.settings_fields.all())
        values = (config.values if config else {}) or {}
        self.fields["enabled"].initial = bool(config and config.enabled)
        if not placements:
            self.fields.pop("enabled")
        for f in self.app_fields:
            self.fields[f"f_{f.key}"] = self._build(f, values.get(f.key, f.default))
        for p in placements:
            if p.owner_can_choose_slot and len(p.slots()) > 1:
                from . import blocks

                choices = [(s, blocks.SLOTS[s].label) for s in p.slots() if s in blocks.SLOTS]
                current = ((config.slot_choices if config else {}) or {}).get(str(p.pk)) or p.default_slot
                self.fields[f"slot_{p.pk}"] = forms.ChoiceField(
                    label=f"Where to show: {p.label}", choices=choices, initial=current, required=True,
                )

    # -- field factory ----------------------------------------------------
    def _build(self, f, initial):
        common = {"label": f.label, "help_text": f.help_text, "required": f.required, "initial": initial}
        t = f.field_type
        if t in ("text", "identifier"):
            return forms.CharField(max_length=f.max_length or 255, **common)
        if t == "textarea":
            return forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), max_length=f.max_length or 2000, **common)
        if t == "rich_text":
            return forms.CharField(widget=forms.Textarea(attrs={"rows": 6, "data-rich": "1"}),
                                   max_length=f.max_length or 20000, **common)
        if t in ("number", "money"):
            kw = {}
            if f.min_value is not None:
                kw["min_value"] = f.min_value
            if f.max_value is not None:
                kw["max_value"] = f.max_value
            return forms.DecimalField(decimal_places=2 if t == "money" else 4, max_digits=14, **kw, **common)
        if t == "toggle":
            common["required"] = False
            common["initial"] = str(initial).lower() in ("1", "true", "on", "yes")
            return forms.BooleanField(**common)
        if t == "select":
            return forms.ChoiceField(choices=[("", "Choose...")] + f.parsed_choices(), **common)
        if t == "multi_select":
            common["initial"] = initial if isinstance(initial, list) else [v for v in str(initial or "").split(",") if v]
            return forms.MultipleChoiceField(choices=f.parsed_choices(), widget=forms.CheckboxSelectMultiple, **common)
        if t == "color":
            return forms.CharField(max_length=7, widget=forms.TextInput(attrs={"type": "color"}), **common)
        if t == "image":
            common["required"] = f.required and not initial
            field = forms.ImageField(**common)
            field.current_url = initial or ""
            return field
        if t == "url":
            return forms.URLField(max_length=500, assume_scheme="https", **common)
        if t == "phone":
            return forms.RegexField(regex=r"^\+?[0-9 ]{6,18}$", max_length=20, **common)
        if t == "email":
            return forms.EmailField(**common)
        if t == "date_time":
            return forms.DateTimeField(widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
                                       input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"], **common)
        if t in ("product_picker", "category_picker", "coupon_picker"):
            return forms.ChoiceField(choices=[("", "Choose...")] + self._store_choices(t), **common)
        if t == "secret":
            common["initial"] = ""
            common["required"] = f.required and not ((self.config.secrets if self.config else {}) or {}).get(f.key)
            common["help_text"] = (f.help_text + " " if f.help_text else "") + "Saved securely; leave blank to keep the current value."
            return forms.CharField(widget=forms.PasswordInput(render_value=False), max_length=500, **common)
        return forms.CharField(max_length=255, **common)

    def _store_choices(self, t):
        try:
            if t == "product_picker":
                from commerce.models import Product

                rows = Product.objects.for_site(self.site).filter(is_deleted=False).order_by().values_list("id", "title")[:300]
            elif t == "category_picker":
                from commerce.models import Category

                rows = Category.objects.for_site(self.site).filter(is_deleted=False).order_by().values_list("id", "name")[:300]
            else:
                from commerce.models import Coupon

                rows = Coupon.objects.for_site(self.site).filter(is_deleted=False).exclude(code="").order_by().values_list("code", "code")[:300]
            return sorted(((str(a), str(b)) for a, b in rows), key=lambda r: r[1].lower())
        except Exception:
            return []

    # -- cleaning ---------------------------------------------------------
    def cleaned_values(self) -> tuple[dict, dict, dict]:
        """``(values, secrets, slot_choices)`` ready to store."""
        values = dict((self.config.values if self.config else {}) or {})
        secrets = dict((self.config.secrets if self.config else {}) or {})
        for f in self.app_fields:
            raw = self.cleaned_data.get(f"f_{f.key}")
            t = f.field_type
            if t == "image":
                if raw:
                    url = self._store_image(raw, f.key)
                    if url:
                        values[f.key] = url
                elif self.data.get(f"clear_f_{f.key}") == "1":
                    values[f.key] = ""
                continue
            if t == "secret":
                if raw:
                    secrets[f.key] = _secret_box().encrypt(str(raw), purpose="marketplace.app_secret")
                continue
            values[f.key] = self._clean_one(t, raw)
        slots = {}
        for p in self.placements:
            key = f"slot_{p.pk}"
            if key in self.cleaned_data and self.cleaned_data[key] in p.slots():
                slots[str(p.pk)] = self.cleaned_data[key]
        return values, secrets, slots

    @staticmethod
    def _clean_one(t, raw):
        if raw is None:
            return ""
        if t == "rich_text":
            return clean_html(raw)
        if t == "color":
            return clean_color(raw)
        if t == "url":
            return clean_https_url(raw)
        if t == "identifier":
            return clean_identifier(raw)
        if t in ("number", "money"):
            return str(Decimal(raw)) if raw != "" else ""
        if t == "toggle":
            return bool(raw)
        if t == "multi_select":
            return [str(v)[:80] for v in raw][:50]
        if t == "date_time":
            return raw.isoformat()
        return str(raw).replace("\x00", "")[:20000]

    def _store_image(self, upload, key) -> str:
        if upload.size > MAX_IMAGE_BYTES:
            self.add_error(None, "Images must be 5 MB or smaller.")
            return ""
        raw = upload.read()
        from core.image_webp import convert_to_webp

        converted = convert_to_webp(raw)
        if converted is None:
            # Not decodable as a raster image (SVG, PDF, ...) unless already WebP.
            if not raw[:4] == b"RIFF":
                self.add_error(None, "That file is not an image we can use (PNG, JPG or WEBP).")
                return ""
            data = raw
        else:
            data = converted[0]
        import secrets as _s

        name = f"marketplace/sites/{self.site.pk}/{self.app.slug}/{key}-{_s.token_hex(6)}.webp"
        saved = default_storage.save(name, ContentFile(data))
        try:
            return default_storage.url(saved)
        except Exception:
            return ""
