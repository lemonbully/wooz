"""marketplace.onewooz.com routes (namespace ``marketplace``)."""

from django.urls import path

from . import views

app_name = "marketplace"

urlpatterns = [
    path("", views.home, name="home"),
    path("browse/", views.browse, name="browse"),
    path("apps/<slug:slug>/", views.app_detail, name="app"),
    path("apps/<slug:slug>/install/", views.install, name="install"),
    path("apps/<slug:slug>/items/<str:key>/", views.item_detail, name="item"),
    path("apps/<slug:slug>/items/<str:key>/get/", views.install, name="install_item"),
    path("library/", views.library, name="library"),
    path("auth/connect/", views.connect, name="connect"),
    path("auth/handoff/", views.handoff, name="handoff"),
    path("auth/sign-out/", views.sign_out, name="sign_out"),
]
