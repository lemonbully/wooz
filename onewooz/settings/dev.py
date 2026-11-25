"""Development settings.

Runs with zero manual setup: SQLite, local-memory cache, console email, and
secrets that are generated on first import and cached in a gitignored
``.secrets/`` directory. Nothing here is a committed secret and nothing here is
safe outside a developer machine.
"""

from __future__ import annotations

from .base import *  # noqa: F403
from .base import BASE_DIR, SECRETS_DIR
from . import env

DEBUG = True

SECRET_KEY = env.get_or_create_dev_secret("ONEWOOZ_SECRET_KEY", SECRETS_DIR)
FIELD_ENCRYPTION_KEY = env.get_or_create_dev_secret(
    "ONEWOOZ_FIELD_ENCRYPTION_KEY", SECRETS_DIR
)
CADDY_ASK_SECRET = env.get_or_create_dev_secret("ONEWOOZ_CADDY_ASK_SECRET", SECRETS_DIR)

# --------------------------------------------------------------------------
# Hosts
# --------------------------------------------------------------------------
# Add the .localhost equivalents so all four surfaces are reachable locally
# without editing the hosts file: *.localhost resolves to 127.0.0.1 in every
# modern browser.

MARKETING_HOST = env.get("ONEWOOZ_MARKETING_HOST", "7vg4xqqt-8000.inc1.devtunnels.ms")
APP_HOST = env.get("ONEWOOZ_APP_HOST", "app.onewooz.localhost")
ADMIN_HOST = env.get("ONEWOOZ_ADMIN_HOST", "arcane.onewooz.localhost")
MARKETPLACE_HOST = env.get("ONEWOOZ_MARKETPLACE_HOST", "marketplace.onewooz.localhost")
STOREFRONT_APEX = env.get("ONEWOOZ_STOREFRONT_APEX", "shop.localhost")
# UGC collaboration (referral) storefront apex  dev uses a *.localhost name so
# <sub>.myugcstore.localhost resolves to 127.0.0.1 in modern browsers.
REFERRAL_ROOT_DOMAIN = env.get("ONEWOOZ_REFERRAL_ROOT_DOMAIN", "myugcstore.localhost")

# Port runserver is on. Everything below that needs a port derives from this,
# so running on a different port is one environment variable, not a hunt
# through the settings for hardcoded 8000s.
DEV_PORT = env.get("ONEWOOZ_DEV_PORT", "8000")

PLATFORM_HOSTS = {MARKETING_HOST, f"www.{MARKETING_HOST}", APP_HOST, ADMIN_HOST, MARKETPLACE_HOST}

ALLOWED_HOSTS = [
    MARKETING_HOST,
    f"www.{MARKETING_HOST}",
    APP_HOST,
    ADMIN_HOST,
    MARKETPLACE_HOST,
    f".{STOREFRONT_APEX}",
    f".{REFERRAL_ROOT_DOMAIN}",
    "localhost",
    "127.0.0.1",
    "[::1]",
    "7vg4xqqt-8000.inc1.devtunnels.ms"
]

# Django matches the Origin header against these exactly, port included, so a
# mismatched port here shows up as a confusing 403 on every form POST.
CSRF_TRUSTED_ORIGINS = [
    f"http://{host}:{DEV_PORT}"
    for host in (
        MARKETING_HOST,
        f"www.{MARKETING_HOST}",
        APP_HOST,
        ADMIN_HOST,
        MARKETPLACE_HOST,
        STOREFRONT_APEX,
        f"*.{STOREFRONT_APEX}",
        # UGC referral apex (collaborator portal) + collaborator storefront
        # subdomains. The host router forces X-Forwarded-Proto=https for these,
        # so with SECURE_PROXY_SSL_HEADER set a same-origin POST over dev http is
        # otherwise rejected; trusting the http origin keeps local login/checkout
        # working. (Production uses real https, so this is a dev-only allowance.)
        REFERRAL_ROOT_DOMAIN,
        f"*.{REFERRAL_ROOT_DOMAIN}",
        "localhost",
        "127.0.0.1",
    )
]

# The relying-party id must be the current origin's host or a parent of it.
# "onewooz.localhost" is a parent of both app. and admin., so one passkey
# works across the dashboard and the admin surface locally, mirroring how
# WEBAUTHN_RP_ID = "onewooz.com" behaves in production.
#
# Passkeys will not work on http://127.0.0.1:8000  an IP address is not a
# valid relying-party id. Use the named hosts to test them.
WEBAUTHN_RP_ID = env.get("ONEWOOZ_WEBAUTHN_RP_ID", MARKETING_HOST)

