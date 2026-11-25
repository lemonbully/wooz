"""Shared OneWooz settings.

Environment-specific modules (``dev``, ``prod``) import everything from here and
override. Never import this module directly as ``DJANGO_SETTINGS_MODULE``.

Host topology
-------------
One Django process serves four distinct surfaces, separated by host. A single
middleware (:class:`core.middleware.HostRoutingMiddleware`) resolves the host
and swaps ``request.urlconf``, so each surface has its own URL namespace and no
surface can reach another's routes.

    onewooz.com            -> onewooz.urls.marketing
    app.onewooz.com        -> onewooz.urls.app
    arcane.onewooz.com     -> onewooz.urls.admin
    *.onewooz.site         -> onewooz.urls.storefront   (generated storefronts)
    verified custom domain -> onewooz.urls.storefront

Storefronts deliberately live on a *different registrable domain* from the
brand and dashboard. That is a public-suffix boundary: a tenant storefront
cannot set a cookie that reaches onewooz.com, and tenant-injected script is
cross-origin to the dashboard.
"""

from __future__ import annotations

from pathlib import Path

from . import env

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent
SECRETS_DIR = BASE_DIR / ".secrets"

env.load_dotenv(BASE_DIR / ".env")

# --------------------------------------------------------------------------
# Hosts
# --------------------------------------------------------------------------

MARKETING_HOST = env.get("ONEWOOZ_MARKETING_HOST", "onewooz.com")
APP_HOST = env.get("ONEWOOZ_APP_HOST", "app.onewooz.com")
ADMIN_HOST = env.get("ONEWOOZ_ADMIN_HOST", "arcane.onewooz.com")
# OneWooz Apps, the marketplace: its own surface (browse + library only; installs
# and payments happen on APP_HOST behind a fresh re-authentication).
MARKETPLACE_HOST = env.get("ONEWOOZ_MARKETPLACE_HOST", "marketplace.onewooz.com")

# Apex under which generated storefronts get their subdomain. Separate
# registrable domain from MARKETING_HOST on purpose  see module docstring.
STOREFRONT_APEX = env.get("ONEWOOZ_STOREFRONT_APEX", "onewooz.site")

# Apex for UGC collaboration (referral) storefronts. Each verified collaborator
# sells the store's assigned products on <their-subdomain>.<REFERRAL_ROOT_DOMAIN>,
# e.g. ayushman.myugcstore.com. A separate registrable domain again, so tenant
# script on a collaborator store can never reach the dashboard session.
REFERRAL_ROOT_DOMAIN = env.get("ONEWOOZ_REFERRAL_ROOT_DOMAIN", "myugcstore.com")

# Where owners point their custom domain (a CNAME/ALIAS target). Set this to the
# public hostname of the Caddy edge that terminates TLS on demand (e.g.
# "edge.onewooz.com") so custom domains get an automatic free certificate. When
# unset, the store's own <slug>.onewooz.site is shown (served by the ALB, no
# per-domain cert). The verify step checks the domain resolves to this target.
CUSTOM_DOMAIN_TARGET = env.get("ONEWOOZ_CUSTOM_DOMAIN_TARGET", "")

# Platform billing  OneWooz's commerce fee on each store's orders. This is the
# default applied to a store that has no explicit BillingConfig yet; it is
# written onto the per-store BillingConfig the first time billing runs (see
# commerce.billing_service.get_billing_config), so OneWooz can later override the
# rate for an individual store without touching this default.
PLATFORM_BILLING = {
    "enabled": True,
    # "percent" charges a share of each order's total; "fixed" a flat amount.
    "per_order_charge_type": env.get("ONEWOOZ_COMMISSION_TYPE", "percent"),
    "per_order_charge_value": env.get("ONEWOOZ_COMMISSION_VALUE", "2.00"),
    # The commerce fee is only ever charged on **delivered** orders. This is a
    # fixed product rule, not a per-store option: billing normalises every
    # store's charge event to "delivered" regardless of what is set here (see
    # commerce.billing_service.get_billing_config / COMMERCE_FEE_EVENT).
    "order_charge_event": env.get("ONEWOOZ_COMMISSION_EVENT", "delivered"),
    "billing_cycle_type": env.get("ONEWOOZ_BILLING_CYCLE", "monthly"),
}

# IP geolocation  turns a sign-in / visitor IP into a place for the security
# page and storefront analytics (see accounts.geo). If a MaxMind GeoIP2 database
# is present (GEOIP_PATH + the geoip2 package) it is used first, offline. For
# accuracy without any database, a free web service is queried for public IPs and
# the result cached per IP; set GEOIP_HTTP_FALLBACK=false to disable that and
# rely on the offline database (or country header) alone.
GEOIP_PATH = env.get("ONEWOOZ_GEOIP_PATH", "") or None
GEOIP_HTTP_FALLBACK = env.get_bool("ONEWOOZ_GEOIP_HTTP_FALLBACK", True)
# {ip} is substituted with the address. The default (ipwho.is) needs no API key
# and returns JSON with city / region / country / country_code.
GEOIP_HTTP_URL = env.get("ONEWOOZ_GEOIP_HTTP_URL", "https://ipwho.is/{ip}")
GEOIP_HTTP_TIMEOUT = float(env.get("ONEWOOZ_GEOIP_HTTP_TIMEOUT", "2.5"))

# Fixed public IP(s) of the custom-domain edge (the Caddy host's Elastic IP).
# Owners whose registrar can't CNAME a root domain add an A record to these
# instead. Comma-separated. Verification also accepts a domain that resolves to
# any of these. Keep in step with the DNS name in CUSTOM_DOMAIN_TARGET.
CUSTOM_DOMAIN_A_RECORDS = env.get_list("ONEWOOZ_CUSTOM_DOMAIN_A_RECORDS", []) or []

# Redis set of verified custom domains that Caddy's on-demand-TLS ask endpoint
# consults (SISMEMBER) before issuing a certificate. Populated when an owner
# verifies a domain; see commerce.views.domains and core.middleware.CaddyAskMiddleware.
CADDY_VERIFIED_DOMAINS_KEY = env.get(
    "ONEWOOZ_CADDY_VERIFIED_DOMAINS_KEY", "onewooz:verified_domains"
)

# Hosts that are ours and are never treated as a tenant storefront.
PLATFORM_HOSTS = {MARKETING_HOST, f"www.{MARKETING_HOST}", APP_HOST, ADMIN_HOST, MARKETPLACE_HOST}

ALLOWED_HOSTS = env.get_list("ONEWOOZ_ALLOWED_HOSTS") or [
    MARKETING_HOST,
    f"www.{MARKETING_HOST}",
    APP_HOST,
    ADMIN_HOST,
    MARKETPLACE_HOST,
    f".{STOREFRONT_APEX}",
    f".{REFERRAL_ROOT_DOMAIN}",
]

# Verified tenant domains are admitted at request time against a cached set
# rather than by editing ALLOWED_HOSTS. See core.hosts.is_known_host.

