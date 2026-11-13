"""Growth: discounts (coupons) and collaborations (referrals)."""

from __future__ import annotations

import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from commerce import activity
from commerce import coupon_form as coupon_editor
from commerce import emails as collab_emails
from commerce import instant_discounts
from commerce.context import get_owned_site, store_context
from commerce.models import (
    Category,
    Collaboration,
    CollaborationPayoutRequest,
    Coupon,
    Customer,
    Product,
)
from core import links, tokens
from core.queries import sorted_rows

CONTRACT_PURPOSE = "commerce.collab_contract"
INVITE_TTL = timedelta(hours=48)


@login_required
def coupons(request, site_id):
    site = get_owned_site(request, site_id)
    # Instant discounts have their own page; keep them out of the main list.
    qs = (
        Coupon.objects.for_site(site)
        .filter(is_deleted=False)
        .exclude(conditions__has_key=instant_discounts.CONDITION_KEY)
    )
    context = store_context(request, site, "coupons")
    context.update({"coupons": qs})
    return render(request, "store/coupons.html", context)


@login_required
def instant_discounts_page(request, site_id):
    """List and create instant (short-lived, self-applying) discounts.

    Both live on one page so the owner can spin up several in a row  creation
    posts back here, and the new discount appears in the list above the form.
    """
    site = get_owned_site(request, site_id)

    if request.method == "POST":
        cleaned, errors = instant_discounts.parse_and_validate(request.POST, site)
        if not errors:
            coupon = instant_discounts.save_instant(site, cleaned)
            activity.record(
                "store.coupon.created", request=request, site=site,
                target=coupon, target_kind="Coupon", label=coupon.display_name,
            )
            messages.success(
                request,
                f"Instant discount live  code {coupon.code} works for "
                f"{instant_discounts.duration_label(coupon)}.",
            )
            return redirect("store:instant_discounts", site_id=site.id)
        state = instant_discounts.state_from_post(request.POST)
    else:
        state = instant_discounts.blank_state()
        errors = []

    products = [
        {"id": str(p.id), "title": p.title, "price": p.price, "sku": p.sku}
        for p in sorted_rows(Product.objects.for_site(site).filter(is_deleted=False).order_by("title"))
    ]
    items = [
        {
            "coupon": c,
            "summary": instant_discounts.summarise(c, site.currency),
            "duration": instant_discounts.duration_label(c),
        }
        for c in instant_discounts.for_site(site)
    ]

    context = store_context(request, site, "coupons")
    context.update(
        {
            "form": state,
            "errors": errors,
            "currency": site.currency,
            "products": products,
            "items": items,
            "durations": instant_discounts.DURATIONS,
        }
    )
    return render(request, "store/instant_discounts.html", context)


@login_required
@require_POST
def instant_discount_delete(request, site_id, coupon_id):
    site = get_owned_site(request, site_id)
    coupon = get_object_or_404(
        instant_discounts.for_site(site), pk=coupon_id
    )
    coupon.soft_delete()
    activity.record(
        "store.coupon.deleted", request=request, site=site,
        target=coupon, target_kind="Coupon", label=coupon.display_name,
    )
    messages.success(request, "Instant discount ended.")
    return redirect("store:instant_discounts", site_id=site.id)


def _coupon_editor_context(request, site, instance, state, errors):
    """Shared context for the discount editor (products/categories/customers)."""
    products = [
        {"id": str(p.id), "title": p.title, "price": p.price, "sku": p.sku}
        for p in sorted_rows(Product.objects.for_site(site).filter(is_deleted=False).order_by("title"))
    ]
    categories = [
        {"id": str(c.id), "name": c.name}
        for c in Category.objects.for_site(site).filter(is_deleted=False).order_by("name")
    ]
    customers = [
        {"id": str(c.id), "name": c.name, "email": c.email}
        for c in Customer.objects.for_site(site).filter(is_deleted=False).order_by("email")
    ]
    context = store_context(request, site, "coupons")
    context.update(
        {
            "form": state,
            "errors": errors,
            "coupon": instance,
            "is_edit": bool(instance),
            "currency": site.currency,
            "products": products,
            "categories": categories,
            "customers": customers,
        }
    )
    return context


