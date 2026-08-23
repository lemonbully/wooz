from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.http import require_http_methods


@login_required
def dashboard(request):
    """The owner's home — account overview across all their stores.

    Tenancy (``tenancy.Site``) lands in Phase 2, so there are no stores to list
    yet: ``sites`` is empty and the KPI figures read as "—". The shape of the
    context is the shape the real data will fill, so wiring it later is a
    substitution, not a rewrite.
    """
    sites: list = []  # → request.user's Site queryset once tenancy exists.

    # Aggregate KPIs across every store, last 30 days. ``value=None`` renders as
    # an empty dash with an explanatory caption until a store is live.
    kpis = [
        {"label": "Revenue", "value": None, "caption": "Last 30 days"},
        {"label": "Orders", "value": None, "caption": "Last 30 days"},
        {"label": "Sessions", "value": None, "caption": "Last 30 days"},
        {"label": "Conversion", "value": None, "caption": "Last 30 days"},
    ]

    # Getting-started checklist. "done" reflects real state we can already know;
    # the rest activate as later phases ship.
    has_store = bool(sites)
    steps = [
        {
            "title": "Create your account",
            "desc": "Your OneWooz account is ready and secured.",
            "state": "done",
        },
        {
            "title": "Build your first store",
            "desc": "Answer a few questions and we publish a working storefront.",
            "state": "done" if has_store else "now",
        },
        {
            "title": "Add your products",
            "desc": "Import a catalogue or add items one by one, with photos and stock.",
            "state": "todo",
        },
        {
            "title": "Connect your domain",
            "desc": "Point your own domain at OneWooz — we handle the certificate.",
            "state": "todo",
        },
        {
            "title": "Start selling",
            "desc": "Take payments, fulfil orders and track everything from here.",
            "state": "todo",
        },
    ]

    return render(
        request,
        "ud/dashboard.html",
        {
            "nav_active": "overview",
            "sites": sites,
            "kpis": kpis,
            "steps": steps,
            "activity": [],  # → recent AuditEvent / order feed once those exist.
        },
    )


@require_http_methods(["GET", "POST"])
@login_required
def create_site(request):
    """Onboarding entry — collects the few facts the store generator needs.

    The real generator (industry content packs → a live store on
    ``<slug>.onewooz.site``) lands with Phase 2. Until then this captures the
    brand brief and confirms it back, so the flow and UI are real and the
    persistence step is the only thing left to connect.
    """
    if request.method == "POST":
        brief = {
            "business_type": (request.POST.get("business_type") or "").strip(),
            "industry": (request.POST.get("industry") or "").strip(),
            "brand_name": (request.POST.get("brand_name") or "").strip()[:120],
            "tagline": (request.POST.get("tagline") or "").strip()[:160],
        }
        return render(
            request,
            "ud/create_site.html",
            {"nav_active": "create", "submitted": True, "brief": brief},
        )

    return render(
        request,
        "ud/create_site.html",
        {"nav_active": "create", "submitted": False},
    )
