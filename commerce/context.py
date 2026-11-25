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
    """Return a store the signed-in user may manage, or raise 404.

    Access is granted to the store **owner** and to any active **staff member**
    the owner has added (see :mod:`commerce.staff`). The resolved access 
    owner-full or the staff member's per-page grant  is stashed on
    ``request.store_access`` so the nav filter and the staff gate middleware read
    it without another lookup. A staff member's page/action limits are then
    enforced structurally by :class:`commerce.middleware.StaffAccessGateMiddleware`.

    Soft-deleted stores are invisible. Opening a store marks it the active
    profile so the dashboard resumes it and the switcher shows it.
    """
    from django.http import Http404

    from . import staff as staff_mod

    site = get_object_or_404(Site.objects.filter(is_deleted=False), pk=site_id)
    access = staff_mod.access_for(request, site)
    if access is None:
        raise Http404("No such store.")
    request.store_access = access
    remember_active_site(request, site)
    return site


def remember_active_site(request, site) -> None:
    """Record ``site`` as the active store  in the session (fast, per browser)
    and, persistently, on the account so the choice survives logout/login until
    another store is opened. The account row is written only when the value
    actually changes, so a normal store page load issues no extra write."""
    sid = str(site.pk)
    try:
        request.session["active_site_id"] = sid
    except Exception:
        # A missing / read-only session must never break a store page.
        pass
    user = getattr(request, "user", None)
    if not (user and getattr(user, "is_authenticated", False)):
        return
    if str(getattr(user, "active_site_id", "") or "") == sid:
        return
    try:
        # request.user is a SimpleLazyObject; __class__ proxies to the real User
        # model, whereas type() would return the lazy wrapper.
        user.__class__.objects.filter(pk=user.pk).update(active_site_id=site.pk)
        user.active_site_id = site.pk
    except Exception:
        # Persistence is best-effort; the session copy still carries the choice.
        pass


LAST_PAGE_MAX = 500


def remember_last_page(request, site_id: str, path: str) -> None:
    """Record the store page the owner was last on (session + account)."""
    if not path or not path.startswith("/") or path.startswith("//"):
        return
    if len(path) > LAST_PAGE_MAX:
        path = path.split("?", 1)[0][:LAST_PAGE_MAX]
    value = f"{site_id}|{path}"
    try:
        request.session["active_site_path"] = value
    except Exception:
        pass
    user = getattr(request, "user", None)
    if not (user and getattr(user, "is_authenticated", False)):
        return
    if (getattr(user, "active_site_path", "") or "") == value:
        return
    try:
        user.__class__.objects.filter(pk=user.pk).update(active_site_path=value)
        user.active_site_path = value
    except Exception:
        pass


def resume_url(request, site) -> str:
    """Where "Continue" on the dashboard should go for ``site``: the exact
    store page the owner was last on, else the store's overview.

    The remembered value is ``"<site id>|<path>"``; it is used only when it
    belongs to this store and is a same-origin path, so a stale or foreign
    value harmlessly falls back to the overview.
    """
    from django.urls import reverse

    fallback = reverse("store:overview", args=[site.pk])
    candidates = []
    try:
        candidates.append(request.session.get("active_site_path") or "")
    except Exception:
        pass
    user = getattr(request, "user", None)
    candidates.append(getattr(user, "active_site_path", "") or "")
    sid = str(site.pk)
    for value in candidates:
        owner, sep, path = value.partition("|")
        if not sep or owner != sid:
            continue
        if path.startswith("/") and not path.startswith("//") and sid in path:
            return path
    return fallback


def active_site_id(request) -> str | None:
    """The id of the store the owner is currently working on.

    The session first (set the moment a store page is opened), then the value
    persisted on the account, which outlives the session  so after a fresh
    login the last-chosen store is still selected. Callers validate the id
    against the owner's live stores, so a stale id harmlessly resolves to None.
    """
    sid = request.session.get("active_site_id")
    if sid:
        return str(sid)
    user = getattr(request, "user", None)
    if user and getattr(user, "is_authenticated", False):
        persisted = getattr(user, "active_site_id", None)
        if persisted:
            return str(persisted)
    return None


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
            NavItem("media", "Media library", "store:media", "image"),
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
            NavItem("subscribers", "Subscribers", "store:subscribers", "mail"),
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
            NavItem("email_marketing", "Email marketing", "store:email_marketing", "mail"),
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


