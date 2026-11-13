"""The storefront content editor.

A section-based editor over ``Site.content`` with per-section autosave. Each
section owns a whitelisted set of content keys (``SECTIONS``); a save merges only
those keys, so one section can never clobber another's data, and unknown keys
posted by a stale client are dropped rather than stored.

The option set mirrors the reference tool's edit page  brand, appearance,
hero, home sections, about, testimonials, FAQ, footer/social, contact, SEO,
policies, B2B certifications, and custom scripts  so owners have the same
control over their storefront.
"""

from __future__ import annotations

import json
import re

from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from commerce import activity
from commerce.context import get_owned_site, store_context
from storefront.context import build_context

# --------------------------------------------------------------------------
# Section schema  the single source of truth for what the editor exposes.
#
# Field types the client understands:
#   text · textarea · richtext · url · number · color · image · checkbox
#   select (options) · object (fixed subfields) · list (repeatable rows)
# A section may declare "audience": "b2b" | "d2c" to show only for that model.
# --------------------------------------------------------------------------

# Style choices render as visual cards (type "select_cards"): each carries a
# small SVG mockup of the layout so the owner picks by seeing it, not by reading
# a dropdown. The SVGs are schematic  currentColor + opacity  so they sit on
# the editor's light panels and adapt if the palette ever changes.
_HERO_STYLES = [
    {
        "value": "style1", "label": "Classic slider",
        "desc": "Full-width centred text over a rotating banner, with dots.",
        "preview": (
            '<svg viewBox="0 0 160 90" preserveAspectRatio="xMidYMid slice">'
            '<rect width="160" height="90" fill="currentColor" opacity="0.06"/>'
            '<rect x="52" y="30" width="56" height="7" rx="2" fill="currentColor" opacity="0.35"/>'
            '<rect x="60" y="43" width="40" height="4" rx="2" fill="currentColor" opacity="0.2"/>'
            '<rect x="66" y="54" width="28" height="9" rx="2" fill="currentColor" opacity="0.5"/>'
            '<circle cx="74" cy="80" r="2.2" fill="currentColor" opacity="0.6"/>'
            '<circle cx="82" cy="80" r="2.2" fill="currentColor" opacity="0.22"/>'
            '<circle cx="90" cy="80" r="2.2" fill="currentColor" opacity="0.22"/></svg>'
        ),
    },
    {
        "value": "style2", "label": "Full-background",
        "desc": "Edge-to-edge image with the copy anchored bottom-left.",
        "preview": (
            '<svg viewBox="0 0 160 90" preserveAspectRatio="xMidYMid slice">'
            '<rect width="160" height="90" fill="currentColor" opacity="0.55"/>'
            '<rect x="14" y="48" width="62" height="8" rx="2" fill="#fff" opacity="0.95"/>'
            '<rect x="14" y="60" width="44" height="4" rx="2" fill="#fff" opacity="0.7"/>'
            '<rect x="14" y="70" width="28" height="9" rx="2" fill="#fff" opacity="0.9"/></svg>'
        ),
    },
    {
        "value": "style3", "label": "Split hero",
        "desc": "A coloured copy panel beside a tall product image.",
        "preview": (
            '<svg viewBox="0 0 160 90" preserveAspectRatio="xMidYMid slice">'
            '<rect width="80" height="90" fill="currentColor" opacity="0.1"/>'
            '<rect x="80" width="80" height="90" fill="currentColor" opacity="0.3"/>'
            '<rect x="14" y="32" width="46" height="7" rx="2" fill="currentColor" opacity="0.42"/>'
            '<rect x="14" y="45" width="34" height="4" rx="2" fill="currentColor" opacity="0.25"/>'
            '<rect x="14" y="57" width="24" height="9" rx="2" fill="currentColor" opacity="0.52"/></svg>'
        ),
    },
]
_CATEGORY_STYLES = [
    {
        "value": "style1", "label": "Card grid",
        "desc": "Rectangular cards in a row with the title beneath.",
        "preview": (
            '<svg viewBox="0 0 160 90">'
            '<rect x="60" y="14" width="40" height="5" rx="2" fill="currentColor" opacity="0.3"/>'
            '<rect x="16" y="34" width="28" height="34" rx="3" fill="currentColor" opacity="0.16"/>'
            '<rect x="50" y="34" width="28" height="34" rx="3" fill="currentColor" opacity="0.16"/>'
            '<rect x="84" y="34" width="28" height="34" rx="3" fill="currentColor" opacity="0.16"/>'
            '<rect x="118" y="34" width="26" height="34" rx="3" fill="currentColor" opacity="0.16"/></svg>'
        ),
    },
    {
        "value": "style2", "label": "Carousel",
        "desc": "A sliding row of larger cards with arrows.",
        "preview": (
            '<svg viewBox="0 0 160 90">'
            '<rect x="26" y="26" width="34" height="44" rx="3" fill="currentColor" opacity="0.28"/>'
            '<rect x="64" y="26" width="34" height="44" rx="3" fill="currentColor" opacity="0.28"/>'
            '<rect x="102" y="26" width="34" height="44" rx="3" fill="currentColor" opacity="0.28"/>'
            '<path d="M14 48 l7 -7 v14 z" fill="currentColor" opacity="0.4"/>'
            '<path d="M150 48 l-7 -7 v14 z" fill="currentColor" opacity="0.4"/></svg>'
        ),
    },
    {
        "value": "style3", "label": "Circular",
        "desc": "Round category badges with the label below each.",
        "preview": (
            '<svg viewBox="0 0 160 90">'
            '<circle cx="34" cy="42" r="16" fill="currentColor" opacity="0.18"/>'
            '<circle cx="80" cy="42" r="16" fill="currentColor" opacity="0.18"/>'
            '<circle cx="126" cy="42" r="16" fill="currentColor" opacity="0.18"/>'
            '<rect x="24" y="64" width="20" height="4" rx="2" fill="currentColor" opacity="0.3"/>'
            '<rect x="70" y="64" width="20" height="4" rx="2" fill="currentColor" opacity="0.3"/>'
            '<rect x="116" y="64" width="20" height="4" rx="2" fill="currentColor" opacity="0.3"/></svg>'
        ),
    },
]
_VIDEO_POSITIONS = [
    {"value": "top", "label": "Above products"},
    {"value": "bottom", "label": "Below products"},
]

# Brand colour palette  hidden for now. Kept here so it can be switched back on
# by splicing these into the Hero section's "fields" (the former Appearance tab
# now lives at the bottom of Hero). The storefront still reads these keys (owners'
# saved values, or the seeded defaults), so nothing renders differently hidden.
_HIDDEN_APPEARANCE_COLORS = [
    {"name": "primary_color", "label": "Primary colour", "type": "color", "help": "Buttons and key accents."},
    {"name": "secondary_color", "label": "Secondary colour", "type": "color"},
    {"name": "accent_color", "label": "Accent colour", "type": "color"},
    {"name": "text_color", "label": "Text colour", "type": "color"},
    {"name": "bg_color", "label": "Background colour", "type": "color"},
    {"name": "banner_title_color", "label": "Banner title colour", "type": "color"},
    {"name": "banner_description_color", "label": "Banner text colour", "type": "color"},
]


# Everything one page's SEO form edits: Search, Open Graph and Twitter groups,
# plus canonical + hide-from-search. Used by both SEO modes (the per-page tabs,
# and the Home-page form that edits the brand set). Every field is optional: a
# blank one uses the auto-generated value (shown as the placeholder), see
# storefront.seo_pages. Title + keywords share a row, and each share image sits
# beside its title + description (see the #ed-panel-seo rules in editor.css).
def _page_seo_fields():
    return [
        {"name": "_h_search", "label": "Search results", "type": "heading"},
        {"name": "meta_title", "label": "Meta title", "type": "text", "span": 6},
        {"name": "keywords", "label": "Keywords", "type": "text", "span": 6},
        {"name": "meta_description", "label": "Meta description", "type": "textarea", "span": 12},
        {"name": "_h_og", "label": "Open Graph (Facebook, WhatsApp, LinkedIn)", "type": "heading"},
        {"name": "og_title", "label": "OG title", "type": "text", "span": 8},
        {"name": "og_image", "label": "OG image", "type": "image", "span": 4, "demo": "og"},
        {"name": "og_description", "label": "OG description", "type": "textarea", "span": 8},
        {"name": "_h_tw", "label": "Twitter / X card", "type": "heading"},
        {"name": "twitter_title", "label": "Twitter title", "type": "text", "span": 8},
        {"name": "twitter_image", "label": "Twitter image", "type": "image", "span": 4, "demo": "og"},
        {"name": "twitter_description", "label": "Twitter description", "type": "textarea", "span": 8},
        {"name": "_h_adv", "label": "Advanced", "type": "heading"},
        {"name": "canonical_url", "label": "Canonical URL", "type": "url", "span": 8},
        {"name": "noindex", "label": "Hide this page from search engines", "type": "checkbox", "span": 4},
    ]

# A content page's own fields (shared by core pages, their subpages and the
# nested child pages  a 3-level tree, mirroring the reference's CorePage →
# CorePageSubpage → CorePageNestedSubpage).
def _page_fields(extra=None):
    # Spans lay the fields out on one aligned 12-column grid (Title | Slug, then
    # a full-width Subtitle, header image and body), so a page card reads like a
    # blog card. Nested sub/child page lists (passed in ``extra``) sit full width.
    fields = [
        {"name": "title", "label": "Title", "type": "text", "span": 6, "placeholder": "Page title"},
        {"name": "slug", "label": "Slug (URL)", "type": "text", "span": 6, "help": "Lowercase words joined by hyphens, e.g. size-guide. Leave blank to generate from the title."},
        {"name": "subtitle", "label": "Subtitle", "type": "text", "span": 12, "placeholder": "Optional line shown under the title."},
        {"name": "image", "label": "Header image", "type": "image", "span": 12, "ratio": "landscape", "demo": "page"},
        {"name": "content", "label": "Content", "type": "richtext", "span": 12},
    ]
    if extra:
        fields = fields + extra
    return fields

# One custom policy slot on the Policies tab: its page title, then its body.
def _custom_policy_fields(n):
    return [
        {"name": "title", "label": "Policy title", "type": "text", "span": 12,
         "placeholder": ("e.g. Warranty policy", "e.g. Cancellation policy", "e.g. Cookie policy")[(n - 1) % 3],
         "help": "Shown as the page heading and as its link in your store footer. The page address is made from this title."},
        {"name": "content", "label": "Policy content", "type": "richtext", "span": 12},
    ]