# --------------------------------------------------------------------------
# Single-host development
# --------------------------------------------------------------------------
# On a bare loopback host there are no subdomains, so the four surfaces are
# separated by path prefix instead and everything stays on 127.0.0.1:8000:
#
#     http://127.0.0.1:8000/                 marketing
#     http://127.0.0.1:8000/user/dashboard/  owner dashboard
#     http://127.0.0.1:8000/admin/           internal admin
#     http://127.0.0.1:8000/s/<store>/       a generated storefront
#
# The prefix is moved into SCRIPT_NAME, so reverse() and {% url %} reproduce
# it automatically  no view or template contains a prefixed path.
#
# The named hosts keep working host-based, which is the arrangement production
# actually uses; test there before shipping anything host-sensitive.

DEV_SURFACE_PREFIXES = {
    "marketing": "",
    "app": "/user",
    "admin": "/admin",
    "storefront": "/s",
    # Collaborator portal (referral apex). On a real host use
    # ``myugcstore.localhost:8000/dashboard``; on bare 127.0.0.1 use
    # ``/referral/dashboard`` (this prefix is stripped like the others).
    "referral": "/referral",
    # OneWooz Apps: http://127.0.0.1:8000/marketplace/ (or marketplace.onewooz.localhost:8000)
    "marketplace": "/marketplace",
}

# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {"transaction_mode": "IMMEDIATE"},
    }
}

# --------------------------------------------------------------------------
# Cookies
# --------------------------------------------------------------------------
# No Secure flag and no __Host- prefix: development is served over plain HTTP
# and both would make the cookie unsettable.

SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# --------------------------------------------------------------------------
# Convenience
# --------------------------------------------------------------------------

# Email  send real sign-in / sign-up messages (OTP, magic links, verification)
# when SMTP credentials are provided, otherwise print to the console so the app
# runs with no setup. Same variables as production, in the same priority order:
#   1. Hostinger / generic SMTP  ONEWOOZ_SMTP_USER + ONEWOOZ_SMTP_PASSWORD
#      (+ optional ONEWOOZ_SMTP_HOST/PORT/SSL/TLS/FROM_EMAIL).
#   2. Gmail  ONEWOOZ_GMAIL_USER + ONEWOOZ_GMAIL_APP_PASSWORD.
#   3. Console backend (default).
_SMTP_USER = env.get("ONEWOOZ_SMTP_USER", "")
_SMTP_PASSWORD = env.get("ONEWOOZ_SMTP_PASSWORD", "")
_GMAIL_USER = env.get("ONEWOOZ_GMAIL_USER", "")
_GMAIL_APP_PASSWORD = env.get("ONEWOOZ_GMAIL_APP_PASSWORD", "")

if _SMTP_USER and _SMTP_PASSWORD:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = env.get("ONEWOOZ_SMTP_HOST", "smtp.hostinger.com")
    EMAIL_PORT = env.get_int("ONEWOOZ_SMTP_PORT", 587)
    EMAIL_HOST_USER = _SMTP_USER
    EMAIL_HOST_PASSWORD = _SMTP_PASSWORD
    EMAIL_USE_SSL = env.get_bool("ONEWOOZ_SMTP_SSL", False)
    EMAIL_USE_TLS = False if EMAIL_USE_SSL else env.get_bool("ONEWOOZ_SMTP_TLS", True)
    EMAIL_TIMEOUT = env.get_int("ONEWOOZ_SMTP_TIMEOUT", 20)
    # Display name in front of the mailbox so clients show "OneWooz", not "hello".
    DEFAULT_FROM_EMAIL = env.get("ONEWOOZ_FROM_EMAIL", "OneWooz <%s>" % _SMTP_USER)
    SERVER_EMAIL = DEFAULT_FROM_EMAIL
elif _GMAIL_USER and _GMAIL_APP_PASSWORD:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = "smtp.gmail.com"
    EMAIL_PORT = 587
    EMAIL_USE_TLS = True
    EMAIL_HOST_USER = _GMAIL_USER
    EMAIL_HOST_PASSWORD = _GMAIL_APP_PASSWORD
    EMAIL_TIMEOUT = 15
    DEFAULT_FROM_EMAIL = env.get("ONEWOOZ_FROM_EMAIL", "OneWooz <%s>" % _GMAIL_USER)
    SERVER_EMAIL = DEFAULT_FROM_EMAIL
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Print sign-in / sign-up one-time codes straight to the runserver terminal
# instead of "sending" them, so local auth never depends on reading email.
OTP_CONSOLE_ECHO = True

# Seed a demo store (with a demo category + subcategory) after `migrate`, so
# localhost always has a working store without any manual setup.
DEV_SEED_STORE = True

# Password hashing is deliberately slow. Keep Argon2 in dev so behaviour
# matches production, but with the cheapest parameters the hasher allows.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.Argon2PasswordHasher"]

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Let WhiteNoise serve static files straight from the source dirs (no
# collectstatic), re-checking the filesystem on every request and telling the
# browser to revalidate each time. An edited CSS/JS file is picked up on a plain
# reload  no Ctrl+Shift+R. Prod keeps hashed filenames via the manifest storage,
# where far-future caching is correct because the URL changes when the file does.
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = True
WHITENOISE_MAX_AGE = 0

# Celery runs inline; no broker needed locally.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