def visible_groups(site: Site, allowed=None, staff_pages=None):
    """Navigation for this store's business model, each item flagged locked.

    ``allowed`` is the owner's permitted feature set (from
    :mod:`commerce.features`); ``None`` means everything is allowed. Items are
    returned as plain dicts (not the frozen ``NavItem``) so each carries a
    ``locked`` flag the sidebar renders as an "Upgrade" badge.

    ``staff_pages`` restricts the sidebar to a staff member's granted pages
    (``None`` = owner, so every page shows). A staff member simply never sees a
    page they weren't given  Settings included, since it is never assignable.
    """
    groups = []
    for group in NAV_GROUPS:
        items = []
        for i in group.items:
            if not _applies(i, site):
                continue
            if staff_pages is not None and i.key not in staff_pages:
                continue
            items.append(
                {
                    "key": i.key,
                    "label": i.label,
                    "url_name": i.url_name,
                    "icon": i.icon,
                    "locked": bool(allowed is not None and i.key not in allowed),
                }
            )
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


def feature_choices():
    """(key, label) for every gateable dashboard feature  for the admin form."""
    return [(i.key, i.label) for g in NAV_GROUPS for i in g.items]


def feature_label(key: str) -> str:
    """Human label for a feature key (falls back to a tidied key)."""
    for g in NAV_GROUPS:
        for i in g.items:
            if i.key == key:
                return i.label
    return (key or "").replace("_", " ").title()


def merge_app_nav(request, owner, site, groups: list) -> None:
    """Add the owner's installed apps to ``groups`` (store and account sidebars)."""
    try:
        from marketplace import api as mkt_api

        extra = mkt_api.sidebar_groups(request, owner, site)
    except Exception:
        return
    if not extra:
        return
    for g in groups:
        adds = extra.pop(g["label"], None)
        if adds:
            g["items"].extend(sorted(adds, key=lambda i: (i.get("order", 100), i["label"])))
    leftover = [
        {"label": label, "items": sorted(items, key=lambda i: (i.get("order", 100), i["label"]))}
        for label, items in extra.items() if items
    ]
    if leftover:
        # Own groups ("Apps") come last, right above the sidebar's own Apps link.
        groups.extend(leftover)


def store_context(request, site, active: str) -> dict:
    """Common template context for every store page."""
    from django.urls import reverse

    from . import features
    from . import staff as staff_mod

    # Plan gating is the STORE's, so it always reads the store owner's
    # subscription  whether the owner or a staff member is browsing.
    owner = getattr(site, "owner", None)
    allowed = features.allowed_features_for_owner(owner)
    # Staff members see a sidebar restricted to the pages they were granted;
    # the owner (or an unresolved access) sees everything applicable.
    access = getattr(request, "store_access", None) or staff_mod.access_for(request, site)
    staff_pages = access.allowed_pages() if access is not None else None
    groups = visible_groups(site, allowed, staff_pages)
    # OneWooz Apps placed in the sidebar by installed apps (owner only). Read
    # through the marketplace's fail-soft facade: any marketplace failure just
    # leaves the sidebar as it was.
    if access is None or access.is_owner:
        merge_app_nav(request, owner, site, groups)
    # Unread counters (Orders, Requests, Reviews …): opening a counted page
    # clears its own counter first, then every visible one is counted.
    from . import nav_counts

    if active and not (allowed is not None and active not in allowed):
        nav_counts.mark_seen(request, site, active)
    visible_keys = [i["key"] for g in groups for i in g["items"] if not i["locked"]]
    nav_counts.attach(groups, nav_counts.counts_for(request, site, visible_keys))
    return {
        "site": site,
        "nav_active": active,
        "nav_groups": groups,
        # Access for the current user on this store  templates can hide action
        # controls a staff member may not use (owner → is_owner True).
        "store_access": access,
        "is_store_owner": bool(access is None or access.is_owner),
        # Whether the current page's own feature is gated  the shell falls back
        # to the upgrade wall on this, though the middleware normally intercepts
        # a locked page before its view even runs.
        "feature_locked": bool(allowed is not None and active and active not in allowed),
        "locked_feature_label": feature_label(active),
        # Upgrading happens in-dashboard now, not out on the marketing pricing
        # page: the store surface shares the app urlconf, so this resolves (and
        # carries the dev script prefix) exactly like any other dashboard link.
        "upgrade_url": reverse("ud:upgrade"),
        # The profile switcher's store list comes from the dashboard_nav context
        # processor (profile_sites), so it need not be rebuilt per store page.
    }
