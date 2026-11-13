"""Owner-facing forms for the store-management surface.

Forms never expose the ``site`` field  the view sets it from the resolved,
owned store  so a crafted POST cannot reassign a record to another tenant.
"""

from __future__ import annotations

import re

from django import forms

from tenancy.models import Site

from .models import (
    Category,
    Collaboration,
    GoogleMerchantConfig,
    PaymentGatewayConfig,
    Product,
    ShippingProvider,
    TrackingConfig,
    Warehouse,
    display_ordered_categories,
)


class SiteForm(forms.ModelForm):
    """Owner-editable store settings. Slug/business-model set at creation and
    left out here  changing the public subdomain or the store type post-hoc is
    a deliberate, separate action, not a settings toggle."""

    class Meta:
        model = Site
        fields = [
            "name", "tagline", "currency", "country",
            "support_email", "support_phone",
            "legal_name", "gstin", "pan", "msme_number",
            "abandoned_cart_emails_enabled", "maintenance_mode",
        ]
        # ``authorized_signature`` is set by the settings view from an image
        # picked in the media library, not through this form.
        widgets = {
            "gstin": forms.TextInput(attrs={
                "placeholder": "22AAAAA0000A1Z5", "maxlength": 15, "autocomplete": "off",
                "spellcheck": "false", "class": "set-upper",
            }),
            "pan": forms.TextInput(attrs={
                "placeholder": "AAAAA0000A", "maxlength": 10, "autocomplete": "off",
                "spellcheck": "false", "class": "set-upper",
            }),
            "msme_number": forms.TextInput(attrs={
                "placeholder": "UDYAM-MH-01-0012345", "maxlength": 19, "autocomplete": "off",
                "spellcheck": "false", "class": "set-upper",
            }),
            "legal_name": forms.TextInput(attrs={"placeholder": "e.g. Acme Retail Private Limited"}),
        }
        labels = {"legal_name": "Legal business name"}

    # Formats (all optional). A GSTIN embeds the business PAN at characters
    # 3–12, so when both are given they must agree.
    _PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
    _UDYAM_RE = re.compile(r"^UDYAM-[A-Z]{2}-[0-9]{2}-[0-9]{7}$")

    @staticmethod
    def _compact(value) -> str:
        return re.sub(r"\s+", "", str(value or "")).upper()

    def clean_legal_name(self):
        return " ".join(str(self.cleaned_data.get("legal_name") or "").split())

    def clean_gstin(self):
        from billing import gst

        value = gst.normalise_gstin(self.cleaned_data.get("gstin"))
        if value and not gst.is_valid_gstin(value):
            raise forms.ValidationError("Enter a valid 15-character GSTIN, e.g. 22AAAAA0000A1Z5.")
        return value

    def clean_pan(self):
        value = self._compact(self.cleaned_data.get("pan"))
        if value and not self._PAN_RE.match(value):
            raise forms.ValidationError("Enter a valid 10-character PAN, e.g. AAAAA0000A.")
        return value

    def clean_msme_number(self):
        value = self._compact(self.cleaned_data.get("msme_number"))
        if value and not self._UDYAM_RE.match(value):
            raise forms.ValidationError("Enter a valid Udyam number, e.g. UDYAM-MH-01-0012345.")
        return value

    def clean(self):
        data = super().clean()
        gstin, pan = data.get("gstin"), data.get("pan")
        if gstin and pan and gstin[2:12] != pan:
            self.add_error("pan", "This PAN doesn’t match the one inside your GSTIN.")
        elif gstin and not pan and not self.errors.get("pan"):
            # Fill the PAN from the GSTIN (characters 3–12) when left blank.
            data["pan"] = gstin[2:12]
        return data


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "parent", "description", "image", "order", "is_active", "show_on_homepage"]

    def __init__(self, *args, site=None, **kwargs):
        super().__init__(*args, **kwargs)
        if site is not None:
            # Parent choices must stay within this store.
            qs = Category.objects.for_site(site).filter(is_deleted=False)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            self.fields["parent"].queryset = display_ordered_categories(qs)
            self.fields["parent"].required = False