# --------------------------------------------------------------------------
# Applications
# --------------------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    # Django's model admin, mounted under the arcane admin surface at /db/ and
    # gated by AdminAccessMiddleware (staff + email/password + emailed-OTP). It
    # is registered tenant-safely in custom_admin/admin.py (querysets use the
    # unscoped ``all_objects`` manager so TenantScopedModel list pages evaluate).
    "django.contrib.admin",
    # Must precede staticfiles: disables runserver's own static handler so
    # WhiteNoise serves static in dev too, giving one code path (and correct
    # cache headers) across dev and prod. No-op outside the runserver command.
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "core",
    "accounts",
    "tenancy",
    "commerce",
    "storefront",
    "web",
    "billing",
    "outreach",
    "ud",
    "custom_admin",
    # OneWooz Apps: the marketplace (see MARKETPLACE_PLAN.md).
    "marketplace",
    # Admin-written AI prompts + what is generated from them (store llms.txt).
    "prompts",
    # Ask OneWooz community at onewooz.com/ask (questions, answers, votes).
    "community",
]

MIDDLEWARE = [
    # Correlation id first so every later log line and audit row carries it.
    "core.middleware.RequestIDMiddleware",
    # CloudFront safety + CDN preconnect. Directly under the request-id layer so
    # it sees the final response (every cookie set below it). See its docstring.
    "core.middleware.EdgeDeliveryMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    # Health probes short-circuit before any host resolution: load balancer
    # checks arrive with a non-matching Host header and must never 404.
    "core.middleware.HealthProbeMiddleware",
    # www -> apex, explicit :443 -> none, and HTTP -> HTTPS for the platform
    # hosts, as one clean 301 (the ALB's own redirects write ":443" into the
    # Location). After the health probe so probes are never redirected.
    "core.middleware.CanonicalPlatformHostMiddleware",
    # Answers Caddy's on-demand-TLS "ask" before host resolution, for the same
    # reason as the health probe: it arrives with a non-matching Host header.
    "core.middleware.CaddyAskMiddleware",
    # Resolves the host to a surface and swaps request.urlconf.
    "core.middleware.HostRoutingMiddleware",
    # Attaches request.site on storefront requests (no-op elsewhere). Runs
    # after host routing so request.surface / site_slug are set, and before the
    # CSP is built so a storefront's custom-script opt-in is visible.
    "storefront.middleware.CurrentSiteMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Admin "impersonate a store owner": on the app surface only, overlays
    # request.user with the impersonated owner so every gate/view/template below
    # sees the owner. No-op on every other surface. Runs before the dashboard's
    # own gates so they evaluate against the owner being impersonated.
    "accounts.impersonation.ImpersonationMiddleware",
    # Signed-in state across surfaces (community.bridge): the app surface keeps
    # the identity-free "ow_signed_in" navbar hint in step with the login, and
    # Ask pages link a signed-in OneWooz user to their community member.
    "community.bridge.SignedInHintMiddleware",
    "community.bridge.AskAccountLinkMiddleware",
    # No-op on every surface but admin, where it enforces the network
    # allowlist, the staff flag, the passkey requirement and the idle timeout.
    "custom_admin.middleware.AdminAccessMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    # Subscription-renewal gate: when an owner's paid subscription has lapsed,
    # seals the whole owner dashboard behind a universal renewal popup. Runs
    # before the feature gate so an expired owner sees "renew", not "upgrade".
    # No-op on every other surface.
    "billing.middleware.SubscriptionRenewalGateMiddleware",
    # Staff access gating: confines an owner-added team member to the pages and
    # actions they were granted. Runs before the plan gate so a staff member sees
    # "no access" rather than an upgrade wall. No-op for owners and elsewhere.
    "commerce.middleware.StaffAccessGateMiddleware",
    # Plan-based feature gating for the store dashboard: intercepts a locked
    # store route (free plan) before its view runs. No-op on every other surface.
    "commerce.middleware.PlanFeatureGateMiddleware",
    # Remembers the last store page rendered, for the dashboard's "Resume
    # where you left off" → Continue. Response-side only; no-op elsewhere.
    "commerce.middleware.LastStorePageMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# Fallback only. HostRoutingMiddleware sets request.urlconf on every request.
ROOT_URLCONF = "onewooz.urls.marketing"

WSGI_APPLICATION = "onewooz.wsgi.application"
ASGI_APPLICATION = "onewooz.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                # Populates the ``{{ csrf_token }}`` template variable. Without it
                # that variable renders empty, so any AJAX call that reads its
                # token from ``{{ csrf_token }}`` (the wishlist add/remove and the
                # account page) sends an empty X-CSRFToken and is rejected 403.
                "django.template.context_processors.csrf",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.security",
                "ud.context_processors.dashboard_nav",
                # Lazy: an updated user agreement the signed-in owner must accept.
                "billing.context_processors.agreement_update_gate",
                "web.context_processors.footer_pages",
                # Marketing navbar: "Dashboard" instead of "Sign in" when signed in.
                "community.bridge.nav_context",
                "web.context_processors.seo",
            ],
        },
    },
]

# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
# Overridden per environment. Development uses SQLite so the project runs with
# no manual setup; production requires MySQL credentials from the environment.

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = ["accounts.backends.EmailPasswordBackend"]

# Argon2id first: the winner of the Password Hashing Competition and the only
# entry here that is memory-hard. The rest exist so pre-existing hashes of
# other algorithms are upgraded transparently on next successful login.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# A route name, not a path. Django resolves it through reverse(), which applies
# the script prefix  so the redirect lands correctly whether the dashboard is
# on its own host or under a path prefix in single-host development.
LOGIN_URL = "accounts:sign_in"

# Surfaces are separated by host in production; only development splits them by
# path. Declared empty here so code can read the setting unconditionally.
DEV_SURFACE_PREFIXES: dict[str, str] = {}
DEV_PORT = ""

# --------------------------------------------------------------------------
# Sessions and cookies
# --------------------------------------------------------------------------
# Cookies are host-only (no COOKIE_DOMAIN). That is what isolates the four
# surfaces from each other and each tenant storefront from every other: a
# cookie set on app.onewooz.com is never sent to a storefront host, and a
# cookie set on one custom domain is never sent to another.

SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_NAME = "ow_sid"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_DOMAIN = None
SESSION_COOKIE_AGE = env.get_int("ONEWOOZ_SESSION_AGE", 60 * 60 * 24 * 14)
SESSION_EXPIRE_AT_BROWSER_CLOSE = False

# Admin sessions expire far sooner; enforced in custom_admin middleware.
ADMIN_SESSION_IDLE_TIMEOUT = env.get_int("ONEWOOZ_ADMIN_IDLE_TIMEOUT", 30 * 60)

CSRF_COOKIE_NAME = "ow_csrf"
CSRF_COOKIE_HTTPONLY = False  # read by fetch() to populate X-CSRFToken
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_DOMAIN = None
CSRF_USE_SESSIONS = False
# A failed CSRF check shows OneWooz's own branded page (core.errors), never
# Django's bare "Forbidden (403) CSRF verification failed" page.
CSRF_FAILURE_VIEW = "core.errors.csrf_failure"

# Static origins. Verified tenant domains are added per-request by
# HostRoutingMiddleware rather than enumerated here  that is what lets
# storefront POST endpoints keep CSRF protection instead of being exempted.
CSRF_TRUSTED_ORIGINS = [
    f"https://{MARKETING_HOST}",
    f"https://www.{MARKETING_HOST}",
    f"https://{APP_HOST}",
    f"https://{ADMIN_HOST}",
    f"https://{MARKETPLACE_HOST}",
]

# --------------------------------------------------------------------------
# Security headers
# --------------------------------------------------------------------------

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"

# Terminated by Caddy, which sets X-Forwarded-Proto.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

