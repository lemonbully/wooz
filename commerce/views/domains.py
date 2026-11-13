"""Custom-domain management for a store.

An owner can point their own domain (e.g. ``shop.brand.com``) at their
storefront. The flow has two proofs, both required before a domain is served:

1. **Ownership**  a ``TXT`` record at ``_onewooz-challenge.<domain>`` carrying a
   per-site token only this store's dashboard shows. This proves the person
   connecting the domain actually controls its DNS, and it authorises the edge
   to mint a TLS certificate for the domain.
2. **Pointing**  the domain resolves to us: an ``A``/``AAAA`` record on our
   fixed edge IP, the same for a bare root domain or a subdomain. One IP serves
   every store; Host-based routing (``core.hosts``) sends each verified domain
   to the store that owns it, so there is no per-store DNS target. (A ``CNAME``
   to the legacy ``<slug>.onewooz.site`` target is still accepted, for domains
   connected before this changed.)

``Verify`` checks both with dnspython (against public resolvers, so a freshly
added record is seen without waiting for the box's own resolver cache), then
flips ``custom_domain_verified`` so host routing serves the storefront on that
domain.

TLS for the verified domain is provisioned at the edge (Caddy on-demand TLS,
gated by the Redis set below); until that is in place the domain resolves and
verifies here but is served over plain HTTP.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import socket
import ssl

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from commerce import activity
from commerce.context import get_owned_site, store_context
from storefront.apps import invalidate_custom_domain
from tenancy.models import Site

# A conservative hostname check: labels of letters/digits/hyphens, a dot, and a
# TLD. Rejects schemes, paths, ports and obvious junk.
_DOMAIN_RE = re.compile(
    r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)

# The TXT ownership record. Name is prefixed onto the domain; value is the
# labelled per-site token.
_CHALLENGE_HOST = "_onewooz-challenge"
_TXT_LABEL = "onewooz-site-verification"

# Query public resolvers first so a just-added record is visible immediately;
# fall back to the system resolver if those are unreachable.
_PUBLIC_RESOLVERS = ["1.1.1.1", "8.8.8.8", "9.9.9.9"]


# --------------------------------------------------------------------------
# Verification token  deterministic, stable, no storage/migration needed.
# --------------------------------------------------------------------------
def verify_token(site: Site) -> str:
    """A stable per-site token derived from the server secret and the site id.

    Deterministic (so the TXT record an owner adds stays valid indefinitely) and
    unguessable (HMAC over the secret), so one store cannot claim another's
    domain by reading a token off the page.
    """
    key = (settings.SECRET_KEY or "").encode()
    msg = f"custom-domain-verify:{site.pk}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:40]


def _txt_value(site: Site) -> str:
    """The exact string the owner puts in the TXT record's value."""
    return f"{_TXT_LABEL}={verify_token(site)}"


# --------------------------------------------------------------------------
# DNS helpers (dnspython)
# --------------------------------------------------------------------------
def _query(name: str, rdtype: str) -> list:
    """Resolve ``name``/``rdtype``, trying public resolvers then the system one.

    Returns the answer records, or ``[]`` for NXDOMAIN / no-answer / timeout /
    dnspython-not-installed  verification treats every "not found" the same.

    dnspython (a declared dependency) is imported lazily so a missing package
    can never break importing this module (and with it URL resolution); it just
    degrades DNS lookups to the socket fallback in ``_a_ips``.
    """
    try:
        import dns.exception
        import dns.resolver
    except ImportError:
        return []

    for nameservers in (_PUBLIC_RESOLVERS, None):
        try:
            resolver = dns.resolver.Resolver(configure=nameservers is None)
            if nameservers is not None:
                resolver.nameservers = nameservers
            resolver.lifetime = 5.0
            resolver.timeout = 3.0
            return list(resolver.resolve(name, rdtype))
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return []
        except dns.exception.DNSException:
            continue  # Try the next resolver.
    return []


def _a_ips(hostname: str) -> set[str]:
    ips: set[str] = set()
    for rr in _query(hostname, "A"):
        ips.add(getattr(rr, "address", "").strip())
    for rr in _query(hostname, "AAAA"):
        ips.add(getattr(rr, "address", "").strip())
    ips.discard("")
    if ips:
        return ips
    # Fallback when dnspython is unavailable: the OS resolver (A/AAAA only).
    try:
        for info in socket.getaddrinfo(hostname, None):
            ips.add(info[4][0])
    except (socket.gaierror, socket.herror, OSError):
        pass
    ips.discard("")
    return ips


