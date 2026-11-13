"""Public email-tracking endpoints, mounted on the app surface under ``/e/``.

Login-free capability URLs (like the referral contract link): the signed token
is the authorisation. Reproduced by ``reverse('outreach:open', args=[token])``.
"""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "outreach"

urlpatterns = [
    path("o/<str:token>/", views.open_pixel, name="open"),
    path("c/<str:token>/", views.click, name="click"),
    path("u/<str:token>/", views.unsubscribe, name="unsub"),
]
