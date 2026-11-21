web: gunicorn dayleey.wsgi --bind 0.0.0.0:$PORT --workers 2
worker: python manage.py qcluster
release: python manage.py migrate --noinput && python manage.py setup_schedules
