"""Growth: discounts (coupons) and collaborations (referrals)."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from commerce.context import get_owned_site, store_context
from commerce.forms import CollaborationForm, CouponForm
from commerce.models import Collaboration, Coupon


@login_required
def coupons(request, site_id):
    site = get_owned_site(request, site_id)
    qs = Coupon.objects.for_site(site).filter(is_deleted=False)
    context = store_context(request, site, "coupons")
    context.update({"coupons": qs})
    return render(request, "store/coupons.html", context)


@login_required
def coupon_form(request, site_id, coupon_id=None):
    site = get_owned_site(request, site_id)
    instance = None
    if coupon_id:
        instance = get_object_or_404(
            Coupon.objects.for_site(site).filter(is_deleted=False), pk=coupon_id
        )

    if request.method == "POST":
        form = CouponForm(request.POST, instance=instance)
        if form.is_valid():
            coupon = form.save(commit=False)
            coupon.site = site
            coupon.save()
            messages.success(request, f"Coupon {coupon.code} saved.")
            return redirect("store:coupons", site_id=site.id)
    else:
        form = CouponForm(instance=instance)

    context = store_context(request, site, "coupons")
    context.update({"form": form, "coupon": instance, "is_edit": bool(instance)})
    return render(request, "store/coupon_form.html", context)


@login_required
@require_POST
def coupon_delete(request, site_id, coupon_id):
    site = get_owned_site(request, site_id)
    coupon = get_object_or_404(Coupon.objects.for_site(site), pk=coupon_id)
    coupon.soft_delete()
    messages.success(request, f"Coupon {coupon.code} removed.")
    return redirect("store:coupons", site_id=site.id)


@login_required
def collaborations(request, site_id):
    site = get_owned_site(request, site_id)
    qs = Collaboration.objects.for_site(site)
    context = store_context(request, site, "collaborations")
    context.update({"collaborations": qs})
    return render(request, "store/collaborations.html", context)


@login_required
def collaboration_form(request, site_id):
    site = get_owned_site(request, site_id)
    if request.method == "POST":
        form = CollaborationForm(request.POST)
        if form.is_valid():
            collab = form.save(commit=False)
            collab.site = site
            collab.save()
            messages.success(request, f"{collab.name} invited.")
            return redirect("store:collaborations", site_id=site.id)
    else:
        form = CollaborationForm()

    context = store_context(request, site, "collaborations")
    context.update({"form": form})
    return render(request, "store/collaboration_form.html", context)
