"""Subscription purchase endpoints, mounted on the marketing surface.

Modal-driven from the pricing page (no separate checkout page). The only
side-effect endpoint (``session``) recomputes money server-side and creates the
Cashfree order; payment is confirmed by fetching authoritative status.
"""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path("checkout/quote/", views.quote_view, name="quote"),
    path("checkout/agreement/", views.active_agreement, name="agreement"),
    path("checkout/otp/send/", views.send_otp, name="otp_send"),
    path("checkout/otp/verify/", views.verify_otp, name="otp_verify"),
    path("checkout/session/", views.create_session, name="session"),
    path("checkout/free/", views.free_session, name="free_session"),
    path("checkout/return/", views.payment_return, name="return"),
    path("checkout/webhook/cashfree/", views.webhook, name="webhook"),

    # Admin-issued custom-priced invite: a temporary page running the same
    # checkout at a fixed price for one email. `invite` carries the signed token.
    path("invite/quote/", views.invite_quote, name="invite_quote"),
    path("invite/session/", views.invite_session, name="invite_session"),
    path("invite/<str:token>/", views.invite_checkout, name="invite"),
]
