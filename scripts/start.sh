#!/bin/sh
# Production entrypoint. Both extras are opt-in so a normal multi-service deploy is unchanged.
#   RUN_MIGRATIONS_ON_START=true  apply migrations before serving (no shell/pre-deploy on free hosts)
#   RUN_EMBEDDED_WORKER=true      run a Celery worker + beat inside this container (no worker service on free hosts)
set -e

if [ "${RUN_MIGRATIONS_ON_START:-false}" = "true" ]; then
  python manage.py migrate --noinput
fi

if [ "${RUN_EMBEDDED_WORKER:-false}" = "true" ]; then
  celery -A config worker -l info --concurrency="${CELERY_EMBEDDED_CONCURRENCY:-1}" &
  celery -A config beat -l info --schedule=/tmp/celerybeat-schedule &
fi

exec gunicorn config.wsgi:application --bind "0.0.0.0:${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-3}" --access-logfile -