# Unit choices for the logo max-width controls, mirroring the reference tool's
# edit-website page. Shared by the three logo-size fields below.
_LOGO_SIZE_UNITS = [
    {"value": "px", "label": "px"},
    {"value": "%", "label": "%"},
    {"value": "em", "label": "em"},
    {"value": "rem", "label": "rem"},
    {"value": "vw", "label": "vw"},
]

SECTIONS = [
    {
        "key": "brand",
        "label": "Brand",
        "icon": "brand",
        "hint": "Your store name, logo and favicon.",
        "fields": [
            # -- Announcement bar (first thing on the tab) -------------------
            # A Static bar shows one statement; a Rotating bar is a marquee that
            # loops up to ANNOUNCEMENT_MAX statements left to right with a gap
            # between them. Any statement can turn one word into a link that
            # opens in a new tab. Rendered by storefront/announcement.py.
            {"name": "_h_announcement", "label": "Announcement bar", "type": "heading",
             "help": "The strip across the very top of every storefront page."},
            {"name": "announcement_mode", "label": "Bar style", "type": "segtoggle", "span": 12,
             "options": [
                 {"value": "static", "label": "Static",
                  "icon": '<rect x="3" y="9" width="18" height="6" rx="1"/><path d="M8 12h8"/>'},
                 {"value": "dynamic", "label": "Rotating (marquee)",
                  "icon": '<rect x="3" y="9" width="18" height="6" rx="1"/><path d="M6 12h3M12 12h3"/><path d="m17 10.5 2 1.5-2 1.5"/>'},
             ]},
            {
                "name": "announcement_items",
                "label": "Statements",
                "type": "list",
                "editor": "announcement",
                "item_label": "Statement",
                "max_items": 4,
                "item_fields": [
                    {"name": "text", "label": "Statement", "type": "text", "span": 12,
                     "placeholder": "FREE SHIPPING ON ORDERS OVER ₹999", "maxlength": 160},
                    {"name": "link_word", "label": "Linked word (optional)", "type": "text", "span": 5,
                     "placeholder": "e.g. SHIPPING", "maxlength": 60,
                     "help": "A word or phrase from the statement that becomes a link."},
                    {"name": "link_url", "label": "Link opens", "type": "text", "span": 7,
                     "placeholder": "https://… or /products/", "maxlength": 2000,
                     "help": "A full web address or a page of your store. Opens in a new tab."},
                ],
            },
            # Store name + parent organization share one row (desktop), then the
            # four logos across the full width with the logo's alt text directly
            # beneath the logo itself ("below": the editor nests it inside the
            # logo's column), then per-logo size controls.
            {"name": "_h_identity", "label": "Store identity", "type": "heading"},
            {"name": "site_name", "label": "Store name", "type": "text", "span": 6, "placeholder": "Brand name", "help": "Shown in the header, title bar and footer."},
            # Optional owning company. Emitted as the store's parentOrganization
            # in the storefront structured data (see storefront.structured_data).
            {"name": "parent_organization", "label": "Parent organization (optional)", "type": "text", "span": 6,
             "placeholder": "e.g. Organization Name",
             "help": "If a company or organization owns this brand, enter its name for better SEO. "
                     "Leave blank if the brand is not owned by another organization."},
            # Brand imagery keeps its original format (no WebP transcode): logos and
            # especially the favicon must stay a real PNG/ICO. ``no_webp`` flows
            # through the editor's image picker to the upload so nothing is converted.
            {"name": "desktop_logo", "label": "Logo", "type": "image", "span": 3, "demo": "logo", "no_webp": True},
            {"name": "desktop_logo_alt", "label": "Logo alt text", "type": "text", "below": "desktop_logo",
             "placeholder": "e.g. Acme logo", "help": "Describes the logo for screen readers and SEO."},
            {"name": "mobile_logo", "label": "Mobile logo", "type": "image", "span": 3, "demo": "logo", "no_webp": True},
            {"name": "favicon", "label": "Favicon", "type": "image", "span": 3, "demo": "favicon", "no_webp": True},
            {"name": "footer_logomark", "label": "Footer logo", "type": "image", "span": 3, "demo": "logo", "no_webp": True},
            # Size controls  a max-width number paired with a unit, one per logo.
            # These write the exact content keys the storefront navigation and
            # footer already read (desktop_logo_max_width[_unit], etc.), so a
            # change here resizes the rendered logo immediately.
            {"name": "_h_logo_sizes", "label": "Logo sizes", "type": "heading",
             "help": "Set how wide each logo renders. Leave a width blank to use the theme's default size."},
            # All three width + unit pairs share one row on desktop (2 + 2 each).
            {"name": "desktop_logo_max_width", "label": "Desktop logo width", "type": "number", "span": 2, "help": "Max width of the header logo on desktop."},
            {"name": "desktop_logo_max_width_unit", "label": "Unit", "type": "select", "span": 2, "options": _LOGO_SIZE_UNITS},
            {"name": "mobile_logo_max_width", "label": "Mobile logo width", "type": "number", "span": 2, "help": "Max width of the logo on phones."},
            {"name": "mobile_logo_max_width_unit", "label": "Unit", "type": "select", "span": 2, "options": _LOGO_SIZE_UNITS},
            {"name": "footer_logomark_max_width", "label": "Footer logo width", "type": "number", "span": 2, "help": "Max width of the footer logo."},
            {"name": "footer_logomark_max_width_unit", "label": "Unit", "type": "select", "span": 2, "options": _LOGO_SIZE_UNITS},
        ],
    },
    {
        # Storefront newsletter popup (storefront/newsletter_popup.py renders it).
        # Laid out compactly: the switch, the offer + timing row, then the desktop
        # and mobile banners side by side beside the popup's copy (each banner
        # spans the copy's three rows, see the #ed-panel-newsletter rules in
        # editor.html).
        "key": "newsletter",
        "label": "Newsletter",
        "icon": "newsletter",
        "hint": "A popup that invites visitors to join your newsletter.",
        "fields": [
            {"name": "newsletter_popup_enabled", "label": "Show the newsletter popup to visitors", "type": "checkbox", "span": 12,
             "help": "When on, the popup appears a moment after a visitor opens your store. Signups appear on your Subscribers page tagged “Subscribed from newsletter”."},
            {"name": "newsletter_popup_coupon", "label": "Offer for new subscribers (optional)", "type": "select", "span": 8,
             "options_from": "coupons",
             "help": "Pick one of your discount codes. The heading fills in automatically and the code is revealed after they sign up."},
            {"name": "newsletter_popup_delay", "label": "Show after", "type": "select", "span": 4,
             "options": [
                 {"value": "2", "label": "2 seconds"},
                 {"value": "1", "label": "1 second"},
                 {"value": "3", "label": "3 seconds"},
                 {"value": "5", "label": "5 seconds"},
             ]},
            # Two banners: a portrait one beside the form on desktop and a wide
            # strip above it on phones. The recommended sizes are guidance only
            # (nothing is validated). Desktop shows only its own banner; phones
            # reuse it unless the phone banner was removed.
            {"name": "newsletter_popup_image", "label": "Desktop banner", "type": "image", "span": 3, "ratio": "portrait",
             "help": "Shown beside the form on desktop only. Upload 800 × 1000 px (4:5 portrait) for a perfect fit."},
            {"name": "newsletter_popup_mobile_image", "label": "Mobile banner", "type": "image", "span": 3, "ratio": "portrait",
             "help": "Shown above the form on phones (blank uses the desktop banner). Upload 1200 × 675 px (16:9 landscape) for a perfect fit."},
            {"name": "newsletter_popup_heading", "label": "Heading", "type": "text", "span": 6,
             "placeholder": "Join our newsletter"},
            {"name": "newsletter_popup_text", "label": "Description", "type": "textarea", "span": 6,
             "placeholder": "Be the first to hear about new arrivals, offers and stories."},
            {"name": "newsletter_popup_button", "label": "Button text", "type": "text", "span": 3,
             "placeholder": "Subscribe"},
            {"name": "newsletter_popup_success", "label": "Thank-you message", "type": "text", "span": 3,
             "placeholder": "You’re subscribed. Thank you!"},
        ],
    },
    {
        # One tab for the whole homepage: hero, the category/best-seller/video
        # strip, about, why-choose-us, testimonials and FAQ  each group split by
        # a "heading" divider, in the order the sections appear on the page.
        "key": "home",
        "label": "Home sections",
        "icon": "home",
        "hint": " ",
        "fields": [
            # -- Hero banners (was its own tab) ------------------------------
            {"name": "_h_hero", "label": "Hero banners", "type": "heading",
             "help": "Pick a hero layout, then fill the banners. Fields marked for a style only appear when that style is selected  exactly what renders on your homepage. Switching style resets each banner's colours to automatic ones that read well on the new style's background."},
            # The style picker lives here so its dependent fields (below and inside
            # each banner) can show/hide against it within one tab.
            {"name": "hero_banner_style", "label": "Hero banner style", "type": "select_cards", "options": _HERO_STYLES},
            {"name": "hero_style3_left_bg", "label": "Split hero panel colour", "type": "color", "span": 4, "style_only": "style3"},
            {"name": "hero_style3_left_bg_text", "label": "Split hero panel text", "type": "text", "span": 8, "style_only": "style3"},
            {
                "name": "hero_banners",
                "label": "Banners",
                "type": "list",
                "editor": "banner",
                "item_label": "Banner",
                # Field order + spans are laid out so the right-hand slide-content
                # pane reads as clean, parallel rows (like the mockup). The rule:
                # every 12-column row either pairs two fields of the SAME height
                # AND the same visibility condition (so they show/hide together
                # and never leave a half-empty row), or is a single full-width
                # field. In particular the tall media thumbnails only ever sit
                # beside other media thumbnails  never beside a short input  so
                # nothing floats at the top of an over-tall row.
                #
                # Row plan (image mode / video mode):
                #   media_type ............................. full width (toggle)
                #   image  | mobile_image  (image mode) ... two 16:9/9:16 thumbs
                #   video  | mobile_video  (video mode) ... two thumbs
                #   alt_text | overlay_color .............. input + colour picker
                #   overlay_opacity (slider) ............. full-width 0–10 range
                #   title ................................. full width
                #   accent_text (style 3 only) ............ full width
                #   description ........................... full width
                #   title_colour | description_colour ..... two colour pickers
                #   mobile subheading bg (style 2 only) ... full width
                #   button_text | button_link_type ........ input + select
                #   button link target (conditional) ...... full width
                #   button_text_colour | button_bg ........ two colour pickers
                #
                # Each field also carries a "group" (imgtext / colors / button).
                # On desktop the groups dissolve (display:contents) and the rows
                # above render exactly as one flat grid; on narrow screens the
                # editor collapses each group into a single-open accordion
                # ("Image & Text", "Colors", "Button")  see enhanceCard.
                "item_fields": [
                    {"name": "media_type", "label": "Media type", "type": "select", "span": 12,
                     "options": [
                         {"value": "image", "label": "Image"},
                         {"value": "video", "label": "Video"},
                     ],
                     "help": "Choose whether this banner shows an image or a video. A video plays automatically, on a loop, with no sound."},
                    # Media row (image mode): the two image thumbnails side by side,
                    # equal width so they line up. The portrait mobile preview
                    # stretches to the landscape height via .ed-field--fill.
                    {"name": "image", "label": "Banner image (16:9)", "type": "image", "span": 6, "group": "imgtext", "ratio": "landscape", "demo": "hero", "show_if": {"field": "media_type", "in": ["image", ""]}},
                    {"name": "mobile_image", "label": "Mobile image (9:16)", "type": "image", "span": 6, "group": "imgtext", "ratio": "portrait", "demo": "hero_mobile", "show_if": {"field": "media_type", "in": ["image", ""]}},
                    # Media row (video mode): the two video thumbnails side by side.
                    {"name": "video", "label": "Banner video (16:9)", "type": "video", "span": 6, "group": "imgtext", "show_if": {"field": "media_type", "in": ["video"]}, "help": "MP4 or WEBM. Autoplays muted and loops."},
                    {"name": "mobile_video", "label": "Mobile video (9:16)", "type": "video", "span": 6, "group": "imgtext", "show_if": {"field": "media_type", "in": ["video"]}},
                    # Two single-line inputs on one row (shown in both modes),
                    # equal width so they read as parallel; the two-line label
                    # reserve (see .bn .ed-card__body .ed-field__label) keeps both
                    # inputs on the same line even when one label wraps.
                    {"name": "alt_text", "label": "Image alt text", "type": "text", "span": 6, "group": "imgtext"},
                    {"name": "overlay_color", "label": "Overlay colour", "type": "color", "span": 6, "group": "imgtext", "help": "Tints the banner image/video behind the text. Default black."},
                    # A 0–10 slider sits over the whole banner media (image or
                    # video), never the text  the storefront reads it via the
                    # ``banner_overlay_opacity`` filter (0 = none, 10 = solid).
                    {"name": "overlay_opacity", "label": "Overlay darkness (0–10)", "type": "range", "span": 12, "group": "imgtext", "min": 0, "max": 10, "step": 1, "default": 5, "help": "Darkens the image/video behind the text for readability. 0 is off and each step up is a little darker; even 10 keeps your banner visible behind the tint."},
                    # Title then description, each full width (no dangling partner).
                    {"name": "title", "label": "Banner title", "type": "text", "span": 12, "group": "imgtext"},
                    {"name": "accent_text", "label": "Accent text", "type": "text", "span": 12, "group": "imgtext", "help": "Small text above the heading in the split hero.", "style_only": "style3"},
                    {"name": "description", "label": "Description", "type": "textarea", "span": 12, "group": "imgtext"},
                    # Colour row  two pickers of equal width.
                    {"name": "title_color", "label": "Title colour", "type": "color", "span": 6, "group": "colors", "placeholder": "Auto", "help": "Blank = automatic, readable on this style's background."},
                    {"name": "description_color", "label": "Description colour", "type": "color", "span": 6, "group": "colors", "placeholder": "Auto", "help": "Blank = automatic, readable on this style's background."},
                    {"name": "description_mobile_bg_color", "label": "Mobile subheading background", "type": "color", "span": 12, "group": "colors", "style_only": "style2"},
                    # Button text + target on one row, then the button colours.
                    {"name": "button_text", "label": "Button text", "type": "text", "span": 6, "group": "button"},
                    {
                        "name": "button_link_type", "label": "Button links to", "type": "select", "span": 6, "group": "button",
                        "options": [
                            {"value": "home", "label": "Home"},
                            {"value": "about", "label": "About us"},
                            {"value": "shop", "label": "Shop"},
                            {"value": "categories", "label": "Categories page"},
                            {"value": "category", "label": "A particular category"},
                            {"value": "product", "label": "A particular product"},
                            {"value": "custom", "label": "Custom URL"},
                        ],
                    },
                    # Conditional link target  full width so it never leaves a gap.
                    {"name": "button_link_category_id", "label": "Category", "type": "select", "span": 12, "group": "button", "options_from": "categories", "show_if": {"field": "button_link_type", "in": ["category"]}},
                    {"name": "button_link_product_id", "label": "Product", "type": "select", "span": 12, "group": "button", "options_from": "products", "show_if": {"field": "button_link_type", "in": ["product"]}},
                    {"name": "button_custom_url", "label": "Custom URL", "type": "url", "span": 12, "group": "button", "show_if": {"field": "button_link_type", "in": ["custom"]}},
                    # Button colour row  two pickers of equal width.
                    {"name": "button_text_color", "label": "Button text colour", "type": "color", "span": 6, "group": "button", "placeholder": "Auto"},
                    {"name": "button_bg_color", "label": "Button background", "type": "color", "span": 6, "group": "button", "placeholder": "Auto"},
                    # Split hero only: the "Image button", a frosted-glass button with
                    # rounded corners centred on the banner image (right half). Text
                    # only; blank text hides it. Keys keep their original circle_*
                    # names so saved banners carry over unchanged.
                    # Its link resolves to ``circle_url`` (see _resolve_banner_urls);
                    # "Same as banner button" reuses ``button_url``.
                    {"name": "circle_text", "label": "Image button text", "type": "text", "span": 6, "group": "button", "style_only": "style3",
                     "placeholder": "e.g. Shop now", "maxlength": 40,
                     "help": "A glass button shown in the centre of the banner image. Leave blank to hide it."},
                    {"name": "circle_text_color", "label": "Image button text colour", "type": "color", "span": 6, "group": "button", "style_only": "style3",
                     "placeholder": "White", "help": "Blank = white."},
                    {
                        "name": "circle_link_type", "label": "Image button links to", "type": "select", "span": 12, "group": "button", "style_only": "style3",
                        "options": [
                            {"value": "button", "label": "Same as the banner button"},
                            {"value": "home", "label": "Home"},
                            {"value": "about", "label": "About us"},
                            {"value": "shop", "label": "Shop"},
                            {"value": "categories", "label": "Categories page"},
                            {"value": "category", "label": "A particular category"},
                            {"value": "product", "label": "A particular product"},
                            {"value": "custom", "label": "Custom URL"},
                        ],
                    },
                    {"name": "circle_link_category_id", "label": "Image button category", "type": "select", "span": 12, "group": "button", "style_only": "style3", "options_from": "categories", "show_if": {"field": "circle_link_type", "in": ["category"]}},
                    {"name": "circle_link_product_id", "label": "Image button product", "type": "select", "span": 12, "group": "button", "style_only": "style3", "options_from": "products", "show_if": {"field": "circle_link_type", "in": ["product"]}},
                    {"name": "circle_custom_url", "label": "Image button URL", "type": "url", "span": 12, "group": "button", "style_only": "style3", "show_if": {"field": "circle_link_type", "in": ["custom"]}},
                ],
            },
            # Appearance options (formerly their own tab) live here, beneath the
            # banners  the brand colour palette stays hidden for now (kept in
            # ``_HIDDEN_APPEARANCE_COLORS``; splice it back in to re-enable).
            {"name": "category_section_style", "label": "Collections section style", "type": "select_cards", "options": _CATEGORY_STYLES},
            {"name": "enable_product_hover_swap", "label": "Swap product image on hover", "type": "checkbox"},

            # (The category, best-seller & video headings are merged into the
            # About split block below; the Reels strip now lives at the very
            # bottom of this tab  see "_h_reels".)

            # -- About + featured content (compact two-panel split editor) ----
            # The About heading carries layout:"split", so every field below it up
            # to the next heading renders as one compact two-panel block 
            # "01 Section content" (the text fields) beside "02 Media" (the image
            # dropzones), with a tip bar underneath (see editor.html buildSplit).
            # The old standalone "Categories, best-sellers & video" fields are
            # merged in here rather than living in their own tab. Every content
            # key is unchanged, so the storefront reads them exactly as before.
            {"name": "_h_about", "label": "About", "type": "heading", "layout": "split",
             "help": "Your About section, plus the category, best-seller and video headings.",
             "panels": [
                 {"id": "content", "num": "01", "title": "Section content",
                  "note": "Fill in the details to update this section."},
                 {"id": "media", "num": "02", "title": "Media",
                  "note": "Images shown alongside these sections."},
             ],
             "tip": "For best results, use high-quality images with a 16:9 ratio. "
                    "Supported formats: PNG, JPG, WEBP. Max size: 5MB."},
            # Left panel  About
            {"name": "about_section_title", "label": "About heading", "type": "text", "span": 6, "panel": "content", "placeholder": "Our story"},
            {"name": "about_section_subtitle", "label": "About subheading", "type": "text", "span": 6, "panel": "content"},
            {"name": "about_section_content", "label": "About body", "type": "textarea", "span": 12, "panel": "content", "placeholder": "Tell customers who you are…"},
            {"name": "about_button_text", "label": "Button label", "type": "text", "span": 6, "panel": "content"},
            {"name": "about_button_url", "label": "Button link", "type": "url", "span": 6, "panel": "content"},
            # Left panel  Categories & best-sellers
            {"name": "category_section_heading", "label": "Category section heading", "type": "text", "span": 6, "panel": "content", "placeholder": "Shop by category", "help": "Main heading for the category section."},
            {"name": "category_section_subheading", "label": "Category section subtext", "type": "text", "span": 6, "panel": "content", "placeholder": "Browse our most popular collections.", "help": "Supporting text below the heading."},
            {"name": "best_sellers_subtitle", "label": "Best-sellers subtext", "type": "text", "span": 12, "panel": "content", "placeholder": "Our most-loved products, chosen by customers like you.", "help": "Best-sellers are the products you flag in your catalogue."},
            # Left panel  Video
            {"name": "video_title", "label": "Video section title", "type": "text", "span": 6, "panel": "content", "placeholder": "See us in action", "help": "Title for the video section."},
            {"name": "video_url", "label": "Video URL", "type": "url", "span": 6, "panel": "content", "placeholder": "https://www.youtube.com/watch?v=...", "help": "A YouTube/Vimeo link or a hosted video. Leave blank to hide the section."},
            {"name": "video_section_position", "label": "Video position", "type": "select", "span": 6, "panel": "content", "options": _VIDEO_POSITIONS, "help": "Choose where the video appears on the page."},
            {"name": "video_description", "label": "Video description", "type": "textarea", "span": 12, "panel": "content", "maxlength": 300, "placeholder": "Add a short description for the video section...", "help": "Optional description to give more context about the video."},
            # Right panel  media. The About section shows EITHER an image OR a
            # video: the toggle chooses which, and only that media's inputs show
            # (image → the image; video → the video link + an optional thumbnail
            # used as the poster). Both the toggle and its dependent fields live
            # in this one panel so the show_if wiring resolves within it.
            {
                "name": "about_media_type", "label": "About media", "type": "segtoggle", "span": 12, "panel": "media",
                "options": [
                    {"value": "image", "label": "Image", "icon": '<rect x="3" y="4" width="18" height="14" rx="1"/><circle cx="8.5" cy="9.5" r="1.5"/><path d="m4 17 5-5 4 4 3-3 4 4"/>'},
                    {"value": "video", "label": "Video", "icon": '<rect x="3" y="5" width="14" height="14" rx="1"/><path d="m17 9 4-2v10l-4-2"/>'},
                ],
                "help": "Choose whether the About section shows an image or a video.",
            },
            {"name": "about_image", "label": "About image", "type": "image", "span": 12, "panel": "media", "variant": "dropzone", "ratio": "landscape", "demo": "about",
             "show_if": {"field": "about_media_type", "in": ["image", ""]}},
            {"name": "about_video_url", "label": "About video URL", "type": "url", "span": 12, "panel": "media",
             "show_if": {"field": "about_media_type", "in": ["video"]}, "help": "A YouTube/Vimeo link or a hosted video file."},
            {"name": "about_video_thumbnail", "label": "About video thumbnail (optional)", "type": "image", "span": 12, "panel": "media", "variant": "dropzone", "ratio": "landscape", "demo": "about",
             "show_if": {"field": "about_media_type", "in": ["video"]}, "help": "Optional cover shown before the video plays. Leave blank to use a default."},

            # -- Why choose us (compact tabbed editor) ----------------------
            # editor:"tabs" renders each point as a numbered tab; only the active
            # point's fields show. The icon can be a Font Awesome class OR an
            # uploaded image (icon_image)  the storefront shows the image when
            # present, for both B2B and D2C.
            {"name": "_h_why", "label": "Why choose us", "type": "heading", "help": "Reasons to buy from you."},
            {"name": "why_choose_us_subtitle", "label": "Subtext", "type": "text", "span": 12},
            {
                "name": "why_choose_us",
                "label": "Points",
                "type": "list",
                "editor": "tabs",
                "item_label": "Point",
                "item_fields": [
                    {"name": "icon", "label": "Icon (Font Awesome class)", "type": "text", "span": 6, "placeholder": "fa-truck", "help": "e.g. fa-truck. Leave blank if you upload an image instead."},
                    {"name": "icon_image", "label": "Or upload icon", "type": "image", "span": 6, "help": "Shown instead of the Font Awesome icon (B2B and D2C)."},
                    {"name": "title", "label": "Title", "type": "text", "span": 12},
                    {"name": "description", "label": "Description", "type": "textarea", "span": 12},
                ],
            },

            # -- Testimonials (compact tabbed editor) -----------------------
            {"name": "_h_testimonials", "label": "Testimonials", "type": "heading", "help": "What your customers say."},
            {"name": "testimonials_title", "label": "Heading", "type": "text", "span": 6},
            {"name": "testimonials_subtitle", "label": "Subtext", "type": "text", "span": 6},
            {
                "name": "testimonials",
                "label": "Quotes",
                "type": "list",
                "editor": "tabs",
                "item_label": "Testimonial",
                "item_fields": [
                    {"name": "name", "label": "Name", "type": "text", "span": 6},
                    {"name": "role", "label": "Role / location", "type": "text", "span": 6},
                    {"name": "content", "label": "Quote", "type": "textarea", "span": 12},
                    {"name": "rating", "label": "Rating (1–5)", "type": "number", "span": 6},
                    {"name": "image", "label": "Photo", "type": "image", "span": 6, "demo": "avatar"},
                ],
            },

            # -- FAQ (compact tabbed editor) --------------------------------
            {"name": "_h_faq", "label": "FAQ", "type": "heading", "help": "Answer common questions."},
            {"name": "faq_title", "label": "Heading", "type": "text", "span": 6},
            {"name": "faq_subtitle", "label": "Subtext", "type": "text", "span": 6},
            {
                "name": "faqs",
                "label": "Questions",
                "type": "list",
                "editor": "tabs",
                "item_label": "Question",
                "item_fields": [
                    {"name": "question", "label": "Question", "type": "text", "span": 12},
                    {"name": "answer", "label": "Answer", "type": "textarea", "span": 12},
                ],
            },

            # -- Reels / short-video strip ("Join the tribe")  at the bottom -
            # editor:"reels" is a responsive grid (6 per row on desktop, 4 on
            # tablet, 2 on mobile) of autoplaying short-video cells with an
            # "Add reel" tile. Saved into video_section.videos (see save_section);
            # left empty, the storefront shows demo reels.
            {"name": "_h_reels", "label": "Reels (short videos)", "type": "heading",
             "help": "Short vertical videos for the 'Join the tribe' strip. Six sit in a row on desktop (four on tablet, two on mobile). Leave empty and demo videos show until you add your own."},
            {
                "name": "reels",
                "label": "Reel videos",
                "type": "list",
                "editor": "reels",
                "item_label": "Reel",
                "item_fields": [
                    {"name": "video", "label": "Video (9:16)", "type": "video", "ratio": "portrait", "help": "Autoplays muted and loops. MP4/WEBM."},
                ],
            },
        ],
    },
    {
        "key": "blogs",
        "label": "Blogs",
        "icon": "blogs",
        "hint": "Articles shown on your storefront's blog. Add as many as you like.",
        "fields": [
            {
                "name": "blog_posts",
                "label": "Posts",
                "type": "list",
                # Bespoke accordion editor: each post is a collapsible card, one
                # open at a time, with the two-column field layout below (see
                # buildBlogsEditor in editor.html).
                "editor": "blogs",
                "item_label": "Post",
                # Spans lay the fields out on one aligned 12-column grid: two
                # per row (Title/Slug), three per row (Category/Author/Date),
                # Cover Image beside Excerpt, and the Body full-width. See
                # buildBlogsEditor.
                "item_fields": [
                    {"name": "title", "label": "Title", "type": "text", "span": 6, "placeholder": "Post title"},
                    {"name": "slug", "label": "Slug (URL)", "type": "text", "span": 6, "help": "Lowercase words joined by hyphens, e.g. our-story."},
                    {"name": "category", "label": "Category Label", "type": "text", "span": 4, "placeholder": "e.g. Wellness"},
                    {"name": "author", "label": "Author", "type": "text", "span": 4, "placeholder": "Author name"},
                    {"name": "date", "label": "Date (Y-m-d)", "type": "text", "span": 4, "placeholder": "2026-08-01"},
                    {
                        "name": "status", "label": "Status", "type": "select",
                        "options": [
                            {"value": "published", "label": "Published"},
                            {"value": "draft", "label": "Draft"},
                        ],
                    },
                    {"name": "image", "label": "Cover Image", "type": "image", "span": 6, "demo": "blog"},
                    {"name": "excerpt", "label": "Excerpt", "type": "textarea", "span": 6, "maxlength": 300, "placeholder": "A short summary shown on the blog listing."},
                    {"name": "description", "label": "Body", "type": "richtext", "span": 12},
                ],
            },
        ],
    },
    {
        "key": "about_page",
        "label": "About page",
        "icon": "about",
        # Fields flow top-to-bottom in the SAME order the About page renders:
        # editorial intro → feature block 1 (+ metrics) → brand section. Each "heading" is a full-width
        # divider; the fields under it are placed with spans so text and its image
        # sit side by side, exactly like the Home-sections and Blogs editors.
        # Fields are paired so the two in any row have matching heights (two
        # textareas, two single-line inputs, or a description beside an image
        # that the description stretches to meet); anything without a height
        # match spans the full width on its own row.
        "fields": [
            # 1  Editorial intro (the two paragraphs + the wide banner below them).
            {"name": "_h_ab_editorial", "label": "Editorial intro", "type": "heading",
             "help": "The two opening paragraphs and the wide banner image beneath them."},
            {"name": "editorial_intro_1", "label": "Paragraph 1", "type": "textarea", "span": 6},
            {"name": "editorial_intro_2", "label": "Paragraph 2", "type": "textarea", "span": 6},
            {"name": "editorial_banner_image", "label": "Banner image", "type": "image", "span": 12, "demo": "editorial",
             "help": "Shown full width beneath the intro. Upload 1920 × 800 px (12:5) for a perfect fit; phones show its centre at 16:9."},

            # 2  Feature block 1. Desktop: title, description and the six metric
            # fields (three per row) fill the left half; the image fills the
            # right half, spanning all four of those rows (editor.css). The image
            # comes second so the grid can place it beside the title. The metrics
            # are ordered values first, then labels, so the three values fill one
            # row and each label sits directly beneath its own value.
            {"name": "_h_ab_block1", "label": "Feature block 1", "type": "heading",
             "help": "A heading and description beside an image, with three headline metrics beneath."},
            {"name": "cowork_block1_title", "label": "Title", "type": "text", "span": 6},
            {"name": "cowork_block1_image", "label": "Image", "type": "image", "span": 6, "demo": "cowork1"},
            {"name": "cowork_block1_desc", "label": "Description", "type": "textarea", "span": 6},
            {"name": "cowork_metric1_value", "label": "Metric 1 · value", "type": "text", "span": 2, "help": "e.g. 200+."},
            {"name": "cowork_metric2_value", "label": "Metric 2 · value", "type": "text", "span": 2},
            {"name": "cowork_metric3_value", "label": "Metric 3 · value", "type": "text", "span": 2},
            {"name": "cowork_metric1_label", "label": "Metric 1 · label", "type": "text", "span": 2, "help": "e.g. Offices."},
            {"name": "cowork_metric2_label", "label": "Metric 2 · label", "type": "text", "span": 2},
            {"name": "cowork_metric3_label", "label": "Metric 3 · label", "type": "text", "span": 2},

            # 3  Brand section. Desktop: the three text inputs stacked in the left
            # half, the two images as squares side by side in the right half. "below" nests the title + description under the eyebrow, and
            # the secondary image under the main one, so each side is one block
            # (editor.css).
            {"name": "_h_ab_brand", "label": "Brand section", "type": "heading",
             "help": "Your brand story, beside a two-image collage."},
            {"name": "aus_brand_section_label", "label": "Eyebrow label", "type": "text", "span": 6, "help": "Small label above the story, e.g. “Our story”."},
            {"name": "aus_brand_section_title", "label": "Story title", "type": "text", "below": "aus_brand_section_label"},
            {"name": "aus_brand_section_description", "label": "Story description", "type": "textarea", "below": "aus_brand_section_label"},
            {"name": "aus_brand_main_image", "label": "Main image", "type": "image", "span": 6, "demo": "about"},
            {"name": "aus_brand_secondary_image", "label": "Secondary image", "type": "image", "demo": "cowork2", "below": "aus_brand_main_image"},
        ],
    },
    {
        "key": "footer",
        "label": "Footer & social",
        "icon": "footer",
        "hint": "Footer text and social links.",
        # Laid out like the Home / About tabs: a heading divider, then the social
        # links paired two per row inside their bordered group.
        "fields": [
            {"name": "_h_footer_text", "label": "Footer", "type": "heading",
             "help": "The short blurb shown in your storefront footer."},
            {"name": "footer_description", "label": "Footer text", "type": "textarea", "span": 12,
             "placeholder": "A line or two about your brand, shown in the footer."},
            {"name": "_h_footer_social", "label": "Social links", "type": "heading",
             "help": "Paste the full profile URL for each network. Leave any blank to hide its icon."},
            {
                "name": "social_links",
                "label": "Social profiles",
                "type": "object",
                # Two per row (span 6); the last sits full width so the grid ends
                # clean rather than leaving a half-empty cell.
                "subfields": [
                    {"name": "facebook", "label": "Facebook", "type": "url", "span": 6, "placeholder": "https://facebook.com/yourpage"},
                    {"name": "instagram", "label": "Instagram", "type": "url", "span": 6, "placeholder": "https://instagram.com/yourhandle"},
                    {"name": "twitter", "label": "Twitter / X", "type": "url", "span": 6, "placeholder": "https://x.com/yourhandle"},
                    {"name": "youtube", "label": "YouTube", "type": "url", "span": 6, "placeholder": "https://youtube.com/@yourchannel"},
                    {"name": "linkedin", "label": "LinkedIn", "type": "url", "span": 6, "placeholder": "https://linkedin.com/company/…"},
                    {"name": "pinterest", "label": "Pinterest", "type": "url", "span": 6, "placeholder": "https://pinterest.com/yourhandle"},
                    {"name": "google_my_business", "label": "Google Business", "type": "url", "span": 12, "placeholder": "Your Google Business profile link"},
                ],
            },
        ],
    },
    {
        "key": "contact",
        "label": "Contact",
        "icon": "contact",
        "hint": "Everything shown on your Contact page  details, the person customers hear from, office hours, a map, and contact FAQs.",
        "fields": [
            # The heading + subtext above the enquiry form at the top of the page.
            {"name": "_h_contact_form", "label": "Contact form", "type": "heading",
             "help": "The heading and short line shown above the enquiry form on your Contact page."},
            {"name": "contact_form_title", "label": "Heading", "type": "text", "span": 12,
             "placeholder": "Let's talk about your project"},
            {"name": "contact_form_subtitle", "label": "Subtext", "type": "textarea", "span": 12,
             "placeholder": "We help companies and individuals build out their brand guidelines.",
             "help": "Leave either blank to keep the default text."},
            # One structured card. Its subfields carry spans + sub-headings so the
            # ~18 details read as four logical groups (channels, address, the
            # contact person, office hours) rather than a tall stack of inputs 
            # exactly the layout the Home / About tabs use.
            {
                "name": "contact_info",
                "label": "Contact details",
                "type": "object",
                "subfields": [
                    # Channels + address in the left half: Email, then Phone and
                    # WhatsApp side by side, then the Address. The Google Maps
                    # link and its live preview fill the right half, spanning
                    # those three rows (editor.css), so the map comes second in
                    # field order for the grid to place it beside Email. See
                    # storefront.maps for how a pasted link is converted.
                    {"name": "contact_email", "label": "Email", "type": "text", "span": 6},
                    {"name": "map_location", "label": "Google Maps URL", "type": "map", "span": 6,
                     "placeholder": "Paste a Google Maps link or embed code",
                     "help": "In Google Maps, open your business, tap Share, then Copy link (or Embed a map → Copy HTML) and paste it here. Your address works too. The map appears on your Contact page."},
                    {"name": "mobile_number", "label": "Phone", "type": "text", "span": 3, "help": "Shown as a tap-to-call link."},
                    {"name": "whatsapp_number", "label": "WhatsApp", "type": "text", "span": 3, "help": "With country code, e.g. 919876543210."},
                    {"name": "address", "label": "Address", "type": "textarea", "span": 6},
                    # The rest of the address.
                    {"name": "_h_addr", "label": "City & region", "type": "heading"},
                    {"name": "city", "label": "City", "type": "text", "span": 4},
                    {"name": "state", "label": "State", "type": "text", "span": 4},
                    {"name": "postal_code", "label": "Postal code", "type": "text", "span": 4},
                    {"name": "country", "label": "Country", "type": "text", "span": 12},
                    # The person customers "hear from" on the contact page.
                    {"name": "_h_person", "label": "Contact person", "type": "heading",
                     "help": "Who customers hear from on the contact page."},
                    # Name, Designation and Message stacked in the left two-thirds;
                    # the Photo fills the right third, spanning those three rows
                    # (editor.css), so it comes second for the grid to place it
                    # beside Name.
                    {"name": "ceo_name", "label": "Name", "type": "text", "span": 8, "help": "e.g. your founder or head of support."},
                    {"name": "ceo_profile_picture", "label": "Photo", "type": "image", "span": 4, "demo": "avatar"},
                    {"name": "ceo_designation", "label": "Designation", "type": "text", "span": 8, "help": "e.g. Founder / CEO / Support lead."},
                    {"name": "ceo_message", "label": "Message to customers", "type": "textarea", "span": 8, "help": "A short welcome shown at the top of the contact page."},
                    # Office hours.
                    {"name": "_h_hours", "label": "Office hours", "type": "heading"},
                    {"name": "office_start_day", "label": "Open from (day)", "type": "text", "span": 3, "help": "e.g. Monday."},
                    {"name": "office_end_day", "label": "Open to (day)", "type": "text", "span": 3, "help": "e.g. Friday."},
                    {"name": "office_start_time", "label": "Open from (time)", "type": "text", "span": 3, "help": "e.g. 09:30."},
                    {"name": "office_end_time", "label": "Open to (time)", "type": "text", "span": 3, "help": "e.g. 18:30."},
                    {"name": "office_holidays", "label": "Weekly holidays", "type": "tags", "span": 12, "placeholder": "Type a day and press Enter…", "help": "Add each closed day as a tag, e.g. Sunday."},
                ],
            },
            # Grievance officer + National Consumer Helpline (shown on the Contact
            # page under the contact-person card, beside the form).
            {"name": "_h_grievance", "label": "Grievance officer & consumer care", "type": "heading",
             "help": "Who customers can escalate a complaint to, and when. Shown on your Contact page."},
            {
                "name": "grievance_officer",
                "label": "Grievance officer",
                "type": "object",
                "subfields": [
                    {"name": "name", "label": "Officer name", "type": "text", "span": 4, "placeholder": "Full name"},
                    {"name": "email", "label": "Officer email", "type": "text", "span": 4, "placeholder": "grievance@yourstore.com"},
                    {"name": "mobile", "label": "Officer mobile", "type": "text", "span": 4, "placeholder": "+91 …"},
                ],
            },
            {"name": "grievance_availability", "label": "Officer availability", "type": "schedule", "span": 12,
             "help": "Switch on the days the officer is available and set the hours for each day."},
            {"name": "national_consumer_helpline", "label": "National Consumer Helpline", "type": "text", "span": 12,
             "placeholder": "e.g. 1915 or 1800-11-4000",
             "help": "Shown on your Contact page with a tap-to-call link."},
            # Contact FAQ  a heading divider, the paired heading/subtext, then the
            # questions in the same tabbed editor the Home FAQ uses.
            {"name": "_h_contact_faq", "label": "Contact FAQ", "type": "heading",
             "help": "Common questions shown on the contact page."},
            {"name": "contact_faqs_title", "label": "Heading (prefix)", "type": "text", "span": 4, "help": "e.g. “Frequently asked ”."},
            {"name": "contact_faqs_title_suffix", "label": "Heading (suffix)", "type": "text", "span": 4, "help": "e.g. “questions”."},
            {"name": "contact_faqs_subtitle", "label": "Subtext", "type": "text", "span": 4},
            {
                "name": "contact_faqs",
                "label": "Questions",
                "type": "list",
                "editor": "tabs",
                "item_label": "Question",
                "item_fields": [
                    {"name": "question", "label": "Question", "type": "text", "span": 12},
                    {"name": "answer", "label": "Answer", "type": "textarea", "span": 12},
                ],
            },
        ],
    },
    {
        "key": "seo",
        "label": "SEO",
        "icon": "seo",
        "hint": "How your store appears in search results and social shares. Keep one set for the whole site, or tailor it per page; empty fields use each page's auto value.",
        "fields": [
            {
                "name": "seo_mode", "label": "SEO scope", "type": "segtoggle", "span": 12,
                "options": [
                    {"value": "same", "label": "Same for every page",
                     "icon": '<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M3 9h18"/>'},
                    {"value": "custom", "label": "Different for every page",
                     "icon": '<rect x="3" y="4" width="8" height="7" rx="1"/><rect x="13" y="4" width="8" height="7" rx="1"/><rect x="3" y="13" width="8" height="7" rx="1"/><rect x="13" y="13" width="8" height="7" rx="1"/>'},
                ],
                "help": "Same: every page gets its own title, description and share image generated from its content, with your home page SEO below filling anything missing. Different: edit each page on its own tab; a blank field still uses that page's auto value.",
            },
            # "Same for every page": the brand set, edited in the same Home-page
            # form the per-page editor shows (editor "seo_home"), and hidden in
            # "Different" mode, where the Home tab edits the home page instead.
            # Still stored as the `seo` object: storefront.seo_pages applies it
            # to the home page and uses it to fill gaps on every other page.
            {
                "name": "seo",
                "label": "Home page SEO",
                "type": "object",
                "editor": "seo_home",
                "subfields": _page_seo_fields() + [
                    {"name": "raw_schema_json", "label": "Custom schema (JSON-LD)", "type": "textarea", "span": 12,
                     "help": "Optional structured data added to your home page, as a JSON object."},
                ],
                "show_if": {"field": "seo_mode", "in": ["same"]},
            },
            # One tab per storefront page (Different mode). The page list comes
            # from storefront.seo_pages.page_catalog via the editor's datasets.
            {
                "name": "seo_pages", "label": "Page SEO", "type": "seo_pages",
                "subfields": _page_seo_fields(),
                "show_if": {"field": "seo_mode", "in": ["custom"]},
            },
        ],
    },
    {
        "key": "policies",
        "label": "Policies",
        "icon": "policies",
        "layout": "accordion",
        "hint": "Your store's policy pages. Open one to edit it  only one stays open at a time.",
        "fields": [
            {"name": "privacy_policy", "label": "Privacy policy", "type": "richtext"},
            {"name": "terms_conditions", "label": "Terms & conditions", "type": "richtext"},
            {"name": "refund_policy", "label": "Refund policy", "type": "richtext"},
            {"name": "shipping_policy", "label": "Shipping policy", "type": "richtext"},
            # Three optional extra policies the owner names themselves. Each one
            # with a title or body becomes a page at /policies/<slug>/, linked
            # from the store footer, the policy tabs and the UGC storefront (see
            # storefront/policies.py). Leave both blank to keep the slot unused.
            {"name": "custom_policy_1", "label": "Custom policy 1 (optional)", "type": "object", "hide_label": True,
             "subfields": _custom_policy_fields(1)},
            {"name": "custom_policy_2", "label": "Custom policy 2 (optional)", "type": "object", "hide_label": True,
             "subfields": _custom_policy_fields(2)},
            {"name": "custom_policy_3", "label": "Custom policy 3 (optional)", "type": "object", "hide_label": True,
             "subfields": _custom_policy_fields(3)},
        ],
    },
    {
        "key": "certifications",
        "label": "Certificates",
        "icon": "certifications",
        # B2B stores only: trade buyers vet suppliers on these. The storefront's
        # /certificates/ page (and its SEO tab entry) exists for B2B stores alone.
        "audience": "b2b",
        "hint": "Certificates shown to trade buyers on your Certificates page. Add as many as you like  each with a name, a preview image and the certificate file (PDF or image) buyers can open.",
        "fields": [
            {
                "name": "certificates",
                "label": "Certificates",
                "type": "list",
                # Bespoke accordion editor (one open at a time, drag to reorder),
                # the same feel as the Blogs tab. See buildCertificatesEditor.
                "editor": "certificates",
                "item_label": "Certificate",
                # Name full width, then the preview image beside the file.
                "item_fields": [
                    {"name": "title", "label": "Certificate name", "type": "text", "span": 12,
                     "placeholder": "e.g. ISO 9001:2015 Quality Management"},
                    {"name": "image", "label": "Certificate image", "type": "image", "span": 6, "demo": "certificate",
                     "help": "The preview shown on the card. A scan or photo of the certificate works best."},
                    {"name": "file", "label": "Certificate file", "type": "file", "span": 6,
                     "accept": "application/pdf,image/*",
                     "help": "The document buyers open: a PDF or an image, up to 10 MB."},
                ],
            },
        ],
    },
    {
        "key": "core_pages",
        "label": "Core pages",
        "icon": "pages",
        "hint": "Custom content pages, each with optional subpages and child pages  a three-level tree, like Services → Consulting → Onboarding. A page lives at /<slug>/, a subpage at /<slug>/<subpage>/, and a child at /<slug>/<subpage>/<child>/. Pages are reachable from the storefront footer (Core Pages).",
        "fields": [
            {
                "name": "core_pages",
                "label": "Pages",
                "type": "list",
                # Bespoke accordion editor: each page is a collapsible card, one
                # open at a time, with the two-column body from _page_fields 
                # the same feel as the Blogs tab (see buildPagesEditor).
                "editor": "pages",
                "item_label": "Page",
                "item_fields": _page_fields([
                    {
                        "name": "subpages",
                        "label": "Subpages",
                        "type": "list",
                        "item_label": "Subpage",
                        "item_fields": _page_fields([
                            {
                                "name": "children",
                                "label": "Child pages",
                                "type": "list",
                                "item_label": "Child page",
                                "item_fields": _page_fields(),
                            },
                        ]),
                    },
                ]),
            },
        ],
    },
    {
        "key": "scripts",
        "label": "Custom scripts",
        "icon": "scripts",
        "hint": "Advanced: inject tracking or custom CSS/JS. Only enable if you trust the code.",
        "fields": [
            {"name": "allows_custom_scripts", "label": "Allow custom scripts", "type": "checkbox", "site_field": True},
            {"name": "head_scripts", "label": "Head scripts", "type": "textarea", "help": "e.g. Meta Pixel or Google Analytics snippets."},
            {"name": "custom_css", "label": "Custom CSS", "type": "textarea"},
            {"name": "custom_js", "label": "Custom JS", "type": "textarea"},
        ],
    },
]

