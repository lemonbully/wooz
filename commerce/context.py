"""Shared helpers for the store-management surface.

Every management page is scoped to one store the signed-in user owns. These
helpers resolve that store (404 for anything not theirs), and build the
navigation model so the sidebar knows which areas apply to a D2C store, a B2B
store, or one that is both.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.shortcuts import get_object_or_404

from tenancy.models import Site


def get_owned_site(request, site_id) -> Site:
    """Return the owner's store or raise 404, and mark it the active profile.

    Scoping is by ownership: a user can only reach stores where they are the
    ``owner``. Soft-deleted stores are invisible. Every store is its own
    isolated profile — its data is always queried through ``.for_site(site)``,
    so two stores on one account can never mix. Recording the active store in
    the session lets the dashboard resume it and the switcher show it.
    """
    site = get_object_or_404(
        Site.objects.filter(is_deleted=False), pk=site_id, owner=request.user
    )
    try:
        request.session["active_site_id"] = str(site.pk)
    except Exception:
        # A missing/again read-only session must never break a store page.
        pass
    return site


def owner_sites(request):
    """Every store (profile) the signed-in owner controls, newest first."""
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return []
    return list(request.user.sites.filter(is_deleted=False).order_by("-created_at"))


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    url_name: str
    icon: str  # key into the icon set in templates/store/_icons.html
    audience: str = "all"  # "all", "d2c", or "b2b"


@dataclass(frozen=True)
class NavGroup:
    label: str
    items: list = field(default_factory=list)


# The full store navigation. Each former "Manage" tab is its own page here.
# ``audience`` hides items that don't apply to the store's business model.
NAV_GROUPS = [
    NavGroup(
        "Storefront",
        [
            NavItem("editor", "Edit content", "store:editor", "layout"),
            NavItem("domain", "Custom domain", "store:domain", "globe"),
        ],
    ),
    NavGroup(
        "Operate",
        [
            NavItem("overview", "Overview", "store:overview", "grid"),
            NavItem("orders", "Orders", "store:orders", "bag"),
            NavItem("requests", "Requests", "store:requests", "undo"),
            NavItem("enquiries", "Enquiries", "store:enquiries", "inbox"),
            NavItem("customers", "Customers", "store:customers", "users"),
        ],
    ),
    NavGroup(
        "Catalogue",
        [
            NavItem("products", "Products", "store:products", "box"),
            NavItem("categories", "Categories", "store:categories", "layers"),
            NavItem("inventory", "Inventory", "store:inventory", "warehouse"),
            NavItem("out_of_stock", "Out of stock", "store:out_of_stock", "alert"),
            NavItem("reviews", "Reviews", "store:reviews", "star", audience="d2c"),
        ],
    ),
    NavGroup(
        "Grow",
        [
            NavItem("analytics", "Analytics", "store:analytics", "chart"),
            NavItem("coupons", "Discounts", "store:coupons", "tag"),
            NavItem("collaborations", "Collaborations", "store:collaborations", "handshake", audience="d2c"),
            NavItem("tracking", "Tracking & ads", "store:tracking", "bullhorn"),
            NavItem("google_merchant", "Google Merchant", "store:google_merchant", "google"),
        ],
    ),
    NavGroup(
        "Money & delivery",
        [
            NavItem("payments", "Payments", "store:payments", "card"),
            NavItem("shipping", "Shipping", "store:shipping", "truck"),
            NavItem("bills", "Bills", "store:bills", "invoice"),
        ],
    ),
    NavGroup(
        "Store",
        [
            NavItem("settings", "Settings", "store:settings", "cog"),
        ],
    ),
]


def visible_groups(site: Site):
    """Filter the navigation to what applies to this store's business model."""
    groups = []
    for group in NAV_GROUPS:
        items = [i for i in group.items if _applies(i, site)]
        if items:
            groups.append({"label": group.label, "items": items})
    return groups


def _applies(item: NavItem, site: Site) -> bool:
    if item.audience == "all":
        return True
    if item.audience == "d2c":
        return site.is_d2c
    if item.audience == "b2b":
        return site.is_b2b
    return True


def store_context(request, site, active: str) -> dict:
    """Common template context for every store page."""
    return {
        "site": site,
        "nav_active": active,
        "nav_groups": visible_groups(site),
        # The profile switcher's store list comes from the dashboard_nav context
        # processor (profile_sites), so it need not be rebuilt per store page.
    }
