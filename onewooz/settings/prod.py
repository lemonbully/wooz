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
# not recognise  the marketing/app/admin hosts match exactly, storefronts must
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

# MySQL fails a query outright (error 1038, "Out of sort memory") when one row
# it has to sort does not fit the session sort buffer (256 KB by default). The
# code keeps unbounded columns out of sorts (``core.queries``); this raises the
# ceiling as defence in depth, so a large row that slips through degrades to a
# slower sort instead of a 500. MySQL 8 grows the buffer on demand, so small
# sorts use no more memory than before. Set to 0 to keep the server default.
DB_SORT_BUFFER_BYTES = env.get_int("ONEWOOZ_DB_SORT_BUFFER_BYTES", 4 * 1024 * 1024)

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
            **(
                {"init_command": f"SET SESSION sort_buffer_size={int(DB_SORT_BUFFER_BYTES)}"}
                if DB_SORT_BUFFER_BYTES > 0
                else {}
            ),
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
# Two S3 buckets, kept deliberately separate:
#   * default → tenant/user uploads (the "media" bucket). Private-by-default,
#     never overwritten, short cache.
#   * staticfiles → collected CSS/JS/fonts/images (the "static" bucket). A
#     DISTINCT bucket so a compromised upload can never overwrite an asset the
#     browser executes, and so the two have independent lifecycles and cache
#     policies. Assets are content-hashed (manifest) and served immutable;
#     collectstatic uploads them on each deploy.

#
# CDN: one CloudFront distribution (cdn.onewooz.com, deploy/aws/cdn/) fronts
# BOTH buckets  /static/* → static bucket, everything else → uploads bucket.
# Set ONEWOOZ_STATIC_CDN_DOMAIN and ONEWOOZ_MEDIA_CDN_DOMAIN to cdn.onewooz.com
# once the distribution is deployed; unset, URLs fall back to S3 directly.

AWS_STORAGE_BUCKET_NAME = env.get("ONEWOOZ_MEDIA_BUCKET", required=True)
AWS_S3_REGION_NAME = env.get("ONEWOOZ_AWS_REGION", "ap-south-1")
AWS_S3_CUSTOM_DOMAIN = env.get("ONEWOOZ_MEDIA_CDN_DOMAIN", "")
AWS_QUERYSTRING_AUTH = False
AWS_DEFAULT_ACL = None
AWS_S3_FILE_OVERWRITE = False
# Upload keys are never overwritten (a re-upload gets a new suffixed name), so a
# URL's bytes never change and a week in the browser is safe. The CDN's own
# media cache policy holds them at the edge for longer.
AWS_S3_OBJECT_PARAMETERS = {"CacheControl": "public, max-age=604800"}

# Dedicated static-assets bucket, separate from the uploads bucket above. An
# optional CDN domain (CloudFront) fronts it; otherwise the S3 virtual-hosted
# endpoint is used directly.
AWS_STATIC_BUCKET_NAME = env.get("ONEWOOZ_STATIC_BUCKET", required=True)
AWS_STATIC_CUSTOM_DOMAIN = env.get("ONEWOOZ_STATIC_CDN_DOMAIN", "")
_STATIC_HOST = AWS_STATIC_CUSTOM_DOMAIN or (
    f"{AWS_STATIC_BUCKET_NAME}.s3.{AWS_S3_REGION_NAME}.amazonaws.com"
)

STORAGES = {
    "default": {"BACKEND": "storages.backends.s3.S3Storage"},
    "staticfiles": {
        # S3 + manifest hashing, with CSS/JS minified in place at collectstatic
        # time (see onewooz.staticfiles). Production only  dev keeps the plain,
        # un-minified storage, so authored source is never transformed locally.
        "BACKEND": "onewooz.staticfiles.MinifyingManifestStaticStorage",
        "OPTIONS": {
            "bucket_name": AWS_STATIC_BUCKET_NAME,
            "region_name": AWS_S3_REGION_NAME,
            # Pin the URL host (CDN if given, else the S3 virtual-hosted
            # endpoint) so emitted asset URLs, STATIC_URL and the CSP below all
            # agree on one origin regardless of boto's addressing-style default.
            "custom_domain": _STATIC_HOST,
            "location": "static",
            "querystring_auth": False,
            "default_acl": None,
            # Filenames are content-hashed, so overwriting is safe and the assets
            # can be cached forever.
            "file_overwrite": True,
            "object_parameters": {"CacheControl": "max-age=31536000, immutable"},
        },
    },
}

