"""Built-in icon set for owner-made trust badges.

A store owner creates their own trust badge (a short name plus an icon) from
the product form's badge picker. Icons cannot be uploaded: the owner chooses
from this fixed, curated set, so every badge renders crisply, matches the
storefront's line style and carries no untrusted markup.

Each icon is the *inner* markup of a 24×24 stroke SVG (Feather/Lucide-style
geometry, drawn for this project), rendered with ``currentColor`` so it takes
the surrounding text colour. Keys are stable identifiers stored on the badge
(``content["custom_trust_badges"][i]["icon"]``); never rename one.
"""

from __future__ import annotations

from django.utils.safestring import mark_safe

_SHIELD = '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>'
_HEART = '<path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>'
_SEAL = '<path d="M12 2l2.4 2.2 3.2-.4.9 3.1 2.9 1.5-1.2 3 1.2 3-2.9 1.5-.9 3.1-3.2-.4L12 22l-2.4-2.2-3.2.4-.9-3.1-2.9-1.5 1.2-3-1.2-3 2.9-1.5.9-3.1 3.2.4z"/>'
_PACKAGE = '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><path d="M3.27 6.96 12 12.01l8.73-5.05"/><path d="M12 22.08V12"/>'
_CALENDAR = '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>'

# (key, label, group, inner svg markup)
ICONS: list[tuple[str, str, str, str]] = [
    # --- Shipping & delivery
    ("truck", "Truck", "Shipping", '<path d="M1 3h15v13H1z"/><path d="M16 8h4l3 3v5h-7z"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/>'),
    ("package", "Package", "Shipping", _PACKAGE),
    ("package-check", "Package check", "Shipping", '<path d="M21 10V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l1-.57"/><path d="M3.27 6.96 12 12.01l8.73-5.05M12 22V12"/><path d="m16 19 2 2 4-4"/>'),
    ("archive", "Box", "Shipping", '<path d="M21 8v13H3V8"/><path d="M1 3h22v5H1z"/><path d="M10 12h4"/>'),
    ("rocket", "Rocket", "Shipping", '<path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/>'),
    ("plane", "Plane", "Shipping", '<path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z"/>'),
    ("globe", "Globe", "Shipping", '<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>'),
    ("map-pin", "Map pin", "Shipping", '<path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>'),
    ("map", "Map", "Shipping", '<path d="M1 6v16l7-4 8 4 7-4V2l-7 4-8-4z"/><path d="M8 2v16M16 6v16"/>'),
    ("compass", "Compass", "Shipping", '<circle cx="12" cy="12" r="10"/><path d="m16.24 7.76-2.12 6.36-6.36 2.12 2.12-6.36z"/>'),
    ("home", "Home", "Shipping", '<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/>'),
    ("warehouse", "Warehouse", "Shipping", '<path d="M22 8.35V20a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V8.35A2 2 0 0 1 3.26 6.5l8-3.2a2 2 0 0 1 1.48 0l8 3.2A2 2 0 0 1 22 8.35z"/><path d="M6 18h12M6 14h12M6 22V10h12v12"/>'),
    ("factory", "Factory", "Shipping", '<path d="M2 20h20"/><path d="M4 20V8l5 3V8l5 3V4h6v16"/><path d="M8 16h.01M12 16h.01M16 16h.01"/>'),
    ("store", "Store", "Shipping", '<path d="M3 9l1.5-5h15L21 9"/><path d="M3 9h18v2a3 3 0 0 1-6 0 3 3 0 0 1-6 0 3 3 0 0 1-6 0z"/><path d="M5 12v9h14v-9"/><path d="M10 21v-5h4v5"/>'),
    ("car", "Car", "Shipping", '<path d="M5 17H3v-5l2-5h14l2 5v5h-2"/><circle cx="7.5" cy="17.5" r="2"/><circle cx="16.5" cy="17.5" r="2"/><path d="M9.5 17.5h5M3 12h18"/>'),
    ("bicycle", "Bicycle", "Shipping", '<circle cx="5.5" cy="17.5" r="3.5"/><circle cx="18.5" cy="17.5" r="3.5"/><circle cx="15" cy="5" r="1"/><path d="M12 17.5V14l-3-3 4-3 2 3h2"/>'),
    ("anchor", "Anchor", "Shipping", '<circle cx="12" cy="5" r="3"/><path d="M12 22V8M5 12H2a10 10 0 0 0 20 0h-3"/>'),
    # --- Payment & pricing
    ("lock", "Lock", "Payment", '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>'),
    ("unlock", "Unlock", "Payment", '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/>'),
    ("key", "Key", "Payment", '<circle cx="7.5" cy="15.5" r="5.5"/><path d="m21 2-9.6 9.6"/><path d="m15.5 7.5 3 3L22 7l-3-3"/>'),
    ("credit-card", "Card", "Payment", '<rect x="1" y="4" width="22" height="16" rx="2"/><path d="M1 10h22"/>'),
    ("wallet", "Wallet", "Payment", '<path d="M20 12V8H6a2 2 0 0 1 0-4h12v4"/><path d="M4 6v12a2 2 0 0 0 2 2h14v-4"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/>'),
    ("banknote", "Cash", "Payment", '<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2"/><path d="M6 12h.01M18 12h.01"/>'),
    ("coins", "Coins", "Payment", '<circle cx="8" cy="8" r="6"/><path d="M18.09 10.37A6 6 0 1 1 10.34 18"/><path d="M7 6h1v4"/>'),
    ("rupee", "Rupee", "Payment", '<path d="M6 3h12M6 8h12M6 13l8.5 8M6 13h3a5 5 0 0 0 0-10"/>'),
    ("dollar", "Dollar", "Payment", '<path d="M12 1v22"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>'),
    ("percent", "Percent", "Payment", '<path d="M19 5 5 19"/><circle cx="6.5" cy="6.5" r="2.5"/><circle cx="17.5" cy="17.5" r="2.5"/>'),
    ("sale", "Sale badge", "Payment", _SEAL + '<path d="m15 9-6 6M9 9h.01M15 15h.01"/>'),
    ("tag", "Tag", "Payment", '<path d="M20.59 13.41 13.42 20.58a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><path d="M7 7h.01"/>'),
    ("receipt", "Receipt", "Payment", '<path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1z"/><path d="M16 8H8M16 12H8M13 16H8"/>'),
    ("gift", "Gift", "Payment", '<path d="M20 12v10H4V12"/><path d="M2 7h20v5H2z"/><path d="M12 22V7"/><path d="M12 7H7.5a2.5 2.5 0 0 1 0-5C11 2 12 7 12 7z"/><path d="M12 7h4.5a2.5 2.5 0 0 0 0-5C13 2 12 7 12 7z"/>'),
    ("shopping-bag", "Shopping bag", "Payment", '<path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"/><path d="M3 6h18"/><path d="M16 10a4 4 0 0 1-8 0"/>'),
    ("cart", "Cart", "Payment", '<circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/>'),
    # --- Quality & trust
    ("shield", "Shield", "Quality", _SHIELD),
    ("shield-check", "Shield check", "Quality", _SHIELD + '<path d="m9 12 2 2 4-4"/>'),
    ("shield-plus", "Shield plus", "Quality", _SHIELD + '<path d="M12 9v6M9 12h6"/>'),
    ("award", "Award", "Quality", '<circle cx="12" cy="8" r="7"/><path d="M8.21 13.89 7 23l5-3 5 3-1.21-9.12"/>'),
    ("trophy", "Trophy", "Quality", '<path d="M8 21h8M12 17v4"/><path d="M7 4h10v5a5 5 0 0 1-10 0z"/><path d="M17 5h3v2a3 3 0 0 1-3 3M7 5H4v2a3 3 0 0 0 3 3"/>'),
    ("medal", "Medal", "Quality", '<circle cx="12" cy="15" r="6"/><path d="M8.5 10 5 2h4l3 6 3-6h4l-3.5 8"/>'),
    ("star", "Star", "Quality", '<path d="m12 2 3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01z"/>'),
    ("verified", "Verified", "Quality", _SEAL + '<path d="m9 12 2 2 4-4"/>'),
    ("check-circle", "Check circle", "Quality", '<circle cx="12" cy="12" r="10"/><path d="m8 12 3 3 5-6"/>'),
    ("check", "Check", "Quality", '<path d="M20 6 9 17l-5-5"/>'),
    ("thumbs-up", "Thumbs up", "Quality", '<path d="M7 10v12"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88z"/>'),
    ("gem", "Gem", "Quality", '<path d="M6 3h12l4 6-10 13L2 9z"/><path d="M11 3 8 9l4 13 4-13-3-6M2 9h20"/>'),
    ("crown", "Crown", "Quality", '<path d="m2 4 3 12h14l3-12-6 7-4-7-4 7z"/><path d="M5 20h14"/>'),
    ("sparkles", "Sparkles", "Quality", '<path d="M12 3l1.9 5.8L20 11l-6.1 2.2L12 19l-1.9-5.8L4 11l6.1-2.2z"/><path d="M5 3v4M3 5h4M19 17v4M17 19h4"/>'),
    ("certificate", "Certificate", "Quality", '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M16 13H8M16 17H8M10 9H8"/>'),
    ("clipboard-check", "Clipboard check", "Quality", '<path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1"/><path d="m9 14 2 2 4-4"/>'),
    ("target", "Target", "Quality", '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>'),
    ("infinity", "Infinity", "Quality", '<path d="M12 12c-2-2.67-4-4-6-4a4 4 0 1 0 0 8c2 0 4-1.33 6-4zm0 0c2 2.67 4 4 6 4a4 4 0 0 0 0-8c-2 0-4 1.33-6 4z"/>'),
    ("flag", "Flag", "Quality", '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><path d="M4 22v-7"/>'),
    ("bookmark", "Bookmark", "Quality", '<path d="m19 21-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/>'),
    ("fingerprint", "Fingerprint", "Quality", '<path d="M12 11v4a6 6 0 0 1-1 3.5"/><path d="M8 12a4 4 0 0 1 8 0v2a10 10 0 0 1-.6 3.4"/><path d="M5 13v-1a7 7 0 0 1 12.2-4.7"/><path d="M19 12v1.5"/>'),
    ("layers", "Layers", "Quality", '<path d="m12 2 10 5-10 5L2 7z"/><path d="m2 17 10 5 10-5M2 12l10 5 10-5"/>'),
    # --- Returns & warranty
    ("refresh", "Refresh", "Returns", '<path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>'),
    ("rotate-ccw", "Return", "Returns", '<path d="M1 4v6h6"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>'),
    ("repeat", "Exchange", "Returns", '<path d="m17 1 4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><path d="m7 23-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>'),
    ("shuffle", "Shuffle", "Returns", '<path d="M16 3h5v5"/><path d="M4 20 21 3"/><path d="M21 16v5h-5"/><path d="m15 15 6 6M4 4l5 5"/>'),
    ("x-circle", "Cross", "Returns", '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6M9 9l6 6"/>'),
    ("ban", "Not allowed", "Returns", '<circle cx="12" cy="12" r="10"/><path d="m4.93 4.93 14.14 14.14"/>'),
    ("calendar", "Calendar", "Returns", _CALENDAR),
    ("calendar-check", "Calendar check", "Returns", _CALENDAR + '<path d="m9 16 2 2 4-4"/>'),
    ("clock", "Clock", "Returns", '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>'),
    ("timer", "Timer", "Returns", '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2 2"/><path d="M10 2h4M12 2v3"/>'),
    ("hourglass", "Hourglass", "Returns", '<path d="M5 22h14M5 2h14M17 22v-4.17a2 2 0 0 0-.59-1.42L12 12l-4.41 4.41A2 2 0 0 0 7 17.83V22M7 2v4.17a2 2 0 0 0 .59 1.42L12 12l4.41-4.41A2 2 0 0 0 17 6.17V2"/>'),
    ("zap", "Lightning", "Returns", '<path d="M13 2 3 14h9l-1 8 10-12h-9z"/>'),
    # --- Support & people
    ("headset", "Headset", "Support", '<path d="M3 14v-2a9 9 0 0 1 18 0v2"/><rect x="2" y="14" width="4" height="6" rx="1"/><rect x="18" y="14" width="4" height="6" rx="1"/><path d="M20 20a4 4 0 0 1-4 3h-3"/>'),
    ("phone", "Phone", "Support", '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>'),
    ("message", "Chat", "Support", '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>'),
    ("mail", "Mail", "Support", '<path d="M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"/><path d="m22 6-10 7L2 6"/>'),
    ("bell", "Bell", "Support", '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>'),
    ("info", "Info", "Support", '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>'),
    ("help", "Help", "Support", '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3M12 17h.01"/>'),
    ("alert", "Alert", "Support", '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><path d="M12 9v4M12 17h.01"/>'),
    ("users", "Users", "Support", '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>'),
    ("user-check", "User check", "Support", '<path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="8.5" cy="7" r="4"/><path d="m17 11 2 2 4-4"/>'),
    ("handshake", "Handshake", "Support", '<path d="m11 17 2 2a1 1 0 1 0 3-3"/><path d="m14 14 2.5 2.5a1 1 0 1 0 3-3l-3.88-3.88a3 3 0 0 0-4.24 0l-.88.88a1 1 0 1 1-3-3l2.81-2.81a5.79 5.79 0 0 1 7.06-.87l.47.28a2 2 0 0 0 1.42.25L21 4"/><path d="m21 3 1 11h-2M3 3 2 14l6.5 6.5a1 1 0 1 0 3-3M3 4h8"/>'),
    ("hand", "Hand", "Support", '<path d="M18 11V6a2 2 0 0 0-4 0v5M14 10V4a2 2 0 0 0-4 0v6M10 10.5V6a2 2 0 0 0-4 0v8"/><path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15"/>'),
    ("heart", "Heart", "Support", _HEART),
    ("smile", "Smile", "Support", '<circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><path d="M9 9h.01M15 9h.01"/>'),
    ("eye", "Eye", "Support", '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>'),
    ("search", "Search", "Support", '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>'),
    ("book", "Book", "Support", '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>'),
    ("graduation", "Expert", "Support", '<path d="M22 10 12 5 2 10l10 5z"/><path d="M6 12v5c3 3 9 3 12 0v-5"/>'),
    ("lightbulb", "Idea", "Support", '<path d="M9 18h6M10 22h4"/><path d="M15.09 14c.18-.98.65-1.74 1.41-2.5A4.65 4.65 0 0 0 18 8 6 6 0 0 0 6 8c0 1 .23 2.23 1.5 3.5A4.61 4.61 0 0 1 8.91 14"/>'),
    # --- Nature & materials
    ("leaf", "Leaf", "Nature", '<path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10z"/><path d="M2 21c0-3 1.85-5.36 5.08-6C9.5 14.52 12 13 13 12"/>'),
    ("sprout", "Sprout", "Nature", '<path d="M7 20h10"/><path d="M10 20c5.5-2.5.8-6.4 3-10"/><path d="M9.5 9.4c1.1.8 1.8 2.2 2.3 3.7-2 .4-3.5.4-4.8-.3-1.2-.6-2.3-1.9-3-4.2 2.8-.5 4.4 0 5.5.8z"/><path d="M14.1 6a7 7 0 0 0-1.1 4c1.9-.1 3.3-.6 4.3-1.4 1-1 1.6-2.3 1.7-4.6-2.7.1-4 1-4.9 2z"/>'),
    ("tree", "Tree", "Nature", '<path d="M12 22v-7"/><path d="M17 15H7l3-4H8l4-6 4 6h-2z"/>'),
    ("recycle", "Recycle", "Nature", '<path d="M7 19H4.8a1.8 1.8 0 0 1-1.6-2.7L7.2 9.5"/><path d="M11 19h8.2a1.8 1.8 0 0 0 1.6-2.7l-1.2-2.1"/><path d="m14 16-3 3 3 3"/><path d="M8.3 13.6 7.2 9.5 3.1 10.6"/><path d="m9.3 5.6 1.1-1.9a1.8 1.8 0 0 1 3.1 0l3.9 6.8"/><path d="m13.4 10.5 4.1 1.1 1.1-4.1"/>'),
    ("droplet", "Droplet", "Nature", '<path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z"/>'),
    ("sun", "Sun", "Nature", '<circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>'),
    ("moon", "Moon", "Nature", '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>'),
    ("snowflake", "Snowflake", "Nature", '<path d="M12 2v20M2 12h20M4.93 4.93l14.14 14.14M19.07 4.93 4.93 19.07"/><path d="m9 3 3 2 3-2M9 21l3-2 3 2M3 9l2 3-2 3M21 9l-2 3 2 3"/>'),
    ("flame", "Flame", "Nature", '<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.07-2.14-.22-4.05 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.15.43-2.29 1-3a2.5 2.5 0 0 0 2.5 2.5z"/>'),
    ("feather", "Feather", "Nature", '<path d="M20.24 12.24a6 6 0 0 0-8.49-8.49L5 10.5V19h8.5z"/><path d="M16 8 2 22M17.5 15H9"/>'),
    ("wind", "Wind", "Nature", '<path d="M9.59 4.59A2 2 0 1 1 11 8H2m10.59 11.41A2 2 0 1 0 14 16H2m15.73-8.27A2.5 2.5 0 1 1 19.5 12H2"/>'),
    ("cloud", "Cloud", "Nature", '<path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"/>'),
    ("umbrella", "Umbrella", "Nature", '<path d="M23 12a11.05 11.05 0 0 0-22 0zm-5 7a3 3 0 0 1-6 0v-7"/>'),
    ("mountain", "Mountain", "Nature", '<path d="m8 3 4 8 5-5 5 15H2z"/>'),
    ("wheat", "Wheat", "Nature", '<path d="M2 22 16 8"/><path d="M3.47 12.53 5 11l1.53 1.53a3.5 3.5 0 0 1 0 4.94L5 19l-1.53-1.53a3.5 3.5 0 0 1 0-4.94z"/><path d="M7.47 8.53 9 7l1.53 1.53a3.5 3.5 0 0 1 0 4.94L9 15l-1.53-1.53a3.5 3.5 0 0 1 0-4.94z"/><path d="M11.47 4.53 13 3l1.53 1.53a3.5 3.5 0 0 1 0 4.94L13 11l-1.53-1.53a3.5 3.5 0 0 1 0-4.94z"/><path d="M20 2h2v2a4 4 0 0 1-4 4h-2V6a4 4 0 0 1 4-4z"/>'),
    ("paw", "Paw", "Nature", '<circle cx="11" cy="4" r="2"/><circle cx="18" cy="8" r="2"/><circle cx="20" cy="16" r="2"/><path d="M9 10a5 5 0 0 1 5 5v3.5a3.5 3.5 0 0 1-6.84 1.04Q6.52 17.48 4.46 16.84A3.5 3.5 0 0 1 5.5 10z"/>'),
    ("apple", "Fruit", "Nature", '<path d="M12 20.94c1.5 0 2.75 1.06 4 1.06 3 0 6-8 6-12.22A4.91 4.91 0 0 0 17 5c-2.22 0-4 1.44-5 2-1-.56-2.78-2-5-2a4.9 4.9 0 0 0-5 4.78C2 14 5 22 8 22c1.25 0 2.5-1.06 4-1.06z"/><path d="M10 2c1 .5 2 2 2 5"/>'),
    # --- Health & lifestyle
    ("activity", "Activity", "Lifestyle", '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>'),
    ("heart-pulse", "Heart pulse", "Lifestyle", _HEART + '<path d="M3.5 12h4l1.5-3 3 6 1.5-3h7"/>'),
    ("pill", "Pill", "Lifestyle", '<path d="M10.5 20.5 3.5 13.5a4.95 4.95 0 0 1 7-7l7 7a4.95 4.95 0 0 1-7 7z"/><path d="m8.5 8.5 7 7"/>'),
    ("medical", "Medical", "Lifestyle", '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M12 8v8M8 12h8"/>'),
    ("thermometer", "Thermometer", "Lifestyle", '<path d="M14 14.76V3.5a2.5 2.5 0 0 0-5 0v11.26a4.5 4.5 0 1 0 5 0z"/>'),
    ("dumbbell", "Fitness", "Lifestyle", '<path d="M6.5 6.5v11M17.5 6.5v11M3 9v6M21 9v6M6.5 12h11"/>'),
    ("coffee", "Coffee", "Lifestyle", '<path d="M18 8h1a4 4 0 0 1 0 8h-1"/><path d="M2 8h16v9a4 4 0 0 1-4 4H6a4 4 0 0 1-4-4z"/><path d="M6 1v3M10 1v3M14 1v3"/>'),
    ("utensils", "Food", "Lifestyle", '<path d="M3 2v7c0 1.1.9 2 2 2h4a2 2 0 0 0 2-2V2M7 2v20"/><path d="M21 15V2a5 5 0 0 0-5 5v6c0 1.1.9 2 2 2h3zm0 0v7"/>'),
    ("shirt", "Apparel", "Lifestyle", '<path d="M20.38 3.46 16 2a4 4 0 0 1-8 0L3.62 3.46a2 2 0 0 0-1.34 2.23l.58 3.47a1 1 0 0 0 .99.84H6v10c0 1.1.9 2 2 2h8a2 2 0 0 0 2-2V10h2.15a1 1 0 0 0 .99-.84l.58-3.47a2 2 0 0 0-1.34-2.23z"/>'),
    ("watch", "Watch", "Lifestyle", '<circle cx="12" cy="12" r="7"/><path d="M12 9v3l1.5 1.5M16.51 17.35l-.35 3.83a2 2 0 0 1-2 1.82H9.83a2 2 0 0 1-2-1.82l-.35-3.83m.01-10.7.35-3.83A2 2 0 0 1 9.83 1h4.35a2 2 0 0 1 2 1.82l.35 3.83"/>'),
    ("glasses", "Glasses", "Lifestyle", '<circle cx="6" cy="15" r="4"/><circle cx="18" cy="15" r="4"/><path d="M14 15a2 2 0 0 0-4 0M2.5 13 5 7c.7-1.3 1.4-2 3-2M21.5 13 19 7c-.7-1.3-1.5-2-3-2"/>'),
    ("scissors", "Scissors", "Lifestyle", '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4 8.12 15.88M14.47 14.48 20 20M8.12 8.12 12 12"/>'),
    ("music", "Music", "Lifestyle", '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>'),
    # --- Tech & tools
    ("cpu", "Chip", "Tech", '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3"/>'),
    ("battery", "Battery", "Tech", '<rect x="1" y="6" width="18" height="12" rx="2"/><path d="M23 13v-2"/><path d="M5 10v4M9 10v4"/>'),
    ("battery-charging", "Charging", "Tech", '<path d="M5 18H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h3.19M15 6h2a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-3.19"/><path d="M23 13v-2"/><path d="m11 6-4 6h6l-4 6"/>'),
    ("power", "Power", "Tech", '<path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><path d="M12 2v10"/>'),
    ("wifi", "Wi-Fi", "Tech", '<path d="M5 12.55a11 11 0 0 1 14.08 0M1.42 9a16 16 0 0 1 21.16 0M8.53 16.11a6 6 0 0 1 6.95 0M12 20h.01"/>'),
    ("bluetooth", "Bluetooth", "Tech", '<path d="m6.5 6.5 11 11L12 23V1l5.5 5.5-11 11"/>'),
    ("smartphone", "Phone device", "Tech", '<rect x="5" y="2" width="14" height="20" rx="2"/><path d="M12 18h.01"/>'),
    ("monitor", "Monitor", "Tech", '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>'),
    ("tv", "TV", "Tech", '<rect x="2" y="7" width="20" height="15" rx="2"/><path d="m17 2-5 5-5-5"/>'),
    ("camera", "Camera", "Tech", '<path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/>'),
    ("headphones", "Headphones", "Tech", '<path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/>'),
    ("download", "Download", "Tech", '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5M12 15V3"/>'),
    ("wrench", "Wrench", "Tech", '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>'),
    ("settings", "Settings", "Tech", '<circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="7"/><path d="M12 1v3M12 20v3M4.22 4.22l2.12 2.12M17.66 17.66l2.12 2.12M1 12h3M20 12h3M4.22 19.78l2.12-2.12M17.66 6.34l2.12-2.12"/>'),
    ("hammer", "Hammer", "Tech", '<path d="m15 12-8.5 8.5a2.12 2.12 0 1 1-3-3L12 9"/><path d="M17.64 15 22 10.64"/><path d="m20.91 11.7-1.25-1.25a2.4 2.4 0 0 1-.71-1.71V7.86L16.06 5a5.7 5.7 0 0 0-4-1.66H9l.92.82A6.18 6.18 0 0 1 12 8.4v1.56l2 2h2.47l2.26 1.91"/>'),
    ("ruler", "Ruler", "Tech", '<path d="M21.3 8.7 8.7 21.3a1 1 0 0 1-1.4 0l-4.6-4.6a1 1 0 0 1 0-1.4L15.3 2.7a1 1 0 0 1 1.4 0l4.6 4.6a1 1 0 0 1 0 1.4z"/><path d="m7.5 10.5 2 2M10.5 7.5l2 2M13.5 4.5l2 2M4.5 13.5l2 2"/>'),
    ("scale", "Scale", "Tech", '<path d="M16 16.5 19 9l3 7.5a3.5 3.5 0 0 1-6 0z"/><path d="M2 16.5 5 9l3 7.5a3.5 3.5 0 0 1-6 0z"/><path d="M7 21h10M12 3v18M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2"/>'),
    ("grid", "Grid", "Tech", '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>'),
]

ICON_MAP: dict[str, str] = {key: inner for key, _label, _group, inner in ICONS}

_SVG_OPEN = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"'
)


def svg(key: str, css_class: str = "") -> str:
    """The full ``<svg>`` for ``key`` (safe markup from this module only), or
    ``""`` for an unknown key."""
    inner = ICON_MAP.get(str(key or ""))
    if not inner:
        return ""
    cls = f' class="{css_class}"' if css_class else ""
    return mark_safe(f"{_SVG_OPEN}{cls}>{inner}</svg>")


def catalog() -> list[dict]:
    """``[{key, label, group}]`` for the picker (markup is built client-side
    from :data:`ICON_MAP`, sent once as JSON)."""
    return [{"key": k, "label": label, "group": group} for k, label, group, _i in ICONS]
