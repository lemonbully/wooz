from __future__ import annotations

from django.http import Http404
from django.shortcuts import render
from django.utils.crypto import get_random_string

#: Long-form legal pages that still share the generic prose shell. Everything
#: else now has a bespoke page. Restricting the converter to these two means an
#: unknown slug never reaches the generic view.
PAGES = {
    "privacy": "Privacy",
    "terms": "Terms of service",
}


def home(request):
    return render(request, "web/home.html")


def platform(request):
    return render(request, "web/platform.html")


def pricing(request):
    return render(request, "web/pricing.html")


def solutions(request):
    return render(request, "web/solutions.html")


def customers(request):
    return render(request, "web/customers.html")


def about(request):
    return render(request, "web/about.html")


def security(request):
    return render(request, "web/security.html")


def contact(request):
    """Contact page: FAQs plus a categorised enquiry form.

    The POST branch only acknowledges the submission and mints a reference — no
    persistence yet; the routing/inbox wiring lands with the tenancy phase.
    """
    context = {}
    if request.method == "POST":
        context["sent"] = True
        context["ref"] = "ENQ-" + get_random_string(6, "0123456789")
        context["category"] = request.POST.get("category", "")
    return render(request, "web/contact.html", context)


def support(request):
    """Support desk: raise a ticket by problem type and account identity.

    Same scaffold posture as ``contact`` — acknowledges and mints a ticket
    reference; the ticketing backend is a later phase.
    """
    context = {}
    if request.method == "POST":
        context["sent"] = True
        context["ref"] = "TCK-" + get_random_string(6, "0123456789")
        context["topic"] = request.POST.get("topic", "")
    return render(request, "web/support.html", context)


def page(request, slug: str):
    if slug not in PAGES:
        raise Http404
    return render(
        request,
        f"web/pages/{slug}.html",
        {"page_title": PAGES[slug]},
    )
