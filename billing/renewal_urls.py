"""Renewal checkout endpoints, mounted on the owner-dashboard (app) surface.

Same-origin with the dashboard so the renewal popup's fetches carry the app
session cookie and need no cross-surface calls. Kept out of ``billing.urls``
(which is the marketing pricing checkout) so the two flows never share a route:
this one is authenticated and in-dashboard, that one is anonymous on marketing.
"""

from __future__ import annotations

from django.urls import path

from . import renewal_views

app_name = "billing_renew"

urlpatterns = [
    path("quote/", renewal_views.renew_quote, name="renew_quote"),
    path("agreement/", renewal_views.renew_agreement, name="renew_agreement"),
    path("session/", renewal_views.renew_session, name="renew_session"),
    path("free/", renewal_views.renew_free, name="renew_free"),
    path("return/", renewal_views.renew_return, name="renew_return"),
]