_SECTION_BY_KEY = {s["key"]: s for s in SECTIONS}


def _sections_for(site):
    """Sections applicable to this store's business model."""
    out = []
    for section in SECTIONS:
        audience = section.get("audience", "all")
        if audience == "all" or (audience == "b2b" and site.is_b2b) or (audience == "d2c" and site.is_d2c):
            out.append(section)
    return out


def editor_tab_choices():
    """(key, label) for every content-editor tab  the vocabulary a FREE plan's
    ``included_editor_tabs`` picker (Django admin) chooses from. Sourced from
    ``SECTIONS`` so it never drifts from what the editor actually shows."""
    return [(s["key"], s["label"]) for s in SECTIONS]


# --------------------------------------------------------------------------
# Value coercion / validation against the schema
# --------------------------------------------------------------------------


def _coerce_scalar(field_type, value):
    if value is None:
        return "" if field_type != "checkbox" else False
    if field_type == "checkbox":
        return bool(value)
    if field_type in ("number", "range"):
        try:
            return int(value)
        except (TypeError, ValueError):
            try:
                return float(value)
            except (TypeError, ValueError):
                return ""
    return str(value)


def _clean_item(item_fields, raw):
    if not isinstance(raw, dict):
        return {}
    # Recurse through _clean_field so a list item can itself contain lists or
    # objects (e.g. a core page holding subpages holding child pages).
    return {f["name"]: _clean_field(f, raw.get(f["name"])) for f in item_fields}


