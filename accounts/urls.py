"""Authentication routes. Mounted on app.onewooz.com only."""

from __future__ import annotations

from django.urls import path

from . import impersonation, views

app_name = "accounts"

urlpatterns = [
    # Admin impersonation: the app-surface entry (reached via a signed hand-off
    # link minted on the admin surface) and the "stop impersonating" action the
    # dashboard banner posts to.
    path("impersonate/enter/", impersonation.enter, name="impersonate_enter"),
    path("impersonate/stop/", impersonation.stop, name="impersonate_stop"),
    # Password
    path("sign-in/", views.sign_in, name="sign_in"),
    # Sign-up is disabled for now  no self-service account creation. The route
    # name is kept so existing links and {% url %} references still resolve, but
    # it redirects to sign-in (see views.sign_up). Re-enable by pointing this at
    # the original view, whose implementation is preserved (commented) in views.
    path("sign-up/", views.sign_up, name="sign_up"),
    path("sign-out/", views.sign_out, name="sign_out"),
    # Forgot password  request a reset link, then set a new password from it.
    path("password/reset/", views.password_reset_request, name="password_reset_request"),
    path("password/reset/set/", views.password_reset_confirm, name="password_reset_confirm"),
    # Emailed one-time code  a step in every sign-in and sign-up
    path("sign-in/email-code/", views.email_otp, name="email_otp"),
    # Second factor
    path("sign-in/verify/", views.mfa_challenge, name="mfa_challenge"),
    path("security/authenticator/", views.totp_enrol, name="totp_enrol"),
    # Authenticator enrolment from the security page (modal, JSON)
    path("security/authenticator/start/", views.totp_setup_begin, name="totp_setup_begin"),
    path("security/authenticator/confirm/", views.totp_setup_verify, name="totp_setup_verify"),
    # Switch two-step verification to emailed codes (removes the authenticator).
    path("security/two-step/use-email/", views.two_factor_use_email, name="two_factor_use_email"),
    # Magic link
    path("sign-in/link/", views.magic_link_request, name="magic_link_request"),
    path("sign-in/link/open/", views.magic_link_consume, name="magic_link_consume"),
    # Email code
    path("sign-in/code/", views.login_code_request, name="login_code_request"),
    path("sign-in/code/verify/", views.login_code_verify, name="login_code_verify"),
    # Passkeys
    path("passkeys/register/begin/", views.passkey_register_begin, name="passkey_register_begin"),
    path("passkeys/register/finish/", views.passkey_register_finish, name="passkey_register_finish"),
    path("passkeys/auth/begin/", views.passkey_auth_begin, name="passkey_auth_begin"),
    path("passkeys/auth/finish/", views.passkey_auth_finish, name="passkey_auth_finish"),
    path("passkeys/<uuid:credential_id>/delete/", views.passkey_delete, name="passkey_delete"),
    # Account
    path("profile/", views.profile, name="profile"),
    path("security/", views.security, name="security"),
    # Live sessions: a heartbeat the dashboard polls (so a remotely-ended
    # session logs out fast), ending one session or all others, and revoking a
    # trusted browser.
    path("security/session/ping/", views.session_ping, name="session_ping"),
    path("security/session/end/", views.session_terminate, name="session_terminate"),
    path("security/session/end-others/", views.sessions_terminate_others, name="sessions_terminate_others"),
    path("security/trusted/<uuid:device_id>/remove/", views.trusted_device_remove, name="trusted_device_remove"),
]
