"""Context available to every owner-dashboard page.

Two things every authenticated app page needs, resolved once here so no view has
to assemble them:

* The account sidebar (``ud/_nav.html``) links into a *store's* management pages,
  which are per-store routes; ``primary_*`` gives it a sensible default target.
* The top-bar profile switcher needs the owner's full list of stores and which
  one is "current" — the store in the URL if on a store page, else the last one
  worked on (``active_site_id`` in the session). ``profile_*`` provides that so
  the switcher, the account identity and the current store are present on every
  page, and the whole thing is gated on authentication.
"""

from __future__ import annotations

from commerce.context import visible_groups


def dashboard_nav(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        # Anonymous app pages (sign-in/up) never render the dashboard chrome,
        # but return an explicit flag so any guard can rely on it.
        return {"is_owner_authenticated": False}

    sites = list(user.sites.filter(is_deleted=False).order_by("-created_at"))

    # Which store is "current": the one addressed by the URL, else the last one
    # the owner worked on (recorded in the session by get_owned_site).
    current = None
    match = getattr(request, "resolver_match", None)
    if match is not None:
        site_id = match.kwargs.get("site_id")
        if site_id is not None:
            current = next((s for s in sites if str(s.id) == str(site_id)), None)
    if current is None:
        active_id = request.session.get("active_site_id")
        if active_id:
            current = next((s for s in sites if str(s.id) == str(active_id)), None)

    # The account sidebar's store links point at the store the owner is actually
    # working on — the switched-to (active) profile — and only fall back to the
    # most recent store when none has been chosen yet this session. This is what
    # keeps a switched profile "sticky" across visits to the dashboard.
    primary = current or (sites[0] if sites else None)
    return {
        "is_owner_authenticated": True,
        # Profile switcher + identity, available on every authenticated page.
        "profile_sites": sites,
        "profile_current": primary,
        "profile_count": len(sites),
        # Account sidebar store target = the active profile.
        "primary_site": primary,
        "primary_nav_groups": visible_groups(primary) if primary else None,
        "sites_count": len(sites),
    }