WEEK_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _clean_schedule(raw):
    """A weekly availability as ``[{"day", "start", "end"}]`` in week order.

    Accepts the JSON string the editor's schedule widget posts (or a list).
    Keeps only real weekdays with valid ``HH:MM`` times where the end is after
    the start; one entry per day.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except (TypeError, ValueError):
            raw = []
    by_day = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        day = str(item.get("day") or "").strip().title()
        start = str(item.get("start") or "").strip()[:5]
        end = str(item.get("end") or "").strip()[:5]
        if day in WEEK_DAYS and _HHMM.match(start) and _HHMM.match(end) and end > start:
            by_day[day] = {"day": day, "start": start, "end": end}
    return [by_day[d] for d in WEEK_DAYS if d in by_day]


_SEO_PAGE_KEY = re.compile(
    r"^(home|about|all_products|categories|blogs|contact|core_pages|certificates"
    r"|privacy|terms|refund|shipping|policy:[1-3]"
    r"|core:[A-Za-z0-9][A-Za-z0-9_-]*(/[A-Za-z0-9][A-Za-z0-9_-]*){0,2}"
    r"|blog:[A-Za-z0-9][A-Za-z0-9_-]*"
    r"|(product|category):[0-9a-f-]{32,36})$"
)


def _clean_seo_pages(field, raw):
    """``{page key: {seo field: value}}``. Keys must look like a real page key;
    a page whose fields are all blank is dropped (it simply uses its auto SEO)."""
    out = {}
    for key, values in (raw.items() if isinstance(raw, dict) else []):
        key = str(key or "").strip()
        if len(key) > 220 or not _SEO_PAGE_KEY.match(key) or not isinstance(values, dict):
            continue
        cleaned = {}
        for sf in field["subfields"]:
            if sf.get("type") == "heading":
                continue
            val = _coerce_scalar(sf["type"], values.get(sf["name"]))
            if isinstance(val, str):
                val = val.strip()
                if val == REMOVED_MEDIA:
                    val = ""
            if val not in ("", False, None):
                cleaned[sf["name"]] = val
        if cleaned:
            out[key] = cleaned
    return out


def _clean_file_url(raw) -> str:
    """A stored document link. It is rendered as an ``href`` on the storefront,
    so only http(s) and site-relative URLs are kept (never ``javascript:``)."""
    url = str(raw or "").strip()[:2000]
    if url.startswith("/") and not url.startswith("//"):
        return url
    if url.lower().startswith(("https://", "http://")):
        return url
    return ""


def _clean_field(field, raw):
    ftype = field["type"]
    if ftype == "file":
        return _clean_file_url(raw)
    if ftype == "schedule":
        return _clean_schedule(raw)
    if ftype == "seo_pages":
        return _clean_seo_pages(field, raw)
    if ftype == "list":
        items = raw if isinstance(raw, list) else []
        if field.get("max_items"):
            items = items[: field["max_items"]]
        return [_clean_item(field["item_fields"], it) for it in items]
    if ftype == "object":
        obj = raw if isinstance(raw, dict) else {}
        # Heading subfields are visual dividers only  they carry no value, so
        # they are not stored (they'd otherwise pollute the object with "" keys).
        return {
            sf["name"]: _coerce_scalar(sf["type"], obj.get(sf["name"]))
            for sf in field["subfields"]
            if sf.get("type") != "heading"
        }
    return _coerce_scalar(ftype, raw)


# --------------------------------------------------------------------------
# Owner-removed media
# --------------------------------------------------------------------------
# An empty media slot means "not set yet", and the storefront fills it with a
# reference photo / demo video so a new store looks finished. When the owner
# clicks the delete icon ON a media preview, the slot must instead stay empty on
# the live site. The editor marks such a slot with REMOVED_MEDIA; on save it is
# stored as "" plus a sibling "<field>_removed": True flag (top level, inside an
# object, or inside a list item), and the storefront skips its fallback wherever
# that flag is set. Picking new media clears the flag. The editor gets the
# marker back on reload (_restore_removed_media), so it re-sends it on every
# later save of that section and the flag survives list rebuilds and reorders.

#: What the editor's delete icon puts in a cleared media slot. Never stored.
REMOVED_MEDIA = "__removed__"
_MEDIA_TYPES = ("image", "video")


def _settle_removed_media(fields, obj, prev=None):
    """Turn every REMOVED_MEDIA marker in ``obj`` (cleaned values for
    ``fields``) into ``""`` + ``<name>_removed: True``. Recurses into objects
    and list items, so banners, blog posts and nested pages are covered.

    ``prev`` is the same structure as last saved. A slot that held media there
    and arrives empty now is also a removal: that is how an editor tab opened
    before the marker existed (or any client that just blanks the value)
    deletes media, and it must not bring the demo photo back. A slot already
    flagged stays flagged until new media is picked. List items are paired with
    the previous list by position."""
    if not isinstance(obj, dict):
        return obj
    prev = prev if isinstance(prev, dict) else {}
    for f in fields:
        name, ftype = f.get("name"), f.get("type")
        if not name or ftype == "heading":
            continue
        if ftype in _MEDIA_TYPES:
            value = obj.get(name)
            was = str(prev.get(name) or "").strip()
            if value == REMOVED_MEDIA:
                obj[name] = ""
                obj[f"{name}_removed"] = True
            elif str(value or "").strip():
                obj.pop(f"{name}_removed", None)
            elif (was and was != REMOVED_MEDIA) or prev.get(f"{name}_removed"):
                obj[name] = ""
                obj[f"{name}_removed"] = True
            else:
                obj.pop(f"{name}_removed", None)
        elif ftype == "object" and isinstance(obj.get(name), dict):
            _settle_removed_media(f.get("subfields") or [], obj[name], prev.get(name))
        elif ftype == "list" and isinstance(obj.get(name), list):
            before = prev.get(name) if isinstance(prev.get(name), list) else []
            for i, item in enumerate(obj[name]):
                _settle_removed_media(f.get("item_fields") or [], item, before[i] if i < len(before) else None)
    return obj


def _restore_removed_media(fields, obj):
    """The reverse, for the editor: a slot flagged as removed (and still empty)
    is handed back as REMOVED_MEDIA so the editor keeps it marked."""
    if not isinstance(obj, dict):
        return obj
    for f in fields:
        name, ftype = f.get("name"), f.get("type")
        if not name or ftype == "heading":
            continue
        if ftype in _MEDIA_TYPES:
            if obj.get(f"{name}_removed") and not obj.get(name):
                obj[name] = REMOVED_MEDIA
        elif ftype == "object" and isinstance(obj.get(name), dict):
            _restore_removed_media(f.get("subfields") or [], obj[name])
        elif ftype == "list" and isinstance(obj.get(name), list):
            for item in obj[name]:
                _restore_removed_media(f.get("item_fields") or [], item)
    return obj


def _seo_page_catalog(site):
    """The SEO tab's per-page list (grouped), each page with its auto values."""
    from storefront.seo_pages import page_catalog

    try:
        return page_catalog(site)
    except Exception:
        return []


