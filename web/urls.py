"""Routes for onewooz.com — the public marketing site."""

from __future__ import annotations

from django.urls import path, register_converter

from . import views


class PageSlugConverter:
    """Matches only the static pages we actually publish.

    Restricting the pattern at the routing layer means an unknown slug never
    reaches a view and cannot be used to probe for template names.
    """

    regex = "|".join(views.PAGES)

    def to_python(self, value):
        return value

    def to_url(self, value):
        return value


register_converter(PageSlugConverter, "page")

app_name = "web"

urlpatterns = [
    path("", views.home, name="home"),
    path("platform/", views.platform, name="platform"),
    path("pricing/", views.pricing, name="pricing"),
    path("solutions/", views.solutions, name="solutions"),
    path("customers/", views.customers, name="customers"),
    path("about/", views.about, name="about"),
    path("security/", views.security, name="security"),
    path("contact/", views.contact, name="contact"),
    path("support/", views.support, name="support"),
    path("<page:slug>/", views.page, name="page"),
]
