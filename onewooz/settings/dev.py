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
ADMIN_HOST = env.get("ONEWOOZ_ADMIN_HOST", "admin.onewooz.localhost")
STOREFRONT_APEX = env.get("ONEWOOZ_STOREFRONT_APEX", "shop.localhost")

# Port runserver is on. Everything below that needs a port derives from this,
# so running on a different port is one environment variable, not a hunt
# through the settings for hardcoded 8000s.
DEV_PORT = env.get("ONEWOOZ_DEV_PORT", "8000")

PLATFORM_HOSTS = {MARKETING_HOST, f"www.{MARKETING_HOST}", APP_HOST, ADMIN_HOST}

ALLOWED_HOSTS = [
    MARKETING_HOST,
    f"www.{MARKETING_HOST}",
    APP_HOST,
    ADMIN_HOST,
    f".{STOREFRONT_APEX}",
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
        STOREFRONT_APEX,
        f"*.{STOREFRONT_APEX}",
        "localhost",
        "127.0.0.1",
    )
]

# The relying-party id must be the current origin's host or a parent of it.
# "onewooz.localhost" is a parent of both app. and admin., so one passkey
# works across the dashboard and the admin surface locally, mirroring how
# WEBAUTHN_RP_ID = "onewooz.com" behaves in production.
#
# Passkeys will not work on http://127.0.0.1:8000 — an IP address is not a
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
# it automatically — no view or template contains a prefixed path.
#
# The named hosts keep working host-based, which is the arrangement production
# actually uses; test there before shipping anything host-sensitive.

DEV_SURFACE_PREFIXES = {
    "marketing": "",
    "app": "/user",
    "admin": "/admin",
    "storefront": "/s",
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

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Password hashing is deliberately slow. Keep Argon2 in dev so behaviour
# matches production, but with the cheapest parameters the hasher allows.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.Argon2PasswordHasher"]

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Celery runs inline; no broker needed locally.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
