"""Routes for the owner dashboard.

Mounted at the root of ``app.onewooz.com`` in production, and under ``/user``
on a loopback host in development. Nothing here writes either prefix  the
script prefix supplies it.

Authentication routes are contributed separately by ``accounts``.
"""

from __future__ import annotations

from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "ud"

urlpatterns = [
    # The surface root sends you to the dashboard rather than duplicating the
    # view on two URLs, so there is one canonical address to link and bookmark.
    path("", RedirectView.as_view(pattern_name="ud:dashboard", permanent=False), name="home"),
    path("dashboard/", views.dashboard, name="dashboard"),
    # In-dashboard plan upgrade (reuses the billing_renew checkout endpoints).
    path("upgrade/", views.upgrade, name="upgrade"),
    # Business Hub: News / Help / Important posts sent from the admin.
    path("business-hub/", views.business_hub, name="business_hub"),
    # Notification bell (top navbar): mark read + a light unread-count poll.
    path("notifications/read/", views.notifications_read, name="notifications_read"),
    path("notifications/unread/", views.notifications_unread, name="notifications_unread"),
    # Dashboard AI assistant (text via Grok, images via OpenAI)  server-side.
    path("assistant/", views.ai_chat, name="ai_chat"),
    path("new/", views.create_site, name="create_site"),
    path("new/subcategories/", views.subcategories_json, name="subcategories_json"),
    path("stores/<uuid:site_id>/template/", views.select_template, name="select_template"),
    # Deleting a store: send a confirmation code, then verify it and delete.
    path("stores/<uuid:site_id>/delete/send-code/", views.delete_site_send_code, name="delete_site_send_code"),
    path("stores/<uuid:site_id>/delete/confirm/", views.delete_site_confirm, name="delete_site_confirm"),
]