# Templates resolve static assets through the staticfiles storage above (so
# {% static %} already emits absolute bucket/CDN URLs); STATIC_URL is kept
# consistent for any code that reads it directly.
STATIC_URL = f"https://{_STATIC_HOST}/static/"

if AWS_S3_CUSTOM_DOMAIN:
    MEDIA_URL = f"https://{AWS_S3_CUSTOM_DOMAIN}/"

# Owner-uploaded product/store media (images and, crucially, videos) live in the
# uploads bucket, served from a *different origin* than the page. media-src is
# strict everywhere ('self' data: blob')  so an uploaded video served from
# S3/CloudFront is blocked on EVERY surface: the storefront PDP, and equally the
# owner dashboard's media library and the product-form video previews. img-src
# already allows https: generally, so images were fine; video is what breaks.
# Admit the uploads origin(s) to the BASE media-src so all surfaces can play
# them (the storefront then adds only its demo-video hosts on top). Both S3
# virtual-hosted spellings (with and without the region segment) are included
# because boto's addressing default decides which host it emits, plus the CDN
# domain when one is configured.
_MEDIA_ORIGINS = [
    f"https://{AWS_STORAGE_BUCKET_NAME}.s3.{AWS_S3_REGION_NAME}.amazonaws.com",
    f"https://{AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com",
]
if AWS_S3_CUSTOM_DOMAIN:
    _MEDIA_ORIGINS.insert(0, f"https://{AWS_S3_CUSTOM_DOMAIN}")
# Demo/reference videos and the storefront's autoplaying reels ship as *collected
# static assets* (e.g. static/storefront/v1.<hash>.mp4), so they are served from
# the STATIC bucket/CDN, not the uploads bucket above. Without the static origin
# in media-src, every such <video> is blocked by CSP on the storefront  which is
# exactly the "videos/images not rendering" symptom on <slug>.onewooz.site and on
# custom domains (the CSP is identical on both, since these are absolute S3/CDN
# URLs). Admit the static origin(s) too. Owner-uploaded media already lives in the
# uploads bucket above, so it is unaffected.
_MEDIA_ORIGINS.append(f"https://{_STATIC_HOST}")
_MEDIA_ORIGINS.append(f"https://{AWS_STATIC_BUCKET_NAME}.s3.amazonaws.com")
CSP_MEDIA_SRC = tuple(CSP_MEDIA_SRC) + tuple(_MEDIA_ORIGINS)  # noqa: F405

# Static now loads from a different origin than the app, so the CSP must admit
# that origin for every asset type the browser fetches from it. img-src already
# allows https: generally, so only script/style/font/connect need widening.
_STATIC_ORIGIN = f"https://{_STATIC_HOST}"
CSP_SCRIPT_SRC = tuple(CSP_SCRIPT_SRC) + (_STATIC_ORIGIN,)  # noqa: F405
CSP_STYLE_SRC = tuple(CSP_STYLE_SRC) + (_STATIC_ORIGIN,)  # noqa: F405
CSP_FONT_SRC = tuple(CSP_FONT_SRC) + (_STATIC_ORIGIN,)  # noqa: F405
CSP_CONNECT_SRC = tuple(CSP_CONNECT_SRC) + (_STATIC_ORIGIN,)  # noqa: F405