PERMISSIONS_POLICY = (
    "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
    "magnetometer=(), microphone=(), payment=(self), usb=()"
)

# Content-Security-Policy. Built per-request with a nonce; see
# core.middleware.SecurityHeadersMiddleware. Storefronts may relax script-src
# only when their owner has explicitly opted into custom tracking scripts, and
# only for that storefront host.
CSP_DEFAULT_SRC = ("'self'",)
CSP_SCRIPT_SRC = ("'self'",)
CSP_STYLE_SRC = ("'self'", "https://fonts.googleapis.com")
CSP_FONT_SRC = ("'self'", "https://fonts.gstatic.com", "data:")
CSP_IMG_SRC = ("'self'", "data:", "blob:", "https:")
CSP_CONNECT_SRC = ("'self'",)
CSP_FRAME_ANCESTORS = ("'none'",)
CSP_BASE_URI = ("'self'",)
# Google's sign-in origin: "Connect with Google" (Google Merchant) is a form POST
# that redirects to the OAuth consent screen, and form-action covers redirects.
CSP_FORM_ACTION = ("'self'", "https://accounts.google.com")
CSP_OBJECT_SRC = ("'none'",)
# Audio/video sources. Self-hosted media plus data:/blob:. Storefronts widen
# this (below) to admit the short-video hosts their reels are served from.
CSP_MEDIA_SRC = ("'self'", "data:", "blob:")
# Web workers. Only the dashboard's own push service worker (/sw.js on the app
# host) is registered; stated explicitly rather than left to fall back.
CSP_WORKER_SRC = ("'self'",)

# The generated storefronts are ported from a Tailwind + Font Awesome design
# that relies on inline <style> blocks, dynamic style="" attributes and a
# handful of inline scripts. They live on a *separate registrable domain*
# (onewooz.site)  a public-suffix boundary that already isolates them from the
# dashboard and from each other  so the storefront surface, and only that
# surface, gets a CSP loose enough for that markup. The dashboard, admin and
# marketing surfaces keep the strict nonce-only policy above.
CSP_STOREFRONT_STYLE_EXTRA = (
    "'unsafe-inline'",
    "https://cdnjs.cloudflare.com",
    "https://fonts.googleapis.com",
)
CSP_STOREFRONT_SCRIPT_EXTRA = (
    "'unsafe-inline'",
    # The ported storefront designs load the Tailwind Play CDN, which generates
    # their utility classes at runtime; without it the storefront renders
    # unstyled. It injects styles inline, already covered by CSP_STOREFRONT_STYLE_EXTRA.
    "https://cdn.tailwindcss.com",
    # Payment gateway checkout SDKs, loaded on the checkout page.
    "https://checkout.razorpay.com",
    "https://sdk.cashfree.com",
    "https://*.cashfree.com",
)
CSP_STOREFRONT_FONT_EXTRA = ("https://cdnjs.cloudflare.com",)
# Video hosts a storefront may stream its reels / short videos from. Includes
# the sample-video bucket the built-in D2C demo store uses so its reels autoplay
# (owners uploading their own videos serve them from our own media, i.e. 'self').
CSP_STOREFRONT_MEDIA_EXTRA = (
    "https://storage.googleapis.com",
    "https://commondatastorage.googleapis.com",
)
# The storefront's frame-src baseline plus the payment gateways' hosted checkout
# frames/popups. Falls back to CSP_FRAME_SRC on non-storefront surfaces.
CSP_FRAME_SRC = ("'self'",)
CSP_STOREFRONT_FRAME_EXTRA = (
    "https://api.razorpay.com",
    "https://checkout.razorpay.com",
    # Any Cashfree subdomain  the v3 SDK's hosted checkout iframe varies by
    # environment and device (see the payment notes below); the wildcard keeps
    # the storefront checkout from breaking on mobile.
    "https://*.cashfree.com",
    # A product's promo video may be a YouTube link (see
    # storefront.views._build_product_video); its embed plays in an iframe on the
    # PDP, so the embed hosts must be admitted or the frame is CSP-blocked.
    "https://www.youtube.com",
    "https://www.youtube-nocookie.com",
)
# Google Maps embeds: the contact page's "Find us on the map" frame, and the
# live preview under the map field in the owner's content editor (app surface).
# storefront.maps normalises every embed onto these hosts.
CSP_MAP_FRAME_HOSTS = (
    "https://www.google.com",
    "https://maps.google.com",
)
CSP_STOREFRONT_FRAME_EXTRA = CSP_STOREFRONT_FRAME_EXTRA + CSP_MAP_FRAME_HOSTS
# Video players a Business Hub post may embed in the owner dashboard (app
# surface) when its video is a YouTube / Vimeo link (see ud.models.video_link_embed).
CSP_HUB_VIDEO_FRAME_HOSTS = (
    "https://www.youtube-nocookie.com",
    "https://player.vimeo.com",
)
# XHR/fetch endpoints the gateway SDKs call from the storefront page.
CSP_STOREFRONT_CONNECT_EXTRA = (
    "https://api.razorpay.com",
    "https://lumberjack.razorpay.com",
    "https://*.cashfree.com",
)
# Gateway checkout SDKs open their hosted checkout by submitting a form to the
# gateway (Cashfree v3 POSTs to api.cashfree.com/pg/view/sessions/checkout), so
# the storefront's form-action must admit them or the payment window is blocked.
CSP_STOREFRONT_FORM_EXTRA = (
    "https://*.cashfree.com",
    "https://api.razorpay.com",
    "https://checkout.razorpay.com",
)
# When an owner opts a storefront into custom tracking scripts, these hosts are
# also admitted to that storefront's script-src. Covers every provider offered
# under Tracking & Ads: Google (Analytics/Tag Manager/Ads), Meta, TikTok,
# Pinterest and Microsoft (Bing UET).
CSP_TENANT_SCRIPT_HOSTS = (
    "https://www.googletagmanager.com",
    "https://www.google-analytics.com",
    "https://www.googleadservices.com",
    "https://googleads.g.doubleclick.net",
    "https://connect.facebook.net",
    "https://analytics.tiktok.com",
    "https://s.pinimg.com",
    "https://bat.bing.com",
)
# The fetch/beacon endpoints those same tags call, admitted to connect-src under
# the identical opt-in. Image beacons ride img-src (already allows https:).
CSP_TENANT_CONNECT_HOSTS = (
    "https://www.google-analytics.com",
    "https://region1.google-analytics.com",
    "https://analytics.google.com",
    "https://www.googletagmanager.com",
    "https://googleads.g.doubleclick.net",
    "https://www.facebook.com",
    "https://analytics.tiktok.com",
    "https://ct.pinterest.com",
    "https://bat.bing.com",
)

# The owner dashboard's content editor loads the free (self-hostable) TinyMCE
# build from jsDelivr  script, skin CSS and icon fonts. Admitted on the app
# surface only. To self-host later, drop the files under static/ and clear this.
CSP_EDITOR_HOSTS = ("https://cdn.jsdelivr.net",)

