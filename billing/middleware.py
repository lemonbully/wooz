"""Seal the owner dashboard when a paid subscription has lapsed.

An owner whose paid / one-time subscription has expired (see
:mod:`billing.renewal`) cannot use the dashboard until they renew. Rather than a
dedicated renewal *page* they can navigate away from, this intercepts **every**
owner-dashboard route  the account home (``ud``) and every per-store page
(``store``)  before its view runs, and serves one universal, un-dismissable
renewal popup instead. Change the URL by hand and the same wall answers: there
is no route behind it that leaks data.

What stays reachable while locked, so the owner is never trapped and can always
renew or leave:

* the ``accounts`` surface (sign out, sign in, security)  never walled;
* the renewal endpoints themselves (``billing_renew``)  the popup calls them;
* post-payment onboarding, the public collaboration-contract link, and the
  health probes  none of which are dashboard "data".

Runs its ``process_view`` *before* :class:`commerce.middleware.PlanFeatureGateMiddleware`
so an expired owner sees the renewal wall, not a per-area upgrade wall. On a
locked GET it renders the popup (HTTP 402); a locked POST / AJAX call  a direct
hit at a mutating endpoint  is refused with 402 JSON so nothing changes.
"""

from __future__ import annotations

from django.http import JsonResponse
from django.shortcuts import render

from . import renewal

#: URL namespaces that make up the owner dashboard  the surfaces to wall. The
#: account (auth) namespace is deliberately absent so an owner can always sign
#: out or manage their account while locked.
_WALLED_NAMESPACES = frozenset({"ud", "store", "store_apps"})


class SubscriptionRenewalGateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = getattr(request, "resolver_match", None)
        if match is None or (match.namespace or "") not in _WALLED_NAMESPACES:
            return None

        user = getattr(request, "user", None)
        if not (user and getattr(user, "is_authenticated", False)):
            return None  # Let the view's own auth (login_required) handle it.

        if not renewal.state(request).locked:
            # A deleted account that signed in while its paid plan had lapsed
            # waits here: the first dashboard request after the renewal settles
            # brings the account and its stores back (accounts.deletion).
            if getattr(user, "account_deleted_at", None) and not getattr(request, "impersonation", None):
                from accounts import deletion

                if deletion.restore_if_unlocked(request, user):
                    from django.shortcuts import redirect

                    # Reload so the store switcher and context see the stores.
                    if request.method == "GET":
                        return redirect(request.get_full_path())
            return None

        # Actions and AJAX get a hard, cheap refusal  the page they came from
        # already shows the wall; this stops a direct hit from mutating or
        # exfiltrating anything.
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        if request.method != "GET" or is_ajax:
            return JsonResponse(
                {
                    "ok": False,
                    "renewal_required": True,
                    "error": "Your subscription has ended. Renew to continue.",
                },
                status=402,
            )

        return _render_wall(request)


def _render_wall(request):
    """Render the universal renewal popup for a locked GET."""
    from . import cashfree

    from .pricing import quote

    state = renewal.state(request)
    # The owner renews the plan they had, and only that plan. ``renewal_card``
    # is None only when that plan was retired; then the full list is offered so
    # the owner is never trapped with nothing to buy.
    plan = renewal.renewal_card(state)
    if plan is not None:
        cards = []
        has_purchasable = True
        q = quote(plan)
        initial_quote = q.as_json()
    else:
        cards = renewal.renewal_cards("")
        has_purchasable = any(getattr(c, "is_purchasable", False) for c in cards)
        initial_quote = None

    # The popup hands a paid renewal off to Cashfree's hosted checkout via their
    # JS SDK, so opt this one response into the payment-CSP relaxation (see
    # core.middleware). Only bother when there is actually a paid plan to buy and
    # the gateway is configured; otherwise the page stays nonce-only.
    cashfree_ready = cashfree.is_configured()
    if has_purchasable and cashfree_ready:
        request.csp_payment = True

    ctx = {
        "page_title": "Renew your subscription",
        "renewal": state,
        "plan": plan,
        "quote": initial_quote,
        "cards": cards,
        "cashfree_ready": cashfree_ready,
        "account_restore_until": _restore_until(request),
    }
    return render(request, "billing/renewal_wall.html", ctx, status=402)


def _restore_until(request):
    """When the owner deleted their account: the last day renewing restores it."""
    deleted_at = getattr(getattr(request, "user", None), "account_deleted_at", None)
    if not deleted_at:
        return None
    from accounts import deletion

    return deletion.recover_until(deleted_at)
