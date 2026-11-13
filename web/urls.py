"""Routes for onewooz.com  the public marketing site."""

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
    # Crawler files at the site root. They carry a dot, so the restricted
    # ``<page:slug>`` route below never matches them; declared here for clarity.
    path("robots.txt", views.robots_txt, name="robots_txt"),
    path("sitemap.xml", views.sitemap_xml, name="sitemap_xml"),
    path("platform/", views.platform, name="platform"),
    path("pricing/", views.pricing, name="pricing"),
    path("solutions/", views.solutions, name="solutions"),
    path("customers/", views.customers, name="customers"),
    path("about/", views.about, name="about"),
    path("security/", views.security, name="security"),
    path("contact/", views.contact, name="contact"),
    path("support/", views.support, name="support"),
    # Marketing blog.
    path("blog/", views.blog_list, name="blog_list"),
    path("blog/<slug:slug>/", views.blog_detail, name="blog_detail"),
    # Marketing core pages (page → subpage → child), managed in the admin.
    # The index lists the whole tree; it must precede the <slug> routes.
    path("pages/", views.core_pages_index, name="core_pages"),
    path("pages/<slug:slug>/", views.core_page, name="core_page"),
    path("pages/<slug:slug>/<slug:sub_slug>/", views.core_subpage, name="core_subpage"),
    path(
        "pages/<slug:slug>/<slug:sub_slug>/<slug:child_slug>/",
        views.core_child,
        name="core_child",
    ),
    # Admin-authored legal policies (listed in the footer under "Legal").
    path("legal/<slug:slug>/", views.policy, name="policy"),
    # Restricted converter (only the two legal pages)  keep last.
    path("<page:slug>/", views.page, name="page"),
]
