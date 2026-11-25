"""The building blocks an App is composed from.

Everything here is **code**: reviewed, deployed, and the only things that ever
run. An App in the admin only *references* these by key. That split is what
lets staff create any app at any time (price, dashboard placement, storefront
placement, owner settings) without a deploy, while nothing typed into the admin
can ever execute on our servers.

Adding a block later:

* a new owner-settings field type: add a :class:`FieldType` to
  :data:`FIELD_TYPES` and teach :mod:`marketplace.forms` to clean it;
* a new dashboard placement point: add it to :data:`PLACEMENT_POINTS` and
  drop ``{% app_point "<key>" %}`` where it should appear;
* a new storefront slot: add a :class:`Slot` and render it in every theme
  (page slots in the base templates; inline slots as ``{% app_slot "<key>" %}``
  at the right spot in the page template, the theme-contract marker. A
  fallback selector in ``static/js/marketplace/ow-slots.js`` only covers a
  theme that has not placed the marker yet);
* a new storefront widget: add a :class:`WidgetType` and its template at
  ``templates/marketplace/widgets/<key>.html``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Owner settings field types
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldType:
    key: str
    label: str
    help: str = ""


FIELD_TYPES: dict[str, FieldType] = {
    f.key: f
    for f in (
        FieldType("text", "Short text", "One line, up to 255 characters (or the max length you set)."),
        FieldType("textarea", "Long text", "Several lines of plain text."),
        FieldType("rich_text", "Rich text", "Formatted text. Cleaned to safe HTML (no scripts)."),
        FieldType("number", "Number", "A whole or decimal number; respects min / max."),
        FieldType("money", "Money", "An amount in rupees with two decimals."),
        FieldType("toggle", "On / off switch", ""),
        FieldType("select", "Dropdown (one)", "Choices: one per line as value|Label."),
        FieldType("multi_select", "Checkboxes (many)", "Choices: one per line as value|Label."),
        FieldType("color", "Colour", "A hex colour like #4b2bf5."),
        FieldType("image", "Image upload", "PNG, JPG or WEBP; re-encoded to WebP on upload."),
        FieldType("url", "Link (https)", "An https:// address."),
        FieldType("phone", "Phone number", "Digits, spaces and + only."),
        FieldType("email", "Email address", ""),
        FieldType("date_time", "Date and time", ""),
        FieldType("product_picker", "Product picker", "One of the store's products."),
        FieldType("category_picker", "Category picker", "One of the store's categories."),
        FieldType("coupon_picker", "Discount code picker", "One of the store's discount codes."),
        FieldType("secret", "Secret (API key, token)", "Stored encrypted; never shown again after saving."),
        FieldType("identifier", "Tracking ID", "Letters, digits, dash, dot and underscore only (safe inside scripts)."),
    )
}


def field_type_choices() -> list[tuple[str, str]]:
    return [(f.key, f.label) for f in FIELD_TYPES.values()]


# --------------------------------------------------------------------------
# Dashboard: page kinds and placement points
# --------------------------------------------------------------------------

PAGE_KINDS: dict[str, str] = {
    "settings_form": "Owner settings form (the fields you define on the app)",
    "module_page": "Page provided by the app's code module",
    "external_link": "Link out to an external tool (new tab, signed store token appended)",
    "embedded": "Partner page embedded in a sandboxed frame",
    "info": "Information page (the text you write below)",
}

#: Where in the owner dashboard an app can appear.
PLACEMENT_POINTS: dict[str, str] = {
    "sidebar": "Store sidebar (pick the group and order)",
    "settings_card": "Card in Store → Settings",
    "overview_card": "Card on the store Overview page",
    "dashboard_card": "Card on the account Dashboard (all stores)",
    "top_bar": "Icon in the store top bar",
    "editor_link": "Link in Edit content",
    "product_panel": "Panel on the product edit page",
    "order_panel": "Panel on the order detail page",
}

#: Sidebar groups an app can join. The first six are the dashboard's own groups
#: (commerce.context.NAV_GROUPS); "Apps" is a group of its own, after them.
NAV_GROUP_CHOICES: list[tuple[str, str]] = [
    ("Storefront", "Storefront"),
    ("Operate", "Operate"),
    ("Catalogue", "Catalogue"),
    ("Grow", "Grow"),
    ("Money & delivery", "Money & delivery"),
    ("Store", "Store"),
    ("Apps", "Apps (own group)"),
]

#: Icon keys available in templates/store/_icons.html.
ICON_CHOICES: list[tuple[str, str]] = [
    (k, k) for k in (
        "grid", "layout", "image", "globe", "bag", "undo", "inbox", "users", "mail",
        "box", "layers", "warehouse", "alert", "star", "chart", "tag", "handshake",
        "bullhorn", "google", "card", "truck", "invoice", "cog", "eye", "video",
        "play", "pencil", "lock", "wallet", "bell", "search", "check", "printer",
    )
]

# --------------------------------------------------------------------------
# Storefront: pages, slots and widgets
# --------------------------------------------------------------------------

#: Storefront page kinds a placement can be limited to.
STOREFRONT_PAGES: dict[str, str] = {
    "all": "Every page",
    "home": "Home page",
    "pdp": "Product pages",
    "collection": "Shop and category pages",
    "cart": "Cart",
    "checkout": "Checkout",
    "order_confirmation": "Order confirmation",
    "content": "Other pages (about, blogs, policies, contact)",
}

#: storefront URL name -> page kind (see storefront/urls.py).
URLNAME_TO_PAGE: dict[str, str] = {
    "public_website": "home",
    "public_product_detail": "pdp",
    "public_all_products": "collection",
    "public_products_index": "collection",
    "public_categories": "collection",
    "public_category_detail": "collection",
    "public_cart": "cart",
    "public_checkout": "checkout",
    "public_order_confirmation": "order_confirmation",
}


@dataclass(frozen=True)
class Slot:
    key: str
    label: str
    #: "page" slots are rendered server-side by every theme's base template.
    #: "inline" slots are delivered as <template> fragments and mounted next to
    #: a per-theme anchor by static/js/marketplace/ow-slots.js.
    scope: str
    #: Pages this slot exists on ("all" = every page).
    pages: tuple[str, ...] = ("all",)


SLOTS: dict[str, Slot] = {
    s.key: s
    for s in (
        Slot("head.end", "Inside <head> (tracking pixels only)", "page"),
        Slot("body.start", "Top of the page body", "page"),
        Slot("header.above", "Above the header", "page"),
        Slot("header.below", "Below the header", "page"),
        Slot("footer.above", "Above the footer", "page"),
        Slot("body.end", "End of the page body", "page"),
        Slot("float.bottom_left", "Floating, bottom left", "page"),
        Slot("float.bottom_right", "Floating, bottom right", "page"),
        Slot("popup", "Popup", "page"),
        Slot("home.after_hero", "Home: after the hero", "inline", ("home",)),
        Slot("home.after_categories", "Home: after Categories", "inline", ("home",)),
        Slot("home.after_bestsellers", "Home: after Best sellers", "inline", ("home",)),
        Slot("home.after_about", "Home: after About", "inline", ("home",)),
        Slot("home.after_faq", "Home: after FAQ", "inline", ("home",)),
        Slot("pdp.after_title", "Product: after the title", "inline", ("pdp",)),
        Slot("pdp.after_price", "Product: after the price", "inline", ("pdp",)),
        Slot("pdp.after_variations", "Product: below the variations", "inline", ("pdp",)),
        Slot("pdp.after_buy_box", "Product: after the buy buttons", "inline", ("pdp",)),
        Slot("pdp.after_details", "Product: after the details tabs", "inline", ("pdp",)),
        Slot("collection.top", "Shop / category: above the products", "inline", ("collection",)),
        Slot("cart.after_items", "Cart: after the items", "inline", ("cart",)),
        Slot("checkout.after_summary", "Checkout: after the order summary", "inline", ("checkout",)),
    )
}

PAGE_SLOTS = tuple(k for k, s in SLOTS.items() if s.scope == "page")
INLINE_SLOTS = tuple(k for k, s in SLOTS.items() if s.scope == "inline")


def slot_choices() -> list[tuple[str, str]]:
    return [(s.key, s.label) for s in SLOTS.values()]


@dataclass(frozen=True)
class WidgetType:
    key: str
    label: str
    #: Owner/admin value keys the widget reads. Define AppSettingFields with
    #: these keys (or fix them in the placement's options) to fill them.
    fields: tuple[str, ...] = ()
    required: tuple[str, ...] = ()
    #: Slots this widget may be placed in (empty = any non-head slot).
    slots: tuple[str, ...] = ()
    description: str = ""
    #: Widget-level options staff may set on the placement (JSON keys).
    options: tuple[str, ...] = field(default_factory=tuple)


_ANY_BODY = tuple(k for k in SLOTS if k != "head.end")
_FLOATS = ("float.bottom_left", "float.bottom_right")

WIDGET_TYPES: dict[str, WidgetType] = {
    w.key: w
    for w in (
        WidgetType(
            "announcement", "Announcement bar",
            fields=("text", "link_url", "link_label", "bg_color", "text_color", "dismissible"),
            required=("text",),
            slots=("header.above", "header.below", "body.start", "footer.above") + INLINE_SLOTS,
            description="One line of text with an optional link.",
        ),
        WidgetType(
            "banner", "Image banner",
            fields=("image", "heading", "text", "link_url", "link_label", "bg_color", "text_color", "align"),
            required=("image",),
            slots=("header.below", "footer.above") + INLINE_SLOTS,
            description="A full-width image with optional heading, text and button.",
        ),
        WidgetType(
            "rich_text", "Text block",
            fields=("heading", "content", "max_width", "align"),
            required=("content",),
            slots=_ANY_BODY,
            description="Formatted text (cleaned to safe HTML).",
        ),
        WidgetType(
            "html_block", "Custom HTML block",
            fields=("html",),
            required=("html",),
            slots=_ANY_BODY,
            description="HTML restricted to a safe allowlist. No scripts, no event handlers.",
        ),
        WidgetType(
            "image_strip", "Image strip",
            fields=("heading", "image_1", "image_2", "image_3", "image_4", "image_5", "image_6", "link_url"),
            required=("image_1",),
            slots=("header.below", "footer.above") + INLINE_SLOTS,
            description="Up to six images in a row (logos, press, gallery).",
        ),
        WidgetType(
            "trust_strip", "Trust strip",
            fields=("heading", "item_1", "item_2", "item_3", "item_4", "bg_color", "text_color"),
            required=("item_1",),
            slots=("header.below", "footer.above") + INLINE_SLOTS,
            description="Short promises with check marks (free returns, secure pay, ...).",
        ),
        WidgetType(
            "countdown", "Countdown",
            fields=("heading", "text", "ends_at", "bg_color", "text_color", "link_url", "link_label"),
            required=("ends_at",),
            slots=("header.above", "header.below", "footer.above") + INLINE_SLOTS,
            description="Counts down to a date; hides itself when the time is up.",
        ),
        WidgetType(
            "floating_button", "Floating button",
            fields=("kind", "phone", "url", "label", "message", "bg_color", "text_color"),
            required=("kind",),
            slots=_FLOATS,
            description="WhatsApp, call or link button that floats in a corner.",
        ),
        WidgetType(
            "popup", "Popup",
            fields=("heading", "text", "image", "button_label", "button_url", "delay_seconds", "show_once"),
            required=("heading",),
            slots=("popup",),
            description="A popup shown after a delay; optionally once per visitor.",
        ),
        WidgetType(
            "size_chart", "Size chart (expandable)",
            fields=("heading", "content"),
            required=("content",),
            slots=("pdp.after_price", "pdp.after_buy_box", "pdp.after_details"),
            description="An expandable panel on product pages.",
        ),
        WidgetType(
            "faq_block", "FAQ block",
            fields=("heading", "q_1", "a_1", "q_2", "a_2", "q_3", "a_3", "q_4", "a_4", "q_5", "a_5"),
            required=("q_1", "a_1"),
            slots=("footer.above",) + INLINE_SLOTS,
            description="Up to five questions and answers.",
        ),
        WidgetType(
            "product_badge", "Product badge",
            fields=("text", "bg_color", "text_color"),
            required=("text",),
            slots=("pdp.after_title", "pdp.after_price"),
            description="A small pill on product pages (e.g. 'Ships in 24h').",
        ),
        WidgetType(
            "pixel", "Tracking pixel / script",
            fields=("tracking_id",),
            required=("tracking_id",),
            slots=("head.end", "body.end"),
            description=(
                "Loads a script from an origin staff approved on the app. Options: "
                '{"src": "https://host/path?id={tracking_id}", "inline": "optional init code with {tracking_id}"}. '
                "Owner values are restricted to safe identifier characters."
            ),
            options=("src", "inline"),
        ),
        WidgetType(
            "module_widget", "Rendered by the app's code module",
            fields=(),
            slots=_ANY_BODY,
            description="The app's module renders this itself (needs a module).",
            options=("widget",),
        ),
    )
}


def widget_type_choices() -> list[tuple[str, str]]:
    return [(w.key, w.label) for w in WIDGET_TYPES.values()]


def allowed_slots_for(widget_key: str) -> tuple[str, ...]:
    w = WIDGET_TYPES.get(widget_key)
    if w is None:
        return ()
    return w.slots or _ANY_BODY
