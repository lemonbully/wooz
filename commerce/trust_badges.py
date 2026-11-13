"""Trust badges offered on products: the platform set plus each store's own.

* The **platform set** is the admin-managed :class:`~commerce.models.TrustBadge`
  catalogue (or the built-in names when none is configured), minus the badges
  OneWooz has retired (:data:`REMOVED_BADGES`).
* A store's **own badges** are made by the owner from the product form's "+"
  tile: a name of at most two words and an icon chosen from the fixed set in
  :mod:`commerce.trust_icons` (no uploads). Stored per store at
  ``site.content["custom_trust_badges"] = [{"name", "icon"}]``  no migration.

A product stores the badge *names* it shows (``Product.trusted_icons``, max 4).
"""

from __future__ import annotations

import re

from . import trust_icons

#: Built-in fallback names (used only when no TrustBadge rows exist).
DEFAULT_BADGES = [
    "Pan India Delivery", "1 year warranty",
    "Secure Payment", "Free Shipping", "Fast Delivery", "Secure Packaging",
]

#: Retired badges: never offered, dropped on save and hidden on the storefront,
#: whether they come from the admin catalogue or an older product.
#: "Non returnable" was retired with the non-returnable return policy: a
#: product is returnable or replacement-only, and the product page shows a
#: return-policy badge linked to the refund policy instead.
REMOVED_BADGES = frozenset({
    "6 month warranty", "easy returns", "free return", "cash on delivery",
    "non returnable", "nonreturnable", "not returnable", "no returns", "no return",
})

MAX_PER_PRODUCT = 4

#: Return-policy badges, always offered (whatever the admin catalogue holds):
#: ``(name, icon key in commerce.trust_icons, Product.return_type)``. Ticking
#: one sets the product's return policy to match (the product form locks the
#: field to it), and only one of them can be on a product at a time.
RETURN_POLICY_BADGES = (
    ("Returnable", "rotate-ccw", "returnable"),
    ("Replacement Only", "repeat", "replacement"),
)
_RETURN_POLICY_BY_NAME = {
    "returnable": "returnable",
    "replacement only": "replacement",
    "replacement": "replacement",
}
MAX_CUSTOM_PER_STORE = 40
NAME_MAX_WORDS = 2
NAME_MAX_CHARS = 24
_NAME_RE = re.compile(r"^[\w&+'./%-]+( [\w&+'./%-]+)?$", re.UNICODE)


def norm(name) -> str:
    """Comparison form of a badge name: lowercase, hyphens as spaces, single-spaced."""
    return " ".join(str(name or "").replace("-", " ").replace("_", " ").split()).lower()


def is_removed(name) -> bool:
    return norm(name) in REMOVED_BADGES


#: How some badges are written for shoppers and owners, whatever capitalisation
#: or hyphenation the stored name has (stored names stay as they are, so
#: products that already show the badge keep it ticked).
_DISPLAY_NAMES = {
    "1 year warranty": "1-Year Warranty",
    "one year warranty": "1-Year Warranty",
    "non returnable": "Non-Returnable",
    "nonreturnable": "Non-Returnable",
}


def display_name(name) -> str:
    """The badge's name as shown on the product form and the storefront."""
    return _DISPLAY_NAMES.get(norm(name), " ".join(str(name or "").split()))


def is_warranty(name) -> bool:
    return "warranty" in norm(name)


#: Badge names that state the product cannot be returned.
_NON_RETURNABLE = frozenset({"non returnable", "not returnable", "no returns", "no return", "nonreturnable"})


def is_non_returnable(name) -> bool:
    """A "Non returnable" badge is the product's return policy: it locks the
    product's return type to non-returnable (0 return days)."""
    return norm(name) in _NON_RETURNABLE


def return_policy_of(name) -> str:
    """``"returnable"`` / ``"replacement"`` for a return-policy badge, else ``""``."""
    return _RETURN_POLICY_BY_NAME.get(norm(name), "")