def _seo_home_auto(site):
    """The home page's auto SEO without the brand set: the placeholders of the
    "Same for every page" form, which edits the brand set itself."""
    from storefront.seo_pages import home_auto

    try:
        return home_auto(site)
    except Exception:
        return {}


def _catalog_datasets(site):
    """Category/product option lists for the banner link-target dropdowns."""
    from commerce.models import Category, Product, display_ordered_categories

    cats = display_ordered_categories(
        Category.objects.for_site(site).filter(is_active=True, is_deleted=False)
    )
    from core.queries import sorted_rows

    # No index serves ORDER BY title; sort keys only, not whole product rows.
    prods = sorted_rows(
        Product.objects.for_site(site)
        .filter(is_active=True, is_deleted=False)
        .order_by("title")
    )
    return {
        "categories": [{"value": str(c.id), "label": c.name, "slug": c.slug} for c in cats],
        "products": [{"value": str(p.id), "label": p.title, "slug": p.slug} for p in prods],
    }


def _newsletter_coupon_options(site):
    """The Newsletter tab's offer dropdown: the store's valid discount codes,
    each carrying the heading it gives the popup."""
    from storefront.newsletter_popup import coupon_options

    try:
        return coupon_options(site)
    except Exception:
        return []


def _ensure_slug(node):
    from django.utils.text import slugify

    if isinstance(node, dict) and not str(node.get("slug") or "").strip() and node.get("title"):
        node["slug"] = slugify(node["title"])[:200]