def _txt_strings(name: str) -> list[str]:
    out: list[str] = []
    for rr in _query(name, "TXT"):
        try:
            # A TXT record may be split into multiple quoted chunks; join them.
            value = b"".join(rr.strings).decode("utf-8", "replace")
        except Exception:
            value = str(rr).strip('"')
        out.append(value.strip())
    return out


def _cname_target(site: Site) -> str:
    """The value the owner should CNAME their subdomain to.

    When a Caddy on-demand-TLS edge is configured (``CUSTOM_DOMAIN_TARGET``),
    domains point there so they get an automatic certificate; otherwise they
    point at the store's own subdomain (served by the ALB).
    """
    return getattr(settings, "CUSTOM_DOMAIN_TARGET", "") or f"{site.slug}.{settings.STOREFRONT_APEX}"


def _a_records() -> list[str]:
    """Fixed edge IP(s) owners can point an apex A record at."""
    return list(getattr(settings, "CUSTOM_DOMAIN_A_RECORDS", []) or [])


def _ownership_ok(site: Site, domain: str) -> bool:
    """True when the TXT ownership token is present under the domain."""
    want = _txt_value(site)
    token = verify_token(site)
    # The dedicated challenge host is preferred; also accept the token placed at
    # the domain root, for providers that make a subdomain TXT awkward.
    candidates = _txt_strings(f"{_CHALLENGE_HOST}.{domain}") + _txt_strings(domain)
    for value in candidates:
        if value == want or value == token:
            return True
    return False


def _points_here(site: Site, domain: str) -> bool:
    """True when the domain resolves to our edge (A/AAAA) or CNAMEs to target."""
    target = _cname_target(site).rstrip(".").lower()

    # CNAME to our target (typical for a subdomain).
    for rr in _query(domain, "CNAME"):
        if str(getattr(rr, "target", "")).rstrip(".").lower() == target:
            return True

    # A/AAAA on one of our fixed edge IPs, or on whatever the target resolves to.
    accepted = set(_a_records()) | _a_ips(target)
    domain_ips = _a_ips(domain)
    return bool(domain_ips and accepted and (domain_ips & accepted))


# --------------------------------------------------------------------------
# TLS / HTTPS certificate status
# --------------------------------------------------------------------------
def _ssl_status(domain: str) -> str:
    """Probe ``https://domain`` and report the certificate state.

    Returns:
      * ``"active"``   a TLS handshake to :443 succeeded and the presented
        certificate is trusted *and* valid for this exact hostname (SNI). The
        store loads over real HTTPS.
      * ``"pending"``  :443 answered but the certificate isn't valid for the
        domain yet (e.g. only the platform wildcard is served, or the edge is
        still minting the cert on first contact  this probe itself triggers
        on-demand issuance), or nothing answers on :443 yet.

    Only the handshake and the peer certificate are inspected  no HTTP request
    is sent and no response body is read.
    """
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((domain, 443), timeout=6) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                # Reaching here means the CA chain verified AND the hostname
                # matched  a browser would show the padlock.
                ssock.getpeercert()
        return "active"
    except ssl.SSLCertVerificationError:
        # A cert is served but it's untrusted or for another name (the ALB
        # wildcard)  the per-domain cert isn't in place yet.
        return "pending"
    except (ssl.SSLError, socket.timeout, ConnectionRefusedError, OSError):
        # No HTTPS listener, handshake reset, DNS not there yet, etc.
        return "pending"


# --------------------------------------------------------------------------
# Redis hint for the on-demand-TLS edge.
# --------------------------------------------------------------------------
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
        # No Redis in dev, or a transient error  the DB flag is still the
        # source of truth for routing; only the auto-cert hint is skipped.
        pass


def _invalidate(domain: str) -> None:
    """Drop cached routing verdicts for the domain and its www twin (either way
    round), since a request on either host resolves to the same store."""
    if not domain:
        return
    bare = domain[4:] if domain.startswith("www.") else domain
    invalidate_custom_domain(bare)
    invalidate_custom_domain(f"www.{bare}")


