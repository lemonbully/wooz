#!/bin/sh
# OneWooz container entrypoint. The first argument selects the role:
#
#   web      collectstatic + migrate (advisory-locked), then Gunicorn
#   worker   Celery worker
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
: "${CELERY_CONCURRENCY:=2}"
: "${CELERY_LOGLEVEL:=info}"

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
      --graceful-timeout 30 \
      --max-requests 1000 \
      --max-requests-jitter 100 \
      --access-logfile - \
      --error-logfile -
    ;;
  worker)
    exec celery -A onewooz worker \
      --loglevel "$CELERY_LOGLEVEL" \
      --concurrency "$CELERY_CONCURRENCY"
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
