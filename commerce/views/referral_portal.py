"""The collaborator portal, served on the UGC referral apex (``myugcstore.com``).

A collaborator signs in with just their email + a one-time code (no password) and
lands on a dashboard of the orders attributed to them, their earnings, and their
payout requests. Recognition is by :attr:`Collaboration.email`  the same email
the store owner invited  across every store that collaborator works with; if
they collaborate with several, a store switcher picks the active one.

Auth is session-only and host-scoped to ``myugcstore.com`` (cookies are
host-only, no COOKIE_DOMAIN), so it is completely separate from OneWooz accounts
and from any storefront session. The login is a plain two-step form POST
(email -> code) so it needs no client JS and the CSRF token rides the form.
"""

from __future__ import annotations

import secrets
import time
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from commerce import activity
from commerce import emails as collab_emails
from commerce.models import (
    Collaboration,
    CollaborationMedia,
    CollaborationPayoutRequest,
    CollaborationProductImage,
    Order,
    OrderItem,
    Product,
    Shipment,
)
from commerce.models.marketing import COLLAB_IMAGE_SLOTS
from core.queries import sorted_rows

SESSION_EMAIL = "referral_portal_email"
SESSION_OTP = "referral_portal_otp"
OTP_TTL_SECONDS = 600  # 10 minutes
_LIVE = (Collaboration.Status.VERIFIED, Collaboration.Status.ACTIVE)

# Dashboard order tabs → the Order statuses each groups.
_ORDER_TABS = (
    ("all", "All", ()),
    ("pending", "Pending", (Order.Status.CREATED,)),
    ("accepted", "Accepted", (Order.Status.CONFIRMED, Order.Status.PAID)),
    ("shipped", "Shipped", (Order.Status.SHIPPED,)),
    ("delivered", "Delivered", (Order.Status.DELIVERED,)),
    ("cancelled", "Cancelled", (Order.Status.CANCELLED,)),
)


# -- helpers ----------------------------------------------------------------


def _norm_email(raw: str) -> str:
    return (raw or "").strip().lower()


def _collaborations_for(email: str):
    """Every live collaboration whose invited email matches (across all stores)."""
    email = _norm_email(email)
    base = Collaboration.objects.across_all_sites()
    if not email:
        return base.none()  # scoped (via across_all_sites) + empty, so .exists() is safe
    # Rows join the store's whole ``content`` JSON: evaluate with
    # ``core.queries.sorted_rows`` (not ``list()``) so only keys are sorted.
    return (
        base.filter(email=email, status__in=_LIVE)
        .select_related("site")
        .order_by("-created_at")
    )


def _logged_in_email(request) -> str:
    return _norm_email(request.session.get(SESSION_EMAIL) or "")


def _referral_root() -> str:
    return getattr(settings, "REFERRAL_ROOT_DOMAIN", "myugcstore.com")


def _store_url(collab) -> str:
    sub = (getattr(collab, "subdomain", "") or "").strip()
    return f"https://{sub}.{_referral_root()}" if sub else ""


def _assigned_products(collab):
    """The products this collaborator may customise (their scoped catalogue)."""
    base = Product.objects.for_site(collab.site).filter(is_active=True, is_deleted=False)
    if collab.product_scope == Collaboration.Scope.SPECIFIC:
        return sorted_rows(base.filter(collaborations=collab).order_by("title"), limit=200)
    if collab.category_scope == Collaboration.Scope.SPECIFIC:
        cat_ids = list(collab.categories.values_list("id", flat=True))
        return sorted_rows(base.filter(category_id__in=cat_ids).order_by("title"), limit=200)
    return sorted_rows(base.order_by("title"), limit=200)


def _require_active(request):
    """Return the active collaboration for a logged-in portal request, or None."""
    email = _logged_in_email(request)
    if not email:
        return None
    collaborations = sorted_rows(_collaborations_for(email))
    if not collaborations:
        return None
    return _active_collaboration(request, collaborations)


def _customize_redirect(active):
    return redirect(f"{reverse('referral_dashboard')}?store={active.id}&section=customize")


