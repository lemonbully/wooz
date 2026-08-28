"""Production settings.

Every secret is required from the environment. There are no fallbacks: a
missing value raises :class:`~onewooz.settings.env.ImproperlyConfigured` at
import time and the process refuses to boot. This is a direct response to the
platform this product replaces, which shipped a working TLS-issuance secret as
an in-code default and committed it to source control.
"""

from __future__ import annotations

from .base import *  # noqa: F403
from .base import (  # noqa: F401
    ADMIN_HOST,
    APP_HOST,
    MARKETING_HOST,
    STOREFRONT_APEX,
)
from . import env

DEBUG = False

# Host validation is delegated to core.middleware.HostRoutingMiddleware, which
# resolves every Host header to a surface and returns 404 for anything it does
# not recognise — the marketing/app/admin hosts match exactly, storefronts must
# be ``<slug>.onewooz.site``, and a custom domain is admitted only when a Site
# has verified it. That per-request gate is what lets owners connect arbitrary
# custom domains without editing an allowlist or redeploying, so ALLOWED_HOSTS
# is opened here and the middleware is the real allowlist. Override with
# ONEWOOZ_ALLOWED_HOSTS only if you want to additionally hard-limit hosts.
ALLOWED_HOSTS = env.get_list("ONEWOOZ_ALLOWED_HOSTS") or ["*"]

SECRET_KEY = env.get("ONEWOOZ_SECRET_KEY", required=True)
FIELD_ENCRYPTION_KEY = env.get("ONEWOOZ_FIELD_ENCRYPTION_KEY", required=True)
CADDY_ASK_SECRET = env.get("ONEWOOZ_CADDY_ASK_SECRET", required=True)

# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": env.get("ONEWOOZ_DB_NAME", required=True),
        "USER": env.get("ONEWOOZ_DB_USER", required=True),
        "PASSWORD": env.get("ONEWOOZ_DB_PASSWORD", required=True),
        "HOST": env.get("ONEWOOZ_DB_HOST", required=True),
        "PORT": env.get("ONEWOOZ_DB_PORT", "3306"),
        "CONN_MAX_AGE": env.get_int("ONEWOOZ_DB_CONN_MAX_AGE", 60),
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            "charset": "utf8mb4",
            "sql_mode": "STRICT_TRANS_TABLES",
            "isolation_level": "read committed",
        },
    }
}

REDIS_URL = env.get("ONEWOOZ_REDIS_URL", required=True)

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": "onewooz",
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            # A Redis outage degrades the site rather than erroring it. Every
            # cache read in this codebase has a database fallback.
            "IGNORE_EXCEPTIONS": True,
            "SOCKET_CONNECT_TIMEOUT": 5,
            "SOCKET_TIMEOUT": 5,
            "CONNECTION_POOL_KWARGS": {"max_connections": 100},
        },
    }
}
DJANGO_REDIS_IGNORE_EXCEPTIONS = True

# --------------------------------------------------------------------------
# Cookies
# --------------------------------------------------------------------------
# The __Host- prefix is enforced by the browser: it requires Secure, Path=/ and
# *no* Domain attribute. That makes it impossible for a subdomain to overwrite
# the cookie, which is the attack the prefix exists to stop.

SESSION_COOKIE_SECURE = True
SESSION_COOKIE_NAME = "__Host-ow_sid"
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_NAME = "__Host-ow_csrf"

# --------------------------------------------------------------------------
# Transport security
# --------------------------------------------------------------------------
# Caddy performs the HTTP->HTTPS redirect at the edge, so Django does not.

SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = env.get_bool("ONEWOOZ_HSTS_PRELOAD", False)

# --------------------------------------------------------------------------
# Admin surface
# --------------------------------------------------------------------------
# Network-layer barrier evaluated before authentication is attempted. Empty
# means "no allowlist"; the admin middleware treats that as a misconfiguration
# and refuses every request rather than failing open.

ADMIN_IP_ALLOWLIST = env.get_list("ONEWOOZ_ADMIN_IP_ALLOWLIST")

# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

AWS_STORAGE_BUCKET_NAME = env.get("ONEWOOZ_MEDIA_BUCKET", required=True)
AWS_S3_REGION_NAME = env.get("ONEWOOZ_AWS_REGION", "ap-south-1")
AWS_S3_CUSTOM_DOMAIN = env.get("ONEWOOZ_MEDIA_CDN_DOMAIN", "")
AWS_QUERYSTRING_AUTH = False
AWS_DEFAULT_ACL = None
AWS_S3_FILE_OVERWRITE = False
AWS_S3_OBJECT_PARAMETERS = {"CacheControl": "max-age=3600"}