# Platform-owned analytics (Microsoft Clarity + Google Analytics), loaded from
# base.html on the MARKETING surface only (onewooz.com). The owner dashboard,
# admin and tenant storefronts must not send data to a third party, so both the
# snippet (base.html) and these CSP hosts are gated to marketing in
# core.middleware  never the app/admin surfaces, and never a storefront (which
# renders from storefront/theme_base.html and keeps its own opt-in tracking).
CSP_ANALYTICS_SCRIPT_HOSTS = (
    "https://www.clarity.ms",
    # The loader on www.clarity.ms pulls the versioned engine from
    # scripts.clarity.ms; the wildcard covers both and any future subdomain.
    "https://*.clarity.ms",
    # Google Analytics tag (gtag.js).
    "https://www.googletagmanager.com",
)
CSP_ANALYTICS_CONNECT_HOSTS = (
    "https://www.clarity.ms",
    "https://*.clarity.ms",
    "https://c.bing.com",
    # Google Analytics collection endpoints.
    "https://www.googletagmanager.com",
    "https://www.google-analytics.com",
    "https://region1.google-analytics.com",
    "https://analytics.google.com",
    "https://www.google.com",
)

# The marketing subscription checkout hands off to Cashfree hosted checkout via
# their JS SDK. That one page (billing.views.start renders it) sets
# request.csp_payment, and only then are these hosts admitted  the rest of the
# strict marketing surface is untouched.
#
# A wildcard over Cashfree's subdomains, scoped to this one checkout response.
# The v3 SDK spreads its hosted flow across several subdomains that differ
# between sandbox and production and by device  sdk, payments, payments-test,
# sandbox, api, www  and on mobile it renders the checkout as a full-screen
# iframe. Enumerating them exactly meant a missing host (e.g. payments-test in
# connect-src) blocked that iframe with Chrome's "This content is blocked" page.
# The wildcard admits any cashfree.com subdomain so the flow is never broken by a
# host we forgot; it applies only to the payment page.
CSP_PAYMENT_SCRIPT_EXTRA = ("https://sdk.cashfree.com", "https://*.cashfree.com")
CSP_PAYMENT_FRAME_EXTRA = ("https://*.cashfree.com",)
CSP_PAYMENT_CONNECT_EXTRA = ("https://*.cashfree.com",)
CSP_PAYMENT_FORM_EXTRA = ("https://*.cashfree.com",)

# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------
# name -> (limit, window seconds). Enforced by core.ratelimit.
# The reference platform had no rate limiting on any storefront endpoint.

RATE_LIMITS = {
    "login.ip": (50, 300),
    "login.identifier": (15, 900),
    "otp.send.email": (5, 900),
    "otp.send.ip": (20, 900),
    "otp.verify": (5, 900),
    "magiclink.send": (5, 900),
    "signup.ip": (10, 3600),
    "password_reset.send": (5, 3600),
    "domain.verify": (5, 120),
    # Admin Excel reports (custom_admin.views.export_excel), per staff account.
    "admin.export": (30, 300),
    "cart.mutate": (120, 60),
    # Content editor's live Google Maps preview (may resolve a share link).
    "editor.map_preview": (30, 60),
    # Magic SEO pencil (Edit content > SEO): runs started per store per hour.
    # Each run is many AI calls, so this bounds cost; status polls are not counted.
    "editor.magic_seo": (6, 3600),
    "checkout.submit": (10, 300),
    # Storefront account self-service (details / address book saves).
    "account.profile": (30, 300),
    # Owner "Delete My Account" (accounts.views.account_delete_*), per account.
    "account.delete": (10, 900),
    "analytics.ingest": (300, 60),
    "webhook.courier": (600, 60),
    # Live courier tracking pulls: store-wide "Sync now" (Shipping page) and the
    # per-order Sync button. Each call hits the courier's API, so cap them.
    "shipping.tracking_sync": (6, 600),
    "shipping.order_sync": (60, 600),
    # Live Shiprocket wallet "Refresh" + pickup-location lookups.
    "shipping.wallet_refresh": (20, 600),
    "shipping.pickup_lookup": (15, 600),
    # Dashboard AI assistant  cap generations per owner so a runaway loop or a
    # compromised session can't run up the provider bill. Images are the pricier
    # call, so this is deliberately modest.
    "ai.generate": (30, 3600),
    "enquiry.submit": (10, 3600),
    # onewooz.com/ask: questions per visitor IP, plus a platform-wide daily
    # ceiling on AI answers so a flood cannot run up the OpenAI bill (over it,
    # questions still save and wait for a team reply). Polling an answer's
    # status is cheap but still capped per IP.
    "ask.submit": (8, 3600),
    "ask.ai.global": (500, 86400),
    "ask.status": (300, 300),
    # Ask OneWooz community. Joining is an email code (per address + per IP);
    # posting limits are per member.
    "ask.join.send": (5, 900),
    "ask.join.send.ip": (20, 900),
    "ask.join.verify": (6, 900),
    "ask.profile.save": (12, 3600),
    "ask.link": (30, 3600),  # dashboard -> Ask account-link hand-offs per user  # photo / website / brand edits per member
    "ask.post": (10, 3600),
    "ask.answer": (30, 3600),
    "ask.reply": (40, 3600),
    "ask.vote": (120, 300),
    "ask.report": (20, 3600),
    "ask.search": (90, 60),
    # Live desk column refreshes, weekly digest sign-ups, and the team desk's
    # AI-drafted answers (an OpenAI call each, so per team member).
    "ask.live": (120, 300),
    "ask.digest.subscribe": (6, 3600),
    "ask.digest.subscribe.email": (3, 86400),
    "ask.desk.suggest": (60, 3600),
    "review.submit": (5, 3600),
    "newsletter.subscribe": (10, 3600),
    # Business Hub support tickets, per owner.
    "support.ticket": (10, 3600),
    # Google Merchant: full syncs an owner may start per hour.
    "merchant.sync": (12, 3600),
    # Media library: cap uploads per owner so a compromised session or a bot
    # cannot flood storage. Sized for a genuine bulk import (a catalogue of
    # product photos) while staying well short of an abusive rate.
    "media.upload": (120, 3600),
    # Subscription checkout on the marketing site. The live-breakdown quote is
    # cheap and typed against often; starting a payment is heavier and rarer.
    "subscription.quote": (60, 60),
    "subscription.checkout": (10, 300),
    # Email OTP that gates the subscription payment (agreement acceptance step).
    # Capped per address and per IP so the confirmation mail can't be used to
    # bomb an inbox, and the code can't be brute-forced.
    "subscription.otp.send": (5, 900),
    "subscription.otp.send.ip": (20, 900),
    "subscription.otp.verify": (6, 900),
    # In-dashboard renewal (authenticated owner)  starting a renewal payment or
    # a free-plan switch. Rarer and heavier than a live quote.
    "subscription.renew": (10, 300),
    # Post-payment onboarding form submit, per connection. Loose enough to
    # absorb genuine correction of a mistyped field, tight enough that the
    # account-creating endpoint can't be hammered.
    "onboarding.submit": (20, 300),
    # Browser push: saving this browser's subscription (also re-run after each
    # sign-in) and the "send a test notification" button, per account.
    "push.subscribe": (30, 3600),
    "push.test": (10, 600),
    # OneWooz Apps (marketplace). Re-authentication attempts per account and per
    # IP (a wrong password / code counts), the email code that re-authenticates,
    # hand-offs from the dashboard to the marketplace, installs and paid checkouts.
    "marketplace.reauth": (8, 900),
    "marketplace.reauth.ip": (30, 900),
    "marketplace.reauth.code": (4, 900),
    "marketplace.handoff": (30, 300),
    "marketplace.install": (20, 600),
    "marketplace.buy": (10, 300),
    "marketplace.settings": (30, 300),
}

