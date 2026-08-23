"""Request-level infrastructure.

Ordering in ``settings.MIDDLEWARE`` matters and is documented there. In short:
correlation id first so everything downstream can log it, health probes before
host resolution so a load-balancer check with a mismatched Host never 404s, and
host routing before sessions so the surface is known by the time a cookie is
read.
"""

from __future__ import annotations

import secrets
import uuid

from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden, HttpResponseNotFound
from django.urls import set_script_prefix
from django.utils.deprecation import MiddlewareMixin

from . import hosts as host_utils
from .logging import request_id_var

__all__ = [
    "RequestIDMiddleware",
    "HealthProbeMiddleware",
    "CaddyAskMiddleware",
    "HostRoutingMiddleware",
    "SecurityHeadersMiddleware",
]

_URLCONF_FOR_SURFACE = {
    host_utils.Surface.MARKETING: "onewooz.urls.marketing",
    host_utils.Surface.APP: "onewooz.urls.app",
    host_utils.Surface.ADMIN: "onewooz.urls.admin",
    host_utils.Surface.STOREFRONT: "onewooz.urls.storefront",
}


class RequestIDMiddleware:
    """Attach a correlation id to the request, the logs, and the response.

    Accepts an inbound ``X-Request-ID`` only from our own edge — an arbitrary
    client-supplied value would let a caller poison log correlation or inject
    control characters into log lines — so the header is validated as a UUID
    before it is trusted.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.META.get("HTTP_X_REQUEST_ID", "")
        try:
            request_id = str(uuid.UUID(incoming))
        except (ValueError, AttributeError, TypeError):
            request_id = str(uuid.uuid4())

        request.request_id = request_id
        token = request_id_var.set(request_id)
        try:
            response = self.get_response(request)
        finally:
            request_id_var.reset(token)

        response["X-Request-ID"] = request_id
        return response


class HealthProbeMiddleware:
    """Answer liveness probes before any host or tenant logic runs.

    Load balancer health checks arrive with the target group's own Host header,
    which matches nothing in ALLOWED_HOSTS. Handling them here means a probe
    never depends on host resolution, database availability, or the state of
    the domain cache.
    """

    PATHS = frozenset({"/health", "/health/", "/health/live", "/health/live/"})

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path in self.PATHS:
            return HttpResponse("ok", content_type="text/plain")
        return self.get_response(request)


class CaddyAskMiddleware:
    """Answer Caddy's on-demand-TLS "ask" before host resolution.

    Caddy calls this from the same instance (over loopback, or the app upstream
    in a compose stack) to decide whether to obtain a TLS certificate for the
    hostname a visitor just requested. Like a health probe it arrives with a
    Host header that matches no surface, so it must be handled *before*
    :class:`HostRoutingMiddleware` — otherwise it 404s in production and Caddy
    can never issue a certificate for a storefront or a custom domain.

    A 200 authorises issuance; anything else denies it, which is what stops
    Caddy from spending Let's Encrypt rate limit on hostnames that are not ours.
    The request is authenticated with the shared ``CADDY_ASK_SECRET`` so the
    endpoint is inert even if it is ever reached from outside.

    What is authorised:

    * the platform hosts (marketing / app / admin),
    * any label under the storefront apex (``<slug>.onewooz.site``),
    * a custom domain present in the verified-domain set in Redis — empty until
      the ``domains`` app (Phase 6) populates it, so unknown domains are denied
      by default.
    """

    PATH = "/internal/caddy/ask"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.rstrip("/") == self.PATH:
            return self._decide(request)
        return self.get_response(request)

    def _decide(self, request):
        import hmac

        expected = getattr(settings, "CADDY_ASK_SECRET", "") or ""
        provided = request.GET.get("secret", "")
        if not expected or not hmac.compare_digest(provided, expected):
            return HttpResponseForbidden("forbidden")

        domain = (request.GET.get("domain") or "").strip().lower().rstrip(".")
        if not domain or len(domain) > 253:
            return HttpResponseForbidden("bad domain")
        if domain.startswith("www."):
            domain = domain[4:]

        if self._is_platform(domain) or self._is_apex(domain) or self._is_verified(domain):
            return HttpResponse("ok", content_type="text/plain")
        return HttpResponseForbidden("not authorised")

    @staticmethod
    def _is_platform(domain):
        names = {
            getattr(settings, name, "")
            for name in ("MARKETING_HOST", "APP_HOST", "ADMIN_HOST")
        }
        names.discard("")
        return domain in names

    @staticmethod
    def _is_apex(domain):
        apex = (getattr(settings, "STOREFRONT_APEX", "") or "").lower()
        return bool(apex) and (domain == apex or domain.endswith("." + apex))

    @staticmethod
    def _is_verified(domain):
        key = getattr(settings, "CADDY_VERIFIED_DOMAINS_KEY", "onewooz:verified_domains")
        try:
            from django_redis import get_redis_connection

            conn = get_redis_connection("default")
            return bool(conn.sismember(key, domain))
        except Exception:
            # No Redis (dev) or a transient error: fail closed. Platform and
            # storefront-apex hosts are handled above and do not depend on this.
            return False


class HostRoutingMiddleware:
    """Resolve the Host header to a surface and select its URLconf.

    This replaces the path-rewriting approach used by the platform this
    product supersedes, which mutated ``request.path_info`` in place to fake a
    ``/website/s/<slug>/`` prefix and then issued redirects in both directions
    to hide the rewrite. Selecting a urlconf instead means storefront routes
    are written exactly as a visitor sees them, and a storefront request is
    structurally incapable of resolving a dashboard route.

    Sets on the request:

    ``request.surface``       one of :class:`core.hosts.Surface`
    ``request.site_slug``     subdomain label for apex storefronts, else ""
    ``request.is_custom_domain``
    ``request.urlconf``       the module for this surface
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host()

        # Populated by the storefront app once tenancy exists.
        request.site = None
        request.dev_path_mode = False

        if settings.DEBUG and host_utils.is_loopback(host):
            return self._route_by_path(request)

        # Django resets the script prefix per request from the WSGI
        # environment, but set it explicitly so a worker thread that last
        # served a prefixed development request cannot leak it into this one.
        set_script_prefix("/")

        resolution = host_utils.resolve(host)

        request.surface = resolution.surface
        request.site_slug = resolution.slug
        request.is_custom_domain = resolution.is_custom_domain

        if resolution.surface is host_utils.Surface.UNKNOWN:
            # An unrecognised host is not ours to serve. In production this is
            # rarely reached: Caddy refuses to issue a certificate for a host
            # the ask endpoint does not recognise, so TLS fails first. In
            # development it usually means the developer used a host that no
            # surface claims, so say which ones do.
            if settings.DEBUG:
                return self._unknown_host_help(resolution.host)
            return HttpResponseNotFound("Not found")

        request.urlconf = _URLCONF_FOR_SURFACE[resolution.surface]

        # Storefronts POST to their own host, which is not in the static
        # CSRF_TRUSTED_ORIGINS list. Rather than exempting those views from
        # CSRF entirely — the reference platform exempted 33 of them — trust
        # the origin of the already-verified host, for this request only.
        if resolution.surface is host_utils.Surface.STOREFRONT:
            scheme = "https" if request.is_secure() else "http"
            request.csrf_trusted_origins_override = [f"{scheme}://{resolution.host}"]

        return self.get_response(request)

    # -- single-host development ------------------------------------------

    def _route_by_path(self, request):
        """Serve all four surfaces from one loopback host, split by path.

        127.0.0.1 has no subdomains, so the Host header cannot separate the
        surfaces the way it does in production. The first path segment does it
        instead, and is then moved from ``path_info`` into ``SCRIPT_NAME``.

        Moving it rather than merely stripping it is the important part: the
        script prefix is what ``reverse()`` and ``{% url %}`` prepend, so every
        link, redirect and form action comes out correctly prefixed without a
        single view or template knowing this mode exists.
        """
        resolution = host_utils.resolve_dev_path(request.path_info)

        request.surface = resolution.surface
        request.site_slug = resolution.slug
        request.is_custom_domain = False
        request.dev_path_mode = True

        if resolution.surface is host_utils.Surface.UNKNOWN:
            return self._unknown_host_help(request.get_host())

        request.urlconf = _URLCONF_FOR_SURFACE[resolution.surface]

        if resolution.prefix:
            request.path_info = resolution.remainder
            request.META["SCRIPT_NAME"] = resolution.prefix
            set_script_prefix(f"{resolution.prefix}/")
        else:
            set_script_prefix("/")

        return self.get_response(request)

    @staticmethod
    def _unknown_host_help(host: str) -> HttpResponse:
        """Development-only page explaining the host map.

        Four surfaces behind one process is not obvious from a 404, and the
        address ``runserver`` prints is not one of them by default.
        """
        port = getattr(settings, "DEV_PORT", "8000")
        prefixes = getattr(settings, "DEV_SURFACE_PREFIXES", {})

        rows = [
            ("Marketing", "marketing", settings.MARKETING_HOST),
            ("Dashboard", "app", settings.APP_HOST),
            ("Admin", "admin", settings.ADMIN_HOST),
            ("Storefronts", "storefront", f"*.{settings.STOREFRONT_APEX}"),
        ]

        listed = "".join(
            f"<tr>"
            f'<td style="padding:7px 24px 7px 0;white-space:nowrap">{label}</td>'
            f'<td style="padding:7px 24px 7px 0"><a style="color:#4b2bf5" '
            f'href="http://127.0.0.1:{port}{prefixes.get(key, "") or "/"}">'
            f'127.0.0.1:{port}{prefixes.get(key, "") or "/"}</a></td>'
            f'<td style="padding:7px 0;color:#73736a">{named}:{port}</td>'
            f"</tr>"
            for label, key, named in rows
        )

        return HttpResponseNotFound(
            f"""<!doctype html><meta charset="utf-8">
<title>No surface for this path</title>
<div style="font:15px/1.6 ui-sans-serif,system-ui,sans-serif;max-width:46rem;margin:5rem auto;padding:0 1.5rem;color:#171714">
  <h1 style="font-size:1.375rem;letter-spacing:-.02em;margin:0 0 .5rem">Nothing serves this address</h1>
  <p style="color:#55554d;margin:0 0 1.75rem">
    OneWooz runs four surfaces in one process. In production each has its own
    host. Locally you can reach them two ways &mdash; by path on
    <code>127.0.0.1</code>, or by name (<code>*.localhost</code> resolves to
    127.0.0.1 in current browsers, so no hosts file editing is needed).
  </p>
  <table style="border-collapse:collapse;font-size:14px">
    <tr style="text-align:left;color:#9b9b92;font-size:12px;letter-spacing:.08em;text-transform:uppercase">
      <th style="padding:0 24px 8px 0;font-weight:600">Surface</th>
      <th style="padding:0 24px 8px 0;font-weight:600">By path</th>
      <th style="padding:0 0 8px;font-weight:600">By host</th>
    </tr>
    {listed}
  </table>
  <p style="color:#55554d;margin:1.75rem 0 0;font-size:14px">
    A storefront needs its store named: <code>/s/&lt;store&gt;/</code> by path,
    or <code>&lt;store&gt;.{settings.STOREFRONT_APEX}:{port}</code> by host.
  </p>
</div>""",
            content_type="text/html; charset=utf-8",
        )


