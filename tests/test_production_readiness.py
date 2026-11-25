"""Tests for production settings validation, readiness probes, atomic budget counters, and log pruning."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import Client
from django.utils import timezone
from django_q.models import Schedule

from apps.accounts.models import Person
from apps.messaging.budget import get_or_create_global_counter, record_delivery, record_sent
from apps.messaging.models import MessageLog, UsageCounter
from dayleey.settings.validation import validate_production_settings


def _valid_prod_cfg(**overrides):
    cfg = {
        "SECRET_KEY": "a" * 50,
        "DATABASE_URL": "postgres://user:pass@db.example:5432/dayleey?sslmode=require",
        "ALLOW_SQLITE_IN_PROD": False,
        "WA_DRY_RUN": False,
        "WA_TRANSPORT": "cloud_api",
        "ALLOWED_HOSTS": ["api.dayleey.example"],
        "WA_APP_SECRET": "real-meta-app-secret",
        "WA_VERIFY_TOKEN": "real-verify-token-xyz",
        "WA_ACCESS_TOKEN": "real-system-user-token",
        "WA_PHONE_NUMBER_ID": "1234567890",
    }
    cfg.update(overrides)
    return cfg


def test_validate_production_settings_accepts_valid_cloud_api():
    validate_production_settings(_valid_prod_cfg())


@pytest.mark.parametrize(
    "overrides,expected_substr",
    [
        ({"SECRET_KEY": "dev-insecure-secret-key-change-me"}, "SECRET_KEY"),
        ({"SECRET_KEY": "short"}, "SECRET_KEY"),
        ({"DATABASE_URL": ""}, "DATABASE_URL"),
        ({"WA_DRY_RUN": True}, "WA_DRY_RUN"),
        ({"ALLOWED_HOSTS": ["localhost", "127.0.0.1"]}, "ALLOWED_HOSTS"),
        ({"WA_TRANSPORT": "dry_run"}, "WA_TRANSPORT"),
        ({"WA_TRANSPORT": "baileys"}, "WA_TRANSPORT"),
        ({"WA_APP_SECRET": "dev-app-secret"}, "WA_APP_SECRET"),
        ({"WA_VERIFY_TOKEN": "dayleey-verify-token"}, "WA_VERIFY_TOKEN"),
        ({"WA_ACCESS_TOKEN": ""}, "WA_ACCESS_TOKEN"),
        ({"WA_PHONE_NUMBER_ID": ""}, "WA_PHONE_NUMBER_ID"),
    ],
)
def test_validate_production_settings_rejects_unsafe_defaults(overrides, expected_substr):
    with pytest.raises(RuntimeError, match=expected_substr):
        validate_production_settings(_valid_prod_cfg(**overrides))


@pytest.mark.django_db
def test_healthz_and_readyz_endpoints():
    Schedule.objects.get_or_create(
        name="dayleey_per_minute_tick",
        defaults={
            "func": "apps.engine.scheduler.tick",
            "schedule_type": Schedule.MINUTES,
            "minutes": 1,
            "repeats": -1,
        },
    )
    c = Client()
    h = c.get("/healthz/")
    assert h.status_code == 200
    assert h.json()["status"] == "ok"

    r = c.get("/readyz/")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready" or body["status"] == "healthy"
    assert body["healthy"] is True
    assert body["db_ok"] is True
    assert body["scheduler_registered"] is True


@pytest.mark.django_db
def test_atomic_counters_and_idempotent_delivery():
    person = Person.objects.create(phone="+2348012345678", name="Ada", opted_in=True)
    g1 = get_or_create_global_counter()
    g2 = get_or_create_global_counter()
    assert g1.pk == g2.pk

    record_sent(person)
    record_sent(person)

    log = MessageLog.objects.create(
        person=person,
        phone=person.phone,
        direction=MessageLog.Direction.OUTBOUND,
        wa_message_id="wamid.ATOMIC1",
        msg_type=MessageLog.MsgType.TEMPLATE,
        template_name="partner_daily_report",
        status=MessageLog.Status.DELIVERED,
    )
    record_delivery(log)
    record_delivery(log)  # Duplicate call must be a no-op

    g = UsageCounter.objects.get(pk=g1.pk)
    p_counter = UsageCounter.objects.get(month=g.month, person=person)
    assert g.sent_total == 2
    assert g.delivered_template == 1
    assert g.est_cost == Decimal("0.006800")
    assert p_counter.sent_total == 2
    assert p_counter.delivered_template == 1
    assert log.delivery_counted is True
    assert log.billable is True


@pytest.mark.django_db
def test_prune_message_logs_redacts_only_old_payloads():
    person = Person.objects.create(phone="+2348098765432", name="Obi", opted_in=True)
    old_log = MessageLog.objects.create(
        person=person,
        phone=person.phone,
        direction=MessageLog.Direction.OUTBOUND,
        wa_message_id="wamid.OLD1",
        msg_type=MessageLog.MsgType.TEMPLATE,
        payload={"text": "secret priority text"},
        status=MessageLog.Status.DELIVERED,
        billable=True,
        est_cost=Decimal("0.006800"),
    )
    MessageLog.objects.filter(pk=old_log.pk).update(
        created_at=timezone.now() - timedelta(days=45)
    )
    recent_log = MessageLog.objects.create(
        person=person,
        phone=person.phone,
        direction=MessageLog.Direction.INBOUND,
        wa_message_id="wamid.NEW1",
        msg_type=MessageLog.MsgType.TEXT,
        payload={"text": "still recent"},
        status=MessageLog.Status.RECEIVED,
    )

    out = StringIO()
    call_command("prune_message_logs", "--days", "30", stdout=out)

    old_log.refresh_from_db()
    recent_log.refresh_from_db()
    assert old_log.payload == {"redacted": True}
    assert old_log.billable is True
    assert old_log.est_cost == Decimal("0.006800")
    assert recent_log.payload == {"text": "still recent"}
