"""Fail-fast validation helpers for production configuration."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _get(cfg: Any, key: str, default: Any = None) -> Any:
    if isinstance(cfg, Mapping):
        return cfg.get(key, default)
    return getattr(cfg, key, default)


def validate_production_settings(cfg: Any) -> None:
    """Validate critical production invariants and raise RuntimeError on unsafe defaults."""
    errors: list[str] = []

    secret_key = str(_get(cfg, "SECRET_KEY", "") or "")
    if not secret_key or len(secret_key) < 32 or secret_key.startswith("dev-insecure"):
        errors.append("SECRET_KEY must be set to a random string of at least 32 characters.")

    allow_sqlite = bool(_get(cfg, "ALLOW_SQLITE_IN_PROD", False))
    database_url = str(_get(cfg, "DATABASE_URL", "") or "").strip()
    if not database_url and not allow_sqlite:
        errors.append("DATABASE_URL must be set to PostgreSQL in production.")

    if bool(_get(cfg, "WA_DRY_RUN", False)):
        errors.append("WA_DRY_RUN must be False in production.")

    allowed_hosts = [
        str(h).strip()
        for h in (_get(cfg, "ALLOWED_HOSTS", []) or [])
        if str(h).strip() and str(h).strip() not in {"localhost", "127.0.0.1"}
    ]
    if not allowed_hosts:
        errors.append("ALLOWED_HOSTS must include at least one non-localhost production domain or IP.")

    transport = str(_get(cfg, "WA_TRANSPORT", "cloud_api") or "").strip().lower()
    if transport != "cloud_api":
        errors.append("WA_TRANSPORT must be 'cloud_api' in production.")
    else:
        app_secret = str(_get(cfg, "WA_APP_SECRET", "") or "").strip()
        verify_token = str(_get(cfg, "WA_VERIFY_TOKEN", "") or "").strip()
        access_token = str(_get(cfg, "WA_ACCESS_TOKEN", "") or "").strip()
        phone_number_id = str(_get(cfg, "WA_PHONE_NUMBER_ID", "") or "").strip()
        if not app_secret or app_secret == "dev-app-secret":
            errors.append("WA_APP_SECRET must be set to your real Meta App Secret in cloud_api mode.")
        if not verify_token or verify_token == "dayleey-verify-token":
            errors.append("WA_VERIFY_TOKEN must be changed from its dev default in cloud_api mode.")
        if not access_token:
            errors.append("WA_ACCESS_TOKEN must be set in cloud_api mode.")
        if not phone_number_id:
            errors.append("WA_PHONE_NUMBER_ID must be set in cloud_api mode.")

    if errors:
        raise RuntimeError("Invalid production configuration:\n- " + "\n- ".join(errors))
