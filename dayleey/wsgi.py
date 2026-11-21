"""WSGI config for Dayleey AI."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dayleey.settings.prod")

application = get_wsgi_application()
