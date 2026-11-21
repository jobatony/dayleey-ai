#!/bin/sh
set -eu
python manage.py migrate --noinput
python manage.py setup_schedules
exec gunicorn dayleey.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers "${GUNICORN_WORKERS:-2}" \
  --timeout "${GUNICORN_TIMEOUT:-30}" \
  --access-logfile - \
  --error-logfile -
