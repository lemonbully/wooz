"""Integration & settings pages: payments, shipping, tracking, Google Merchant."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from commerce.context import get_owned_site, store_context
from commerce.forms import (
    GoogleMerchantForm,
    PaymentConfigForm,
    ShippingProviderForm,
    TrackingConfigForm,
    WarehouseForm,
)
from commerce.models import (
    GoogleMerchantConfig,
    PaymentGatewayConfig,
    ShippingProvider,
    TrackingConfig,
    Warehouse,
)


def _get_or_new(model, site):
    """Fetch the store's singleton config row, or an unsaved one to bind to."""
    existing = model.objects.for_site(site).first()
    return existing or model(site=site)


@login_required
def payments(request, site_id):
    site = get_owned_site(request, site_id)
    instance = _get_or_new(PaymentGatewayConfig, site)
    if request.method == "POST":
        form = PaymentConfigForm(request.POST, request.FILES, instance=instance)
        if form.is_valid():
            config = form.save(commit=False)
            config.site = site
            config.save()
            messages.success(request, "Payment settings saved.")
            return redirect("store:payments", site_id=site.id)
    else:
        form = PaymentConfigForm(instance=instance)

    context = store_context(request, site, "payments")
    context.update({"form": form, "config": instance})
    return render(request, "store/payments.html", context)


@login_required
def tracking(request, site_id):
    site = get_owned_site(request, site_id)
    instance = _get_or_new(TrackingConfig, site)
    if request.method == "POST":
        form = TrackingConfigForm(request.POST, instance=instance)
        if form.is_valid():
            config = form.save(commit=False)
            config.site = site
            config.save()
            messages.success(request, "Tracking & ads settings saved.")
            return redirect("store:tracking", site_id=site.id)
    else:
        form = TrackingConfigForm(instance=instance)

    context = store_context(request, site, "tracking")
    context.update({"form": form})
    return render(request, "store/tracking.html", context)


@login_required
def google_merchant(request, site_id):
    site = get_owned_site(request, site_id)
    instance = _get_or_new(GoogleMerchantConfig, site)
    if request.method == "POST":
        form = GoogleMerchantForm(request.POST, instance=instance)
        if form.is_valid():
            config = form.save(commit=False)
            config.site = site
            if config.merchant_id and config.service_account_json:
                config.status = GoogleMerchantConfig.Status.CONNECTED
            config.save()
            messages.success(request, "Google Merchant settings saved.")
            return redirect("store:google_merchant", site_id=site.id)
    else:
        form = GoogleMerchantForm(instance=instance)

    context = store_context(request, site, "google_merchant")
    context.update({"form": form, "config": instance})
    return render(request, "store/google_merchant.html", context)


@login_required
def shipping(request, site_id):
    site = get_owned_site(request, site_id)
    provider = ShippingProvider.objects.for_site(site).first() or ShippingProvider(site=site)
    warehouses = Warehouse.objects.for_site(site)

    if request.method == "POST":
        intent = request.POST.get("intent")
        if intent == "provider":
            pform = ShippingProviderForm(request.POST, instance=provider, site=site)
            wform = WarehouseForm()
            if pform.is_valid():
                p = pform.save(commit=False)
                p.site = site
                p.save()
                messages.success(request, "Shipping provider saved.")
                return redirect("store:shipping", site_id=site.id)
        elif intent == "warehouse":
            wform = WarehouseForm(request.POST)
            pform = ShippingProviderForm(instance=provider, site=site)
            if wform.is_valid():
                w = wform.save(commit=False)
                w.site = site
                w.save()
                messages.success(request, f"Warehouse “{w.name}” added.")
                return redirect("store:shipping", site_id=site.id)
        else:
            pform = ShippingProviderForm(instance=provider, site=site)
            wform = WarehouseForm()
    else:
        pform = ShippingProviderForm(instance=provider, site=site)
        wform = WarehouseForm()

    context = store_context(request, site, "shipping")
    context.update({"pform": pform, "wform": wform, "provider": provider, "warehouses": warehouses})
    return render(request, "store/shipping.html", context)