# -- auth -------------------------------------------------------------------


def index(request):
    if _logged_in_email(request) and _collaborations_for(_logged_in_email(request)).exists():
        return redirect("referral_dashboard")
    return redirect("referral_login")


def login(request):
    email = _logged_in_email(request)
    if email and _collaborations_for(email).exists():
        return redirect("referral_dashboard")
    return render(request, "referral_portal/login.html", {"step": "email"})


@require_POST
def login_send_otp(request):
    email = _norm_email(request.POST.get("email"))
    if "@" not in email or "." not in email.split("@")[-1]:
        return render(
            request,
            "referral_portal/login.html",
            {"step": "email", "error": "Enter a valid email address.", "email": email},
        )

    # Mint and stash a code; only actually email it when the address belongs to a
    # live collaborator. The UI response is identical either way, so the portal
    # never discloses whether an email is registered.
    code = f"{secrets.randbelow(1_000_000):06d}"
    request.session[SESSION_OTP] = {"code": code, "exp": time.time() + OTP_TTL_SECONDS, "email": email}
    if _collaborations_for(email).exists():
        collab_emails.send_referral_login_otp(email, code, minutes=OTP_TTL_SECONDS // 60)

    return render(
        request,
        "referral_portal/login.html",
        {
            "step": "otp",
            "email": email,
            "message": "If that email has a collaboration, we've sent it a 6-digit code.",
        },
    )


@require_POST
def login_verify(request):
    email = _norm_email(request.POST.get("email"))
    code = (request.POST.get("code") or "").strip()
    stash = request.session.get(SESSION_OTP) or {}

    valid = (
        stash
        and _norm_email(stash.get("email")) == email
        and stash.get("code") == code
        and time.time() < float(stash.get("exp") or 0)
        and _collaborations_for(email).exists()
    )
    if not valid:
        return render(
            request,
            "referral_portal/login.html",
            {"step": "otp", "email": email, "error": "That code is invalid or has expired."},
        )

    request.session[SESSION_EMAIL] = email
    request.session.pop(SESSION_OTP, None)
    request.session.cycle_key()
    return redirect("referral_dashboard")


def logout(request):
    request.session.pop(SESSION_EMAIL, None)
    request.session.pop(SESSION_OTP, None)
    return redirect("referral_login")


# -- dashboard --------------------------------------------------------------


def _active_collaboration(request, collaborations):
    """Pick the collaboration the dashboard is showing (``?store=<id>``)."""
    wanted = (request.GET.get("store") or request.POST.get("store") or "").strip()
    if wanted:
        for collab in collaborations:
            if str(collab.id) == wanted:
                return collab
    return collaborations[0]


def dashboard(request):
    email = _logged_in_email(request)
    if not email:
        return redirect("referral_login")
    collaborations = sorted_rows(_collaborations_for(email))
    if not collaborations:
        return logout(request)

    active = _active_collaboration(request, collaborations)

    orders_qs = (
        Order.objects.for_site(active.site)
        .filter(collaboration=active)
        .order_by("-created_at")
    )
    active_tab = (request.GET.get("tab") or "all").strip()
    tab_statuses = {key: statuses for key, _, statuses in _ORDER_TABS}
    if active_tab not in tab_statuses:
        active_tab = "all"

    tabs = []
    for key, label, statuses in _ORDER_TABS:
        qs = orders_qs.filter(status__in=statuses) if statuses else orders_qs
        tabs.append({"key": key, "label": label, "count": qs.count(), "active": key == active_tab})

    filtered = orders_qs
    if tab_statuses[active_tab]:
        filtered = orders_qs.filter(status__in=tab_statuses[active_tab])
    recent_orders = list(filtered[:25])

    payout_requests = list(
        CollaborationPayoutRequest.objects.for_site(active.site)
        .filter(collaboration=active)
        .order_by("-created_at")[:20]
    )

    # One attributed order opened in detail (?order=<id>): only ever an order
    # credited to this collaborator, so ids can't be probed across stores.
    selected_order = _order_detail(active, request.GET.get("order"))

    # Customisation state: cover, reels, and the assigned products with their
    # current per-collaborator image override.
    cover = (
        CollaborationMedia.objects.for_site(active.site)
        .filter(collaboration=active, cover=True)
        .order_by("-updated_at")
        .first()
    )
    reels = list(
        CollaborationMedia.objects.for_site(active.site)
        .filter(collaboration=active, media_type=CollaborationMedia.Kind.VIDEO)
        .order_by("-created_at")
    )
    products = _assigned_products(active)
    ov_map = {}
    for ov in CollaborationProductImage.objects.for_site(active.site).filter(
        collaboration=active, product__in=[p.pk for p in products], slot="image1"
    ):
        ov_map[ov.product_id] = ov
    product_photos = []
    for p in products:
        ov = ov_map.get(p.pk)
        base = list(p.images.all()[:1])
        product_photos.append({
            "id": str(p.id),
            "title": p.title,
            "base_url": (base[0].image.url if base and base[0].image else ""),
            "override_url": (ov.image.url if (ov and ov.image) else ""),
            "visible": (ov.is_visible if ov else True),
        })

    context = {
        "email": email,
        "collaborations": collaborations,
        "active": active,
        "store_url": _store_url(active),
        "metrics": {
            "total_orders": orders_qs.count(),
            "earned": active.total_earned or Decimal("0.00"),
            "paid": active.total_paid or Decimal("0.00"),
            "outstanding": active.outstanding,
        },
        "order_tabs": tabs,
        "active_tab": active_tab,
        "recent_orders": recent_orders,
        "payout_requests": payout_requests,
        "commission_label": (
            f"{active.commission_value:.0f}% per sale"
            if active.commission_type == Collaboration.CommissionType.PERCENT
            else f"₹{active.commission_value:.2f} per sale"
        ),
        "cover_url": (cover.file.url if (cover and cover.file) else ""),
        "profile_url": (active.profile_picture.url if active.profile_picture else ""),
        "reels": reels,
        "product_photos": product_photos,
        "section": (request.GET.get("section") or "").strip(),
        "selected_order": selected_order,
        "payout_notice": {
            "sent": "Payout requested. The store owner has been notified and will pay you shortly.",
            "pending": "You already have a payout request waiting. The store owner will settle it soon.",
            "nothing": "There is no outstanding balance to request right now.",
        }.get((request.GET.get("payout") or "").strip(), ""),
    }
    return render(request, "referral_portal/dashboard.html", context)


def _mask_name(name: str) -> str:
    """The shopper's first name and last initial only: a creator sees who
    bought, not the customer's full identity."""
    parts = (name or "").split()
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1][0]}."


