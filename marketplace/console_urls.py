"""Marketplace console routes (namespace ``mkt_console``), mounted on the admin
surface at ``arcane.onewooz.com/marketplace/`` behind AdminAccessMiddleware."""

from django.urls import path

from . import console

app_name = "mkt_console"

urlpatterns = [
    path("", console.overview, name="overview"),
    path("toggle/", console.toggle_marketplace, name="toggle"),
    path("apps/", console.apps, name="apps"),
    path("apps/new/", console.app_edit, name="app_new"),
    path("apps/<uuid:app_id>/", console.app_edit, name="app_edit"),
    path("apps/<uuid:app_id>/action/", console.app_action, name="app_action"),
    path("designs/", console.designs, name="designs"),
    path("licences/", console.licences_view, name="licences"),
    path("licences/<uuid:licence_id>/action/", console.licence_action, name="licence_action"),
    path("orders/", console.orders_view, name="orders"),
    path("orders/<uuid:order_id>/check/", console.order_check, name="order_check"),
    path("health/", console.health_view, name="health"),
    path("alerts/", console.alerts_view, name="alerts"),
    path("alerts/<uuid:recipient_id>/", console.alerts_view, name="alert_edit"),
    path("alerts/<uuid:recipient_id>/action/", console.alert_action, name="alert_action"),
    path("settings/", console.settings_view, name="settings"),
    path("activity/", console.activity, name="activity"),
    path("logs/", console.logs_view, name="logs"),
]
