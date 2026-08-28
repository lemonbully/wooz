"""Owner-facing forms for the store-management surface.

Forms never expose the ``site`` field — the view sets it from the resolved,
owned store — so a crafted POST cannot reassign a record to another tenant.
"""

from __future__ import annotations

from django import forms

from tenancy.models import Site

from .models import (
    Category,
    Collaboration,
    Coupon,
    GoogleMerchantConfig,
    PaymentGatewayConfig,
    Product,
    ShippingProvider,
    TrackingConfig,
    Warehouse,
)


class SiteForm(forms.ModelForm):
    """Owner-editable store settings. Slug/business-model set at creation and
    left out here — changing the public subdomain or the store type post-hoc is
    a deliberate, separate action, not a settings toggle."""

    class Meta:
        model = Site
        fields = [
            "name", "tagline", "currency", "country",
            "support_email", "support_phone",
            "abandoned_cart_emails_enabled", "maintenance_mode",
        ]


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
            self.fields["parent"].queryset = qs
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
            "category", "title", "description", "currency",
            # Pricing / tax / stock
            "sku", "mrp", "price", "gst_percentage", "hsn_code",
            "inventory", "low_stock_threshold",
            # Dimensions & weight
            "product_length", "product_breadth", "product_height", "product_unit",
            "weight_value", "weight_unit",
            "package_length", "package_breadth", "package_height", "package_unit",
            "package_weight_value", "package_weight_unit",
            # Compliance
            "country_of_origin", "dangerous_goods", "return_type",
            "guarantee_warranty", "manufacturer_contact", "packer_contact",
            # Media
            "video_link", "video_orientation",
            # Flags
            "is_active", "is_featured",
        ]
        labels = {
            "title": "Product title",
            "mrp": "MRP",
            "price": "Selling price",
            "gst_percentage": "GST %",
            "hsn_code": "HSN code",
            "inventory": "Stock available",
            "sku": "SKU",
            "video_link": "Video link",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 6}),
            "guarantee_warranty": forms.TextInput(),
            "manufacturer_contact": forms.TextInput(attrs={"placeholder": "Name, phone, address"}),
            "packer_contact": forms.TextInput(attrs={"placeholder": "Name, phone, address"}),
        }

    #: Fields the reference marks required (shared by D2C and B2B).
    REQUIRED = ("category", "title", "description", "price", "gst_percentage", "hsn_code")

    def __init__(self, *args, site=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.site = site
        if site is not None:
            self.fields["category"].queryset = Category.objects.for_site(site).filter(is_deleted=False)
        for name in self.REQUIRED:
            self.fields[name].required = True
        # Everything else optional so a step can be left blank and revisited.
        for name, field in self.fields.items():
            if name not in self.REQUIRED:
                field.required = False


class CouponForm(forms.ModelForm):
    class Meta:
        model = Coupon
        fields = [
            "code", "discount_type", "value", "active", "starts_at", "ends_at",
            "min_order_value", "max_discount_amount", "usage_limit",
        ]
        widgets = {
            "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }


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


class GoogleMerchantForm(forms.ModelForm):
    class Meta:
        model = GoogleMerchantConfig
        fields = ["merchant_id", "service_account_json", "auto_sync", "default_country", "default_language"]
        widgets = {"service_account_json": forms.Textarea(attrs={"rows": 5})}


class WarehouseForm(forms.ModelForm):
    class Meta:
        model = Warehouse
        fields = [
            "name", "contact_name", "phone", "email", "address_line",
            "city", "state", "pincode", "country", "is_default",
        ]


class ShippingProviderForm(forms.ModelForm):
    api_token = forms.CharField(required=False, widget=forms.PasswordInput(render_value=True))

    class Meta:
        model = ShippingProvider
        fields = ["provider", "enabled", "auto_ship", "api_token", "client_name", "default_warehouse"]

    def __init__(self, *args, site=None, **kwargs):
        super().__init__(*args, **kwargs)
        if site is not None:
            self.fields["default_warehouse"].queryset = Warehouse.objects.for_site(site)
            self.fields["default_warehouse"].required = False