# Every HTML page preconnects to the CDN (core.middleware.EdgeDeliveryMiddleware)
# so the TLS handshake for CSS/JS/fonts/media overlaps HTML parsing. With one
# distribution for both buckets this is a single origin.
CDN_PRECONNECT_ORIGINS = tuple(
    dict.fromkeys(
        [_STATIC_ORIGIN] + ([f"https://{AWS_S3_CUSTOM_DOMAIN}"] if AWS_S3_CUSTOM_DOMAIN else [])
    )
)

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
#   0. Amazon SES  ONEWOOZ_EMAIL_USE_SES=true. ACTIVE sender in production.
#      Sends through core.mail.SESEmailBackend, authenticated by the ECS task
#      role, so there is NO SMTP username or password. Set on the web + worker
#      task definitions:
#         ONEWOOZ_EMAIL_USE_SES    "true" to select this path
#         ONEWOOZ_FROM_EMAIL       verified SES sender, e.g. no-reply@onewooz.com
#                                  (a bare address or a full "Name <addr>" header)
#         ONEWOOZ_FROM_NAME        optional display name; default "OneWooz"
#      The From address's domain must be a verified SES identity and permitted by
#      the task role's SES policy (deploy/aws/ses/). Region + optional config set
#      are shared with store mail (STORE_EMAIL_SES_* below).
#   1. Generic SMTP  ONEWOOZ_SMTP_* (Hostinger is the default host). The
#      fallback when ONEWOOZ_EMAIL_USE_SES is unset/false:
#         ONEWOOZ_SMTP_USER        full mailbox, e.g. otp@yourdomain.com
#         ONEWOOZ_SMTP_PASSWORD    that mailbox's password  (store as a secret)
#         ONEWOOZ_SMTP_HOST        default "smtp.hostinger.com"
#         ONEWOOZ_SMTP_PORT        default 587
#         ONEWOOZ_SMTP_SSL         "true" for port 465 (implicit SSL); else TLS
#         ONEWOOZ_SMTP_TLS         default true (STARTTLS on 587)
#         ONEWOOZ_FROM_NAME        optional display name; default "OneWooz"
#         ONEWOOZ_FROM_EMAIL       optional; defaults to "OneWooz <mailbox above>"
#   2. Gmail  ONEWOOZ_GMAIL_USER + ONEWOOZ_GMAIL_APP_PASSWORD.
#   3. Amazon SES SMTP / legacy SMTP  ONEWOOZ_EMAIL_* (last-resort fallback).
#
# SES accepts any From on a verified domain, so ONEWOOZ_FROM_EMAIL is free to be
# a no-reply address. The SMTP fallbacks (Hostinger/Gmail) instead reject a
# mismatched From, so there the address defaults to the authenticated mailbox and
# ONEWOOZ_FROM_EMAIL should keep that same address (a display name may be added).

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_TIMEOUT = env.get_int("ONEWOOZ_SMTP_TIMEOUT", 20)

# .strip() every credential: env.get returns the environment value verbatim,
# and a secret injected from SSM/Secrets Manager very often carries a trailing
# newline (from how the parameter was written  e.g. `echo "x" | put-parameter`).
# An SMTP username or password with a stray newline authenticates as a different,
# wrong credential, which the server rejects with "535 authentication failed"
# even though the stored value looks correct. Surrounding whitespace is never
# valid in any of these, so stripping it is always safe.
_SMTP_USER = env.get("ONEWOOZ_SMTP_USER", "").strip()
_SMTP_PASSWORD = env.get("ONEWOOZ_SMTP_PASSWORD", "").strip()
_GMAIL_USER = env.get("ONEWOOZ_GMAIL_USER", "").strip()
# A Gmail app password is shown in 4-char groups; the real password has NO
# spaces. Remove every space (not just the ends) so a value pasted "abcd efgh
# ijkl mnop" still authenticates.
_GMAIL_APP_PASSWORD = env.get("ONEWOOZ_GMAIL_APP_PASSWORD", "").replace(" ", "").strip()

_USE_SES_EMAIL = env.get_bool("ONEWOOZ_EMAIL_USE_SES", False)