def _normalize(raw: str) -> str:
    d = (raw or "").strip().lower()
    d = d.removeprefix("https://").removeprefix("http://")
    d = d.split("/", 1)[0].split(":", 1)[0].rstrip(".")
    return d


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------
@login_required
def domain(request, site_id):
    site = get_owned_site(request, site_id)
    # The custom-domain how-to video, if OneWooz has uploaded one (managed in the
    # Django admin as web.TutorialVideo, keyed "custom_domain"). Platform-wide.
    from web.models import TutorialVideo

    tutorial_video = (
        TutorialVideo.objects.filter(
            key=TutorialVideo.Placement.CUSTOM_DOMAIN, is_published=True
        )
        .order_by("-created_at")
        .first()
    )
    context = store_context(request, site, "domain")
    context.update(
        {
            "crumb": "Custom domain",
            "cname_target": _cname_target(site),
            "a_records": _a_records(),
            "storefront_apex": settings.STOREFRONT_APEX,
            # DNS records the owner must add.
            "verify_txt_host": _CHALLENGE_HOST,
            "verify_txt_value": _txt_value(site),
            # Root domains also get a "www → @" CNAME so www.<domain> works
            # (optional; never checked by verification).
            "show_www_record": bool(site.custom_domain) and not site.custom_domain.startswith("www."),
            "tutorial_video": tutorial_video,
        }
    )
    return render(request, "store/domain.html", context)


@login_required
def domain_ssl_status(request, site_id):
    """AJAX: current HTTPS certificate state for the store's custom domain.

    The page polls this after a domain verifies. Probing also nudges an
    on-demand-TLS edge to mint the certificate, so repeated checks move a
    freshly-verified domain from ``pending`` to ``active`` on their own.
    """
    site = get_owned_site(request, site_id)
    if not site.custom_domain:
        return JsonResponse({"ok": True, "status": "no_domain"})
    if not site.custom_domain_verified:
        return JsonResponse({"ok": True, "status": "unverified"})
    return JsonResponse(
        {"ok": True, "status": _ssl_status(site.custom_domain), "domain": site.custom_domain}
    )


@require_POST
@login_required
def domain_save(request, site_id):
    site = get_owned_site(request, site_id)
    domain = _normalize(request.POST.get("custom_domain", ""))

    if not domain or not _DOMAIN_RE.match(domain):
        messages.error(request, "Enter a valid domain, e.g. shop.yourbrand.com")
        return redirect("store:domain", site_id=site.id)

    if domain.endswith("." + settings.STOREFRONT_APEX) or domain == settings.STOREFRONT_APEX:
        messages.error(request, "That is a OneWooz address  enter your own domain instead.")
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
        _invalidate(old)
        _caddy_verified_set(old, False)
    _invalidate(domain)
    activity.record(
        "store.domain.connected", request=request, site=site,
        target=site, target_kind="Site", label=domain,
    )
    messages.success(request, "Domain saved. Add the DNS records below, then verify.")
    return redirect("store:domain", site_id=site.id)


@require_POST
@login_required
def domain_verify(request, site_id):
    site = get_owned_site(request, site_id)
    if not site.custom_domain:
        messages.error(request, "Add a domain first.")
        return redirect("store:domain", site_id=site.id)

    domain = site.custom_domain
    owns = _ownership_ok(site, domain)
    points = _points_here(site, domain)

    if owns and points:
        site.custom_domain_verified = True
        site.save(update_fields=["custom_domain_verified", "updated_at"])
        _invalidate(domain)
        _caddy_verified_set(domain, True)
        activity.record(
            "store.domain.verified", request=request, site=site,
            target=site, target_kind="Site", label=domain,
        )
        messages.success(
            request,
            f"Verified. Your storefront now answers on {domain}  a secure "
            "certificate is issued automatically on the first visit.",
        )
        return redirect("store:domain", site_id=site.id)

    # Not verified  say precisely which proof is missing.
    site.custom_domain_verified = False
    site.save(update_fields=["custom_domain_verified", "updated_at"])
    _invalidate(domain)
    _caddy_verified_set(domain, False)

    if owns and not points:
        messages.error(
            request,
            "Ownership confirmed, but the domain isn’t pointing to OneWooz yet. "
            "Add the A record shown below (host @ for a root domain, or your "
            "subdomain name), then try again in a few minutes  DNS can take a while.",
        )
    elif points and not owns:
        messages.error(
            request,
            "The domain points to OneWooz, but we couldn’t find the TXT "
            "verification record. Add the TXT record shown below and try again.",
        )
    else:
        messages.error(
            request,
            "Not verified yet. Add both the TXT verification record and the "
            "A record shown below, then try again in a few minutes  DNS "
            "changes can take a while to propagate.",
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
        _invalidate(old)
        _caddy_verified_set(old, False)
        activity.record(
            "store.domain.removed", request=request, site=site,
            target=site, target_kind="Site", label=old,
        )
    messages.success(request, "Custom domain removed.")
    return redirect("store:domain", site_id=site.id)
