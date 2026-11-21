"""Development & local test settings."""
from .base import *  # noqa: F401, F403
from .base import _env_bool

DEBUG = True

# In dev/test, run django-q tasks synchronously unless DJANGO_Q_SYNC=False (for local qcluster testing)
Q_CLUSTER = {**Q_CLUSTER, "sync": _env_bool("DJANGO_Q_SYNC", True)}  # noqa: F405
