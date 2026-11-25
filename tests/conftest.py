"""Ensure pytest runs isolated from live .env settings (dry-run, synchronous queue, clean allowlist)."""
import pytest


@pytest.fixture(autouse=True)
def _isolate_test_settings(settings):
    settings.WA_DRY_RUN = True
    settings.WA_TRANSPORT = "dry_run"
    settings.WA_TEMPLATES_APPROVED = True
    settings.PAUSE_ALL = False
    settings.BETA_ALLOWLIST_ENABLED = False
    settings.BETA_ALLOWLIST_PHONES = set()
    settings.Q_CLUSTER = {**settings.Q_CLUSTER, "sync": True}