@login_required
def coupon_form(request, site_id, coupon_id=None):
    site = get_owned_site(request, site_id)
    instance = None
    if coupon_id:
        instance = get_object_or_404(
            Coupon.objects.for_site(site).filter(is_deleted=False), pk=coupon_id
        )

    if request.method == "POST":
        cleaned, errors = coupon_editor.parse_and_validate(request.POST, site, instance)
        if not errors:
            coupon = coupon_editor.save_coupon(site, cleaned, instance)
            activity.record(
                "store.coupon.updated" if instance else "store.coupon.created",
                request=request, site=site, target=coupon,
                target_kind="Coupon", label=coupon.display_name,
            )
            messages.success(request, f"Discount “{coupon.display_name}” saved.")
            return redirect("store:coupons", site_id=site.id)
        state = coupon_editor.state_from_post(request.POST)
    else:
        state = coupon_editor.build_state(instance)
        errors = []

    context = _coupon_editor_context(request, site, instance, state, errors)
    return render(request, "store/coupon_form.html", context)


@login_required
@require_POST
def coupon_delete(request, site_id, coupon_id):
    site = get_owned_site(request, site_id)
    coupon = get_object_or_404(Coupon.objects.for_site(site), pk=coupon_id)
    coupon.soft_delete()
    activity.record(
        "store.coupon.deleted", request=request, site=site,
        target=coupon, target_kind="Coupon", label=coupon.code,
    )
    messages.success(request, f"Coupon {coupon.code} removed.")
    return redirect("store:coupons", site_id=site.id)


# ── Collaborations (referral portals) ───────────────────────────────────────

def _referral_root_domain() -> str:
    return getattr(settings, "REFERRAL_ROOT_DOMAIN", getattr(settings, "STOREFRONT_APEX", "onewooz.site"))