def return_policy_from(names) -> str:
    """The return policy set by the first return-policy badge in ``names``."""
    for n in names or []:
        rt = return_policy_of(n)
        if rt:
            return rt
    return ""


def clean_name(raw) -> tuple[str, str | None]:
    """``(name, error)``: the tidied badge name, or an error message."""
    name = " ".join(str(raw or "").split())
    if not name:
        return "", "Enter a badge name."
    if len(name.split(" ")) > NAME_MAX_WORDS:
        return name, f"Keep it to {NAME_MAX_WORDS} words at most."
    if len(name) > NAME_MAX_CHARS:
        return name, f"Keep it to {NAME_MAX_CHARS} characters at most."
    if not _NAME_RE.match(name):
        return name, "Use letters, numbers and simple punctuation only."
    return name, None


def custom_badges(site) -> list[dict]:
    """The store's own badges, ``[{name, icon}]`` (valid icons only)."""
    raw = (site.content or {}).get("custom_trust_badges") if site is not None else None
    out = []
    for b in raw if isinstance(raw, list) else []:
        if isinstance(b, dict) and b.get("name") and b.get("icon") in trust_icons.ICON_MAP:
            out.append({"name": str(b["name"]), "icon": str(b["icon"])})
    return out


def platform_badges() -> list[dict]:
    """The platform set as ``[{name, image}]``, retired badges removed."""
    from .models import TrustBadge

    badges = list(TrustBadge.objects.filter(is_active=True).order_by("order", "name"))
    if badges:
        items = []
        for b in badges:
            try:
                image = b.image.url if b.image else ""
            except Exception:
                image = ""
            items.append({"name": b.name, "image": image})
    else:
        items = [{"name": n, "image": ""} for n in DEFAULT_BADGES]
    items = [b for b in items if not is_removed(b["name"])]
    # The return-policy badges are always offered, first.
    taken = {norm(b["name"]) for b in items}
    builtin = [
        {"name": name, "image": ""} for name, _icon, _rt in RETURN_POLICY_BADGES if norm(name) not in taken
    ]
    return builtin + items


def options(site) -> list[dict]:
    """Everything the product form's picker lists:
    ``[{name, image, icon, svg, custom}]`` (platform badges first)."""
    builtin_icons = {norm(name): icon for name, icon, _rt in RETURN_POLICY_BADGES}
    out = []
    for b in platform_badges():
        icon = "" if b.get("image") else builtin_icons.get(norm(b["name"]), "")
        out.append({
            **b, "label": display_name(b["name"]), "icon": icon,
            "svg": trust_icons.svg(icon) if icon else "", "custom": False,
            "return_policy": return_policy_of(b["name"]),
        })
    taken = {norm(b["name"]) for b in out}
    for b in custom_badges(site):
        if norm(b["name"]) in taken:
            continue
        taken.add(norm(b["name"]))
        out.append({
            "name": b["name"], "label": display_name(b["name"]), "image": "", "icon": b["icon"],
            "svg": trust_icons.svg(b["icon"]), "custom": True,
        })
    return out


def clean_selection(names) -> list[str]:
    """A product's chosen badge names: retired ones dropped, de-duplicated,
    at most one return-policy badge, capped at :data:`MAX_PER_PRODUCT`."""
    out, seen = [], set()
    has_policy = False
    for n in names or []:
        n = " ".join(str(n or "").split())
        if not n or is_removed(n) or norm(n) in seen:
            continue
        if return_policy_of(n):
            if has_policy:
                continue
            has_policy = True
        seen.add(norm(n))
        out.append(n)
    return out[:MAX_PER_PRODUCT]


def icon_svg_map(site) -> dict:
    """``{badge name: <svg>}`` for the store's own badges (storefront render)."""
    out = {
        name: trust_icons.svg(icon, "w-6 h-6 sm:w-5 sm:h-5") for name, icon, _rt in RETURN_POLICY_BADGES
    }
    out.update({
        b["name"]: trust_icons.svg(b["icon"], "w-6 h-6 sm:w-5 sm:h-5")
        for b in custom_badges(site)
    })
    return out
