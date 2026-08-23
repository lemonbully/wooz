"""Routes for admin.onewooz.com.

Every route is behind :class:`custom_admin.middleware.AdminAccessMiddleware`,
which checks the network allowlist before anything else and then requires a
staff account holding a passkey-established session.

Filled out further during the admin phase.
"""

from __future__ import annotations

from django.urls import path

from accounts import views as account_views

from . import views

app_name = "custom_admin"

urlpatterns = [
    path("sign-in/", views.sign_in, name="sign_in"),
    # The only credential this surface accepts. Reachable before a session
    # exists — see AdminAccessMiddleware.PUBLIC_PATHS — but still behind the
    # network allowlist.
    path("passkeys/auth/begin/", account_views.passkey_auth_begin, name="passkey_auth_begin"),
    path("passkeys/auth/finish/", account_views.passkey_auth_finish, name="passkey_auth_finish"),
    path("sign-out/", account_views.sign_out, name="sign_out"),
    path("", views.overview, name="overview"),
]