def _unique_handle(site, base: str) -> str:
    base = slugify(base) or f"creator-{secrets.token_hex(2)}"
    candidate, n = base, 2
    existing = set(
        Collaboration.objects.for_site(site).values_list("handle", flat=True)
    )
    while candidate in existing:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def _invite_hours() -> int:
    """The invite validity window in whole hours (for email copy)."""
    return int(INVITE_TTL.total_seconds() // 3600)


def _mint_invite(collab) -> None:
    """Generate a fresh OTP + signed 48h contract token for a collaboration."""
    now = timezone.now()
    collab.otp = f"{secrets.randbelow(1_000_000):06d}"
    collab.otp_expires_at = now + INVITE_TTL
    collab.invite_token = tokens.sign(CONTRACT_PURPOSE, {"sid": str(collab.site_id), "cid": str(collab.id)})
    collab.invite_token_expires_at = now + INVITE_TTL


def _contract_url(request, collab) -> str:
    from django.urls import reverse

    return request.build_absolute_uri(reverse("collab_contract", args=[collab.invite_token]))


@login_required
def collaborations(request, site_id):
    site = get_owned_site(request, site_id)

    if request.method == "POST":
        return _create_collaboration(request, site)

    partners = Collaboration.objects.for_site(site).prefetch_related("products", "categories")

    # Filters (mirrors the reference's ref_* query params).
    q = (request.GET.get("ref_q") or "").strip()
    status = (request.GET.get("ref_status") or "").strip()
    sort = (request.GET.get("ref_sort") or "recent").strip()
    try:
        min_orders = int(request.GET.get("ref_min_orders") or 0)
    except ValueError:
        min_orders = 0
    try:
        min_earnings = Decimal(request.GET.get("ref_min_earnings") or "0")
    except (InvalidOperation, ValueError):
        min_earnings = Decimal("0")
    pending_payout = request.GET.get("ref_pending_payout") == "1"

    if q:
        partners = partners.filter(Q(name__icontains=q) | Q(email__icontains=q))
    if status:
        partners = partners.filter(status=status)
    if min_orders:
        partners = partners.filter(total_orders__gte=min_orders)
    if min_earnings:
        partners = partners.filter(total_earned__gte=min_earnings)
    partners = list(partners)
    if pending_payout:
        partners = [p for p in partners if p.outstanding > 0]
    if sort == "pending_payout":
        partners.sort(key=lambda p: p.outstanding, reverse=True)
    elif sort == "earnings":
        partners.sort(key=lambda p: p.total_earned, reverse=True)
    else:
        partners.sort(key=lambda p: p.created_at, reverse=True)

    # Payout requests creators raised from their myUGCstore dashboard: every
    # pending one, plus the latest settled ones for context.
    payout_qs = CollaborationPayoutRequest.objects.for_site(site).select_related("collaboration")
    pending_payouts = list(
        payout_qs.filter(status=CollaborationPayoutRequest.Status.PENDING).order_by("created_at")
    )
    recent_payouts = list(
        payout_qs.exclude(status=CollaborationPayoutRequest.Status.PENDING).order_by("-updated_at")[:10]
    )

    all_partners = Collaboration.objects.for_site(site)
    stats = {
        "partners": all_partners.count(),
        "verified": all_partners.filter(status=Collaboration.Status.VERIFIED).count(),
        "awaiting": all_partners.filter(status=Collaboration.Status.INVITED).count(),
    }

    context = store_context(request, site, "collaborations")
    context.update(
        {
            "partners": partners,
            "stats": stats,
            "referral_root_domain": _referral_root_domain(),
            "products_all": Product.objects.for_site(site).filter(is_deleted=False),
            "categories_all": Category.objects.for_site(site).filter(is_deleted=False),
            "ref_q": q,
            "ref_status": status,
            "ref_sort": sort,
            "ref_min_orders": min_orders,
            "ref_min_earnings": min_earnings,
            "ref_pending_payout": pending_payout,
            "pending_payouts": pending_payouts,
            "recent_payouts": recent_payouts,
        }
    )
    return render(request, "store/collaborations.html", context)


def _create_collaboration(request, site):
    email = (request.POST.get("email") or "").strip()
    if not email:
        messages.error(request, "An email address is required to invite a collaborator.")
        return redirect("store:collaborations", site_id=site.id)

    name = email.split("@")[0]
    subdomain = slugify(request.POST.get("subdomain_prefix") or name)

    # A collaboration's subdomain is a public store address too, so it obeys the
    # same reserved-keyword blocklist as a store slug.
    from tenancy.reserved import is_reserved

    if is_reserved(subdomain):
        messages.error(
            request,
            "That subdomain is reserved and can't be used. Please choose a different one.",
        )
        return redirect("store:collaborations", site_id=site.id)

    commission_type = request.POST.get("commission_type") or Collaboration.CommissionType.FIXED
    if commission_type not in Collaboration.CommissionType.values:
        commission_type = Collaboration.CommissionType.FIXED
    try:
        commission_value = Decimal(request.POST.get("commission_value") or "0")
    except (InvalidOperation, ValueError):
        commission_value = Decimal("0")

    product_scope = Collaboration.Scope.ALL if request.POST.get("assign_all_products") else Collaboration.Scope.SPECIFIC
    category_scope = Collaboration.Scope.ALL if request.POST.get("assign_all_categories") else Collaboration.Scope.SPECIFIC

    collab = Collaboration.objects.create(
        site=site,
        name=name,
        email=email,
        mobile_number=(request.POST.get("mobile_number") or "").strip(),
        handle=_unique_handle(site, subdomain or name),
        subdomain=subdomain,
        status=Collaboration.Status.INVITED,
        commission_type=commission_type,
        commission_value=commission_value,
        product_scope=product_scope,
        category_scope=category_scope,
    )
    if product_scope == Collaboration.Scope.SPECIFIC:
        ids = request.POST.getlist("products")
        if ids:
            collab.products.set(Product.objects.for_site(site).filter(pk__in=ids))
    if category_scope == Collaboration.Scope.SPECIFIC:
        ids = request.POST.getlist("categories")
        if ids:
            collab.categories.set(Category.objects.for_site(site).filter(pk__in=ids))

    _mint_invite(collab)
    collab.save(update_fields=["otp", "otp_expires_at", "invite_token", "invite_token_expires_at"])
    activity.record(
        "store.collaboration.created", request=request, site=site,
        target=collab, target_kind="Collaboration", label=collab.name or email,
    )
    # Email the creator their invite + contract link from no-reply@myugcstore.com.
    # Best-effort: whether or not it lands, the owner is shown the OTP and can
    # share the link by hand as a fallback.
    emailed = collab_emails.send_collaboration_invite(
        collab, contract_url=_contract_url(request, collab), hours=_invite_hours()
    )
    if emailed:
        messages.success(
            request,
            f"Invite sent to {email}  the OTP is {collab.otp} (valid 48h). "
            "You can also share the contract link yourself.",
        )
    else:
        messages.success(
            request,
            f"Invite created for {email}. Share the contract link  the OTP is {collab.otp} (valid 48h).",
        )
    return redirect("store:collaborations", site_id=site.id)


@login_required
@require_POST
def collaboration_resend(request, site_id, collab_id):
    site = get_owned_site(request, site_id)
    collab = get_object_or_404(Collaboration.objects.for_site(site), pk=collab_id)
    _mint_invite(collab)
    if collab.status in (Collaboration.Status.EXPIRED, Collaboration.Status.SUSPENDED):
        collab.status = Collaboration.Status.INVITED
    collab.save(
        update_fields=["otp", "otp_expires_at", "invite_token", "invite_token_expires_at", "status"]
    )
    contract_url = _contract_url(request, collab)
    # Re-send the invite email (from no-reply@myugcstore.com); best-effort.
    emailed = collab_emails.send_collaboration_invite(
        collab, contract_url=contract_url, hours=_invite_hours()
    )
    return JsonResponse(
        {"success": True, "otp": collab.otp, "contract_url": contract_url, "emailed": emailed}
    )


@login_required
@require_POST
def collaboration_suspend(request, site_id, collab_id):
    site = get_owned_site(request, site_id)
    collab = get_object_or_404(Collaboration.objects.for_site(site), pk=collab_id)
    if collab.status == Collaboration.Status.SUSPENDED:
        collab.status = Collaboration.Status.VERIFIED if collab.verified_at else Collaboration.Status.INVITED
    else:
        collab.status = Collaboration.Status.SUSPENDED
    collab.save(update_fields=["status"])
    activity.record(
        "store.collaboration.updated", request=request, site=site,
        target=collab, target_kind="Collaboration", label=collab.name,
        changes={"status": collab.get_status_display() if hasattr(collab, "get_status_display") else collab.status},
    )
    messages.success(request, f"{collab.name} {'reactivated' if collab.status != 'suspended' else 'suspended'}.")
    return redirect("store:collaborations", site_id=site.id)


@login_required
def collaboration_earnings(request, site_id, collab_id):
    site = get_owned_site(request, site_id)
    collab = get_object_or_404(Collaboration.objects.for_site(site), pk=collab_id)
    return JsonResponse(
        {
            "success": True,
            "name": collab.name,
            "email": collab.email,
            "total_orders": collab.total_orders,
            "total_earned": f"{collab.total_earned:.2f}",
            "total_paid": f"{collab.total_paid:.2f}",
            "outstanding": f"{collab.outstanding:.2f}",
            "commission": (
                f"{collab.commission_value:.2f}% per sale"
                if collab.commission_type == Collaboration.CommissionType.PERCENT
                else f"₹{collab.commission_value:.2f} per sale"
            ),
        }
    )


@login_required
@require_POST
def collaboration_payout(request, site_id, collab_id):
    """Record a payout to a collaborator, reducing their outstanding balance.

    The owner pays creators out of band (bank transfer, UPI…); this just books
    the amount so ``total_paid`` and the outstanding figure stay accurate. The
    amount is clamped to the outstanding balance so a payout can never push the
    balance negative.
    """
    site = get_owned_site(request, site_id)
    collab = get_object_or_404(Collaboration.objects.for_site(site), pk=collab_id)

    raw = (request.POST.get("amount") or "").strip()
    try:
        amount = Decimal(raw).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return JsonResponse({"success": False, "error": "Enter a valid amount."}, status=400)

    outstanding = collab.outstanding
    if amount <= 0:
        return JsonResponse({"success": False, "error": "Enter an amount greater than zero."}, status=400)
    if amount > outstanding:
        return JsonResponse(
            {"success": False, "error": f"That exceeds the outstanding balance (₹{outstanding:.2f})."},
            status=400,
        )

    _book_payout(request, site, collab, amount)
    return JsonResponse(
        {
            "success": True,
            "total_paid": f"{collab.total_paid:.2f}",
            "outstanding": f"{collab.outstanding:.2f}",
            "paid_now": f"{amount:.2f}",
        }
    )


def _book_payout(request, site, collab, amount, *, payout_request=None, note: str = "") -> None:
    """Record ``amount`` paid to ``collab`` and tell them by email.

    Adds to ``total_paid``, settles the creator's pending payout request(s)
    the payment covers (oldest first; or exactly ``payout_request`` when the
    owner paid one from the list), logs the activity and mails the creator.
    """
    collab.total_paid = (collab.total_paid or Decimal("0.00")) + amount
    collab.save(update_fields=["total_paid", "updated_at"])

    now = timezone.now()
    pending = CollaborationPayoutRequest.objects.for_site(site).filter(
        collaboration=collab, status=CollaborationPayoutRequest.Status.PENDING
    )
    if payout_request is not None:
        settled = [payout_request]
    else:
        settled, left = [], amount
        for req in pending.order_by("created_at"):
            if req.amount <= left:
                settled.append(req)
                left -= req.amount
    for req in settled:
        req.status = CollaborationPayoutRequest.Status.PAID
        req.paid_at = now
        req.save(update_fields=["status", "paid_at", "updated_at"])

    activity.record(
        "store.collaboration.paid", request=request, site=site,
        target=collab, target_kind="Collaboration", label=collab.name,
        changes={"amount": f"{amount:.2f}"},
    )
    try:
        collab_emails.send_collaboration_paid(
            collab, amount=amount, dashboard_url=_referral_dashboard_url(request), note=note,
        )
    except Exception:
        pass


@login_required
@require_POST
def collaboration_payout_request_resolve(request, site_id, request_id):
    """Mark a creator's payout request paid (books the payout + emails them)
    or decline it."""
    site = get_owned_site(request, site_id)
    req = get_object_or_404(
        CollaborationPayoutRequest.objects.for_site(site).select_related("collaboration"),
        pk=request_id,
    )
    collab = req.collaboration
    decision = (request.POST.get("decision") or "").strip()
    if req.status != CollaborationPayoutRequest.Status.PENDING:
        messages.info(request, "That payout request was already handled.")
        return redirect("store:collaborations", site_id=site.id)

    if decision == "paid":
        amount = min(req.amount, collab.outstanding)
        if amount <= 0:
            messages.error(request, f"{collab.name} has nothing outstanding to pay.")
            return redirect("store:collaborations", site_id=site.id)
        if amount != req.amount:
            req.amount = amount
            req.save(update_fields=["amount", "updated_at"])
        note = (request.POST.get("note") or "").strip()[:280]
        _book_payout(request, site, collab, amount, payout_request=req, note=note)
        messages.success(request, f"Paid ₹{amount:,.2f} to {collab.name}. They've been emailed.")
    elif decision == "cancelled":
        req.status = CollaborationPayoutRequest.Status.CANCELLED
        req.save(update_fields=["status", "updated_at"])
        messages.success(request, f"Payout request from {collab.name} declined.")
    else:
        messages.error(request, "Unknown action.")
    return redirect("store:collaborations", site_id=site.id)


@login_required
@require_POST
def collaboration_contract_url(request, site_id, collab_id):
    """Return the shareable contract link (for the Copy button)."""
    site = get_owned_site(request, site_id)
    collab = get_object_or_404(Collaboration.objects.for_site(site), pk=collab_id)
    if not collab.invite_token:
        _mint_invite(collab)
        collab.save(update_fields=["otp", "otp_expires_at", "invite_token", "invite_token_expires_at"])
    return JsonResponse({"success": True, "contract_url": _contract_url(request, collab)})


def collaboration_contract(request, token):
    """Public contract-acceptance page reached via the expiring smart link.

    The creator confirms the OTP; on success the collaboration is verified and
    its referral portal goes live. No login  the signed token is the capability.
    """
    try:
        payload = tokens.unsign(CONTRACT_PURPOSE, token)
    except tokens.TokenError:
        return render(request, "store/contract.html", {"error": "This link is invalid or has expired."})

    from tenancy.models import Site

    site = Site.objects.filter(pk=payload.get("sid"), is_deleted=False).first()
    collab = (
        Collaboration.all_objects.filter(pk=payload.get("cid"), site=site).first() if site else None
    )
    if not site or not collab:
        return render(request, "store/contract.html", {"error": "This collaboration no longer exists."})

    if collab.invite_token_expires_at and timezone.now() > collab.invite_token_expires_at:
        collab.status = Collaboration.Status.EXPIRED
        collab.save(update_fields=["status"])
        return render(request, "store/contract.html", {"error": "This invitation has expired. Ask the store to resend it."})

    error = None
    if request.method == "POST":
        entered = (request.POST.get("otp") or "").strip()
        if entered and entered == collab.otp:
            first_time = collab.verified_at is None
            collab.status = Collaboration.Status.VERIFIED
            collab.verified_at = timezone.now()
            collab.contract_signed_at = timezone.now()
            collab.save(update_fields=["status", "verified_at", "contract_signed_at"])
            dashboard_url = _referral_dashboard_url(request)
            if first_time:
                # Confirm by email, with the myUGCstore dashboard link. Best-effort:
                # the page below already shows the same link.
                sub = (collab.subdomain or "").strip()
                collab_emails.send_collaboration_verified(
                    collab,
                    dashboard_url=dashboard_url,
                    store_url=links.referral_store_origin(sub, request) if sub else "",
                )
            return render(
                request,
                "store/contract.html",
                {"collab": collab, "site": site, "verified": True, "dashboard_url": dashboard_url},
            )
        error = "That OTP doesn't match. Check the code and try again."

    return render(
        request,
        "store/contract.html",
        {
            "collab": collab,
            "site": site,
            "verified": collab.status == Collaboration.Status.VERIFIED,
            "error": error,
            "dashboard_url": _referral_dashboard_url(request),
        },
    )


def _referral_dashboard_url(request) -> str:
    """Absolute link to the collaborator's myUGCstore dashboard. Reversed against
    the referral portal's own urlconf at the root prefix (this page runs on
    another surface), then placed on the referral apex."""
    from django.urls import get_script_prefix, set_script_prefix

    previous = get_script_prefix()
    try:
        set_script_prefix("/")
        path = reverse("referral_dashboard", urlconf="onewooz.urls.referral")
    except Exception:
        path = "/"
    finally:
        set_script_prefix(previous)
    return links.referral_portal_url(path, request)
