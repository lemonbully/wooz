"""Forms for the marketplace console (admin surface, UI-based control).

The catalogue forms are shared with the Django admin
(``custom_admin.marketplace_admin``) so validation is identical in both places:
slugs locked after publishing, system apps cannot be hidden, slots checked
against the widget type, widget options checked, https-only origins and links.
"""

from __future__ import annotations

from django import forms
from django.forms import inlineformset_factory

from custom_admin.marketplace_admin import (  # noqa: F401  (re-exported)
    AlertRecipientForm,
    AppForm,
    DashboardPlacementForm,
    LicenceGrantForm,
    StorefrontPlacementForm,
)

from . import blocks
from .models import (
    App,
    AppDashboardPlacement,
    AppItem,
    AppScreenshot,
    AppSettingField,
    AppStorefrontPlacement,
    MarketplaceSettings,
)


class SettingsFieldForm(forms.ModelForm):
    class Meta:
        model = AppSettingField
        fields = ("order", "key", "label", "field_type", "required", "default", "help_text", "choices",
                  "min_value", "max_value", "max_length", "group")
        widgets = {"choices": forms.Textarea(attrs={"rows": 2}), "help_text": forms.TextInput()}


class ItemForm(forms.ModelForm):
    class Meta:
        model = AppItem
        fields = ("order", "key", "name", "tagline", "description", "thumbnail", "preview_url", "fits", "industries",
                  "pricing", "price", "licence_type", "status")
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["key"].disabled = True
            if self.instance.is_system_default:
                for name in ("pricing", "price", "licence_type", "status"):
                    self.fields[name].disabled = True
                    self.fields[name].help_text = "The safe fallback design: always included and published."

    def clean(self):
        data = super().clean()
        if data.get("pricing") == AppItem.Pricing.PRICED and not (data.get("price") or 0) > 0:
            self.add_error("price", "A premium item needs a price above zero.")
        return data


class ScreenshotForm(forms.ModelForm):
    class Meta:
        model = AppScreenshot
        fields = ("order", "image", "caption")


SettingsFieldFormSet = inlineformset_factory(App, AppSettingField, form=SettingsFieldForm, extra=1, can_delete=True)
DashboardFormSet = inlineformset_factory(
    App, AppDashboardPlacement, form=DashboardPlacementForm, extra=1, can_delete=True,
    fields=("point", "nav_group", "order", "label", "summary", "icon_key", "page_kind", "page_key",
            "external_url", "body", "audience", "is_active"),
)
StorefrontFormSet = inlineformset_factory(
    App, AppStorefrontPlacement, form=StorefrontPlacementForm, extra=1, can_delete=True,
    fields=("label", "widget_type", "allowed_slots", "default_slot", "owner_can_choose_slot", "pages", "devices",
            "options", "order", "is_active"),
)
ItemFormSet = inlineformset_factory(App, AppItem, form=ItemForm, extra=1, can_delete=False)
ScreenshotFormSet = inlineformset_factory(App, AppScreenshot, form=ScreenshotForm, extra=1, can_delete=True)


class MarketplaceSettingsForm(forms.ModelForm):
    class Meta:
        model = MarketplaceSettings
        fields = ("is_enabled", "closed_message", "reauth_window_minutes", "announcement")


class PauseForm(forms.Form):
    minutes = forms.IntegerField(min_value=0, max_value=60 * 24 * 30, initial=60,
                                 help_text="0 = until resumed by staff.")
    reason = forms.CharField(max_length=200)


class ReasonForm(forms.Form):
    reason = forms.CharField(max_length=300)


#: Field groups for the app "General" tab (keeps the template declarative).
APP_SECTIONS = (
    ("Identity", ("name", "slug", "tagline", "description", "icon", "icon_key", "accent", "category", "developer",
                  "module")),
    ("Who gets it", ("kind", "is_default", "fits", "status", "featured", "order")),
    ("Price", ("pricing", "price", "currency", "gst_applicable", "trial_days")),
    # Per-month prices, used when pricing is "Billing cycles". Blank = not offered.
    ("Billing cycles", ("cycle_same_price", "price_monthly", "price_quarterly", "price_half_yearly",
                        "price_yearly")),
    # Apps that call an AI model: encrypted key (write-only) and model IDs.
    ("AI provider", ("ai_provider", "ai_api_key", "ai_api_key_clear", "ai_text_model", "ai_image_model")),
    ("Links", ("support_url", "privacy_url")),
    ("Safety", ("is_paused", "pause_reason", "paused_until", "breaker_threshold", "breaker_window_minutes",
                "breaker_pause_minutes", "auto_resume", "allowed_script_origins")),
)

WIDGET_HELP = {w.key: {"label": w.label, "fields": list(w.fields), "required": list(w.required),
                       "options": list(w.options), "description": w.description}
               for w in blocks.WIDGET_TYPES.values()}