def _ensure_post_date(node):
    """A post saved without a date is dated today (Y-m-d), so the listing, the
    article's <time> and BlogPosting.datePublished always have one. An existing
    date is never changed."""
    from django.utils import timezone

    if isinstance(node, dict) and not str(node.get("date") or "").strip():
        node["date"] = timezone.localdate().isoformat()


# A core page is served at the bare slug (/<slug>/), so a top-level page slug
# that equals a reserved storefront path would be shadowed by that route and its
# page would never render. These are the first path segments the storefront
# urlconf already claims; a colliding page slug is suffixed with "-page" instead.
# (Only the top level matters  subpage/child slugs are never a first segment.)
_RESERVED_SLUGS = frozenset({
    "shop", "products", "product", "categories", "category",
    "about", "about-us", "certificates", "contact", "enquiry",
    "blog", "blogs", "core-pages", "page", "pages",
    "privacy-policy", "terms-conditions", "refund-policy",
    "shipping-policy", "custom-policy", "policies",
    "cart", "checkout", "webhooks", "wishlist", "account", "orders",
    "newsletter", "api", "health", "media", "static", "_owner", "s",
})


def _fill_core_page_slugs(pages):
    """Auto-generate blank slugs from titles, down the whole tree, and keep a
    top-level page slug from colliding with a reserved storefront path."""
    for page in pages or []:
        _ensure_slug(page)
        if isinstance(page, dict):
            slug = str(page.get("slug") or "").strip().lower()
            if slug in _RESERVED_SLUGS:
                page["slug"] = f"{slug}-page"
        for sub in (page.get("subpages") or []) if isinstance(page, dict) else []:
            _ensure_slug(sub)
            for child in (sub.get("children") or []) if isinstance(sub, dict) else []:
                _ensure_slug(child)


