"""Production settings."""
import os

from .base import *  # noqa: F401, F403
from .base import ALLOWED_HOSTS, DATABASE_URL, SECRET_KEY, WA_ACCESS_TOKEN, WA_APP_SECRET, WA_DRY_RUN, WA_PHONE_NUMBER_ID, WA_TRANSPORT, WA_VERIFY_TOKEN, BRIDGE_SECRET, _env_bool
from .validation import validate_production_settings

DEBUG = False
ALLOW_SQLITE_IN_PROD = _env_bool("ALLOW_SQLITE_IN_PROD", False)

validate_production_settings(
    {
        "SECRET_KEY": SECRET_KEY,
        "DATABASE_URL": DATABASE_URL,
        "ALLOW_SQLITE_IN_PROD": ALLOW_SQLITE_IN_PROD,
        "WA_DRY_RUN": WA_DRY_RUN,
        "WA_TRANSPORT": WA_TRANSPORT,
        "ALLOWED_HOSTS": ALLOWED_HOSTS,
        "WA_APP_SECRET": WA_APP_SECRET,
        "WA_VERIFY_TOKEN": WA_VERIFY_TOKEN,
        "WA_ACCESS_TOKEN": WA_ACCESS_TOKEN,
        "WA_PHONE_NUMBER_ID": WA_PHONE_NUMBER_ID,
        "BRIDGE_SECRET": BRIDGE_SECRET,
    }
)

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = _env_bool("SECURE_SSL_REDIRECT", True)
SECURE_REDIRECT_EXEMPT = [r"^healthz/$", r"^readyz/$"]
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"

_raw_csrf = os.getenv("CSRF_TRUSTED_ORIGINS", "").strip()
if _raw_csrf:
    CSRF_TRUSTED_ORIGINS = [u.strip() for u in _raw_csrf.split(",") if u.strip()]
else:
    CSRF_TRUSTED_ORIGINS = [
        f"https://{h.lstrip('.')}"
        for h in ALLOWED_HOSTS
        if h not in {"localhost", "127.0.0.1"} and "." in h
    ]

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
