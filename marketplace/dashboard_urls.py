"""App pages inside a store (namespace ``store_apps``), mounted at
``app.onewooz.com/store/<site_id>/apps/``."""

from django.urls import path

from . import dashboard_views

app_name = "store_apps"

urlpatterns = [
    path("p/<uuid:placement_id>/", dashboard_views.page, name="page"),
    path("<slug:slug>/settings/", dashboard_views.configure, name="configure"),
    path("<slug:slug>/", dashboard_views.open_app, name="open"),
]