def _resolve_banner_urls(banners, datasets):
    """Turn each banner's link type + target into the ``button_url`` the
    storefront reads  mirroring the reference's hidden resolved URL."""
    cat_slug = {c["value"]: c["slug"] for c in datasets.get("categories", [])}
    prod_slug = {p["value"]: p["slug"] for p in datasets.get("products", [])}

    def resolve(link_type, category_id, product_id, custom_url):
        if link_type == "home":
            return "/"
        if link_type == "about":
            return "/about/"
        if link_type == "categories":
            return "/categories/"
        if link_type == "category":
            slug = cat_slug.get(str(category_id or ""))
            return f"/category/{slug}/" if slug else "/categories/"
        if link_type == "product":
            slug = prod_slug.get(str(product_id or ""))
            return f"/products/{slug}/" if slug else "/shop/"
        if link_type == "custom":
            return (custom_url or "").strip() or "#"
        return "/shop/"  # shop / default

    for b in banners:
        b["button_url"] = resolve(
            (b.get("button_link_type") or "shop").strip(),
            b.get("button_link_category_id"), b.get("button_link_product_id"), b.get("button_custom_url"),
        )
        # Split hero's image button: its own target, or the banner button's.
        circle_type = (b.get("circle_link_type") or "button").strip()
        if circle_type == "button":
            b["circle_url"] = b["button_url"]
        else:
            b["circle_url"] = resolve(
                circle_type, b.get("circle_link_category_id"),
                b.get("circle_link_product_id"), b.get("circle_custom_url"),
            )
        # Rendered into a style attribute: keep only a plain hex colour.
        color = str(b.get("circle_text_color") or "").strip()
        b["circle_text_color"] = color if _HEX_COLOR.match(color) else ""
        b["circle_text"] = " ".join(str(b.get("circle_text") or "").split())[:40]
    return banners


_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------


@login_required
def editor(request, site_id):
    site = get_owned_site(request, site_id)
    if not site.content:
        from storefront.content_seed import default_content

        site.content = default_content(site)
        site.save(update_fields=["content", "updated_at"])

    from storefront.demo_media import editor_demo_map

    from commerce import features

    # A free-plan owner may only edit the tabs their plan includes; the rest are
    # shown locked as an "Upgrade" (see the editor JS). ``None`` = every tab is
    # editable (paid subscription, or no free-plan gating configured). Copy each
    # section (never mutate the shared SECTIONS constant) and, for a locked tab,
    # drop its fields from the payload  the client renders an upgrade panel, and
    # ``save_section`` refuses a write to it regardless.
    allowed_tabs = features.allowed_editor_tabs(request)

    def _with_lock(section):
        locked = allowed_tabs is not None and section["key"] not in allowed_tabs
        return {**section, "locked": locked, "fields": [] if locked else section["fields"]}

    sections = [_with_lock(s) for s in _sections_for(site)]

    # Surface the reels strip's videos (stored nested at
    # ``video_section.videos``) as the flat ``reels`` list the editor field
    # binds to, so an owner reloads and edits the reels they already have.
    content_data = dict(site.content or {})

    # Prefill the logo-size controls with the theme's effective default widths
    # (the same values the storefront falls back to) so the owner sees the
    # current size rather than a blank box. Display-only: nothing is persisted
    # until the Brand section is saved, and these match what the storefront
    # renders when the keys are absent.
    for _key, _default in (
        ("desktop_logo_max_width", 140),
        ("desktop_logo_max_width_unit", "px"),
        ("mobile_logo_max_width", 100),
        ("mobile_logo_max_width_unit", "px"),
        ("footer_logomark_max_width", 120),
        ("footer_logomark_max_width_unit", "px"),
    ):
        content_data.setdefault(_key, _default)

    # A store saved before the announcement bar had statements keeps its one
    # message: it opens as statement 1 (display-only until the Brand tab saves).
    if not isinstance(content_data.get("announcement_items"), list):
        legacy = str(content_data.get("announcement_text") or "").strip()
        content_data["announcement_items"] = (
            [{"text": legacy, "link_word": "", "link_url": ""}] if legacy else []
        )
    content_data.setdefault("announcement_mode", "static")

    if "reels" not in content_data:
        vs = content_data.get("video_section")
        if isinstance(vs, dict) and isinstance(vs.get("videos"), list):
            content_data["reels"] = [
                {"video": v.get("url", "")}
                for v in vs["videos"]
                if isinstance(v, dict) and v.get("url")
            ]

    # Media the owner removed comes back marked, so the editor keeps it removed
    # (a deep copy: site.content itself is never touched here).
    import copy

    content_data = copy.deepcopy(content_data)
    for _section in SECTIONS:
        _restore_removed_media(_section["fields"], content_data)

    context = store_context(request, site, "editor")
    context.update(
        {
            "crumb": "Edit content",
            # Passed as objects; the template serialises them with json_script so
            # the browser JSON.parses an array/object, not a re-encoded string.
            "sections": sections,
            "content_data": content_data,
            # {demo-key: url} for the editor's ghost previews  a blank image
            # field shows the same reference photo the storefront falls back to,
            # dimmed and badged "Example", so the owner sees where it uploads.
            "demo_media": editor_demo_map(),
            "datasets": {
                **_catalog_datasets(site),
                "seo_pages": _seo_page_catalog(site),
                "seo_home_auto": _seo_home_auto(site),
                "coupons": _newsletter_coupon_options(site),
            },
            "allows_custom_scripts": site.allows_custom_scripts,
            "storefront_url": site.storefront_url(request),
        }
    )
    return render(request, "store/editor.html", context)