class SecurityHeadersMiddleware(MiddlewareMixin):
    """Per-request CSP nonce and the headers Django does not set itself."""

    def process_request(self, request):
        # 16 bytes of entropy, base64 — comfortably above the 128 bits the CSP
        # specification requires of a nonce.
        request.csp_nonce = secrets.token_urlsafe(16)

    def process_response(self, request, response):
        if not response.has_header("Content-Security-Policy"):
            policy = self._build_policy(request)
            if policy:
                response["Content-Security-Policy"] = policy

        if not response.has_header("Permissions-Policy"):
            response["Permissions-Policy"] = settings.PERMISSIONS_POLICY

        # Isolate every surface from cross-origin resource probing. Storefronts
        # need to be embeddable-adjacent (payment gateway returns, oEmbed) so
        # they get the same-site variant rather than same-origin.
        if not response.has_header("Cross-Origin-Opener-Policy"):
            response["Cross-Origin-Opener-Policy"] = "same-origin"
        if not response.has_header("Cross-Origin-Resource-Policy"):
            surface = getattr(request, "surface", None)
            response["Cross-Origin-Resource-Policy"] = (
                "same-site" if surface == host_utils.Surface.STOREFRONT else "same-origin"
            )

        return response

    def _build_policy(self, request) -> str:
        nonce = getattr(request, "csp_nonce", None)
        if not nonce:
            return ""

        script_src = list(settings.CSP_SCRIPT_SRC) + [f"'nonce-{nonce}'"]

        # A storefront whose owner has opted into custom tracking scripts needs
        # a looser script-src. That relaxation applies to that storefront host
        # only and never to the dashboard or admin surfaces.
        site = getattr(request, "site", None)
        if site is not None and getattr(site, "allows_custom_scripts", False):
            script_src.append("'unsafe-inline'")
            script_src.extend(getattr(settings, "CSP_TENANT_SCRIPT_HOSTS", ()))

        directives = {
            "default-src": settings.CSP_DEFAULT_SRC,
            "script-src": tuple(script_src),
            "style-src": settings.CSP_STYLE_SRC,
            "font-src": settings.CSP_FONT_SRC,
            "img-src": settings.CSP_IMG_SRC,
            "connect-src": settings.CSP_CONNECT_SRC,
            "frame-ancestors": settings.CSP_FRAME_ANCESTORS,
            "base-uri": settings.CSP_BASE_URI,
            "form-action": settings.CSP_FORM_ACTION,
            "object-src": settings.CSP_OBJECT_SRC,
        }
        return "; ".join(f"{name} {' '.join(values)}" for name, values in directives.items())