if _USE_SES_EMAIL:
    # --- Amazon SES via the ECS task role (recommended; no SMTP password) ---
    # OneWooz's own transactional mail (OTP / sign-in / sign-up / receipts) is
    # sent through the same SESv2 backend as store→customer mail
    # (core.mail.SESEmailBackend), authenticated by the task role  so there is
    # no SMTP username or password anywhere. The From address must sit on an
    # SES-verified domain; ONEWOOZ_FROM_EMAIL supplies it, either as a bare
    # address ("hello@onewooz.com") or a full header ("OneWooz <hello@onewooz.com>").
    # The SES region and optional configuration set are shared with store mail
    # (STORE_EMAIL_SES_REGION / STORE_EMAIL_SES_CONFIGURATION_SET, below).
    EMAIL_BACKEND = "core.mail.SESEmailBackend"
    _FROM_NAME = env.get("ONEWOOZ_FROM_NAME", "OneWooz")
    _FROM_EMAIL = env.get("ONEWOOZ_FROM_EMAIL", required=True).strip()
    DEFAULT_FROM_EMAIL = (
        _FROM_EMAIL if "<" in _FROM_EMAIL else "%s <%s>" % (_FROM_NAME, _FROM_EMAIL)
    )
    SERVER_EMAIL = DEFAULT_FROM_EMAIL
elif _SMTP_USER and _SMTP_PASSWORD:
    # --- Hostinger / generic SMTP (active) ---
    EMAIL_HOST = env.get("ONEWOOZ_SMTP_HOST", "smtp.hostinger.com").strip()
    EMAIL_PORT = env.get_int("ONEWOOZ_SMTP_PORT", 587)
    EMAIL_HOST_USER = _SMTP_USER
    EMAIL_HOST_PASSWORD = _SMTP_PASSWORD
    EMAIL_USE_SSL = env.get_bool("ONEWOOZ_SMTP_SSL", False)
    EMAIL_USE_TLS = False if EMAIL_USE_SSL else env.get_bool("ONEWOOZ_SMTP_TLS", True)
    # Give the From header a display name ("OneWooz <hello@onewooz.com>") so mail
    # clients show the brand, not the mailbox's local part ("hello"). The address
    # itself stays the authenticated mailbox, so Hostinger's matched-From rule is
    # satisfied  only a name is prepended. ONEWOOZ_FROM_NAME / ONEWOOZ_FROM_EMAIL
    # still override.
    _FROM_NAME = env.get("ONEWOOZ_FROM_NAME", "OneWooz")
    DEFAULT_FROM_EMAIL = env.get("ONEWOOZ_FROM_EMAIL", "%s <%s>" % (_FROM_NAME, _SMTP_USER))
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
    EMAIL_HOST = env.get("ONEWOOZ_EMAIL_HOST", required=True).strip()
    EMAIL_PORT = env.get_int("ONEWOOZ_EMAIL_PORT", 587)
    EMAIL_HOST_USER = env.get("ONEWOOZ_EMAIL_USER", required=True).strip()
    EMAIL_HOST_PASSWORD = env.get("ONEWOOZ_EMAIL_PASSWORD", required=True).strip()
    EMAIL_USE_TLS = True

# --- Store → customer email -----------------------------------------------
# Independent of OneWooz's own transactional sender above. Every store mails its
# customers from <brand>@via.onewooz.website. When ONEWOOZ_STORE_SMTP_HOST is set
# (Cloudflare's smtp.mx.cloudflare.net), core.mail.store_mail_connection sends
# through that relay (STORE_SMTP_* from base). Otherwise it falls back to the
# Amazon SES backend below (auth via the ECS task role  no secret to store).
# See core.mail and tenancy.email.
STORE_EMAIL_BACKEND = env.get("ONEWOOZ_STORE_EMAIL_BACKEND", "core.mail.SESEmailBackend")
STORE_EMAIL_SES_REGION = env.get("ONEWOOZ_STORE_EMAIL_SES_REGION", AWS_S3_REGION_NAME)
STORE_EMAIL_SES_CONFIGURATION_SET = env.get(
    "ONEWOOZ_STORE_EMAIL_SES_CONFIGURATION_SET", ""
)
STOREFRONT_EMAIL_DOMAIN = env.get("ONEWOOZ_STOREFRONT_EMAIL_DOMAIN", "via.onewooz.website")

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
