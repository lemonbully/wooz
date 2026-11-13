"""Routes for arcane.onewooz.com.

Every route is behind :class:`custom_admin.middleware.AdminAccessMiddleware`,
which requires a staff account holding a session established through the admin's
own email + password + emailed-OTP sign-in.

Filled out further during the admin phase.
"""

from __future__ import annotations

from django.urls import path

from accounts import views as account_views

from . import views

app_name = "custom_admin"

urlpatterns = [
    # Two-step staff sign-in: email + password, then an emailed one-time code.
    # Both are reachable before a session exists (AdminAccessMiddleware.PUBLIC_PATHS
    # covers the /sign-in/ prefix) but stay behind the network allowlist.
    path("sign-in/", views.sign_in, name="sign_in"),
    path("sign-in/verify/", views.sign_in_verify, name="sign_in_verify"),
    # Forgot / reset password for staff accounts. Under /sign-in/ so the access
    # middleware treats them as public (still behind the network allowlist).
    path("sign-in/forgot/", views.forgot, name="forgot"),
    path("sign-in/reset/", views.reset, name="reset"),
    path("sign-out/", account_views.sign_out, name="sign_out"),
    path("", views.overview, name="overview"),
    # Email template builder (admin-authored, ChatGPT-assisted).
    path("email-templates/", views.email_templates, name="email_templates"),
    path("email-templates/new/", views.email_template_new, name="email_template_new"),
    path("email-templates/generate/", views.email_template_generate, name="email_template_generate"),
    path("email-templates/<uuid:template_id>/", views.email_template_edit, name="email_template_edit"),
    path("email-templates/<uuid:template_id>/preview/", views.email_template_preview, name="email_template_preview"),
    path("email-templates/<uuid:template_id>/delete/", views.email_template_delete, name="email_template_delete"),
]