class ProductForm(forms.ModelForm):
    """Scalar product fields, validated server-side. Repeatable/list data
    (bullets, FAQs, specs, keywords, accessories, images, variants) is handled
    by the view from the multi-step template, and stored on the model's JSON
    fields. The template lays these fields out across D2C/B2B steps."""

    class Meta:
        model = Product
        fields = [
            # Identity
            "category", "title", "description", "currency", "brand",
            # Pricing / tax / stock
            "sku", "mrp", "price", "gst_percentage", "gst_inclusive", "hsn_code",
            "inventory", "low_stock_threshold",
            # Dimensions & weight
            "product_length", "product_breadth", "product_height", "product_unit",
            "weight_value", "weight_unit",
            "package_length", "package_breadth", "package_height", "package_unit",
            "package_weight_value", "package_weight_unit",
            # Compliance
            "country_of_origin", "dangerous_goods", "return_type", "return_days", "replacement_days",
            "guarantee_warranty", "manufacturer_contact", "packer_contact", "importer_contact",
            # Media
            "video_link", "video_orientation",
            # Flags
            "is_active", "is_featured",
        ]
        labels = {
            "title": "Product title",
            "brand": "Brand",
            "mrp": "MRP",
            "price": "Selling price",
            "gst_percentage": "GST %",
            "gst_inclusive": "GST included in the price",
            "hsn_code": "HSN code",
            "inventory": "Stock available",
            "sku": "SKU",
            "video_link": "Video link",
            "return_type": "Return policy",
            "return_days": "Return days",
            "replacement_days": "Replacement days",
        }
        widgets = {
            "return_days": forms.NumberInput(attrs={"min": 0, "max": 365, "step": 1, "placeholder": "e.g. 7"}),
            "replacement_days": forms.NumberInput(attrs={"min": 0, "max": 365, "step": 1, "placeholder": "e.g. 7"}),
            "description": forms.Textarea(attrs={"rows": 6}),
            "guarantee_warranty": forms.TextInput(),
            "manufacturer_contact": forms.TextInput(attrs={"placeholder": "Name, phone, address"}),
            "packer_contact": forms.TextInput(attrs={"placeholder": "Name, phone, address"}),
            "importer_contact": forms.TextInput(attrs={"placeholder": "Name, phone, address (for imported goods)"}),
            "brand": forms.TextInput(attrs={"placeholder": "Brand name (your own brand or the maker's)", "maxlength": 120}),
        }
        help_texts = {
            "brand": "The brand on the product. Required: enter your own brand name if you make it.",
            "replacement_days": "Days a customer can ask for a replacement. Enter 0 if it can't be replaced.",
        }

    #: Fields the reference marks required (shared by D2C and B2B).
    REQUIRED = ("category", "title", "description", "price", "gst_percentage", "hsn_code", "brand")

    def __init__(self, *args, site=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.site = site
        if site is not None:
            self.fields["category"].queryset = display_ordered_categories(
                Category.objects.for_site(site).filter(is_deleted=False)
            )
        for name in self.REQUIRED:
            self.fields[name].required = True
        # Everything else optional so a step can be left blank and revisited.
        for name, field in self.fields.items():
            if name not in self.REQUIRED:
                field.required = False
        # Return policy: returnable or replacement-only. "Non-returnable" is no
        # longer offered (older products saved with it must pick one of these
        # the next time they are edited).
        if "return_type" in self.fields:
            self.fields["return_type"].choices = [
                ("", "Select a return policy"),
                (Product.ReturnType.RETURNABLE, "Returnable"),
                (Product.ReturnType.REPLACEMENT, "Replacement only"),
            ]

    #: Longest return / replacement window accepted, in days.
    MAX_RETURN_DAYS = 365

    def clean_brand(self):
        brand = " ".join((self.cleaned_data.get("brand") or "").split())
        if not brand:
            raise forms.ValidationError("Enter the brand name.")
        return brand

    def clean(self):
        cleaned = super().clean()
        # The return policy and its window travel together: returnable /
        # replacement-only need the real window (it is what the product page,
        # its return-policy badge and its search markup state); no policy
        # chosen means no window.
        # A ticked "Returnable" / "Replacement Only" trust badge *is* the
        # policy (the form locks the field to it); enforce it here too.
        from .trust_badges import return_policy_from

        badges = self.data.getlist("trusted_icons") if hasattr(self.data, "getlist") else []
        badge_rt = return_policy_from(badges)
        if badge_rt:
            cleaned["return_type"] = badge_rt
            self.errors.pop("return_type", None)
        rt = cleaned.get("return_type") or ""
        days = cleaned.get("return_days")
        if rt in (Product.ReturnType.RETURNABLE, Product.ReturnType.REPLACEMENT):
            if days is None or days < 1:
                self.add_error(
                    "return_days",
                    "Enter how many days customers have to "
                    + ("ask for a replacement." if rt == Product.ReturnType.REPLACEMENT else "return the product."),
                )
            elif days > self.MAX_RETURN_DAYS:
                self.add_error("return_days", f"Keep the window to {self.MAX_RETURN_DAYS} days or fewer.")
        else:
            cleaned["return_days"] = None
        # The separate replacement window belonged to non-returnable products,
        # which are no longer offered.
        cleaned["replacement_days"] = None
        return cleaned


class CollaborationForm(forms.ModelForm):
    class Meta:
        model = Collaboration
        fields = ["name", "email", "handle", "commission_type", "commission_value", "status"]


class PaymentConfigForm(forms.ModelForm):
    key_id = forms.CharField(required=False, widget=forms.PasswordInput(render_value=True))
    key_secret = forms.CharField(required=False, widget=forms.PasswordInput(render_value=True))

    class Meta:
        model = PaymentGatewayConfig
        fields = [
            "gateway", "key_id", "key_secret", "enabled", "cod_enabled",
            "partial_payment_enabled", "partial_payment_type", "partial_payment_value",
            "company_name", "gstin", "state", "pincode", "mobile_number", "email", "address",
        ]


class TrackingConfigForm(forms.ModelForm):
    class Meta:
        model = TrackingConfig
        fields = [
            "google_analytics_id", "google_tag_manager_id", "google_ads_id",
            "meta_pixel_id", "meta_capi_token", "tiktok_pixel_id",
            "pinterest_tag_id", "microsoft_uet_id", "custom_head_html",
        ]
        widgets = {"meta_capi_token": forms.PasswordInput(render_value=True)}


# Target countries / content languages offered for the Google feed. A product's
# price must be in the target country's currency, so India (INR) leads.
GM_COUNTRIES = [
    ("IN", "India"), ("US", "United States"), ("GB", "United Kingdom"), ("AE", "United Arab Emirates"),
    ("SA", "Saudi Arabia"), ("SG", "Singapore"), ("AU", "Australia"), ("CA", "Canada"),
    ("DE", "Germany"), ("FR", "France"), ("NL", "Netherlands"), ("IT", "Italy"), ("ES", "Spain"),
    ("JP", "Japan"), ("MY", "Malaysia"), ("NZ", "New Zealand"), ("ZA", "South Africa"),
]
GM_LANGUAGES = [
    ("en", "English"), ("hi", "Hindi"), ("ar", "Arabic"), ("de", "German"), ("fr", "French"),
    ("es", "Spanish"), ("it", "Italian"), ("nl", "Dutch"), ("ja", "Japanese"), ("ms", "Malay"),
    ("ta", "Tamil"), ("te", "Telugu"), ("bn", "Bengali"), ("mr", "Marathi"), ("gu", "Gujarati"),
]


class GoogleMerchantForm(forms.ModelForm):
    """Feed target + auto-sync. The connection itself is OAuth (no keys)."""

    default_country = forms.ChoiceField(choices=GM_COUNTRIES, label="Target country")
    default_language = forms.ChoiceField(choices=GM_LANGUAGES, label="Content language")

    class Meta:
        model = GoogleMerchantConfig
        fields = ["default_country", "default_language", "auto_sync"]
        labels = {"auto_sync": "Keep Google in sync automatically"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("default_country", "default_language"):
            self.fields[name].widget.attrs["class"] = "pal-select"
        # Keep a value saved before this list existed selectable.
        for name, choices in (("default_country", GM_COUNTRIES), ("default_language", GM_LANGUAGES)):
            current = getattr(self.instance, name, "") or ""
            if current and current not in dict(choices):
                self.fields[name].choices = [(current, current)] + list(choices)


class WarehouseForm(forms.ModelForm):
    class Meta:
        model = Warehouse
        fields = [
            "name", "contact_name", "phone", "email", "address_line",
            "city", "state", "pincode", "country", "is_default",
        ]


class ShippingProviderForm(forms.ModelForm):
    api_token = forms.CharField(required=False, widget=forms.PasswordInput(render_value=True))
    # Shiprocket API credentials. Rendered as password inputs; the email is shown
    # so the owner can confirm which account is connected.
    api_email = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "pal-input", "placeholder": "you@yourbrand.com", "autocomplete": "off"}),
    )
    api_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=True, attrs={"class": "pal-input", "autocomplete": "new-password"}),
    )
    pickup_location = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "pal-input",
                "placeholder": "Exact warehouse name from your Shiprocket dashboard",
                "autocomplete": "off",
            }
        ),
    )

    class Meta:
        model = ShippingProvider
        fields = [
            "provider", "enabled", "auto_ship", "api_token", "client_name",
            "api_email", "api_password", "pickup_location", "default_warehouse",
        ]

    def __init__(self, *args, site=None, **kwargs):
        super().__init__(*args, **kwargs)
        if site is not None:
            self.fields["default_warehouse"].queryset = Warehouse.objects.for_site(site)
            self.fields["default_warehouse"].required = False

    def clean(self):
        cleaned = super().clean()
        # A Shiprocket provider can only be *enabled* once it can actually
        # authenticate and knows where to pick up  otherwise every booking would
        # fail at order time with a less obvious error.
        provider = cleaned.get("provider")
        if (
            provider == ShippingProvider.Provider.SHIPROCKET
            and not (cleaned.get("pickup_location") or "").strip()
            and (cleaned.get("api_email") or "").strip()
            and (cleaned.get("api_password") or "").strip()
        ):
            # No pickup location picked yet: default to the account's primary
            # Shiprocket pickup address (or its only one), so a store that just
            # connected is ready to book without another step.
            cleaned["pickup_location"] = self._default_pickup_location(cleaned)
        if cleaned.get("enabled") and provider == ShippingProvider.Provider.SHIPROCKET:
            missing = []
            if not (cleaned.get("api_email") or "").strip():
                missing.append("API email")
            if not (cleaned.get("api_password") or "").strip():
                missing.append("API password")
            if not (cleaned.get("pickup_location") or "").strip():
                missing.append("pickup location")
            if missing:
                raise forms.ValidationError(
                    "To enable Shiprocket, fill in: " + ", ".join(missing) + "."
                    + (
                        " Click “Load pickup locations” and choose one from your Shiprocket account."
                        if missing == ["pickup location"]
                        else ""
                    )
                )
        return cleaned

    def _default_pickup_location(self, cleaned) -> str:
        """The primary (else only) pickup address of the Shiprocket account the
        submitted credentials sign in to, or "" when it can't be read."""
        from commerce import shipping_shiprocket as sr

        email = (cleaned.get("api_email") or "").strip()
        password = cleaned.get("api_password") or ""
        try:
            if (
                self.instance.pk
                and email == (self.instance.api_email or "").strip()
                and password == (self.instance.api_password or "")
            ):
                locations = sr.list_pickup_locations(self.instance)
            else:
                locations = sr.pickup_locations_for_credentials(
                    email, password, base_url=self.instance.base_url or ""
                )
        except Exception:
            return ""
        primary = next((loc for loc in locations if loc.get("primary")), None)
        if primary is None and len(locations) == 1:
            primary = locations[0]
        return (primary or {}).get("name", "")
