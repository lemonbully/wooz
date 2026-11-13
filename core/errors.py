"""Surface-aware error pages.

Django resolves ``handler404`` / ``handler500`` from the urlconf that was active
when the error occurred, and ``HostRoutingMiddleware`` sets ``request.urlconf``
per surface  so each surface's urlconf points ``handler404``/``handler500`` here
and the visitor lands on a page that knows where "home" is for the surface they
were on (their own storefront, the dashboard, the marketing site).

Both handlers are defensive: an error page that itself raises would replace one
failure with another. The 500 handler in particular renders without a request
context, so a broken context processor (a plausible cause of the 500) cannot be
re-triggered while rendering the apology.
"""

from __future__ import annotations

from django.http import HttpResponse
from django.template import loader
from django.urls import reverse

from .hosts import Surface


def _home(request):
    """(url, label) for the "back" button, chosen by the request's surface.

    In production each surface is its own host, so ``reverse`` within the active
    urlconf yields the right root; the broad fallback covers the case where the
    surface could not be determined (e.g. an error before routing ran).
    """
    surface = getattr(request, "surface", None)
    try:
        if surface is Surface.STOREFRONT:
            return reverse("public_website"), "Back to store"
        if surface is Surface.APP:
            return reverse("ud:dashboard"), "Go to dashboard"
        if surface is Surface.ADMIN:
            return "/", "Back to sign in"
        return reverse("home"), "Back to home"
    except Exception:
        return "/", "Back to home"


def _render(template_name: str, status: int, request) -> HttpResponse:
    try:
        home_url, home_label = _home(request)
    except Exception:
        home_url, home_label = "/", "Back to home"
    try:
        # Rendered without the request so no context processor runs  the error
        # page must not depend on (or re-trigger) whatever failed upstream.
        html = loader.render_to_string(
            template_name, {"home_url": home_url, "home_label": home_label}
        )
        return HttpResponse(html, status=status)
    except Exception:
        return HttpResponse(
            "<h1>Something went wrong</h1>"
            f'<p><a href="{home_url}">{home_label}</a></p>',
            status=status,
        )


def handler404(request, exception=None):
    return _render("404.html", 404, request)


def handler500(request):
    return _render("500.html", 500, request)
