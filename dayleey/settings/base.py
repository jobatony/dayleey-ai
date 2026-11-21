"""Base Django settings for Dayleey AI."""
from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_phone_digits(raw: str) -> str:
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    if len(digits) == 11 and digits[0] == "0" and digits[1] in "789" and digits[2] in "01":
        return "234" + digits[1:]
    return digits


SECRET_KEY = os.getenv("SECRET_KEY", "dev-insecure-secret-key-change-me")
DEBUG = _env_bool("DEBUG", False)
ALLOWED_HOSTS = [
    h.strip() for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "django_q",
    "apps.accounts",
    "apps.quarters",
    "apps.network",
    "apps.messaging",
    "apps.engine",
    "apps.reports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "dayleey.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "dayleey.wsgi.application"

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL, conn_max_age=600, conn_health_checks=True
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
            "OPTIONS": {
                "timeout": 20,
                "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
            },
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Django Q2 (DB-backed ORM broker — no Redis required)
Q_CLUSTER = {
    "name": "dayleey",
    "workers": 1,
    "timeout": 90,
    "retry": 120,
    "queue_limit": 50,
    "bulk": 10,
    "poll": 2,
    "orm": "default",
    "sync": False,
}

# WhatsApp Transport & Cloud API settings
WA_DRY_RUN = _env_bool("WA_DRY_RUN", True)
WA_TRANSPORT = os.getenv("WA_TRANSPORT", "dry_run" if WA_DRY_RUN else "cloud_api").strip().lower()
PAUSE_ALL = _env_bool("PAUSE_ALL", False)
WA_API_VERSION = os.getenv("WA_API_VERSION", "v21.0")
WA_PHONE_NUMBER_ID = os.getenv("WA_PHONE_NUMBER_ID", "")
WA_BOT_PHONE_NUMBER = _normalize_phone_digits(os.getenv("WA_BOT_PHONE_NUMBER", "2348000000000"))
WA_ACCESS_TOKEN = os.getenv("WA_ACCESS_TOKEN", "")
WA_VERIFY_TOKEN = os.getenv("WA_VERIFY_TOKEN", "dayleey-verify-token")
WA_APP_SECRET = os.getenv("WA_APP_SECRET", "dev-app-secret")

# Baileys Prototype Sidecar settings
BRIDGE_SECRET = os.getenv("BRIDGE_SECRET", "dev-bridge-secret")
BRIDGE_URL = os.getenv("BRIDGE_URL", "http://127.0.0.1:3001").rstrip("/")
BAILEYS_ALLOWLIST = {
    item.strip() if "@" in item else _normalize_phone_digits(item)
    for item in os.getenv("BAILEYS_ALLOWLIST", "").split(",")
    if item.strip()
}

# Bot Identity & UX Defaults
_raw_admin_url = os.getenv("ADMIN_URL", "admin/").strip().lstrip("/") or "admin/"
ADMIN_URL = _raw_admin_url if _raw_admin_url.endswith("/") else f"{_raw_admin_url}/"
BOT_DISPLAY_NAME = os.getenv("BOT_DISPLAY_NAME", "Dayleey")
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "Africa/Lagos")
PARTNER_FALLBACK_DAYS = int(os.getenv("PARTNER_FALLBACK_DAYS", "3"))
QUIET_HOURS_START = int(os.getenv("QUIET_HOURS_START", "22"))
QUIET_HOURS_END = int(os.getenv("QUIET_HOURS_END", "6"))
WEEKLY_REPORT_ENABLED = _env_bool("WEEKLY_REPORT_ENABLED", False)
BETA_ALLOWLIST_ENABLED = _env_bool("BETA_ALLOWLIST_ENABLED", False)

# Budget & Rate Card (update Nigeria/Rest of Africa rates from Meta's rate card before launch)
MONTHLY_BUDGET_USD = Decimal(os.getenv("MONTHLY_BUDGET_USD", "25.00"))
FREE_SERVICE_MESSAGES_PER_MONTH = int(os.getenv("FREE_SERVICE_MESSAGES_PER_MONTH", "1000"))
USER_MONTHLY_MSG_CAP = int(os.getenv("USER_MONTHLY_MSG_CAP", "350"))
USER_DAILY_MSG_LIMIT = int(os.getenv("USER_DAILY_MSG_LIMIT", "10"))
PARTNER_DAILY_MSG_LIMIT = int(os.getenv("PARTNER_DAILY_MSG_LIMIT", "2"))

_DEFAULT_RATE_CARD = {
    "default": {"service": 0.0050, "utility": 0.0068, "marketing": 0.0250},
    "NG": {"service": 0.0050, "utility": 0.0068, "marketing": 0.0250},
}
try:
    RATE_CARD = json.loads(os.getenv("RATE_CARD_JSON", "")) or _DEFAULT_RATE_CARD
except (json.JSONDecodeError, TypeError):
    RATE_CARD = _DEFAULT_RATE_CARD

# Optional Sentry
SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(dsn=SENTRY_DSN, integrations=[DjangoIntegration()], traces_sample_rate=0.1)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "[%(asctime)s] %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
}
