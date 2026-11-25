"""Django admin for OneWooz Apps (the marketplace).

This is where staff create any app, at any time, without a deploy:

* **App**: name, price model, fit, visibility, kill switch, circuit breaker.
  - *Owner settings fields*: the form the owner fills in (form builder).
  - *Dashboard placements*: where it shows in the owner dashboard.
  - *Storefront placements*: which widget it puts on stores, and in which slots.
  - *Items*: things sold inside the app (for OneWooz Templates, the designs).
* **Licences**: grant (with a reason) or revoke (with a reason); full history.
* **Health**: breaker state per app; pause / resume.
* **Alert recipients**: who is emailed when an app is paused, a payment
  mismatches, etc.

Nothing typed here can ever execute on the servers: field types, widgets,
slots and modules are code (marketplace.blocks / marketplace.modules) and are
only referenced by name.
"""

from __future__ import annotations

import json

from django import forms
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.core.exceptions import ValidationError
from django.forms.models import model_to_dict
from django.shortcuts import render
from django.utils.html import format_html, format_html_join

from marketplace import blocks, health, licences, modules
from marketplace.models import (
    App,
    AppDashboardPlacement,
    AppLog,
    AppHealth,
    AppItem,
    AppScreenshot,
    AppSettingField,
    AppStorefrontPlacement,
    CatalogRevision,
    Installation,
    Licence,
    LicenceEvent,
    MarketOrder,
    MarketOrderLine,
    MarketplaceAlert,
    MarketplaceAlertRecipient,
    MarketplaceSettings,
    SiteAppConfig,
    SiteAppRender,
)
from marketplace.sanitize import clean_html

from .admin import LeanModelAdmin, TenantSafeModelAdmin


def _jsonable(obj) -> dict:
    from marketplace.ai import SECRET_FIELDS, fingerprint

    out = {}
    for k, v in model_to_dict(obj).items():
        if k in SECRET_FIELDS:
            out[k] = fingerprint(v)   # revisions show that a key changed, never the key
            continue
        try:
            json.dumps(v)
            out[k] = v
        except TypeError:
            out[k] = str(v)
    return out


def _revision(request, app, obj, before: dict | None):
    try:
        CatalogRevision.objects.create(
            app=app, app_slug=getattr(app, "slug", ""), object_kind=obj.__class__.__name__,
            object_id=str(obj.pk), actor_id=str(request.user.pk), actor_label=str(request.user),
            before=before or {}, after=_jsonable(obj),
        )
    except Exception:
        pass


# --------------------------------------------------------------------------
# Settings (singleton)
# --------------------------------------------------------------------------
@admin.register(MarketplaceSettings)
class MarketplaceSettingsAdmin(LeanModelAdmin):
    """Global switches. Turning the marketplace off closes browsing, installs and
    purchases; everything already installed keeps working everywhere."""

    list_display = ("__str__", "is_enabled", "reauth_window_minutes", "updated_at")

    def has_add_permission(self, request):
        return not MarketplaceSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


