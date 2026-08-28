"""Store settings — identity, currency, contact and store-wide toggles."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from commerce.context import get_owned_site, store_context
from commerce.forms import SiteForm


@login_required
def settings(request, site_id):
    site = get_owned_site(request, site_id)
    if request.method == "POST":
        form = SiteForm(request.POST, instance=site)
        if form.is_valid():
            form.save()
            messages.success(request, "Store settings saved.")
            return redirect("store:settings", site_id=site.id)
    else:
        form = SiteForm(instance=site)

    context = store_context(request, site, "settings")
    context.update({"form": form})
    return render(request, "store/settings.html", context)