def _order_detail(active, raw_id) -> dict | None:
    """What the collaborator may see of one of their attributed orders: items,
    amounts, status, delivery city and courier tracking. Never the shopper's
    contact details or full address."""
    raw_id = (raw_id or "").strip()
    if not raw_id:
        return None
    try:
        order = (
            Order.objects.for_site(active.site)
            .filter(collaboration=active, pk=raw_id)
            .first()
        )
    except Exception:  # not a UUID
        return None
    if order is None:
        return None
    items = list(
        OrderItem.objects.for_site(active.site).filter(order=order).order_by("created_at")
    )
    shipment = (
        Shipment.objects.for_site(active.site).filter(order=order).order_by("-created_at").first()
    )
    return {
        "order": order,
        "items": items,
        "customer": _mask_name(order.customer_name),
        "city": ", ".join(p for p in (order.shipping_city, order.shipping_state) if p),
        "shipment_status": shipment.get_status_display() if shipment else "",
        "courier": (getattr(shipment, "courier_name", "") or "") if shipment else "",
        "awb": (shipment.awb or "") if shipment else "",
        "tracking": order.tracking_id or "",
    }


@require_POST
def payout_request(request):
    email = _logged_in_email(request)
    if not email:
        return redirect("referral_login")
    collaborations = sorted_rows(_collaborations_for(email))
    if not collaborations:
        return logout(request)

    active = _active_collaboration(request, collaborations)
    outstanding = active.outstanding

    try:
        requested = Decimal(request.POST.get("amount") or "0").quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        requested = outstanding

    # Clamp to the outstanding balance; never request more than is owed.
    amount = min(requested, outstanding) if requested > 0 else outstanding

    already_pending = (
        CollaborationPayoutRequest.objects.for_site(active.site)
        .filter(collaboration=active, status=CollaborationPayoutRequest.Status.PENDING)
        .exists()
    )
    if amount <= 0:
        notice = "nothing"
    elif already_pending:
        notice = "pending"
    else:
        req = CollaborationPayoutRequest.objects.create(
            site=active.site,
            collaboration=active,
            amount=amount,
            outstanding_snapshot=outstanding,
            note=(request.POST.get("note") or "").strip()[:280],
            status=CollaborationPayoutRequest.Status.PENDING,
        )
        # Reaches the store owner: bell + email + their Collaborations page.
        activity.record(
            "store.collaboration.payout_requested", site=active.site,
            target=req, target_kind="CollaborationPayoutRequest",
            label=f"{active.name} · ₹{amount:,.2f}",
            actor_kind="customer",
        )
        notice = "sent"

    return redirect(f"{reverse('referral_dashboard')}?store={active.id}&payout={notice}")


