"""Ensure pytest runs isolated from live .env settings (dry-run, synchronous queue, clean allowlist)."""
import pytest


@pytest.fixture(autouse=True)
def _isolate_test_settings(settings):
    settings.WA_DRY_RUN = True
    settings.WA_TRANSPORT = "dry_run"
    settings.PAUSE_ALL = False
    settings.BAILEYS_ALLOWLIST = set()
    settings.Q_CLUSTER = {**settings.Q_CLUSTER, "sync": True}