# --------------------------------------------------------------------------
# App composition
# --------------------------------------------------------------------------
class AppForm(forms.ModelForm):
    module = forms.ChoiceField(choices=(), required=False,
                               help_text="Optional code module that gives this app extra behaviour.")
    # The key is write-only: the box is always empty (never rendered back),
    # blank keeps the saved key, and the saved one is only ever shown masked.
    ai_api_key = forms.CharField(
        label="AI API key", required=False, strip=True, max_length=400,
        widget=forms.PasswordInput(render_value=False, attrs={
            "autocomplete": "new-password", "spellcheck": "false", "placeholder": "sk-...", "size": 60,
        }),
    )
    ai_api_key_clear = forms.BooleanField(
        label="Remove the saved key (use the server's OPENAI_API_KEY)", required=False,
    )

    class Meta:
        model = App
        exclude = ()
        widgets = {
            "ai_text_model": forms.TextInput(attrs={"size": 40, "spellcheck": "false", "autocomplete": "off"}),
            "ai_image_model": forms.TextInput(attrs={"size": 40, "spellcheck": "false", "autocomplete": "off"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["module"].choices = modules.choices()
        self._init_ai_fields()

    def _init_ai_fields(self):
        from django.conf import settings

        from marketplace.ai import mask

        saved = (getattr(self.instance, "ai_api_key", "") or "").strip() if self.instance.pk else ""
        server = bool(getattr(settings, "OPENAI_API_KEY", ""))
        # ModelForm copies every instance value into `initial`: the plaintext
        # key must not sit there (a widget or template could print it).
        self.initial.pop("ai_api_key", None)
        if saved:
            self.fields["ai_api_key"].help_text = (
                f"Saved key: {mask(saved)} (encrypted). Leave blank to keep it; paste a new key to replace it."
            )
            self.fields["ai_api_key"].widget.attrs["placeholder"] = f"{mask(saved)}  (saved)"
        else:
            self.fields["ai_api_key"].help_text = (
                "Paste the OpenAI API key this app should use. It is encrypted and never shown again. "
                + ("Blank = the server's OPENAI_API_KEY." if server
                   else "No server OPENAI_API_KEY is set, so AI features of this app need a key here.")
            )
            self.fields["ai_api_key_clear"].widget = forms.HiddenInput()
        # Pre-filled with the models in use today, so the admin sees (and can
        # retype) the exact model IDs instead of an empty "server default".
        for name, default in (("ai_text_model", getattr(settings, "OPENAI_TEXT_MODEL", "")),
                              ("ai_image_model", getattr(settings, "OPENAI_IMAGE_MODEL", ""))):
            if name in self.fields and not (getattr(self.instance, name, "") or "").strip() and not self.is_bound:
                self.initial[name] = default
        self.fields["ai_text_model"].help_text = (
            "Model ID used for writing text. Type any OpenAI model ID to switch; used instead of the "
            f"prompt's own model. Server default: {getattr(settings, 'OPENAI_TEXT_MODEL', '') or 'none'}."
        )
        self.fields["ai_image_model"].help_text = (
            "Model ID used for images (gpt-image-1, dall-e-3, ...). Type any OpenAI image model ID to switch. "
            f"Server default: {getattr(settings, 'OPENAI_IMAGE_MODEL', '') or 'none'}."
        )

    def clean_ai_api_key(self):
        key = (self.cleaned_data.get("ai_api_key") or "").strip()
        if key and (any(c.isspace() for c in key) or len(key) < 20):
            raise ValidationError("That doesn't look like an API key. Paste the whole key, with no spaces.")
        return key

    def clean_ai_text_model(self):
        return self._clean_model_id("ai_text_model")

    def clean_ai_image_model(self):
        return self._clean_model_id("ai_image_model")

    def _clean_model_id(self, name):
        value = (self.cleaned_data.get(name) or "").strip()
        if value and not all(c.isalnum() or c in "-._:/" for c in value):
            raise ValidationError("A model ID has only letters, numbers and - . _ : / (e.g. gpt-4.1).")
        return value

    def _post_clean(self):
        # Blank key box = keep the saved key; the tick box removes it.
        new_key = (self.cleaned_data.get("ai_api_key") or "").strip() if hasattr(self, "cleaned_data") else ""
        clear = bool(getattr(self, "cleaned_data", {}).get("ai_api_key_clear"))
        saved = (getattr(self.instance, "ai_api_key", "") or "") if self.instance.pk else ""
        super()._post_clean()
        self.instance.ai_api_key = new_key or ("" if clear else saved)

    def clean_slug(self):
        slug = self.cleaned_data["slug"]
        if self.instance.pk and self.instance.slug != slug and self.instance.status != App.Status.DRAFT:
            raise ValidationError("The address of a published app cannot change (owners' links would break).")
        return slug

    def clean_description(self):
        return clean_html(self.cleaned_data.get("description"))

    def clean_allowed_script_origins(self):
        raw = self.cleaned_data.get("allowed_script_origins") or ""
        good = []
        for line in raw.splitlines():
            line = line.strip().rstrip("/")
            if not line:
                continue
            if not line.startswith("https://") or "/" in line[8:] or " " in line or ";" in line:
                raise ValidationError(f"{line!r} is not an https origin like https://www.example.com")
            good.append(line)
        return "\n".join(good)

    def clean(self):
        data = super().clean()
        pricing = data.get("pricing")
        price = data.get("price") or 0
        if pricing in (App.Pricing.ONE_TIME, App.Pricing.YEARLY, App.Pricing.MONTHLY) and not price > 0:
            self.add_error("price", "A paid app needs a price above zero.")
        cycle_fields = ("price_monthly", "price_quarterly", "price_half_yearly", "price_yearly")
        for name in cycle_fields:
            value = data.get(name)
            if value is not None and value < 0:
                self.add_error(name, "A price cannot be negative.")
        if pricing == App.Pricing.CYCLES:
            if data.get("cycle_same_price"):
                if not price > 0:
                    self.add_error("price", "With 'Same price for every cycle', set the price per month here.")
            elif not any((data.get(n) or 0) > 0 for n in cycle_fields):
                self.add_error("price_monthly", "Set the per-month price of at least one billing cycle "
                                                "(or tick 'Same price for every cycle').")
        return self._clean_system(data)

    def _clean_system(self, data):
        if self.instance.pk and self.instance.is_system:
            if data.get("status") in (App.Status.DRAFT, App.Status.RETIRED):
                self.add_error("status", "A system app cannot be hidden or retired.")
            if data.get("kind") != App.Kind.SYSTEM:
                self.add_error("kind", "A system app stays a system app.")
        return data


class AppSettingFieldInline(admin.TabularInline):
    model = AppSettingField
    extra = 0
    fields = ("order", "key", "label", "field_type", "required", "default", "help_text", "choices",
              "min_value", "max_value", "max_length", "group")
    verbose_name = "owner settings field"
    verbose_name_plural = "Owner settings form (the fields the owner fills in)"


class DashboardPlacementForm(forms.ModelForm):
    class Meta:
        model = AppDashboardPlacement
        exclude = ()

    def clean(self):
        data = super().clean()
        kind = data.get("page_kind")
        if kind in ("external_link", "embedded") and not (data.get("external_url") or "").startswith("https://"):
            self.add_error("external_url", "Needs an https:// address.")
        if kind == "module_page":
            app = data.get("app") or getattr(self.instance, "app", None)
            module = modules.get(getattr(app, "module", ""))
            if module is None or data.get("page_key") not in module.pages():
                keys = ", ".join(module.pages()) if module else "none (the app has no module)"
                self.add_error("page_key", f"Pick one of the module's pages: {keys}.")
        if data.get("body"):
            data["body"] = clean_html(data["body"])
        return data


class AppDashboardPlacementInline(admin.StackedInline):
    model = AppDashboardPlacement
    form = DashboardPlacementForm
    extra = 0
    fields = (("point", "nav_group", "order", "is_active"), ("label", "icon_key", "audience"), "summary",
              ("page_kind", "page_key"), "external_url", "body")
    verbose_name = "dashboard placement"
    verbose_name_plural = "Dashboard placements (where the app shows in the owner dashboard)"


class StorefrontPlacementForm(forms.ModelForm):
    allowed_slots = forms.MultipleChoiceField(
        choices=blocks.slot_choices(), required=False, widget=forms.CheckboxSelectMultiple,
        help_text="Leave all unticked to allow every slot this widget type supports.",
    )
    pages = forms.MultipleChoiceField(
        choices=list(blocks.STOREFRONT_PAGES.items()), required=False, widget=forms.CheckboxSelectMultiple,
        help_text="Leave all unticked for every page.",
    )

    class Meta:
        model = AppStorefrontPlacement
        exclude = ()
        widgets = {"options": forms.Textarea(attrs={"rows": 3, "cols": 60})}

    def clean(self):
        data = super().clean()
        widget = data.get("widget_type")
        supported = set(blocks.allowed_slots_for(widget))
        chosen = [s for s in (data.get("allowed_slots") or [])]
        bad = [s for s in chosen if s not in supported]
        if bad:
            self.add_error("allowed_slots", f"{', '.join(bad)} not supported by this widget type.")
        effective = chosen or list(supported)
        if data.get("default_slot") not in effective:
            self.add_error("default_slot", "The default slot must be one of the allowed slots.")
        opts = data.get("options") or {}
        if not isinstance(opts, dict):
            self.add_error("options", "Options must be a JSON object, e.g. {\"bg_color\": \"#111111\"}.")
        else:
            wt = blocks.WIDGET_TYPES.get(widget)
            known = set(wt.fields) | set(wt.options) if wt else set()
            unknown = [k for k in opts if k not in known]
            if unknown:
                self.add_error("options", f"Unknown option(s) for this widget: {', '.join(unknown)}. "
                                          f"Allowed: {', '.join(sorted(known)) or 'none'}.")
            if widget == "pixel" and opts.get("src") and not str(opts["src"]).startswith("https://"):
                self.add_error("options", "Pixel src must be an https URL on an approved origin.")
        return data


class AppStorefrontPlacementInline(admin.StackedInline):
    model = AppStorefrontPlacement
    form = StorefrontPlacementForm
    extra = 0
    fields = (("label", "widget_type", "order", "is_active"), ("default_slot", "owner_can_choose_slot", "devices"),
              "allowed_slots", "pages", "options")
    verbose_name = "storefront placement"
    verbose_name_plural = "Storefront placements (widgets the app can put on stores)"


class AppItemInline(admin.TabularInline):
    model = AppItem
    extra = 0
    fields = ("order", "key", "name", "tagline", "fits", "pricing", "price", "licence_type", "status",
              "thumbnail", "preview_url", "industries", "is_system_default")
    readonly_fields = ("is_system_default",)
    verbose_name_plural = "Items sold inside this app (for OneWooz Templates: the designs)"

    def has_delete_permission(self, request, obj=None):
        return False


class AppScreenshotInline(admin.TabularInline):
    model = AppScreenshot
    extra = 0
    fields = ("order", "image", "caption")


@admin.register(App)
class AppAdmin(LeanModelAdmin):
    """Compose an app: price, fit, visibility, where it shows in the dashboard,
    what it puts on stores, and what owners fill in. Saving recompiles every
    affected store in the background."""

    form = AppForm
    inlines = (AppSettingFieldInline, AppDashboardPlacementInline, AppStorefrontPlacementInline,
               AppItemInline, AppScreenshotInline)
    list_display = ("name", "slug", "status", "price_label", "kind", "is_default", "installs", "health_state",
                    "is_paused", "order")
    list_filter = ("status", "pricing", "kind", "category", "is_paused")
    list_editable = ("order",)
    search_fields = ("name", "slug", "tagline")
    readonly_fields = ("config_version", "published_at", "health_panel", "cycle_summary")
    actions = ("pause_apps", "resume_apps", "recompile_stores")
    fieldsets = (
        (None, {"fields": ("name", "slug", "tagline", "description", ("icon", "icon_key", "accent"),
                           ("category", "developer"), "module")}),
        ("Who gets it", {"fields": (("kind", "is_default"), ("fits", "status", "featured", "order"),
                                    "published_at")}),
        ("Price", {"fields": (("pricing", "price", "currency"), ("gst_applicable", "trial_days"))}),
        ("Billing cycles (pricing = 'Billing cycles')", {
            "description": "Prices are per month, like OneWooz plans: the owner pays the per-month price x the "
                           "months in the cycle, up front, plus GST. Give cycles different prices (e.g. cheaper "
                           "per month when billed yearly) or tick 'Same price for every cycle'. Leave a cycle blank "
                           "to not offer it. Price changes apply to new purchases only.",
            "fields": ("cycle_same_price", ("price_monthly", "price_quarterly", "price_half_yearly", "price_yearly"),
                       "cycle_summary"),
        }),
        ("AI provider (apps that use AI, e.g. Magic SEO)", {
            "description": "The key and models this app calls the AI with. The key is encrypted and never shown "
                           "again after saving (only its first and last characters). Model IDs are pre-filled "
                           "with the ones in use: type another model ID to switch, no deploy needed.",
            "fields": ("ai_provider", "ai_api_key", "ai_api_key_clear", ("ai_text_model", "ai_image_model")),
        }),
        ("Links", {"fields": ("support_url", "privacy_url"), "classes": ("collapse",)}),
        ("Safety", {"fields": ("is_paused", "pause_reason", "paused_until",
                               ("breaker_threshold", "breaker_window_minutes", "breaker_pause_minutes", "auto_resume"),
                               "allowed_script_origins", "health_panel", "config_version")}),
    )

    def get_queryset(self, request):
        from marketplace import registry

        registry.sync()
        return super().get_queryset(request)

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Price")
    def price_label(self, obj):
        return obj.pricing_label()

    @admin.display(description="What owners pay (before GST)")
    def cycle_summary(self, obj):
        if not obj or not obj.pk or obj.pricing != App.Pricing.CYCLES:
            return "Shown here once the app is saved with pricing 'Billing cycles'."
        rows = obj.cycle_options()
        if not rows:
            return "No cycle offered yet."
        return format_html(
            "<ul style='margin:0;padding-left:1.1rem'>{}</ul>",
            format_html_join(
                "", "<li><strong>{}</strong>: ₹{} / month, billed ₹{} every {}{}</li>",
                ((r["label"], f"{r['per_month']:,.2f}", f"{r['total']:,.2f}", r["billed"],
                  f" (save {r['save_pct']}%)" if r["save_pct"] else "") for r in rows),
            ),
        )

    @admin.display(description="Installs")
    def installs(self, obj):
        if obj.is_default:
            return "Everyone"
        return Installation.objects.filter(app=obj, status=Installation.Status.ACTIVE).count()

    @admin.display(description="Health")
    def health_state(self, obj):
        h = AppHealth.objects.filter(app=obj).first()
        return h.get_state_display() if h else "Healthy"

    @admin.display(description="Recent errors")
    def health_panel(self, obj):
        if not obj or not obj.pk:
            return "-"
        n = health.errors_in_window(obj.slug, obj.breaker_window_minutes)
        h = AppHealth.objects.filter(app=obj).first()
        last = h.last_error if h else ""
        return format_html("<strong>{}</strong> errors in the last {} min. Last: {}", n,
                           obj.breaker_window_minutes, last or "none")

    def save_model(self, request, obj, form, change):
        before = _jsonable(App.objects.get(pk=obj.pk)) if change else {}
        was_paused = App.objects.filter(pk=obj.pk, is_paused=True).exists() if change else False
        super().save_model(request, obj, form, change)
        _revision(request, obj, obj, before)
        if obj.is_paused != was_paused:
            health.refresh_paused_set()

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for inst in instances:
            before = {}
            if inst.pk:
                old = type(inst).objects.filter(pk=inst.pk).first()
                before = _jsonable(old) if old else {}
            inst.save()
            _revision(request, form.instance, inst, before)
        for deleted in formset.deleted_objects:
            _revision(request, form.instance, deleted, _jsonable(deleted))
            deleted.delete()
        formset.save_m2m()

    @admin.action(description="Pause (kill switch) for 24 hours")
    def pause_apps(self, request, queryset):
        for app in queryset:
            health.pause(app, minutes=60 * 24, reason=f"Paused by {request.user}", by=str(request.user))
        self.message_user(request, "Paused. Dashboard pages and storefront widgets are hidden everywhere.")

    @admin.action(description="Resume")
    def resume_apps(self, request, queryset):
        for app in queryset:
            health.resume(app, by=str(request.user))
        self.message_user(request, "Resumed.")

    @admin.action(description="Recompile every store that has it")
    def recompile_stores(self, request, queryset):
        from marketplace import tasks

        for app in queryset:
            tasks.enqueue_recompile_app(app.pk, force=True)
        self.message_user(request, "Recompile queued.")


@admin.register(AppItem)
class AppItemAdmin(LeanModelAdmin):
    """Items sold inside apps. For OneWooz Templates these are the storefront
    designs: price them, hide them, or make them yearly here. Template 1 is the
    system default and always stays free and published."""

    list_display = ("name", "app", "key", "fits", "pricing", "price", "licence_type", "status", "owners", "order")
    list_filter = ("app", "status", "pricing", "licence_type", "fits")
    list_editable = ("order",)
    search_fields = ("name", "key")
    readonly_fields = ("app", "key", "is_system_default")

    def has_add_permission(self, request):
        return True

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        if obj is None:
            return ["is_system_default"]
        if obj.is_system_default:
            ro += ["pricing", "price", "licence_type", "status"]
        return ro

    @admin.display(description="Owners")
    def owners(self, obj):
        return Licence.objects.filter(item=obj, status=Licence.Status.ACTIVE).count()

    def save_model(self, request, obj, form, change):
        before = _jsonable(AppItem.objects.get(pk=obj.pk)) if change else {}
        super().save_model(request, obj, form, change)
        _revision(request, obj.app, obj, before)
        from marketplace import api

        api.invalidate_all()


# --------------------------------------------------------------------------
# Ownership
# --------------------------------------------------------------------------
@admin.register(Installation)
class InstallationAdmin(LeanModelAdmin):
    list_display = ("app", "owner_email", "status", "source", "installed_at", "reauth_method")
    list_filter = ("status", "source", "app")
    search_fields = ("owner_email",)
    readonly_fields = ("owner_ref", "owner_email", "app", "source", "installed_at", "uninstalled_at",
                       "reauth_method", "reauth_at")
    actions = ("disable", "enable")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Disable (owner cannot re-enable)")
    def disable(self, request, queryset):
        from marketplace import api, tasks

        for inst in queryset:
            inst.status = Installation.Status.DISABLED
            inst.save(update_fields=["status", "updated_at"])
            tasks.enqueue_compile_owner(inst.owner_ref)
            api.invalidate_owner(inst.owner_ref)
        self.message_user(request, "Disabled.")

    @admin.action(description="Enable")
    def enable(self, request, queryset):
        from marketplace import api, tasks

        for inst in queryset:
            inst.status = Installation.Status.ACTIVE
            inst.save(update_fields=["status", "updated_at"])
            tasks.enqueue_compile_owner(inst.owner_ref)
            api.invalidate_owner(inst.owner_ref)
        self.message_user(request, "Enabled.")


class LicenceGrantForm(forms.ModelForm):
    owner = forms.EmailField(help_text="The store owner's account email.")
    reason = forms.CharField(max_length=300, help_text="Required: why this licence is granted (kept in the history).")

    class Meta:
        model = Licence
        fields = ("app", "item", "expires_at")

    def clean(self):
        data = super().clean()
        from accounts.models import User

        email = (data.get("owner") or "").strip().lower()
        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            self.add_error("owner", "No account with that email.")
        self._user = user
        item, app = data.get("item"), data.get("app")
        if item is not None and app is not None and item.app_id != app.pk:
            self.add_error("item", "That item belongs to another app.")
        return data


class LicenceEventInline(admin.TabularInline):
    model = LicenceEvent
    extra = 0
    can_delete = False
    fields = ("created_at", "event", "actor_kind", "actor_label", "reason")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Licence)
class LicenceAdmin(LeanModelAdmin):
    """Every licence an owner holds. Add = grant (with a reason). Revoke and
    restore are actions and always ask for a reason. Nothing is ever deleted."""

    list_display = ("name_snapshot", "owner_email", "app", "source", "status", "expires_at", "created_at")
    list_filter = ("status", "source", "app")
    search_fields = ("owner_email", "name_snapshot", "legacy_ref")
    inlines = (LicenceEventInline,)
    actions = ("revoke_action", "restore_action")

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs["form"] = LicenceGrantForm
        return super().get_form(request, obj, **kwargs)

    def get_fields(self, request, obj=None):
        if obj is None:
            return ("owner", "app", "item", "expires_at", "reason")
        return super().get_fields(request, obj)

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        return [f.name for f in Licence._meta.fields]

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if change:
            return
        user = form._user
        lic = licences.grant(
            licences.owner_of(user), form.cleaned_data["app"], item=form.cleaned_data.get("item"),
            source=Licence.Source.ADMIN_GRANT, expires_at=form.cleaned_data.get("expires_at"),
            actor_kind="admin", actor_label=str(request.user), reason=form.cleaned_data["reason"], request=request,
        )
        if lic.item_id is None and not lic.app.is_system:
            licences.install(licences.owner_of(user), lic.app, source="admin", request=request)
        obj.pk = lic.pk
        obj.id = lic.id

    def save_related(self, request, form, formsets, change):
        if change:
            super().save_related(request, form, formsets, change)

    def _reason_view(self, request, queryset, title, action_name):
        return render(request, "admin/marketplace/reason.html", {
            "title": title, "queryset": queryset, "action_name": action_name,
            "opts": self.model._meta, "action_checkbox_name": helpers.ACTION_CHECKBOX_NAME,
        })

    @admin.action(description="Revoke (asks for a reason)")
    def revoke_action(self, request, queryset):
        reason = (request.POST.get("reason") or "").strip()
        if not reason:
            return self._reason_view(request, queryset, "Revoke licences", "revoke_action")
        for lic in queryset:
            licences.revoke(lic, reason=reason, actor_label=str(request.user), request=request)
        self.message_user(request, f"Revoked {queryset.count()} licence(s). Affected stores fall back safely.")

    @admin.action(description="Restore (asks for a reason)")
    def restore_action(self, request, queryset):
        reason = (request.POST.get("reason") or "").strip()
        if not reason:
            return self._reason_view(request, queryset, "Restore licences", "restore_action")
        for lic in queryset:
            licences.restore(lic, reason=reason, actor_label=str(request.user), request=request)
        self.message_user(request, "Restored.")


@admin.register(LicenceEvent)
class LicenceEventAdmin(LeanModelAdmin):
    list_display = ("created_at", "event", "licence", "actor_kind", "actor_label", "reason")
    list_filter = ("event", "actor_kind")
    search_fields = ("licence__owner_email", "reason")
    readonly_fields = ("licence", "event", "actor_kind", "actor_label", "reason", "context")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


# --------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------
class MarketOrderLineInline(admin.TabularInline):
    model = MarketOrderLine
    extra = 0
    can_delete = False
    fields = ("description", "app", "item", "licence_type", "period_days", "base_amount")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(MarketOrder)
class MarketOrderAdmin(LeanModelAdmin):
    list_display = ("number", "owner_email", "total_amount", "status", "gateway_status", "paid_at", "created_at")
    list_filter = ("status",)
    search_fields = ("number", "owner_email", "cf_order_id")
    date_hierarchy = "created_at"
    inlines = (MarketOrderLineInline,)
    actions = ("confirm_with_cashfree",)

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in MarketOrder._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Check status with Cashfree now")
    def confirm_with_cashfree(self, request, queryset):
        from marketplace import orders

        n = sum(1 for o in queryset if orders.confirm(o))
        self.message_user(request, f"{n} order(s) confirmed paid.")


# --------------------------------------------------------------------------
# Operations
# --------------------------------------------------------------------------
@admin.register(AppHealth)
class AppHealthAdmin(LeanModelAdmin):
    list_display = ("app", "state", "errors_total", "open_count", "current_pause_minutes", "last_error_at")
    list_filter = ("state",)
    readonly_fields = ("app", "state", "errors_total", "last_error_at", "last_error", "last_error_context",
                       "opened_at", "open_count", "current_pause_minutes")
    actions = ("resume",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Resume these apps now")
    def resume(self, request, queryset):
        for h in queryset.select_related("app"):
            health.resume(h.app, by=str(request.user))
        self.message_user(request, "Resumed.")


class AlertRecipientForm(forms.ModelForm):
    alert_types = forms.MultipleChoiceField(
        choices=MarketplaceAlertRecipient.AlertType.choices, required=False, widget=forms.CheckboxSelectMultiple,
        help_text="Leave all unticked to receive every alert type.",
    )

    class Meta:
        model = MarketplaceAlertRecipient
        exclude = ()


@admin.register(MarketplaceAlertRecipient)
class MarketplaceAlertRecipientAdmin(LeanModelAdmin):
    """Staff who are emailed about the marketplace: an app auto-paused by the
    circuit breaker, resumed, failing compiles, payment / licence mismatches."""

    form = AlertRecipientForm
    list_display = ("name", "email", "is_active", "types_label", "min_interval_minutes")
    list_filter = ("is_active",)
    search_fields = ("name", "email")
    filter_horizontal = ("apps",)
    actions = ("send_test",)

    @admin.display(description="Alerts")
    def types_label(self, obj):
        return ", ".join(obj.alert_types or []) or "All"

    @admin.action(description="Send a test alert")
    def send_test(self, request, queryset):
        from django.conf import settings
        from django.core.mail import send_mail

        sent = 0
        for r in queryset:
            try:
                send_mail("[OneWooz Apps] Test alert", "Marketplace alerts will reach you here.",
                          getattr(settings, "DEFAULT_FROM_EMAIL", None), [r.email])
                sent += 1
            except Exception as exc:
                self.message_user(request, f"{r.email}: {exc}", level=messages.ERROR)
        self.message_user(request, f"Sent {sent} test alert(s).")


@admin.register(MarketplaceAlert)
class MarketplaceAlertAdmin(LeanModelAdmin):
    list_display = ("created_at", "kind", "app", "subject", "delivered", "recipients")
    list_filter = ("kind", "delivered")
    readonly_fields = ("kind", "app", "subject", "body", "recipients", "delivered", "error")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(CatalogRevision)
class CatalogRevisionAdmin(LeanModelAdmin):
    list_display = ("created_at", "app_slug", "object_kind", "actor_label")
    list_filter = ("object_kind",)
    search_fields = ("app_slug", "actor_label")
    readonly_fields = ("app", "app_slug", "object_kind", "object_id", "actor_id", "actor_label", "before", "after")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SiteAppConfig)
class SiteAppConfigAdmin(TenantSafeModelAdmin):
    list_display = ("site", "app", "enabled", "updated_at")
    list_filter = ("enabled", "app")
    list_select_related = ("site", "app")
    readonly_fields = ("site", "app", "values", "slot_choices", "enabled", "updated_by_ref")
    exclude = ("secrets",)

    def has_add_permission(self, request):
        return False


@admin.register(SiteAppRender)
class SiteAppRenderAdmin(TenantSafeModelAdmin):
    list_display = ("site", "compiled_at", "has_error")
    list_select_related = ("site",)
    readonly_fields = ("site", "bundle", "script_origins", "compiled_from", "compiled_at", "last_error")
    actions = ("recompile",)

    def has_add_permission(self, request):
        return False

    @admin.display(boolean=True, description="Error")
    def has_error(self, obj):
        return bool(obj.last_error)

    @admin.action(description="Recompile now")
    def recompile(self, request, queryset):
        from marketplace import compiler

        for row in queryset.select_related("site"):
            compiler.compile_site(row.site)
        self.message_user(request, "Recompiled.")


@admin.register(AppLog)
class AppLogAdmin(LeanModelAdmin):
    """Each app's working log (also in the marketplace console > Logs).
    Written by code only; read-only here. Purged after the retention period."""

    list_display = ("created_at", "app_slug", "level", "event", "site_label", "short_message")
    list_filter = ("level", "app_slug")
    search_fields = ("message", "event", "site_label", "run_ref")
    readonly_fields = ("created_at", "app_slug", "level", "event", "message", "site", "site_label", "run_ref", "context")
    fields = readonly_fields
    list_select_related = ()
    # No ordering set: newest first by the UUIDv7 primary key, which the
    # clustered index serves (the rows carry JSON; MySQL must not sort them).

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Message")
    def short_message(self, obj):
        return obj.message[:120]
