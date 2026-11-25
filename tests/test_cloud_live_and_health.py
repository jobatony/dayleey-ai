"""Tests for Cloud API test-number guardrails, beta allowlist env, /readyz/ worker heartbeat, and Admin status view."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.utils import timezone
from django_q.models import Schedule, Task

from apps.accounts.models import Person
from apps.engine.router import InboundMessage, handle_inbound
from apps.messaging.client import WhatsAppClient, clear_dry_run_outbox, pop_dry_run_outbox
from apps.messaging.models import MessageLog


@pytest.mark.django_db
def test_unapproved_templates_block_out_of_window_without_http_post(settings):
    settings.WA_DRY_RUN = False
    settings.WA_TRANSPORT = "cloud_api"
    settings.WA_TEMPLATES_APPROVED = False
    settings.WA_PHONE_NUMBER_ID = "12345"
    settings.WA_ACCESS_TOKEN = "token"

    client = WhatsAppClient(transport="cloud_api")

    # Closed 24h window -> blocked cleanly as templates_unapproved_window_closed, zero HTTP POSTs
    closed_person = Person.objects.create(
        phone="+2348011111111",
        name="Ada",
        opted_in=True,
        window_expires_at=timezone.now() - timedelta(hours=2),
    )
    with patch("apps.messaging.client.httpx.Client.post") as mock_post:
        log = client.send_text(
            closed_person,
            "Daily wrap-up",
            fallback_template=("user_daily_wrapup", ["Ada", "2026-11-25", "Done"]),
            allow_quiet_hours=True,
        )
        assert mock_post.called is False
        assert log.status == MessageLog.Status.BLOCKED
        assert log.error_code == "templates_unapproved_window_closed"

    # Open 24h window -> still sends free-form text via Graph API
    open_person = Person.objects.create(
        phone="+2348022222222",
        name="Bola",
        opted_in=True,
        window_expires_at=timezone.now() + timedelta(hours=12),
    )
    ok_resp = MagicMock(status_code=200)
    ok_resp.json.return_value = {"messages": [{"id": "wamid.OPEN1"}]}
    with patch("apps.messaging.client.httpx.Client.post", return_value=ok_resp) as mock_post:
        log_ok = client.send_text(open_person, "In-window reply", allow_quiet_hours=True)
        assert mock_post.called is True
        assert log_ok.status == MessageLog.Status.SENT


@pytest.mark.django_db
def test_beta_allowlist_phones_auto_allows_listed_and_blocks_others(settings):
    from dayleey.settings.base import _normalize_phone_digits

    clear_dry_run_outbox()
    settings.BETA_ALLOWLIST_ENABLED = True
    # Verify local Nigerian 0... format normalizes to 234...
    settings.BETA_ALLOWLIST_PHONES = {_normalize_phone_digits("07025133613")}

    # Listed tester proceeds straight to onboarding
    handle_inbound(
        InboundMessage(wa_message_id="allow_1", phone="+2347025133613", text="hi"),
        client=WhatsAppClient(dry_run=True),
    )
    out_listed = pop_dry_run_outbox()
    assert len(out_listed) == 1
    assert "Welcome to *Dayleey*" in out_listed[0]["body"]
    assert Person.objects.get(phone="+2347025133613").is_allowlisted is True

    # Unlisted number gets closed-beta notice
    handle_inbound(
        InboundMessage(wa_message_id="allow_2", phone="+2348099999999", text="hi"),
        client=WhatsAppClient(dry_run=True),
    )
    out_unlisted = pop_dry_run_outbox()
    assert len(out_unlisted) == 1
    assert "closed beta" in out_unlisted[0]["body"].lower()
    assert Person.objects.get(phone="+2348099999999").is_allowlisted is False


@pytest.mark.django_db
def test_readyz_and_check_worker_health_detect_fresh_and_stale_ticks(settings):
    settings.Q_CLUSTER = {**settings.Q_CLUSTER, "sync": False}
    Schedule.objects.create(
        name="dayleey_per_minute_tick",
        func="apps.engine.scheduler.tick",
        schedule_type=Schedule.MINUTES,
        minutes=1,
        repeats=-1,
        next_run=timezone.now() - timedelta(minutes=10),
    )

    now = timezone.now()
    Task.objects.create(
        id="tickrecent1234567890123456789012",
        name="recent-tick",
        func="apps.engine.scheduler.tick",
        started=now - timedelta(seconds=61),
        stopped=now - timedelta(seconds=60),
        success=True,
    )

    c = Client()
    ok = c.get("/readyz/")
    assert ok.status_code == 200
    assert ok.json()["healthy"] is True
    assert ok.json()["worker_ok"] is True
    call_command("check_worker_health")

    # Make the latest tick stale (> 5 minutes ago)
    Task.objects.filter(id="tickrecent1234567890123456789012").update(
        stopped=now - timedelta(minutes=10)
    )
    stale = c.get("/readyz/")
    assert stale.status_code == 503
    assert stale.json()["healthy"] is False
    assert stale.json()["worker_ok"] is False

    with pytest.raises(CommandError):
        call_command("check_worker_health")


@pytest.mark.django_db
def test_admin_system_status_dashboard_requires_staff_and_renders(settings):
    c = Client()
    status_url = f"/{settings.ADMIN_URL}status/"

    anon = c.get(status_url)
    assert anon.status_code == 302

    staff = User.objects.create_user(
        username="ops", password="password123", is_staff=True, is_superuser=True
    )
    c.force_login(staff)

    person = Person.objects.create(phone="+2348011112222", name="Tunde", opted_in=True)
    MessageLog.objects.create(
        person=person,
        phone=person.phone,
        direction=MessageLog.Direction.OUTBOUND,
        msg_type=MessageLog.MsgType.TEMPLATE,
        status=MessageLog.Status.BLOCKED,
        error_code="templates_unapproved_window_closed",
        error_message="Waiting for template approval",
    )

    resp = c.get(status_url)
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Dayleey Server Status" in html
    assert "templates_unapproved_window_closed" in html
    assert "Monthly Usage" in html
