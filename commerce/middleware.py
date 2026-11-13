"""Store-dashboard access gates.

Two independent gates run before any store view:

* :class:`PlanFeatureGateMiddleware`  a store's available areas follow the
  **owner's** subscription, so a free-plan owner's locked areas show the upgrade
  wall (and refuse actions), whoever is browsing.
* :class:`StaffAccessGateMiddleware`  a **staff member** (a non-owner the owner
  added) is confined to the pages and actions they were granted
  (:mod:`commerce.staff`); everything else is refused, so a staff member can
  neither see nor act on anything outside their grant.

Both read the site once (memoised on the request) and are no-ops on every
surface but the store dashboard, so they add at most a couple of indexed lookups
to a store page and nothing elsewhere.
"""

from __future__ import annotations

from django.http import JsonResponse
from django.shortcuts import render

from . import features
from . import staff as staff_mod
from .context import feature_label, store_context


def _load_site(request, site_id):
    """Fetch (and memoise) the store for this request, or ``None``."""
    cached = getattr(request, "_gate_site", None)
    if cached is not None and str(cached.pk) == str(site_id):
        return cached
    from tenancy.models import Site

    site = (
        Site.objects.filter(pk=site_id, is_deleted=False)
        .select_related("owner")
        .first()
    )
    if site is not None:
        request._gate_site = site
    return site


def _is_ajax(request) -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


class PlanFeatureGateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = getattr(request, "resolver_match", None)
        if match is None or (match.namespace or "") != "store":
            return None

        feature = features.feature_for_urlname(match.url_name)
        if not feature:
            return None

        user = getattr(request, "user", None)
        site_id = view_kwargs.get("site_id")
        if not site_id or not (user and getattr(user, "is_authenticated", False)):
            return None

        site = _load_site(request, site_id)
        if site is None:
            return None  # gone  let the view 404.

        # Gating is the store's, so it reads the OWNER's plan whether the owner
        # or a staff member is browsing.
        allowed = features.allowed_features_for_owner(getattr(site, "owner", None))
        if allowed is None or feature in allowed:
            return None  # not gated

        if request.method != "GET" or _is_ajax(request):
            return JsonResponse(
                {
                    "ok": False,
                    "upgrade_required": True,
                    "error": "This feature isn't included in your plan. Upgrade to use it.",
                },
                status=402,
            )

        ctx = store_context(request, site, feature)
        ctx["locked_feature_label"] = feature_label(feature)
        return render(request, "store/_locked.html", ctx, status=402)


class StaffAccessGateMiddleware:
    """Confine a staff member to the pages/actions the owner granted them."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = getattr(request, "resolver_match", None)
        if match is None or (match.namespace or "") != "store":
            return None

        user = getattr(request, "user", None)
        site_id = view_kwargs.get("site_id")
        if not site_id or not (user and getattr(user, "is_authenticated", False)):
            return None

        site = _load_site(request, site_id)
        if site is None:
            return None
        if getattr(site, "owner_id", None) == user.id:
            return None  # the owner is never restricted on their own store.

        access = staff_mod.access_for(request, site)
        if access is None:
            return None  # no access at all  the view's get_owned_site 404s.
        # Cache so the view's get_owned_site reuses it.
        request.store_access = access

        url_name = match.url_name
        page = staff_mod.page_for_urlname(url_name)
        allowed_pages = access.allowed_pages() or set()
        if not page or page not in allowed_pages:
            return self._deny(request, site)
        action = staff_mod.required_action(url_name, request.method)
        if not access.can(page, action):
            return self._deny(request, site)
        return None

    def _deny(self, request, site):
        if request.method != "GET" or _is_ajax(request):
            return JsonResponse(
                {
                    "ok": False,
                    "forbidden": True,
                    "error": "You don't have permission to do that on this store.",
                },
                status=403,
            )
        ctx = store_context(request, site, "")
        return render(request, "store/_no_access.html", ctx, status=403)


class LastStorePageMiddleware:
    """Remember the store page the owner was last on, for "Resume where you
    left off" on the dashboard.

    Only a successfully rendered, full-page GET of a store-dashboard route counts
    (no AJAX, no POST, no export/download), so "Continue" always lands on a real
    page. The path is kept in the session and, so it survives logout/login, on
    the account (written only when it changes). The dashboard re-validates it
    against the active store before linking to it  see
    :func:`commerce.context.resume_url`.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            self._remember(request, response)
        except Exception:
            # Bookkeeping must never break a page.
            pass
        return response

    @staticmethod
    def _remember(request, response):
        if request.method != "GET" or _is_ajax(request):
            return
        if response.status_code != 200 or getattr(response, "streaming", False):
            return
        if "text/html" not in (response.get("Content-Type") or ""):
            return
        if response.get("Content-Disposition"):
            return
        match = getattr(request, "resolver_match", None)
        if match is None or (match.namespace or "") != "store":
            return
        site_id = (match.kwargs or {}).get("site_id")
        user = getattr(request, "user", None)
        if not site_id or not (user and getattr(user, "is_authenticated", False)):
            return
        from .context import remember_last_page

        remember_last_page(request, str(site_id), request.get_full_path())
