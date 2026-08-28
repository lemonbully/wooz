"""Custom-domain management for a store.

An owner can point their own domain (e.g. ``shop.brand.com``) at their
storefront. The flow: enter the domain → add the shown DNS record → click
Verify. Verification confirms the domain resolves to this platform (i.e. the
owner actually pointed it at us), then flips ``custom_domain_verified`` so host
routing serves the storefront on that domain.

TLS for the verified domain is provisioned separately at the edge; until that is
in place the domain resolves and verifies here but is served over plain HTTP.
"""

from __future__ import annotations

import re
import socket

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from commerce.context import get_owned_site, store_context
from storefront.apps import invalidate_custom_domain
from tenancy.models import Site

# A conservative hostname check: labels of letters/digits/hyphens, a dot, and a
# TLD. Rejects schemes, paths, ports and obvious junk.
_DOMAIN_RE = re.compile(
    r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)


def _normalize(raw: str) -> str:
    d = (raw or "").strip().lower()
    d = d.removeprefix("https://").removeprefix("http://")
    d = d.split("/", 1)[0].split(":", 1)[0].rstrip(".")
    return d


def _ips(hostname: str) -> set[str]:
    try:
        return {info[4][0] for info in socket.getaddrinfo(hostname, None)}
    except (socket.gaierror, socket.herror, OSError):
        return set()


def _cname_target(site: Site) -> str:
    """The value the owner should CNAME their domain to.

    When a Caddy on-demand-TLS edge is configured (``CUSTOM_DOMAIN_TARGET``),
    domains point there so they get an automatic certificate; otherwise they
    point at the store's own subdomain (served by the ALB).
    """
    return getattr(settings, "CUSTOM_DOMAIN_TARGET", "") or f"{site.slug}.{settings.STOREFRONT_APEX}"


def _caddy_verified_set(domain: str, present: bool) -> None:
    """Add/remove a domain in the Redis set Caddy's ask endpoint checks, so the
    edge will (or won't) mint a TLS certificate for it on demand. No-ops without
    Redis (e.g. local dev)."""
    key = getattr(settings, "CADDY_VERIFIED_DOMAINS_KEY", "onewooz:verified_domains")
    try:
        from django_redis import get_redis_connection

        conn = get_redis_connection("default")
        if present:
            conn.sadd(key, domain)
        else:
            conn.srem(key, domain)
    except Exception:
        # No Redis in dev, or a transient error — the DB flag is still the
        # source of truth for routing; only the auto-cert hint is skipped.
        pass


@login_required
def domain(request, site_id):
    site = get_owned_site(request, site_id)
    context = store_context(request, site, "domain")
    context.update(
        {
            "crumb": "Custom domain",
            "cname_target": _cname_target(site),
            "storefront_apex": settings.STOREFRONT_APEX,
        }
    )
    return render(request, "store/domain.html", context)


@require_POST
@login_required
def domain_save(request, site_id):
    site = get_owned_site(request, site_id)
    domain = _normalize(request.POST.get("custom_domain", ""))

    if not domain or not _DOMAIN_RE.match(domain):
        messages.error(request, "Enter a valid domain, e.g. shop.yourbrand.com")
        return redirect("store:domain", site_id=site.id)

    if domain.endswith("." + settings.STOREFRONT_APEX) or domain == settings.STOREFRONT_APEX:
        messages.error(request, "That is a OneWooz address — enter your own domain instead.")
        return redirect("store:domain", site_id=site.id)

    # One domain per store, globally.
    clash = (
        Site.objects.filter(custom_domain=domain, is_deleted=False)
        .exclude(pk=site.pk)
        .exists()
    )
    if clash:
        messages.error(request, "That domain is already connected to another store.")
        return redirect("store:domain", site_id=site.id)

    old = site.custom_domain
    site.custom_domain = domain
    site.custom_domain_verified = False
    site.save(update_fields=["custom_domain", "custom_domain_verified", "updated_at"])
    if old and old != domain:
        invalidate_custom_domain(old)
        _caddy_verified_set(old, False)
    invalidate_custom_domain(domain)
    messages.success(request, "Domain saved. Add the DNS record below, then verify.")
    return redirect("store:domain", site_id=site.id)


@require_POST
@login_required
def domain_verify(request, site_id):
    site = get_owned_site(request, site_id)
    if not site.custom_domain:
        messages.error(request, "Add a domain first.")
        return redirect("store:domain", site_id=site.id)

    platform_ips = _ips(_cname_target(site))
    domain_ips = _ips(site.custom_domain)

    if domain_ips and platform_ips and (domain_ips & platform_ips):
        site.custom_domain_verified = True
        site.save(update_fields=["custom_domain_verified", "updated_at"])
        invalidate_custom_domain(site.custom_domain)
        # Authorise the edge to mint a TLS certificate for it on demand.
        _caddy_verified_set(site.custom_domain, True)
        messages.success(
            request,
            "Verified. Your storefront now answers on "
            f"{site.custom_domain} — a secure certificate is issued automatically "
            "on first visit.",
        )
    else:
        site.custom_domain_verified = False
        site.save(update_fields=["custom_domain_verified", "updated_at"])
        invalidate_custom_domain(site.custom_domain)
        _caddy_verified_set(site.custom_domain, False)
        messages.error(
            request,
            "Not pointing here yet. Add the DNS record shown below and try again "
            "in a few minutes — DNS can take a while to update.",
        )
    return redirect("store:domain", site_id=site.id)


@require_POST
@login_required
def domain_remove(request, site_id):
    site = get_owned_site(request, site_id)
    old = site.custom_domain
    site.custom_domain = ""
    site.custom_domain_verified = False
    site.save(update_fields=["custom_domain", "custom_domain_verified", "updated_at"])
    if old:
        invalidate_custom_domain(old)
        _caddy_verified_set(old, False)
    messages.success(request, "Custom domain removed.")
    return redirect("store:domain", site_id=site.id)
