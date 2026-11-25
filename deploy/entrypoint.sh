#!/bin/sh
# OneWooz container entrypoint. The first argument selects the role:
#
#   web      collectstatic + migrate (advisory-locked), then Gunicorn
#   marketplace  Gunicorn only, for the marketplace.onewooz.com service
#   worker   Celery worker (queues from CELERY_QUEUES, default "celery")
#   beat     Celery beat (run exactly one replica of this)
#   migrate  run migrations and exit (used as a one-off deploy step)
#   shell    Django shell
#   *        exec the given command verbatim
#
# On web start we ALWAYS collectstatic and migrate, so every pushed image is
# applied on deploy with no separate step to forget. Migrations are safe across
# the several web replicas because `deploy_migrate` takes a MySQL advisory lock
# (only one replica migrates; the rest wait, then find nothing to do). Set
# RUN_MIGRATIONS=0 to opt out (e.g. if you migrate via a one-off task instead).

set -eu

ROLE="${1:-web}"

: "${GUNICORN_WORKERS:=3}"
: "${GUNICORN_THREADS:=2}"
: "${GUNICORN_TIMEOUT:=60}"
# Idle keep-alive must outlast the load balancer's 60 s idle timeout. With
# Gunicorn's 2 s default the ALB sometimes sends a request down a connection
# Gunicorn has just closed, which visitors see as a 502 (7 to 45 a day before
# this change).
: "${GUNICORN_KEEPALIVE:=75}"
: "${CELERY_CONCURRENCY:=2}"
: "${CELERY_LOGLEVEL:=info}"
# Queues this worker consumes. Default: Celery's default queue only. The
# dedicated marketplace worker service sets CELERY_QUEUES=marketplace, used once
# ONEWOOZ_MARKETPLACE_QUEUE=marketplace routes marketplace tasks there.
: "${CELERY_QUEUES:=celery}"
# Celery execution pool. Stay on prefork: several tasks (Google Merchant full
# sync, catalogue jobs) rely on soft_time_limit/time_limit, which the threads
# pool silently ignores, so a stuck job would never be stopped. Scale workers
# out (queue-length autoscaling) instead of switching pools.
: "${CELERY_POOL:=prefork}"

# Pages each fresh Gunicorn process renders once before taking traffic
# (core.gunicorn_conf). Comma-separated host/path; empty disables warm-up.
case "$ROLE" in
  web)         : "${GUNICORN_WARMUP=${ONEWOOZ_MARKETING_HOST:-onewooz.com}/,${ONEWOOZ_APP_HOST:-app.onewooz.com}/sign-in/}" ;;
  marketplace) : "${GUNICORN_WARMUP=${ONEWOOZ_MARKETPLACE_HOST:-marketplace.onewooz.com}/}" ;;
esac
export GUNICORN_WARMUP="${GUNICORN_WARMUP:-}"

# Access log: Gunicorn's standard fields first (existing log queries keep
# parsing), then the Host header and the request time in ms. Health checks are
# not logged at all (core.gunicorn_logging.QuietHealthLogger).
if [ -z "${GUNICORN_ACCESS_FORMAT:-}" ]; then
  GUNICORN_ACCESS_FORMAT='%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %({host}i)s %(M)sms'
fi

case "$ROLE" in
  web)
    # Advisory-locked: replicas booting together must not rewrite the shared
    # S3 static manifest at the same time (that race crash-looped web tasks).
    python manage.py deploy_collectstatic
    if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
      # Advisory-locked so concurrent web replicas don't race (see the command).
      python manage.py deploy_migrate
      # Report which owners still need a frozen entitlement snapshot. Read-only
      # (--dry-run writes nothing), so it is safe to run on every web replica;
      # drop --dry-run here if you want the deploy to actually seed them.
      python manage.py backfill_entitlements --dry-run
    fi
    exec gunicorn onewooz.wsgi:application \
      --bind 0.0.0.0:8000 \
      --workers "$GUNICORN_WORKERS" \
      --threads "$GUNICORN_THREADS" \
      --timeout "$GUNICORN_TIMEOUT" \
      --keep-alive "$GUNICORN_KEEPALIVE" \
      --graceful-timeout 30 \
      --max-requests 1000 \
      --max-requests-jitter 100 \
      --access-logfile - \
      --access-logformat "$GUNICORN_ACCESS_FORMAT" \
      --logger-class core.gunicorn_logging.QuietHealthLogger \
      --config python:core.gunicorn_conf \
      --error-logfile -
    ;;
  marketplace)
    # OneWooz Apps (marketplace.onewooz.com): Gunicorn only. No collectstatic,
    # no migrations: the web service owns those, so this service's deploys and
    # crashes can never touch the schema, the static manifest or the dashboard.
    exec gunicorn onewooz.wsgi:application \
      --bind 0.0.0.0:8000 \
      --workers "${MARKETPLACE_GUNICORN_WORKERS:-2}" \
      --threads "$GUNICORN_THREADS" \
      --timeout "$GUNICORN_TIMEOUT" \
      --keep-alive "$GUNICORN_KEEPALIVE" \
      --graceful-timeout 30 \
      --max-requests 1000 \
      --max-requests-jitter 100 \
      --access-logfile - \
      --access-logformat "$GUNICORN_ACCESS_FORMAT" \
      --logger-class core.gunicorn_logging.QuietHealthLogger \
      --config python:core.gunicorn_conf \
      --error-logfile -
    ;;
  worker)
    exec celery -A onewooz worker \
      --loglevel "$CELERY_LOGLEVEL" \
      --pool "$CELERY_POOL" \
      --concurrency "$CELERY_CONCURRENCY" \
      -Q "$CELERY_QUEUES"
    ;;
  beat)
    exec celery -A onewooz beat \
      --loglevel "$CELERY_LOGLEVEL" \
      --scheduler django_celery_beat.schedulers:DatabaseScheduler
    ;;
  migrate)
    exec python manage.py deploy_migrate
    ;;
  shell)
    exec python manage.py shell
    ;;
  *)
    exec "$@"
    ;;
esac