# --------------------------------------------------------------------------
# Sign-up allowlist (private beta)
# --------------------------------------------------------------------------

# Self-service sign-up is open only to these addresses while OneWooz is in
# private beta; every other address is turned away at the sign-up form. Compared
# case-insensitively. Override with ONEWOOZ_SIGNUP_ALLOWLIST (comma-separated);
# an empty value keeps the built-in test list rather than opening sign-up to all.
SIGNUP_ALLOWLIST = env.get_list(
    "ONEWOOZ_SIGNUP_ALLOWLIST",
    ["brandisebox@gmail.com", "ayushmantiwari2312@gmail.com"],
) or ["brandisebox@gmail.com", "ayushmantiwari2312@gmail.com"]

# When true, one-time codes (sign-in / sign-up OTP) are printed to the terminal
# instead of being emailed  the local-development convenience. Off by default so
# production never logs a live credential; dev settings turn it on.
OTP_CONSOLE_ECHO = env.get_bool("ONEWOOZ_OTP_CONSOLE_ECHO", False)

# When true, a demo store (with a demo category and subcategory) is seeded after
# `migrate` so localhost always has a store to work with. Off by default; the
# seed itself also refuses to run unless DEBUG. Dev settings turn it on.
DEV_SEED_STORE = env.get_bool("ONEWOOZ_DEV_SEED_STORE", False)

# --------------------------------------------------------------------------
# Caching
# --------------------------------------------------------------------------

REDIS_URL = env.get("ONEWOOZ_REDIS_URL", "")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "onewooz-default",
    }
}

CACHE_TTL_SITE = env.get_int("ONEWOOZ_CACHE_TTL_SITE", 6 * 60 * 60)
CACHE_TTL_DOMAIN = env.get_int("ONEWOOZ_CACHE_TTL_DOMAIN", 6 * 60 * 60)
CACHE_TTL_PAGE = env.get_int("ONEWOOZ_CACHE_TTL_PAGE", 6 * 60 * 60)

# --------------------------------------------------------------------------
# Celery
# --------------------------------------------------------------------------

CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = env.get("ONEWOOZ_TIMEZONE", "Asia/Kolkata")
CELERY_ENABLE_UTC = True
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TIME_LIMIT = 600
CELERY_TASK_SOFT_TIME_LIMIT = 540
CELERY_BROKER_URL = "memory://"
CELERY_TASK_ALWAYS_EAGER = True  # dev default; prod overrides

# Periodic schedule. Celery Beat (its own service) fires these; the background
# worker (a separate service) runs the resulting tasks  the actual email sends
# happen there, never in a web request. With the database scheduler in prod
# (django_celery_beat) this static schedule is synced into the DB on startup.
# Marketplace work can run on its own queue so it never delays order emails,
# courier syncs or bills. Opt-in by environment: set ONEWOOZ_MARKETPLACE_QUEUE
# (e.g. "marketplace") only once that queue exists and a worker consumes it
# (deploy/entrypoint.sh: CELERY_QUEUES). Unset = everything on the default
# queue, so deploying this code alone can never strand or break a task.
MARKETPLACE_QUEUE = env.get("ONEWOOZ_MARKETPLACE_QUEUE", "")
CELERY_TASK_ROUTES = {"marketplace.tasks.*": {"queue": MARKETPLACE_QUEUE}} if MARKETPLACE_QUEUE else {}

# Video re-encoding (commerce.tasks.optimise_video) is CPU-heavy and can take a
# few minutes, so it runs on its own "media" queue, consumed by the
# worker-media service that scales from zero when a video arrives. Same opt-in
# rule as above: unset = default queue.
MEDIA_QUEUE = env.get("ONEWOOZ_MEDIA_QUEUE", "")
if MEDIA_QUEUE:
    CELERY_TASK_ROUTES["commerce.tasks.optimise_video"] = {"queue": MEDIA_QUEUE}

# Video compression tunables (commerce.video_optimise).
VIDEO_OPTIMISE_MIN_BYTES = env.get_int("ONEWOOZ_VIDEO_OPTIMISE_MIN_BYTES", 8 * 1024 * 1024)
VIDEO_OPTIMISE_MAX_SIDE = env.get_int("ONEWOOZ_VIDEO_OPTIMISE_MAX_SIDE", 1280)
VIDEO_OPTIMISE_CRF = env.get_int("ONEWOOZ_VIDEO_OPTIMISE_CRF", 26)
# CloudFront distribution in front of the uploads bucket; when set, a replaced
# video's cached copy is invalidated so the small file is served at once.
MEDIA_CDN_DISTRIBUTION_ID = env.get("ONEWOOZ_MEDIA_CDN_DISTRIBUTION_ID", "")

# Longest side, in pixels, of an uploaded raster image after WebP conversion
# (core.image_webp). Phone photos arrive at 4000+ px; 2000 px stays sharp on a
# full-width retina hero and product zoom while cutting the bytes several-fold.
# 0 disables the cap.
IMAGE_MAX_DIMENSION = env.get_int("ONEWOOZ_IMAGE_MAX_DIMENSION", 2000)

# Dashboard notification bell polling (templates/ud/_topbar_actions.html), in
# milliseconds. Every open dashboard tab polls, so this is the single largest
# source of dashboard requests: 15 s visible / 60 s hidden is a third of the
# load of the old 5 s / 10 s with no practical difference to the owner.
BELL_POLL_VISIBLE_MS = env.get_int("ONEWOOZ_BELL_POLL_VISIBLE_MS", 15000)
BELL_POLL_HIDDEN_MS = env.get_int("ONEWOOZ_BELL_POLL_HIDDEN_MS", 60000)