# -- storefront customisation ------------------------------------------------


@require_POST
def profile_save(request):
    """Save storefront title, about blurb, profile picture and cover image."""
    active = _require_active(request)
    if active is None:
        return redirect("referral_login")

    active.storefront_title = (request.POST.get("storefront_title") or "").strip()[:120]
    active.about_storefront = (request.POST.get("about_storefront") or "").strip()[:280]
    fields = ["storefront_title", "about_storefront", "updated_at"]
    if request.FILES.get("profile_picture"):
        active.profile_picture = request.FILES["profile_picture"]
        fields.append("profile_picture")
    active.save(update_fields=fields)

    # Cover image is a CollaborationMedia flagged cover=True; replace any existing.
    if request.FILES.get("cover_image"):
        CollaborationMedia.objects.for_site(active.site).filter(
            collaboration=active, cover=True
        ).delete()
        CollaborationMedia.objects.create(
            site=active.site,
            collaboration=active,
            media_type=CollaborationMedia.Kind.IMAGE,
            file=request.FILES["cover_image"],
            cover=True,
            show_on_storefront=True,
        )
    return _customize_redirect(active)


@require_POST
def reel_upload(request):
    """Upload a video reel shown on the storefront."""
    active = _require_active(request)
    if active is None:
        return redirect("referral_login")
    if request.FILES.get("video"):
        CollaborationMedia.objects.create(
            site=active.site,
            collaboration=active,
            media_type=CollaborationMedia.Kind.VIDEO,
            file=request.FILES["video"],
            title=(request.POST.get("title") or "").strip()[:200],
            show_on_storefront=True,
        )
    return _customize_redirect(active)


@require_POST
def media_delete(request, media_id):
    """Delete one of the collaborator's uploaded media items (cover or reel)."""
    active = _require_active(request)
    if active is None:
        return redirect("referral_login")
    CollaborationMedia.objects.for_site(active.site).filter(
        pk=media_id, collaboration=active
    ).delete()
    return _customize_redirect(active)


@require_POST
def product_image_save(request):
    """Set (or hide) a collaborator's override for a product's primary image."""
    active = _require_active(request)
    if active is None:
        return redirect("referral_login")

    product = (
        Product.objects.for_site(active.site)
        .filter(pk=(request.POST.get("product_id") or ""))
        .first()
    )
    if product is None:
        return _customize_redirect(active)

    slot = (request.POST.get("slot") or "image1").strip()
    if slot not in COLLAB_IMAGE_SLOTS:
        slot = "image1"
    visible = request.POST.get("visible") not in ("0", "false", "off", "no")

    override, _ = CollaborationProductImage.objects.get_or_create(
        site=active.site, collaboration=active, product=product, slot=slot
    )
    if request.FILES.get("image"):
        override.image = request.FILES["image"]
    override.is_visible = visible
    override.save()
    return _customize_redirect(active)
