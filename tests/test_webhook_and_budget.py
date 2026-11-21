"""Tests for WhatsApp webhook verification, HMAC signatures, dedupe, status webhooks, and budget caps."""
import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Person
from apps.messaging.budget import get_or_create_global_counter, record_delivery
from apps.messaging.client import WhatsAppClient, clear_dry_run_outbox, pop_dry_run_outbox
from apps.messaging.models import MessageLog
from apps.messaging.status import process_status_updates


def _sign(body_bytes: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _sample_text_webhook(phone: str, text: str, wa_id: str) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "contacts": [{"profile": {"name": "Ada"}, "wa_id": phone}],
                            "messages": [
                                {
                                    "from": phone,
                                    "id": wa_id,
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


@pytest.mark.django_db
def test_webhook_verify_challenge(settings):
    settings.WA_VERIFY_TOKEN = "secret-verify"
    c = Client()
    resp = c.get(
        "/webhooks/whatsapp/",
        {"hub.mode": "subscribe", "hub.verify_token": "secret-verify", "hub.challenge": "12345"},
    )
    assert resp.status_code == 200
    assert resp.content.decode() == "12345"

    bad = c.get(
        "/webhooks/whatsapp/",
        {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "12345"},
    )
    assert bad.status_code == 403


@pytest.mark.django_db
def test_webhook_signature_and_deduplication(settings):
    settings.WA_APP_SECRET = "test-secret"
    settings.WA_DRY_RUN = True
    clear_dry_run_outbox()

    c = Client()
    payload = _sample_text_webhook("2348011111111", "Hello", "wamid.DUP1")
    raw = json.dumps(payload).encode("utf-8")

    # Bad signature rejected
    bad = c.post(
        "/webhooks/whatsapp/",
        data=raw,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256="sha256=deadbeef",
    )
    assert bad.status_code == 403

    # Valid signature accepted and triggers onboarding greeting
    ok = c.post(
        "/webhooks/whatsapp/",
        data=raw,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=_sign(raw, "test-secret"),
    )
    assert ok.status_code == 200
    first_out = pop_dry_run_outbox()
    assert len(first_out) == 1
    assert "Welcome to *Dayleey*" in first_out[0]["body"]

    # Replay exact same webhook -> deduped, zero extra outbound messages
    replay = c.post(
        "/webhooks/whatsapp/",
        data=raw,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=_sign(raw, "test-secret"),
    )
    assert replay.status_code == 200
    assert pop_dry_run_outbox() == []


@pytest.mark.django_db
def test_budget_free_service_allowance_and_global_kill_switch(settings):
    settings.FREE_SERVICE_MESSAGES_PER_MONTH = 2
    settings.MONTHLY_BUDGET_USD = Decimal("0.0100")
    settings.WA_DRY_RUN = True
    clear_dry_run_outbox()

    person = Person.objects.create(
        phone="+2348099999999",
        name="Tunde",
        opted_in=True,
        window_expires_at=timezone.now() + timedelta(hours=12),
    )
    client = WhatsAppClient(dry_run=True)

    # First 2 service messages are free (cost $0)
    log1 = client.send_text(person, "msg 1", allow_quiet_hours=True)
    log2 = client.send_text(person, "msg 2", allow_quiet_hours=True)
    assert log1.status == MessageLog.Status.DELIVERED and log1.billable is False
    assert log2.status == MessageLog.Status.DELIVERED and log2.billable is False

    # 3rd service message is billable ($0.0050 <= $0.0100 budget) -> allowed
    log3 = client.send_text(person, "msg 3", allow_quiet_hours=True)
    assert log3.status == MessageLog.Status.DELIVERED and log3.billable is True

    # 4th is utility template ($0.0068 -> $0.0050 + $0.0068 = $0.0118 > $0.0100) -> blocked by kill-switch!
    log4 = client.send_template(person, "user_daily_wrapup", ["Tunde", "2026-11-20", "All done"], allow_quiet_hours=True)
    assert log4.status == MessageLog.Status.BLOCKED
    assert log4.error_code == "global_budget_exceeded"


@pytest.mark.django_db
def test_closed_24h_window_uses_fallback_template_or_blocks():
    clear_dry_run_outbox()
    person = Person.objects.create(
        phone="+2348088888888",
        name="Chidi",
        opted_in=True,
        window_expires_at=timezone.now() - timedelta(hours=1),
    )
    client = WhatsAppClient(dry_run=True)

    blocked = client.send_text(person, "Free form outside window", allow_quiet_hours=True)
    assert blocked.status == MessageLog.Status.BLOCKED
    assert blocked.error_code == "window_closed"

    sent_tpl = client.send_text(
        person,
        "Free form outside window",
        fallback_template=("user_daily_wrapup", ["Chidi", "2026-11-20", "Summary"]),
        allow_quiet_hours=True,
    )
    assert sent_tpl.status == MessageLog.Status.DELIVERED
    assert sent_tpl.msg_type == MessageLog.MsgType.TEMPLATE
    assert sent_tpl.template_name == "user_daily_wrapup"
    assert sent_tpl.billable is True


@pytest.mark.django_db
def test_status_webhook_counts_delivery_idempotently():
    person = Person.objects.create(phone="+2348077777777", name="Bola", opted_in=True)
    log = MessageLog.objects.create(
        person=person,
        phone=person.phone,
        direction=MessageLog.Direction.OUTBOUND,
        wa_message_id="wamid.OUT1",
        msg_type=MessageLog.MsgType.TEMPLATE,
        template_name="partner_daily_report",
        status=MessageLog.Status.SENT,
    )
    status_payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "statuses": [{"id": "wamid.OUT1", "status": "delivered"}]
                        }
                    }
                ]
            }
        ]
    }
    assert process_status_updates(status_payload) == 1
    assert process_status_updates(status_payload) == 1  # Duplicate delivery webhook

    log.refresh_from_db()
    assert log.status == MessageLog.Status.DELIVERED
    counter = get_or_create_global_counter()
    assert counter.delivered_template == 1