CELERY_BEAT_SCHEDULE = {
    # Ask OneWooz weekly digest. Checks hourly and sends last week's issue once
    # Monday 09:00 IST has passed; community.DigestIssue stops a second send.
    "community-weekly-digest": {
        "task": "community.tasks.send_weekly_digest",
        "schedule": 60.0 * 60,  # hourly
    },
    # Store llms.txt: daily re-check of every store on a verified domain, so
    # catalogue changes reach the file. Unchanged data costs no API call.
    "prompts-refresh-store-llms-txt": {
        "task": "prompts.tasks.refresh_all_store_llms_txt",
        "schedule": 60.0 * 60 * 24,  # daily
    },
    "outreach-dispatch-due-campaigns": {
        "task": "outreach.tasks.dispatch_due_campaigns",
        "schedule": 60.0,  # every minute
    },
    "outreach-reconcile-batches": {
        "task": "outreach.tasks.reconcile_batches",
        "schedule": 300.0,  # every 5 minutes  safety net for batch finalisation
    },
    # Online orders whose payment never completed stay hidden; clear out the
    # long-abandoned ones (after a final gateway check).
    # Google Merchant: re-push every auto-sync store's catalogue (keeps stock
    # changed by orders current and products from expiring after 30 days) and
    # read back approval statuses.
    "commerce-google-merchant-refresh": {
        "task": "commerce.tasks.google_merchant_refresh_all",
        "schedule": 60.0 * 60 * 6,  # every 6 hours
    },
    # Marketplace app logs (console > Logs): drop lines past retention.
    "marketplace-purge-app-logs": {
        "task": "marketplace.tasks.purge_app_logs",
        "schedule": 60.0 * 60 * 24,  # daily
    },
    # Shiprocket wallet: catch a balance below Rs.100 and alert the owner
    # (email + bell) even when nobody has the dashboard open.
    "commerce-check-shiprocket-wallets": {
        "task": "commerce.tasks.check_shiprocket_wallets",
        "schedule": 60.0 * 60,  # hourly
    },
    # Abandoned cart reminders: 1h / 6h / 24h / 48h / 72h after a signed-in
    # shopper's last cart change, at stores that opted in.
    "commerce-abandoned-cart-reminders": {
        "task": "commerce.tasks.send_abandoned_cart_reminders",
        "schedule": 60.0 * 5,  # every 5 minutes
    },
    "commerce-purge-abandoned-checkouts": {
        "task": "commerce.tasks.purge_abandoned_checkouts",
        "schedule": 60.0 * 60 * 24,  # daily
    },
    # Commerce fee bills: each store is billed automatically every 15 days
    # (checked hourly so a bill lands within the hour its period ends).
    "commerce-generate-due-bills": {
        "task": "commerce.tasks.generate_due_bills",
        "schedule": 60.0 * 60,  # hourly
    },
    # Website templates: a store on a design whose yearly licence has lapsed
    # falls back to a free design until the owner buys it again.
    # Deleted owner accounts: close the ones whose recovery window has passed
    # (accounts.deletion). Nothing is erased; only self-service restore ends.
    "accounts-close-lapsed-deleted-accounts": {
        "task": "accounts.tasks.close_lapsed_deleted_accounts",
        "schedule": 60.0 * 60 * 6,  # every 6 hours
    },
    "billing-expire-template-licences": {
        "task": "billing.tasks.expire_template_licences",
        "schedule": 60.0 * 60 * 6,  # every 6 hours
    },
    # OneWooz Apps (marketplace): lapse licences, remind before they lapse,
    # resume paused apps (circuit breaker), confirm missed payments, and migrate
    # owners who never log in into the design library.
    "marketplace-expire-licences": {
        "task": "marketplace.tasks.expire_licences_task",
        "schedule": 60.0 * 15,
    },
    "marketplace-renewal-reminders": {
        "task": "marketplace.tasks.renewal_reminders_task",
        "schedule": 60.0 * 60 * 6,
    },
    "marketplace-breaker-tick": {
        "task": "marketplace.tasks.breaker_tick_task",
        "schedule": 60.0,
    },
    "marketplace-reconcile-orders": {
        "task": "marketplace.tasks.reconcile_orders_task",
        "schedule": 60.0 * 30,
    },
    "marketplace-library-sweep": {
        "task": "marketplace.tasks.library_sweep_task",
        "schedule": 60.0 * 10,
    },
}

# --------------------------------------------------------------------------
# Internationalisation
# --------------------------------------------------------------------------