STORAGES = {
    "default": {"BACKEND": "storages.backends.s3.S3Storage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

if AWS_S3_CUSTOM_DOMAIN:
    MEDIA_URL = f"https://{AWS_S3_CUSTOM_DOMAIN}/"

# --------------------------------------------------------------------------
# Celery
# --------------------------------------------------------------------------
# SQS as broker: nothing to operate, IAM-role authentication, no credentials in
# the URL. Long polling keeps empty-receive costs near zero.

# SQS by default. Overridable so a local prod-parity run (docker compose) or a
# staging box can point Celery at a Redis broker without standing up SQS.
CELERY_BROKER_URL = env.get("ONEWOOZ_CELERY_BROKER_URL", "sqs://")
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "region": AWS_S3_REGION_NAME,
    "visibility_timeout": 600,
    "wait_time_seconds": 20,
    "polling_interval": 1,
    "queue_name_prefix": env.get("ONEWOOZ_SQS_PREFIX", "onewooz-"),
}
CELERY_RESULT_BACKEND = env.get("ONEWOOZ_CELERY_RESULT_BACKEND", REDIS_URL)
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_TASK_ALWAYS_EAGER = False

INSTALLED_APPS = INSTALLED_APPS + [  # noqa: F405
    "django_celery_beat",
    "django_celery_results",
]

# --------------------------------------------------------------------------
# Email  (transactional: OTP / sign-in / sign-up / verification)
# --------------------------------------------------------------------------
# The sender is chosen from environment variables, in priority order, so it can
# be switched without a code change:
#
#   1. Generic SMTP — ONEWOOZ_SMTP_* (Hostinger is the default host). ACTIVE
#      sender in production. Set these on the web task definition:
#         ONEWOOZ_SMTP_USER        full mailbox, e.g. otp@yourdomain.com
#         ONEWOOZ_SMTP_PASSWORD    that mailbox's password  (store as a secret)
#         ONEWOOZ_SMTP_HOST        default "smtp.hostinger.com"
#         ONEWOOZ_SMTP_PORT        default 587
#         ONEWOOZ_SMTP_SSL         "true" for port 465 (implicit SSL); else TLS
#         ONEWOOZ_SMTP_TLS         default true (STARTTLS on 587)
#         ONEWOOZ_FROM_EMAIL       optional; defaults to the mailbox above
#   2. Gmail — ONEWOOZ_GMAIL_USER + ONEWOOZ_GMAIL_APP_PASSWORD.
#   3. Amazon SES / legacy SMTP — ONEWOOZ_EMAIL_* (last-resort fallback).
#
# Providers like Hostinger and Gmail reject a mismatched From, so the From
# address defaults to the authenticated mailbox; ONEWOOZ_FROM_EMAIL may override
# it (keep the same address — you can add a display name).

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_TIMEOUT = env.get_int("ONEWOOZ_SMTP_TIMEOUT", 20)

_SMTP_USER = env.get("ONEWOOZ_SMTP_USER", "")
_SMTP_PASSWORD = env.get("ONEWOOZ_SMTP_PASSWORD", "")
_GMAIL_USER = env.get("ONEWOOZ_GMAIL_USER", "")
_GMAIL_APP_PASSWORD = env.get("ONEWOOZ_GMAIL_APP_PASSWORD", "")

if _SMTP_USER and _SMTP_PASSWORD:
    # --- Hostinger / generic SMTP (active) ---
    EMAIL_HOST = env.get("ONEWOOZ_SMTP_HOST", "smtp.hostinger.com")
    EMAIL_PORT = env.get_int("ONEWOOZ_SMTP_PORT", 587)
    EMAIL_HOST_USER = _SMTP_USER
    EMAIL_HOST_PASSWORD = _SMTP_PASSWORD
    EMAIL_USE_SSL = env.get_bool("ONEWOOZ_SMTP_SSL", False)
    EMAIL_USE_TLS = False if EMAIL_USE_SSL else env.get_bool("ONEWOOZ_SMTP_TLS", True)
    DEFAULT_FROM_EMAIL = env.get("ONEWOOZ_FROM_EMAIL", _SMTP_USER)
    SERVER_EMAIL = DEFAULT_FROM_EMAIL
elif _GMAIL_USER and _GMAIL_APP_PASSWORD:
    # --- Gmail ---
    EMAIL_HOST = "smtp.gmail.com"
    EMAIL_PORT = 587
    EMAIL_USE_TLS = True
    EMAIL_HOST_USER = _GMAIL_USER
    EMAIL_HOST_PASSWORD = _GMAIL_APP_PASSWORD
    _FROM_NAME = env.get("ONEWOOZ_FROM_NAME", "OneWooz")
    DEFAULT_FROM_EMAIL = "%s <%s>" % (_FROM_NAME, _GMAIL_USER)
    SERVER_EMAIL = DEFAULT_FROM_EMAIL
else:
    # --- Amazon SES / legacy SMTP (fallback only) ---
    EMAIL_HOST = env.get("ONEWOOZ_EMAIL_HOST", required=True)
    EMAIL_PORT = env.get_int("ONEWOOZ_EMAIL_PORT", 587)
    EMAIL_HOST_USER = env.get("ONEWOOZ_EMAIL_USER", required=True)
    EMAIL_HOST_PASSWORD = env.get("ONEWOOZ_EMAIL_PASSWORD", required=True)
    EMAIL_USE_TLS = True

# --------------------------------------------------------------------------
# Error tracking
# --------------------------------------------------------------------------

SENTRY_DSN = env.get("ONEWOOZ_SENTRY_DSN", "")
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration()],
        traces_sample_rate=float(env.get("ONEWOOZ_SENTRY_TRACES", "0.05")),
        environment=env.get("ONEWOOZ_ENV", "production"),
        send_default_pii=False,
    )
