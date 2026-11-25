"""URLs for the collaborator portal (the UGC referral *apex*, ``myugcstore.com``).

Served for the bare referral root (and ``www``). A collaborator signs in with
their email + a one-time code and lands on a dashboard of their attributed
orders, earnings and payout requests. The referral *subdomains*
(``<sub>.myugcstore.com``) are a different surface entirely (the storefront).

Session cookies are host-only (no COOKIE_DOMAIN), so a portal login on
``myugcstore.com`` never touches the owner dashboard or any storefront session.
"""

from __future__ import annotations

from django.urls import path

from commerce.views import referral_portal as views
from core import health

urlpatterns = [
    path("health/live/", health.live, name="health_live"),

    path("", views.index, name="referral_index"),
    path("login/", views.login, name="referral_login"),
    path("login/send-otp/", views.login_send_otp, name="referral_login_send_otp"),
    path("login/verify/", views.login_verify, name="referral_login_verify"),
    path("logout/", views.logout, name="referral_logout"),

    path("dashboard/", views.dashboard, name="referral_dashboard"),
    path("payouts/request/", views.payout_request, name="referral_payout_request"),

    # Storefront customisation (the collaborator controls their own storefront).
    path("customize/profile/", views.profile_save, name="referral_profile_save"),
    path("customize/reel/", views.reel_upload, name="referral_reel_upload"),
    path("customize/media/<uuid:media_id>/delete/", views.media_delete, name="referral_media_delete"),
    path("customize/product-image/", views.product_image_save, name="referral_product_image_save"),
]

handler400 = "core.errors.handler400"
handler403 = "core.errors.handler403"
handler404 = "core.errors.handler404"
handler500 = "core.errors.handler500"
