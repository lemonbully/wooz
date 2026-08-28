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
    admin.onewooz.com      -> onewooz.urls.admin
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
ADMIN_HOST = env.get("ONEWOOZ_ADMIN_HOST", "admin.onewooz.com")

# Apex under which generated storefronts get their subdomain. Separate
# registrable domain from MARKETING_HOST on purpose — see module docstring.
STOREFRONT_APEX = env.get("ONEWOOZ_STOREFRONT_APEX", "onewooz.site")

# Where owners point their custom domain (a CNAME/ALIAS target). Set this to the
# public hostname of the Caddy edge that terminates TLS on demand (e.g.
# "edge.onewooz.com") so custom domains get an automatic free certificate. When
# unset, the store's own <slug>.onewooz.site is shown (served by the ALB, no
# per-domain cert). The verify step checks the domain resolves to this target.
CUSTOM_DOMAIN_TARGET = env.get("ONEWOOZ_CUSTOM_DOMAIN_TARGET", "")

# Redis set of verified custom domains that Caddy's on-demand-TLS ask endpoint
# consults (SISMEMBER) before issuing a certificate. Populated when an owner
# verifies a domain; see commerce.views.domains and core.middleware.CaddyAskMiddleware.
CADDY_VERIFIED_DOMAINS_KEY = env.get(
    "ONEWOOZ_CADDY_VERIFIED_DOMAINS_KEY", "onewooz:verified_domains"
)

# Hosts that are ours and are never treated as a tenant storefront.
PLATFORM_HOSTS = {MARKETING_HOST, f"www.{MARKETING_HOST}", APP_HOST, ADMIN_HOST}

ALLOWED_HOSTS = env.get_list("ONEWOOZ_ALLOWED_HOSTS") or [
    MARKETING_HOST,
    f"www.{MARKETING_HOST}",
    APP_HOST,
    ADMIN_HOST,
    f".{STOREFRONT_APEX}",
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
    # Must precede staticfiles: disables runserver's own static handler so
    # WhiteNoise serves static in dev too, giving one code path (and correct
    # cache headers) across dev and prod. No-op outside the runserver command.
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    # Deliberately no django.contrib.admin: the internal admin is a purpose
    # built app on its own host with passkey-only authentication.
    "core",
    "accounts",
    "tenancy",
    "commerce",
    "storefront",
    "web",
    "ud",
    "custom_admin",
]

MIDDLEWARE = [
    # Correlation id first so every later log line and audit row carries it.
    "core.middleware.RequestIDMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    # Health probes short-circuit before any host resolution: load balancer
    # checks arrive with a non-matching Host header and must never 404.
    "core.middleware.HealthProbeMiddleware",
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
    # No-op on every surface but admin, where it enforces the network
    # allowlist, the staff flag, the passkey requirement and the idle timeout.
    "custom_admin.middleware.AdminAccessMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
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
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.security",
                "ud.context_processors.dashboard_nav",
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
# the script prefix — so the redirect lands correctly whether the dashboard is
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

# Static origins. Verified tenant domains are added per-request by
# HostRoutingMiddleware rather than enumerated here — that is what lets
# storefront POST endpoints keep CSRF protection instead of being exempted.
CSRF_TRUSTED_ORIGINS = [
    f"https://{MARKETING_HOST}",
    f"https://www.{MARKETING_HOST}",
    f"https://{APP_HOST}",
    f"https://{ADMIN_HOST}",
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
CSP_FORM_ACTION = ("'self'",)
CSP_OBJECT_SRC = ("'none'",)

# The generated storefronts are ported from a Tailwind + Font Awesome design
# that relies on inline <style> blocks, dynamic style="" attributes and a
# handful of inline scripts. They live on a *separate registrable domain*
# (onewooz.site) — a public-suffix boundary that already isolates them from the
# dashboard and from each other — so the storefront surface, and only that
# surface, gets a CSP loose enough for that markup. The dashboard, admin and
# marketing surfaces keep the strict nonce-only policy above.
CSP_STOREFRONT_STYLE_EXTRA = (
    "'unsafe-inline'",
    "https://cdnjs.cloudflare.com",
    "https://fonts.googleapis.com",
)
CSP_STOREFRONT_SCRIPT_EXTRA = ("'unsafe-inline'",)
CSP_STOREFRONT_FONT_EXTRA = ("https://cdnjs.cloudflare.com",)
# When an owner opts a storefront into custom tracking scripts, these hosts are
# also admitted to that storefront's script-src.
CSP_TENANT_SCRIPT_HOSTS = (
    "https://www.googletagmanager.com",
    "https://www.google-analytics.com",
    "https://connect.facebook.net",
)

# The owner dashboard's content editor loads the free (self-hostable) TinyMCE
# build from jsDelivr — script, skin CSS and icon fonts. Admitted on the app
# surface only. To self-host later, drop the files under static/ and clear this.
CSP_EDITOR_HOSTS = ("https://cdn.jsdelivr.net",)

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
    "cart.mutate": (120, 60),
    "checkout.submit": (10, 300),
    "analytics.ingest": (300, 60),
    "webhook.courier": (600, 60),
    "enquiry.submit": (10, 3600),
    "review.submit": (5, 3600),
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
STATICFILES_DIRS = [BASE_DIR / "onewooz" / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

# --------------------------------------------------------------------------
# Uploads
# --------------------------------------------------------------------------

DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 2000
FILE_UPLOAD_PERMISSIONS = 0o644

# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------

DEFAULT_FROM_EMAIL = env.get("ONEWOOZ_FROM_EMAIL", "OneWooz <hello@onewooz.com>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

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
    "auth.magic_link": 900,
    "auth.email_verify": 86400,
    "auth.password_reset": 3600,
    "admin.step_up": 300,
    "billing.invoice": 900,
    "commerce.cart_recovery": 7 * 86400,
    "commerce.order_action": 3600,
    "email.unsubscribe": 30 * 86400,
}

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
