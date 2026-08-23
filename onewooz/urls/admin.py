"""URLs for admin.onewooz.com — the internal admin surface.

Reachable only from an allowlisted network, authenticated only by passkey, and
audited in full. Deliberately does not include ``django.contrib.admin``: a
generic model-CRUD surface over every table is the opposite of what a
least-privilege admin should expose.
"""

from __future__ import annotations

from django.urls import include, path

from core import health

urlpatterns = [
    path("health/live/", health.live, name="health_live"),
    path("", include("custom_admin.urls")),
]
