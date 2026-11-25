"""Updated-agreement re-acceptance endpoints, on the owner-dashboard (app) surface.

Called by the "We have updated our user agreement" popup that
``templates/billing/_agreement_update_modal.html`` renders over every dashboard
page until the owner accepts the new version (see :mod:`billing.agreement_update`).
"""

from __future__ import annotations

from django.urls import path

from . import agreement_update

app_name = "billing_agreement"

urlpatterns = [
    path("send-code/", agreement_update.send_code, name="send_code"),
    path("verify/", agreement_update.verify_code, name="verify"),
]
