"""Authentication routes. Mounted on app.onewooz.com only."""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    # Password
    path("sign-in/", views.sign_in, name="sign_in"),
    path("sign-up/", views.sign_up, name="sign_up"),
    path("sign-out/", views.sign_out, name="sign_out"),
    # Emailed one-time code — a step in every sign-in and sign-up
    path("sign-in/email-code/", views.email_otp, name="email_otp"),
    # Second factor
    path("sign-in/verify/", views.mfa_challenge, name="mfa_challenge"),
    path("security/authenticator/", views.totp_enrol, name="totp_enrol"),
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
    path("security/", views.security, name="security"),
]
