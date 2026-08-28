from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from commerce.models import Order
from storefront.content_seed import default_content
from tenancy.models import Site


@login_required
def dashboard(request):
    """The owner's home — account overview across all their stores."""
    sites = list(
        request.user.sites.filter(is_deleted=False).annotate(
            products_count=Count("products", filter=Q(products__is_deleted=False), distinct=True),
            orders_count=Count("orders", distinct=True),
        )
    )
    has_store = bool(sites)
    # Storefront links must carry the dev path prefix when running single-host,
    # which only a request can supply — so resolve them here, not in the template.
    for s in sites:
        s.preview_url = s.storefront_url(request)

    # The profile (store) the owner last worked on, so they can resume it. Each
    # store is an isolated profile; switching between them is explicit.
    active_id = request.session.get("active_site_id")
    active_site = next((s for s in sites if str(s.id) == active_id), None)

    # Aggregate KPIs across every store, last 30 days.
    since = timezone.now() - timedelta(days=30)
    revenue = orders = Decimal("0")
    if sites:
        span = Order.objects.for_sites(sites).filter(created_at__gte=since)
        paid = span.filter(
            status__in=[Order.Status.PAID, Order.Status.SHIPPED, Order.Status.DELIVERED]
        )
        revenue = paid.aggregate(t=Sum("total"))["t"] or Decimal("0")
        orders = span.count()

    kpis = [
        {"label": "Revenue", "value": f"₹{revenue:,.0f}" if has_store else None, "caption": "Last 30 days"},
        {"label": "Orders", "value": orders if has_store else None, "caption": "Last 30 days"},
        {"label": "Stores", "value": len(sites) if has_store else None, "caption": "Published & draft"},
        {"label": "Conversion", "value": None, "caption": "Last 30 days"},
    ]

    steps = [
        {"title": "Create your account", "desc": "Your OneWooz account is ready and secured.", "state": "done"},
        {"title": "Build your first store", "desc": "Answer a few questions and we publish a working storefront.", "state": "done" if has_store else "now"},
        {"title": "Add your products", "desc": "Import a catalogue or add items one by one, with photos and stock.", "state": "now" if has_store else "todo"},
        {"title": "Connect your domain", "desc": "Point your own domain at OneWooz — we handle the certificate.", "state": "todo"},
        {"title": "Start selling", "desc": "Take payments, fulfil orders and track everything from here.", "state": "todo"},
    ]

    return render(
        request,
        "ud/dashboard.html",
        {
            "nav_active": "overview",
            "sites": sites,
            "active_site": active_site,
            "kpis": kpis,
            "steps": steps,
            "activity": [],
        },
    )


@require_http_methods(["GET", "POST"])
@login_required
def create_site(request):
    """Onboarding — captures the brand brief and creates the store record.

    On submit the store is created with a presentable seeded storefront (real
    copy, coherent palette, drafted policies), then the owner picks the design
    on the template-selection page before landing in the content editor.
    """
    if request.method == "POST":
        business_type = (request.POST.get("business_type") or "d2c").strip()
        model_map = {
            "d2c": Site.BusinessModel.D2C,
            "b2b": Site.BusinessModel.B2B,
            "both": Site.BusinessModel.BOTH,
        }
        business_model = model_map.get(business_type, Site.BusinessModel.D2C)
        site = Site.objects.create(
            owner=request.user,
            name=(request.POST.get("brand_name") or "My store").strip()[:120],
            tagline=(request.POST.get("tagline") or "").strip()[:200],
            business_model=business_model,
            theme=Site.default_theme_for(business_model),
        )
        # Seed presentable content now that the store has a name and slug.
        site.content = default_content(site)
        site.save(update_fields=["content", "updated_at"])
        return redirect("ud:select_template", site_id=site.id)

    return render(request, "ud/create_site.html", {"nav_active": "create", "submitted": False})


@require_http_methods(["GET", "POST"])
@login_required
def select_template(request, site_id):
    """Pick the storefront design for a store.

    Two designs are offered: the catalogue storefront (``template1``, B2B) and
    the modern storefront (``template2``, D2C). The one matching the store's
    business model is recommended, but either can be chosen — mirroring how the
    reference's template picker worked.
    """
    site = get_object_or_404(
        Site.objects.filter(is_deleted=False), pk=site_id, owner=request.user
    )

    if request.method == "POST":
        chosen = request.POST.get("theme")
        if chosen in dict(Site.Theme.choices):
            site.theme = chosen
            site.save(update_fields=["theme", "updated_at"])
        return redirect("store:editor", site_id=site.id)

    themes = [
        {
            "value": Site.Theme.TEMPLATE1,
            "name": "Catalogue storefront",
            "audience": "B2B",
            "description": "A clean, catalogue-first design with categories, certificates and an enquiry flow. Built for wholesale and trade buyers.",
            "recommended": site.is_b2b,
        },
        {
            "value": Site.Theme.TEMPLATE2,
            "name": "Modern storefront",
            "audience": "D2C",
            "description": "A bold, image-led shop with hero banners, best-sellers, testimonials and a full checkout-ready layout. Built for selling direct to shoppers.",
            "recommended": site.is_d2c,
        },
    ]

    return render(
        request,
        "ud/select_template.html",
        {
            "nav_active": "create",
            "site": site,
            "themes": themes,
            "storefront_url": site.storefront_url(request),
        },
    )
