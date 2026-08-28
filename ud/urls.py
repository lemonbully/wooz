"""Routes for the owner dashboard.

Mounted at the root of ``app.onewooz.com`` in production, and under ``/user``
on a loopback host in development. Nothing here writes either prefix — the
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
    path("new/", views.create_site, name="create_site"),
    path("stores/<uuid:site_id>/template/", views.select_template, name="select_template"),
]
