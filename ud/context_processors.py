"""Context available to every owner-dashboard page.

Two things every authenticated app page needs, resolved once here so no view has
to assemble them:

* The account sidebar (``ud/_nav.html``) links into a *store's* management pages,
  which are per-store routes; ``primary_*`` gives it a sensible default target.
* The top-bar profile switcher needs the owner's full list of stores and which
  one is "current"  the store in the URL if on a store page, else the last one
  worked on (session, falling back to the ``active_site_id`` persisted on the
  account so it survives logout/login). ``profile_*`` provides that so the
  switcher, the account identity and the current store are present on every
  page, and the whole thing is gated on authentication.
"""

from __future__ import annotations

from commerce import staff as staff_mod
from commerce.context import active_site_id, visible_groups
from commerce.features import allowed_features_for_owner


def bell_href(url: str, request) -> str:
    """Where a notification's "Open" goes, from the request being served.

    Platform code stores app-surface links as absolute URLs built without a
    request (Celery, the admin), e.g. ``https://app.onewooz.com/business-hub/``.
    Rebuild those on the live request's origin so they work on whatever host
    and port (or, in local development, path prefix) the owner is using.
    Relative links and links to other hosts are returned unchanged.
    """
    from urllib.parse import parse_qsl, urlencode, urlsplit

    from django.conf import settings

    from core import links
    from core.hosts import Surface

    url = (url or "").strip()
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme not in ("", "http", "https"):
        return ""
    if not parts.netloc:
        return url
    if (parts.hostname or "").lower() != str(settings.APP_HOST).lower():
        return url
    query, fragment = parts.query, parts.fragment
    # Older ticket-reply links pointed at the Business Hub's "#support" panel;
    # the tickets now live in a popup that ``?tickets=1`` opens.
    if fragment == "support" and parts.path.rstrip("/").endswith("business-hub"):
        pairs = [(k, v) for k, v in parse_qsl(query) if k != "tickets"] + [("tickets", "1")]
        query, fragment = urlencode(pairs), ""
    href = links.surface_url(Surface.APP, parts.path or "/", request)
    if query:
        href += "?" + query
    if fragment:
        href += "#" + fragment
    return href


def dashboard_nav(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        # Anonymous app pages (sign-in/up) never render the dashboard chrome,
        # but return an explicit flag so any guard can rely on it.
        return {"is_owner_authenticated": False}

    owned = list(user.sites.filter(is_deleted=False).order_by("-created_at"))
    owned_ids = {s.id for s in owned}
    for s in owned:
        s.is_staff_site = False
    # Stores the user can reach as a staff member (never ones they own) fold into
    # the same switcher, flagged so owner-only actions don't offer on them.
    staff_sites = staff_mod.accessible_sites(user)
    for s in staff_sites:
        s.is_staff_site = True
    sites = owned + staff_sites

    # The switcher offers a one-click "preview storefront" per store; the public
    # URL must carry the dev path prefix, which only a request can supply  so
    # resolve it here rather than calling the method arg-less in the template.
    for s in sites:
        s.preview_url = s.storefront_url(request)

    # Which store is "current": the one addressed by the URL, else the last one
    # the owner worked on (recorded in the session by get_owned_site).
    current = None
    match = getattr(request, "resolver_match", None)
    if match is not None:
        site_id = match.kwargs.get("site_id")
        if site_id is not None:
            current = next((s for s in sites if str(s.id) == str(site_id)), None)
    if current is None:
        # The active store: session first, then the choice persisted on the
        # account  so a store switched to before signing out is still current
        # after signing back in, until the owner opens another.
        active_id = active_site_id(request)
        if active_id:
            current = next((s for s in sites if str(s.id) == str(active_id)), None)

    # The account sidebar's store links point at the store the owner is actually
    # working on  the switched-to (active) profile  and only fall back to the
    # most recent store when none has been chosen yet this session. This is what
    # keeps a switched profile "sticky" across visits to the dashboard.
    primary = current or (sites[0] if sites else None)
    primary_owned = bool(primary is not None and primary.id in owned_ids)
    # The account sidebar's store links carry the same "Upgrade" locking as the
    # store surface's own sidebar: pass the STORE OWNER's allowed-feature set so a
    # gated area is flagged ``locked`` here too (the owner's plan governs the
    # store whether the owner or a staff member is browsing). ``None`` = nothing
    # gated. For a staff site the links are also restricted to the pages the
    # member was granted.
    primary_nav_groups = None
    if primary is not None:
        allowed = allowed_features_for_owner(getattr(primary, "owner", None))
        staff_pages = None
        if not primary_owned:
            access = staff_mod.access_for(request, primary)
            staff_pages = access.allowed_pages() if access is not None else set()
        primary_nav_groups = visible_groups(primary, allowed, staff_pages)
        # The same unread counters as the store sidebar (Orders, Requests …).
        try:
            from commerce import nav_counts

            keys = [i["key"] for g in primary_nav_groups for i in g["items"] if not i["locked"]]
            nav_counts.attach(primary_nav_groups, nav_counts.counts_for(request, primary, keys))
        except Exception:
            pass

    # Whether the owner has used every store their subscription allows. Badges
    # the "Create a store" nav link with an Upgrade tag (the create view itself
    # refuses past the cap). Best-effort  never blank the chrome over it.
    store_limit_reached = False
    try:
        from billing import subscriptions

        store_limit_reached = subscriptions.store_limit_reached(user, used=len(owned))
    except Exception:
        pass

    # Top-navbar bell: the owner's most recent notifications + unread count.
    # Best-effort  a query hiccup here must never blank the whole chrome.
    notif_items = []
    notif_unread = 0
    try:
        from accounts.models import Notification

        notif_items = list(
            Notification.objects.filter(user=user).order_by("-created_at")[:15]
        )
        for n in notif_items:
            url = n.action_url
            if not url and n.site_id_ref:
                # Older store-activity notifications were saved without a link:
                # work it out from the event they describe.
                try:
                    from commerce.bell_links import url_for_headline

                    url = url_for_headline(n.headline, n.site_id_ref)
                except Exception:
                    url = ""
            n.open_href = bell_href(url, request)
            # "Open" goes straight to where the notification happened (a ticket
            # reply opens the Business Hub with that ticket's popup). Only a
            # notification with nowhere to go opens a detail popup instead.
            n.opens_directly = bool(n.open_href)
        notif_unread = Notification.objects.filter(user=user, is_read=False).count()
    except Exception:
        pass

    return {
        "is_owner_authenticated": True,
        # Profile switcher + identity, available on every authenticated page.
        "profile_sites": sites,
        "profile_current": primary,
        "profile_current_owned": primary_owned,
        "profile_count": len(sites),
        # Account sidebar store target = the active profile.
        "primary_site": primary,
        "primary_nav_groups": primary_nav_groups,
        "sites_count": len(sites),
        # "Create a store" nav link: locked with an Upgrade tag past the cap.
        "store_limit_reached": store_limit_reached,
        # Notification bell.
        "notif_items": notif_items,
        "notif_unread": notif_unread,
    }
