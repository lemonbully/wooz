"""URLs for app.onewooz.com  the authenticated owner dashboard.

Everything under this urlconf belongs to a signed-in site owner. Sign-in and
sign-up live here too, since this is where an owner arrives after clicking a
call to action on the marketing site.
"""

from __future__ import annotations

from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

from billing import onboarding as billing_onboarding
from commerce.views import growth as commerce_growth
from commerce.views import integrations as commerce_integrations
from commerce.views import orders as commerce_orders
from community import bridge as ask_bridge
from core import health

urlpatterns = [
    path("health/live/", health.live, name="health_live"),
    # Ask OneWooz: hands a signed-in owner back to onewooz.com/ask with a
    # one-time token so they don't have to join the community separately.
    # No login_required: a signed-out visitor is simply sent back.
    path("ask-link/", ask_bridge.app_link, name="ask_link"),
    path("health/ready/", health.ready, name="health_ready"),
    # Public referral-contract acceptance (capability URL, no login).
    path("collab/contract/<str:token>/", commerce_growth.collaboration_contract, name="collab_contract"),
    # Public email-marketing tracking (open pixel, click, unsubscribe)  the
    # signed token is the capability; no login. Reached from sent emails.
    path("e/", include(("outreach.urls", "outreach"), namespace="outreach")),
    # Post-payment onboarding: create the account + capture the tax profile.
    # Reached via a signed token minted on the marketing surface once an order
    # settles (see billing.onboarding.onboarding_url). No login  it creates one.
    path("onboarding/<str:token>/", billing_onboarding.onboarding, name="onboarding"),
    # Google OAuth redirect for the Google Merchant connection. One fixed URL
    # for every store (registered on the platform OAuth client); the store
    # travels in the signed ``state``.
    path(
        "integrations/google/callback/",
        commerce_integrations.google_merchant_callback,
        name="google_merchant_callback",
    ),
    # Public courier tracking webhook (Shiprocket et al.)  no login; the
    # store's webhook token (path or x-api-key header) is the credential. The
    # path avoids the words Shiprocket's dashboard rejects ("sr", "kr", ...).
    path("webhooks/tracking/<str:site_key>/", commerce_orders.tracking_webhook, name="courier_tracking_webhook"),
    path(
        "webhooks/tracking/<str:site_key>/<str:token>/",
        commerce_orders.tracking_webhook,
        name="courier_tracking_webhook_token",
    ),
    # Renewal checkout the dashboard popup calls when a subscription has lapsed
    # (billing.middleware.SubscriptionRenewalGateMiddleware serves the popup).
    path("billing/renew/", include("billing.renewal_urls")),
    # "We have updated our user agreement" popup: OTP-confirmed re-acceptance of
    # a new agreement version (billing.agreement_update).
    path("billing/agreement/", include("billing.agreement_urls")),
    # OneWooz Apps: the install gateway (re-auth, consent, payment, My apps)
    # and app pages inside a store. Before the store include on purpose.
    path("marketplace/", include("marketplace.gateway_urls", namespace="mkt_gateway")),
    path("store/<uuid:site_id>/apps/", include("marketplace.dashboard_urls", namespace="store_apps")),
    path("", include("accounts.urls")),
    path("", include("ud.urls")),
    path("store/<uuid:site_id>/", include("commerce.urls")),
]

# Surface-aware error pages  see onewooz.urls.marketing.
handler400 = "core.errors.handler400"
handler403 = "core.errors.handler403"
handler404 = "core.errors.handler404"
handler500 = "core.errors.handler500"

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