LANGUAGE_CODE = "en-in"
TIME_ZONE = env.get("ONEWOOZ_TIMEZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True

# --------------------------------------------------------------------------
# Static and media
# --------------------------------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [
    BASE_DIR / "onewooz" / "static",
    # Template 3 (the owner-facing "Template 2") keeps its CSS/JS beside its
    # pages; served as static/storefront-t3/... so edits there go live as-is.
    ("storefront-t3", BASE_DIR / "templates" / "storefront" / "template3" / "assets"),
    # Template 4 (the owner-facing "Template 3", Eurus look), same arrangement.
    ("storefront-t4", BASE_DIR / "templates" / "storefront" / "template4" / "assets"),
]
# Django's defaults, with the file-system finder swapped for one that resolves
# prefixed STATICFILES_DIRS (above) on Windows too (see core.staticfinders).
STATICFILES_FINDERS = [
    "core.staticfinders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

# Origins every HTML response tells the browser to preconnect to (Link header,
# core.middleware.EdgeDeliveryMiddleware). Empty in dev, where static is
# same-origin; production sets it to the CloudFront CDN host.
CDN_PRECONNECT_ORIGINS: tuple[str, ...] = ()

# --------------------------------------------------------------------------
# Uploads
# --------------------------------------------------------------------------

DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 2000
FILE_UPLOAD_PERMISSIONS = 0o644

# --------------------------------------------------------------------------
# Subscription bill (profile page → Subscription → View bill)
# --------------------------------------------------------------------------
# FALLBACK seller details for an owner's subscription bill. The real values are
# managed in the admin as "Invoice details" (billing.InvoiceDetails); these fill
# only what no active object provides. Defaults are the Brandise Box LLP entity.
# The seller's state (detected from the GSTIN when there is one) decides the GST
# split: a buyer in the same state pays CGST 9% + SGST 9%, anyone else IGST 18%.
PLATFORM_BILLING_ENTITY = {
    "company_name": env.get("ONEWOOZ_BILL_COMPANY", "Brandise Box LLP"),
    "address": env.get(
        "ONEWOOZ_BILL_ADDRESS",
        "Shop No. 1, 149/2, New Thippasandra Post, S K Complex, Malleshpalya, "
        "Bengaluru, Karnataka-560075",
    ),
    "state": env.get("ONEWOOZ_BILL_STATE", "Karnataka"),
    "mobile": env.get("ONEWOOZ_BILL_PHONE", "+91 728 909 0099"),
    "gstin": env.get("ONEWOOZ_BILL_GSTIN", "29AAWFB8078P1Z4"),
    "email": env.get("ONEWOOZ_BILL_EMAIL", ""),
    "hsn": env.get("ONEWOOZ_BILL_HSN", "998313"),
    "signatory_designation": env.get("ONEWOOZ_BILL_SIGNATORY", "Managing Partner"),
    # Terms & Conditions points (also the starting points for a new "Invoice
    # details" object in the admin).
    "terms": [
        "Use of the platform is governed by the active Plan and the Service Agreement / MSA with Brandise Box LLP.",
        "Services are classified as SaaS; GST is applied as per applicable law.",
        "All fees are strictly non-refundable.",
        "Any GSTIN or invoice dispute must be raised within 30 days of invoice date.",
        "Pricing, features, and policies may be updated by the Company at any time.",
        "All disputes fall under Karnataka jurisdiction only.",
    ],
}

# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------

DEFAULT_FROM_EMAIL = env.get("ONEWOOZ_FROM_EMAIL", "OneWooz <hello@onewooz.com>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Inbox that receives support tickets raised from the owner Business Hub.
# Blank → the address in DEFAULT_FROM_EMAIL (see ud.support.team_address).
SUPPORT_TICKETS_EMAIL = env.get("ONEWOOZ_SUPPORT_EMAIL", "")

# --------------------------------------------------------------------------
# Browser push notifications (Web Push)  accounts.webpush
# --------------------------------------------------------------------------
# Every bell notification is also popped up on each browser the owner allowed
# notifications in. The VAPID signing key is generated automatically on first
# use and stored encrypted (accounts.WebPushKey), so nothing needs creating by
# hand; ONEWOOZ_VAPID_PRIVATE_KEY (base64url raw P-256 scalar, or PEM) overrides
# it. The contact is sent to the push services (Google, Mozilla, Apple,
# Microsoft) so they can reach the operator; it must be mailto: or https:.
WEBPUSH_ENABLED = env.get_bool("ONEWOOZ_WEBPUSH_ENABLED", True)
WEBPUSH_VAPID_PRIVATE_KEY = env.get("ONEWOOZ_VAPID_PRIVATE_KEY", "")
WEBPUSH_CONTACT = env.get("ONEWOOZ_WEBPUSH_CONTACT", "mailto:hello@onewooz.com")

# --------------------------------------------------------------------------
# Google Merchant Center (Merchant API v1)
# --------------------------------------------------------------------------
# One OAuth 2.0 "Web application" client for the whole platform (Google Cloud
# console → APIs & Services → Credentials), in the Cloud project that has the
# Merchant API (merchantapi.googleapis.com) and the Site Verification API
# enabled. Its authorised redirect URI is the app surface's
# /integrations/google/callback/ (shown on the store's Google Merchant page).
# Blank → the Google Merchant page explains the integration isn't set up yet.
def _clean_credential(value: str) -> str:
    """Drop surrounding whitespace and quotes: a value saved from cmd.exe as
    'abc' keeps the quotes, and Google then rejects it as an unknown client."""
    return (value or "").strip().strip("'\"").strip()


GOOGLE_OAUTH_CLIENT_ID = _clean_credential(env.get("ONEWOOZ_GOOGLE_OAUTH_CLIENT_ID", ""))
GOOGLE_OAUTH_CLIENT_SECRET = _clean_credential(env.get("ONEWOOZ_GOOGLE_OAUTH_CLIENT_SECRET", ""))
# Developer contact Google emails about the API (used once, when the Cloud
# project is registered with OneWooz's own Merchant Center account from the
# admin). Blank → the connected Google account's email.
GOOGLE_MERCHANT_DEVELOPER_EMAIL = _clean_credential(env.get("ONEWOOZ_GOOGLE_MERCHANT_DEVELOPER_EMAIL", ""))
# OneWooz's OWN Merchant Center account ID. Google requires the Cloud project to
# be registered once with the platform's own account (never each merchant's).
# With this set, simply connecting that account from any store's Google
# Merchant page (signed in as an ADMIN there) performs the registration.
GOOGLE_MERCHANT_PLATFORM_ACCOUNT_ID = _clean_credential(env.get("ONEWOOZ_GOOGLE_MERCHANT_PLATFORM_ACCOUNT_ID", ""))

# TEMPORARY override for email-marketing sends. When true, every campaign email a
# store sends through the email-marketing tool goes out from OneWooz's own
# transactional identity (DEFAULT_FROM_EMAIL = hello@onewooz.com) over the
# already-configured platform SMTP credentials, instead of the store's branded
# SES address. Meant as a stop-gap until per-store SES sending is fully live.
USE_ONEWOOZ_EMAIL = env.get_bool("USE_ONEWOOZ_EMAIL", False)

# Internal inbox notified whenever a subscription is purchased (paid, free grant
# or admin invite). Carries the buyer's email, the plan, its duration and the
# amount paid  see billing.emails.send_subscription_purchase_notice, called from
# the single paid-confirmation chokepoint in billing.cashfree._mark_paid.
SUBSCRIPTION_NOTICE_EMAIL = env.get("ONEWOOZ_SUBSCRIPTION_NOTICE_EMAIL", "myonewooz@gmail.com")

# --- Store → customer email -----------------------------------------------
# Mail a store sends its own customers (order updates, store credit, owner
# notices, storefront sign-in codes) leaves from a per-store branded address on
# ONE shared, SES-verified sending domain  e.g. jackandjones@via.onewooz.com.
# This is deliberately separate from OneWooz's own transactional mail above:
# that keeps DEFAULT_FROM_EMAIL, this is derived per store from its name.
# See tenancy.email (the address) and core.mail (the SES backend).
STOREFRONT_EMAIL_DOMAIN = env.get("ONEWOOZ_STOREFRONT_EMAIL_DOMAIN", "via.onewooz.website")

# The backend store→customer mail is sent through. Dev keeps the console backend
# so it prints to the runserver output; prod overrides this with the SES backend
# (core.mail.SESEmailBackend), which is what lets any local part on the verified
# domain be used (a generic SMTP relay rejects a From it does not own).
STORE_EMAIL_BACKEND = env.get(
    "ONEWOOZ_STORE_EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)

# Dedicated SMTP relay for store→customer mail (e.g. Cloudflare). When
# STORE_SMTP_HOST is set, core.mail.store_mail_connection sends every store email
# through it instead of the backend above  the relay authorises any branded
# From on the verified STOREFRONT_EMAIL_DOMAIN. Credentials are separate from
# OneWooz's own transactional SMTP. Port 465 = implicit TLS (SMTPS); the
# username is Cloudflare's literal "api_token" and the password is the API token.
STORE_SMTP_HOST = env.get("ONEWOOZ_STORE_SMTP_HOST", "")
STORE_SMTP_PORT = env.get_int("ONEWOOZ_STORE_SMTP_PORT", 465)
STORE_SMTP_USER = env.get("ONEWOOZ_STORE_SMTP_USER", "")
STORE_SMTP_PASSWORD = env.get("ONEWOOZ_STORE_SMTP_PASSWORD", "")
STORE_SMTP_USE_SSL = env.get_bool("ONEWOOZ_STORE_SMTP_USE_SSL", True)
# Region SES lives in (falls back to the S3 region in prod) and an optional SES
# configuration set for open/click/bounce event publishing on this mail stream.
STORE_EMAIL_SES_REGION = env.get("ONEWOOZ_STORE_EMAIL_SES_REGION", "")
STORE_EMAIL_SES_CONFIGURATION_SET = env.get(
    "ONEWOOZ_STORE_EMAIL_SES_CONFIGURATION_SET", ""
)

# --- Collaboration (UGC referral) email ------------------------------------
# Mail that recognises a collaborator for a store  the invite / contract
# smart-link that turns a creator into a verified referral partner  leaves from
# a single fixed address on the referral root domain, no-reply@myugcstore.com.
# It is deliberately its own identity: the collaborator is being recruited *to*
# a store's referral programme (their storefront lives at
# <subdomain>.myugcstore.com), so the From they see sits on that same domain,
# not on via.onewooz.website (store→customer mail) or onewooz.com (OneWooz's own
# account mail). The store's name rides along as the From display name so the
# creator can tell which store invited them. Sent through the same transport as
# store→customer mail (STORE_EMAIL_BACKEND → SES in prod), so the referral
# domain must be a verified SES identity and permitted by the task-role policy 
# see deploy/aws/ses/README.md. Address part must stay on REFERRAL_ROOT_DOMAIN.
REFERRAL_EMAIL_DOMAIN = env.get("ONEWOOZ_REFERRAL_EMAIL_DOMAIN", REFERRAL_ROOT_DOMAIN)
REFERRAL_FROM_EMAIL = env.get(
    "ONEWOOZ_REFERRAL_FROM_EMAIL", f"no-reply@{REFERRAL_EMAIL_DOMAIN}"
)

# --------------------------------------------------------------------------
# WebAuthn / passkeys
# --------------------------------------------------------------------------
# Relying-party id is the registrable domain so one credential works across
# app. and admin. subdomains; the admin surface additionally requires the
# credential to be registered against a staff account.

WEBAUTHN_RP_ID = env.get("ONEWOOZ_WEBAUTHN_RP_ID", MARKETING_HOST)
WEBAUTHN_RP_NAME = "OneWooz"
WEBAUTHN_CHALLENGE_TTL = 300

# --------------------------------------------------------------------------
# Signed token lifetimes (seconds), keyed by purpose. See core.tokens.
# --------------------------------------------------------------------------

TOKEN_TTL = {
    # "Your account is deleted" page link handed out right after the delete.
    "account.deleted": 3600,
    # Dashboard -> marketplace sign-in hand-off: single use, one minute.
    "marketplace.handoff": 60,
    # Dashboard -> Ask OneWooz account link (community.bridge): single use, two minutes.
    "ask.link": 120,
    # Ties a marketplace Cashfree return URL to its order.
    "marketplace.order.return": 3600,
    # Signed per-store token appended to an app's external link.
    "marketplace.external": 600,
    # A visitor's own question + answer page on onewooz.com/ask (bookmarkable,
    # and linked from the "the team replied" email).
    "ask.question": 180 * 86400,
    # Ask OneWooz emails: confirm a digest sign-up, and one-click opt-outs
    # (minted fresh in every email, so a long life is harmless).
    "ask.digest.confirm": 3 * 86400,
    "ask.digest.unsub": 400 * 86400,
    "ask.notify.off": 400 * 86400,
    "auth.magic_link": 900,
    "auth.email_verify": 86400,
    "auth.password_reset": 3600,
    "admin.step_up": 300,
    # Admin impersonation hand-off: a very short window to cross from the admin
    # surface to the app surface and establish the session. Not durable  a
    # leaked link is useless within a couple of minutes.
    "admin.impersonate": 120,
    "billing.invoice": 900,
    "commerce.cart_recovery": 7 * 86400,
    "commerce.order_action": 3600,
    # A UGC store's Cashfree payment opened on the owner's own store domain
    # (storefront.hosted_pay): long enough to pay, short enough not to linger.
    "storefront.hosted_pay": 7200,
    # Ties a Cashfree return URL to the subscription order it settles.
    "billing.checkout": 3600,
    # Proof the buyer's email passed the OTP step; the window to finish paying
    # after verifying. Long enough to read the summary and pay, short enough
    # that a leaked token is not durable.
    "billing.otp_verified": 900,
    # Admin-issued custom-priced invite link  a short window to open and
    # complete the checkout (matches SubscriptionInvite.INVITE_TTL_MINUTES).
    "billing.invite": 1200,
    # OAuth ``state`` for the Google Merchant connect round trip.
    "merchant.oauth_state": 900,
    # Post-payment onboarding: the window to finish creating the account and
    # tax profile after an order settles. Generous  the money is already
    # taken, so a buyer who steps away should still be able to complete it 
    # but bounded, and a lost link is recoverable by simply signing up with the
    # same (email-bound) address.
    "billing.onboarding": 7 * 86400,
    # Referral/collaboration contract smart-link: 48h, matching the invite copy.
    "commerce.collab_contract": 48 * 3600,
    "email.unsubscribe": 30 * 86400,
    # Email-marketing tracking links, bound to one delivery. Opens/clicks can
    # arrive weeks after a send, and an unsubscribe link must keep working, so
    # these are generous  the token grants only that one harmless action.
    "outreach.open": 120 * 86400,
    "outreach.click": 120 * 86400,
    "outreach.unsubscribe": 180 * 86400,
}

# Email marketing composer: offer the admin-published (AI-built) EmailTemplate
# designs to store owners. Off for now, so owners only see the built-in,
# field-driven designs (outreach.promo_design). Set True to bring them back.
OUTREACH_SHOW_PUBLISHED_TEMPLATES = False

# Per-worker ceiling for marketing sends (Celery rate_limit syntax). Keeps a
# large campaign under the SES account's sending rate (14/s by default once out
# of the sandbox) so throttled sends do not exhaust their retries and fail.
OUTREACH_SEND_RATE_LIMIT = env.get("ONEWOOZ_OUTREACH_SEND_RATE", "10/s")

# --------------------------------------------------------------------------
# Platform billing (OneWooz's own subscriptions  the billing app)
# --------------------------------------------------------------------------
# These take payment through OneWooz's *own* Cashfree account, distinct from the
# per-tenant storefront gateways in commerce. Credentials come from the
# environment (Secrets Manager in production) with no in-code fallback: an unset
# key simply disables checkout ("online payment isn't available") rather than
# shipping a usable secret. Sandbox by default so a misconfigured production
# environment fails safe rather than charging a live card.

PLATFORM_CASHFREE_APP_ID = env.get("ONEWOOZ_PLATFORM_CASHFREE_APP_ID", "")
PLATFORM_CASHFREE_SECRET_KEY = env.get("ONEWOOZ_PLATFORM_CASHFREE_SECRET_KEY", "")
PLATFORM_CASHFREE_SANDBOX = env.get_bool("ONEWOOZ_PLATFORM_CASHFREE_SANDBOX", True)
# Optional. Webhooks are confirmed by fetching authoritative status regardless,
# so a missing secret degrades to "verify by fetch only", never to trusting a body.
PLATFORM_CASHFREE_WEBHOOK_SECRET = env.get("ONEWOOZ_PLATFORM_CASHFREE_WEBHOOK_SECRET", "")

# GST added on top of the (discounted) subscription amount, as a percentage.
SUBSCRIPTION_GST_RATE = env.get_int("ONEWOOZ_SUBSCRIPTION_GST_RATE", 18)

# Days a lapsed owner's store data is kept after their paid plan ends. The admin
# "Deletion today" / "Deletion in 7 days" Excel reports list owners whose
# deletion date (plan end + this many days) falls in that window.
SUBSCRIPTION_DATA_RETENTION_DAYS = env.get_int("ONEWOOZ_SUBSCRIPTION_DATA_RETENTION_DAYS", 30)

# Deleted stores and deleted owner accounts (accounts.deletion) stay on the
# server, recoverable, for this many days. An account is restored by signing in
# within the window (renewing first if its paid plan lapsed); a single store by
# asking support. Nothing is ever erased afterwards either: orders, customers
# and the account row are kept; only self-service recovery ends.
ACCOUNT_DELETION_RETENTION_DAYS = env.get_int("ONEWOOZ_ACCOUNT_DELETION_RETENTION_DAYS", 60)

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

LOG_LEVEL = env.get("ONEWOOZ_LOG_LEVEL", "INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {"()": "core.logging.RequestIDFilter"},
    },
    "formatters": {
        "standard": {
            "format": "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["request_id"],
            "formatter": "standard",
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django.security": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "onewooz.audit": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}


# --------------------------------------------------------------------------
# AI assistant (dashboard)  image generation + text generation
# --------------------------------------------------------------------------
# Shown on the dashboard once an owner has finished the Getting-started steps.
# Text runs on xAI Grok, images on OpenAI. Both are called server-side, so no
# key ever reaches the browser and no CSP host needs opening. Leave a key unset
# to disable that capability (the assistant hides when neither is configured).
XAI_API_KEY = env.get("XAI_API_KEY", "")
XAI_BASE_URL = env.get("XAI_BASE_URL", "https://api.x.ai/v1")
XAI_TEXT_MODEL = env.get("XAI_TEXT_MODEL", "grok-4-fast")

OPENAI_API_KEY = env.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = env.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_IMAGE_MODEL = env.get("OPENAI_IMAGE_MODEL", "gpt-image-1")
# ChatGPT text model used by the admin email-template builder (server-side).
# Default model; the builder can override it per request by entering a model code.
OPENAI_TEXT_MODEL = env.get("OPENAI_TEXT_MODEL", "gpt-5.6-sol")
