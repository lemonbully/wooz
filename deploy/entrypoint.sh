#!/bin/sh
# OneWooz container entrypoint. The first argument selects the role:
#
#   web      collectstatic, (optional migrate), then Gunicorn
#   worker   Celery worker
#   beat     Celery beat (run exactly one replica of this)
#   migrate  run migrations and exit (used as a one-off deploy step)
#   shell    Django shell
#   *        exec the given command verbatim
#
# Migrations do NOT run automatically on web start: with several web replicas
# that would race. Run them once per deploy via the `migrate` role (an ECS
# run-task / compose one-off) or set RUN_MIGRATIONS=1 for a single-instance box.

set -eu

ROLE="${1:-web}"

: "${GUNICORN_WORKERS:=3}"
: "${GUNICORN_THREADS:=2}"
: "${GUNICORN_TIMEOUT:=60}"
: "${CELERY_CONCURRENCY:=2}"
: "${CELERY_LOGLEVEL:=info}"

case "$ROLE" in
  web)
    python manage.py collectstatic --noinput
    if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
      python manage.py migrate --noinput
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
    exec python manage.py migrate --noinput
    ;;
  shell)
    exec python manage.py shell
    ;;
  *)
    exec "$@"
    ;;
esac
