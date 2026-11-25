"""Example apps for local development (DEBUG only, idempotent).

Seeded after ``migrate`` when ``DEV_SEED_STORE`` is on (same switch as the demo
store), so localhost has apps to browse, install and see on a storefront with
no manual step. Never runs in production. Each example is also a template for
how staff compose an app in the admin:

* **Announcement Bar** (free): settings form in the Storefront sidebar group,
  an announcement widget above or below the header.
* **WhatsApp Chat Button** (paid yearly, 7-day trial): a settings card in Store
  Settings, a floating button bottom-right.
* **Sale Countdown** (free): an Overview card, a countdown bar.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings

EXAMPLES = (
    {
        "app": {
            "slug": "announcement-bar", "name": "Announcement Bar",
            "tagline": "A one-line message across the top of your store, with an optional link.",
            "description": "<p>Tell every visitor about a sale, free shipping or a new launch. "
                           "Pick the colours, add a link, and choose above or below the header.</p>",
            "category": "marketing", "icon_key": "bullhorn", "accent": "#e8590c",
            "pricing": "free", "status": "published", "order": 10, "featured": True,
        },
        "fields": (
            {"key": "text", "label": "Message", "field_type": "text", "required": True, "max_length": 140,
             "default": "Free shipping on orders over Rs 999"},
            {"key": "link_url", "label": "Link (optional)", "field_type": "url"},
            {"key": "link_label", "label": "Link text", "field_type": "text", "default": "Shop now"},
            {"key": "bg_color", "label": "Background colour", "field_type": "color", "default": "#111111"},
            {"key": "text_color", "label": "Text colour", "field_type": "color", "default": "#ffffff"},
            {"key": "dismissible", "label": "Let visitors close it", "field_type": "toggle", "default": "1"},
        ),
        "dashboard": (
            {"point": "sidebar", "nav_group": "Storefront", "order": 50, "label": "Announcement bar",
             "icon_key": "bullhorn", "page_kind": "settings_form"},
        ),
        "storefront": (
            {"label": "Top bar", "widget_type": "announcement", "default_slot": "header.above",
             "allowed_slots": ["header.above", "header.below"], "pages": ["all"]},
        ),
    },
    {
        "app": {
            "slug": "whatsapp-chat", "name": "WhatsApp Chat Button",
            "tagline": "A floating WhatsApp button so shoppers can message you in one tap.",
            "description": "<p>Shoppers tap the button and WhatsApp opens with your number and a "
                           "ready-made message. Works on phones and desktop.</p>",
            "category": "support", "icon_key": "inbox", "accent": "#128c7e",
            "pricing": "yearly", "price": Decimal("499.00"), "trial_days": 7,
            "status": "published", "order": 20,
        },
        "fields": (
            {"key": "phone", "label": "WhatsApp number (with country code)", "field_type": "phone", "required": True},
            {"key": "message", "label": "Pre-filled message", "field_type": "text",
             "default": "Hi! I have a question about a product."},
            {"key": "label", "label": "Button text", "field_type": "text", "default": "Chat with us"},
            {"key": "bg_color", "label": "Button colour", "field_type": "color", "default": "#25d366"},
        ),
        "dashboard": (
            {"point": "settings_card", "order": 10, "label": "WhatsApp chat button",
             "summary": "Your number, message and button colour.", "icon_key": "inbox", "page_kind": "settings_form"},
        ),
        "storefront": (
            {"label": "Floating button", "widget_type": "floating_button", "default_slot": "float.bottom_right",
             "allowed_slots": ["float.bottom_right", "float.bottom_left"], "pages": ["all"],
             "options": {"kind": "whatsapp", "text_color": "#ffffff"}},
        ),
    },
    {
        "app": {
            "slug": "sale-countdown", "name": "Sale Countdown",
            "tagline": "A ticking countdown to the end of your sale. Hides itself when time is up.",
            "description": "<p>Create urgency for a limited-time offer. The bar disappears on its own "
                           "when the sale ends.</p>",
            "category": "sales", "icon_key": "alert", "accent": "#c2255c",
            "pricing": "free", "status": "published", "order": 30,
        },
        "fields": (
            {"key": "heading", "label": "Heading", "field_type": "text", "default": "Sale ends in"},
            {"key": "ends_at", "label": "Sale ends at", "field_type": "date_time", "required": True},
            {"key": "link_url", "label": "Link (optional)", "field_type": "url"},
            {"key": "bg_color", "label": "Background colour", "field_type": "color", "default": "#c2255c"},
        ),
        "dashboard": (
            {"point": "overview_card", "order": 10, "label": "Sale countdown",
             "summary": "Set when your sale ends.", "icon_key": "alert", "page_kind": "settings_form"},
        ),
        "storefront": (
            {"label": "Countdown bar", "widget_type": "countdown", "default_slot": "header.below",
             "allowed_slots": ["header.above", "header.below", "home.after_hero", "pdp.after_price"],
             "pages": ["all"]},
        ),
    },
)


def seed(log=None) -> int:
    if not settings.DEBUG:
        return 0
    from .models import App, AppDashboardPlacement, AppSettingField, AppStorefrontPlacement
    from .registry import sync

    sync(force=True)
    made = 0
    for ex in EXAMPLES:
        spec = ex["app"]
        if App.objects.filter(slug=spec["slug"]).exists():
            continue
        app = App.objects.create(**spec)
        for i, f in enumerate(ex["fields"]):
            AppSettingField.objects.create(app=app, order=i, **f)
        for p in ex["dashboard"]:
            AppDashboardPlacement.objects.create(app=app, **p)
        for p in ex["storefront"]:
            AppStorefrontPlacement.objects.create(app=app, **p)
        made += 1
        if log:
            log(f"marketplace example app: {app.name}")
    return made