@require_POST
@login_required
def save_section(request, site_id, section):
    site = get_owned_site(request, site_id)
    spec = _SECTION_BY_KEY.get(section)
    if spec is None:
        return HttpResponseBadRequest("Unknown section.")

    # A free-plan owner cannot save a tab their plan doesn't include, even by
    # hitting this endpoint directly  the editor only shows an upgrade panel for
    # it, and this is the authoritative refusal behind that.
    from commerce import features

    if features.is_editor_tab_locked(request, section):
        return JsonResponse(
            {
                "ok": False,
                "upgrade_required": True,
                "error": "This content tab isn’t included in your plan. Upgrade to edit it.",
            },
            status=402,
        )

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON.")

    values = payload.get("values") or {}
    content = dict(site.content or {})
    update_fields = ["content", "updated_at"]

    for field in spec["fields"]:
        # "heading" fields are visual dividers only  they carry no value.
        if field.get("type") == "heading":
            continue
        name = field["name"]
        if name not in values:
            continue
        cleaned = _clean_field(field, values[name])
        # Owner-removed media: marker → "" + "<name>_removed" flag (see above).
        _old = site.content or {}
        holder = _settle_removed_media(
            [field], {name: cleaned},
            {name: _old.get(name), f"{name}_removed": _old.get(f"{name}_removed")},
        )
        cleaned = holder[name]
        if field.get("type") in _MEDIA_TYPES:
            if holder.get(f"{name}_removed"):
                content[f"{name}_removed"] = True
            else:
                content.pop(f"{name}_removed", None)
        if field.get("site_field"):
            if name == "allows_custom_scripts":
                site.allows_custom_scripts = bool(cleaned)
                if "allows_custom_scripts" not in update_fields:
                    update_fields.append("allows_custom_scripts")
            continue
        # Resolve each banner's link type/target into a concrete button_url.
        if name == "hero_banners" and isinstance(cleaned, list):
            cleaned = _resolve_banner_urls(cleaned, _catalog_datasets(site))
        # Fill blank slugs from titles so pages/posts are addressable.
        elif name == "core_pages" and isinstance(cleaned, list):
            _fill_core_page_slugs(cleaned)
        elif name == "blog_posts" and isinstance(cleaned, list):
            for post in cleaned:
                _ensure_slug(post)
                _ensure_post_date(post)
        # Announcement bar: trimmed statements with safe links, and a known mode.
        elif name == "announcement_items":
            from storefront import announcement

            cleaned = announcement.clean_items(cleaned)
            # Mirror the first statement into the original single-message key,
            # so anything still reading announcement_text sees the same words.
            content["announcement_text"] = cleaned[0]["text"] if cleaned else ""
        elif name == "announcement_mode":
            from storefront import announcement

            cleaned = announcement.clean_mode(cleaned)
        content[name] = cleaned

    # Keep name/about-title mirrors in step, as the templates read either key.
    if "site_name" in content:
        content["websiteName"] = content["site_name"]
    if "about_section_title" in content:
        content["about_title"] = content["about_section_title"]
    if "about_section_content" in content:
        content["about_content"] = content["about_section_content"]

    # Reels strip: the storefront renders ``video_section.videos`` (a list of
    # ``{url}``). Fold the editor's flat ``reels`` list into that nested shape,
    # preserving the section's title/position. Kept in step so the editor reloads
    # the same reels it saved and the storefront plays them.
    if "reels" in content:
        reels = content.get("reels") or []
        vs = content.get("video_section")
        if not isinstance(vs, dict):
            vs = {}
        # The owner deleted their last reel: keep the strip empty on the live
        # site instead of showing the demo reels again ("reels_removed").
        if "reels" in values:
            had = any(isinstance(v, dict) and v.get("url") for v in (vs.get("videos") or []))
            has = any(isinstance(r, dict) and r.get("video") for r in reels)
            if has:
                content.pop("reels_removed", None)
            elif had:
                content["reels_removed"] = True
        vs["videos"] = [
            {"url": r.get("video", "")}
            for r in reels
            if isinstance(r, dict) and r.get("video")
        ]
        content["video_section"] = vs

    # The contact page reads contact_email / mobile_number; the footer and other
    # older branches read email / phone. Keep both key names in step so every
    # surface shows the same value whichever it reads.
    ci = content.get("contact_info")
    if isinstance(ci, dict):
        if ci.get("contact_email"):
            ci["email"] = ci["contact_email"]
        if ci.get("mobile_number"):
            ci["phone"] = ci["mobile_number"]
        # Google Maps: store the embeddable URL beside the pasted link so the
        # contact page can frame it (a share link is resolved once, here). The
        # schema clean drops these derived keys, so carry the previous ones over
        # first; autosave then re-resolves only when the link actually changed.
        from storefront import maps

        prev = (site.content or {}).get("contact_info")
        if isinstance(prev, dict) and prev.get("map_embed_source") == ci.get("map_location"):
            ci["map_embed_source"] = prev.get("map_embed_source")
            ci["map_embed_url"] = prev.get("map_embed_url", "")
        maps.apply_to_contact_info(ci)

    site.content = content
    site.save(update_fields=update_fields)

    activity.record(
        "store.content.updated", request=request, site=site,
        target=site, target_kind="Site", label=spec.get("label") or section,
        changes={"section": section},
    )

    return JsonResponse({"ok": True, "saved_at": timezone.now().strftime("%H:%M:%S")})


@require_POST
@login_required
def upload_media(request, site_id):
    site = get_owned_site(request, site_id)
    upload = request.FILES.get("file")
    if upload is None:
        return HttpResponseBadRequest("No file.")

    content_type = (upload.content_type or "").lower()
    name = upload.name or "upload"

    # Documents (a certificate's PDF): accepted only when the bytes really are a
    # PDF  the browser-sent content type and extension are both forgeable  and
    # stored under a sanitised name that always ends in .pdf.
    if content_type == "application/pdf" or name.lower().endswith(".pdf"):
        if upload.size and upload.size > _MAX_DOCUMENT_BYTES:
            return JsonResponse({"ok": False, "error": "PDFs must be 10 MB or smaller."}, status=400)
        head = upload.read(5)
        upload.seek(0)
        if head != b"%PDF-":
            return JsonResponse({"ok": False, "error": "That file isn’t a valid PDF."}, status=400)
        from pathlib import PurePath

        from django.utils.text import get_valid_filename

        stem = get_valid_filename(PurePath(name).stem)[:80] or "document"
        path = default_storage.save(f"sites/{site.id}/documents/{stem}.pdf", upload)
        return JsonResponse({"ok": True, "url": default_storage.url(path), "name": f"{stem}.pdf", "kind": "pdf"})

    if not (content_type.startswith("image/") or content_type.startswith("video/")):
        return HttpResponseBadRequest("Images, videos or PDFs only.")

    path = default_storage.save(f"sites/{site.id}/content/{upload.name}", upload)
    return JsonResponse({"ok": True, "url": default_storage.url(path)})


_MAX_DOCUMENT_BYTES = 10 * 1024 * 1024


# -- preview (kept for external use; not embedded in the editor UI) ---------


@login_required
def map_preview(request, site_id):
    """Live preview for the Contact tab's Google Maps field: converts whatever
    the owner pasted (share link, place URL, embed code, address) into the
    embeddable URL the storefront will frame. JSON ``{ok, embed, link}``."""
    from core import ratelimit
    from storefront import maps

    site = get_owned_site(request, site_id)
    if not ratelimit.consume("editor.map_preview", ratelimit.client_identity(request)).allowed:
        return JsonResponse({"ok": False, "error": "Too many requests. Try again in a minute."}, status=429)
    raw = (request.GET.get("link") or "").strip()
    if not raw:
        return JsonResponse({"ok": True, "embed": "", "link": ""})
    embed = maps.embed_for_link(raw)
    if not embed:
        return JsonResponse(
            {
                "ok": False,
                "embed": "",
                "link": "",
                "error": "That doesn’t look like a Google Maps location. Paste a link from Google Maps "
                "(Share → Copy link) or the embed code (Share → Embed a map).",
            }
        )
    ci = (site.content or {}).get("contact_info")
    return JsonResponse({"ok": True, "embed": embed, "link": maps.open_link(raw, ci if isinstance(ci, dict) else {})})


@xframe_options_sameorigin
@login_required
def preview(request, site_id):
    """Render the live storefront homepage (same-origin, frameable)."""
    from django.conf import settings
    from django.http import HttpResponse
    from django.urls import get_urlconf, set_urlconf

    from storefront.context import home_extra

    site = get_owned_site(request, site_id)
    template = f"storefront/{site.template_dir}/home.html"
    ctx = build_context(
        request, site, page_title="Preview", embedded=False, **home_extra(request, site)
    )

    previous_urlconf = get_urlconf()
    set_urlconf("onewooz.urls.storefront")
    try:
        response = render(request, template, ctx)
        _ = response.content
    except Exception as exc:  # pragma: no cover - dev aid
        message = str(exc) if settings.DEBUG else "Preview is unavailable right now."
        response = HttpResponse(
            "<!doctype html><meta charset='utf-8'>"
            "<div style=\"font:14px/1.6 ui-sans-serif,system-ui;padding:2rem;color:#b42318\">"
            "<strong>Preview error</strong><br>" + message + "</div>",
            content_type="text/html; charset=utf-8",
        )
    finally:
        set_urlconf(previous_urlconf)

    # media-src must admit the same origins the main storefront CSP does (the
    # uploads bucket / CDN, plus the demo-video hosts) or reels and product
    # videos are blocked in this preview iframe. Built from settings so it stays
    # in step with the real storefront policy.
    media_src = " ".join(
        list(getattr(settings, "CSP_MEDIA_SRC", ("'self'", "data:", "blob:")))
        + list(getattr(settings, "CSP_STOREFRONT_MEDIA_EXTRA", ()))
    )
    frame_src = " ".join(
        list(getattr(settings, "CSP_FRAME_SRC", ("'self'",)))
        + list(getattr(settings, "CSP_STOREFRONT_FRAME_EXTRA", ()))
    )
    response["X-Frame-Options"] = "SAMEORIGIN"
    response["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
        "font-src 'self' data: https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
        "img-src 'self' data: blob: https:; "
        f"media-src {media_src}; frame-src {frame_src}; frame-ancestors 'self'"
    )
    return response
