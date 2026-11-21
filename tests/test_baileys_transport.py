"""Unit & integration tests for the swappable Baileys sidecar transport."""
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Person
from apps.messaging.client import WhatsAppClient, render_numbered_menu
from apps.messaging.models import MessageLog


def test_render_numbered_menu_for_buttons_and_list():
    text, menu = render_numbered_menu(
        kind="buttons",
        body="How did Q1 go?",
        buttons=[("chk_all_done", "All Done"), ("chk_review_each", "Review Each")],
    )
    assert "1. All Done" in text
    assert "2. Review Each" in text
    assert menu == [
        {"id": "chk_all_done", "label": "All Done"},
        {"id": "chk_review_each", "label": "Review Each"},
    ]

    list_text, list_menu = render_numbered_menu(
        kind="list",
        body="Pick timezone:",
        list_rows=[("tz_lagos", "Africa/Lagos", "WAT"), ("tz_london", "Europe/London", "UK")],
    )
    assert "1. Africa/Lagos — WAT" in list_text
    assert list_menu[1]["id"] == "tz_london"


@pytest.mark.django_db
def test_bridge_inbound_auth_allowlist_and_lid_resolution(settings):
    settings.WA_TRANSPORT = "baileys"
    settings.BRIDGE_SECRET = "bridge-sec"
    settings.BAILEYS_ALLOWLIST = {"2348011111111"}
    c = Client()

    # 1. Missing/bad secret -> 401
    resp = c.post(
        "/bridge/inbound/",
        data=json.dumps({"id": "m1", "jid": "12345@lid", "phone": "2348011111111", "text": "hi"}),
        content_type="application/json",
        HTTP_X_BRIDGE_SECRET="wrong",
    )
    assert resp.status_code == 401

    # 2. Non-allowlisted number -> 403
    resp = c.post(
        "/bridge/inbound/",
        data=json.dumps({"id": "m2", "jid": "999@s.whatsapp.net", "phone": "2348099999999", "text": "hi"}),
        content_type="application/json",
        HTTP_X_BRIDGE_SECRET="bridge-sec",
    )
    assert resp.status_code == 403

    # 2b. Local Nigerian 0... number (08011111111) normalizes to 2348011111111 and matches allowlist!
    mock_resp = MagicMock(status_code=200, text='{"queued":true}')
    with patch("apps.messaging.client.httpx.Client.post", return_value=mock_resp) as mock_post:
        resp_local = c.post(
            "/bridge/inbound/",
            data=json.dumps(
                {"id": "m_local", "jid": "2348011111111@s.whatsapp.net", "phone": "08011111111", "text": "hi"}
            ),
            content_type="application/json",
            HTTP_X_BRIDGE_SECRET="bridge-sec",
        )
        assert resp_local.status_code == 200
        Person.objects.filter(phone="+2348011111111").delete()

    # 3. Allowlisted @lid sender with resolved phone -> 200, stores wa_jid, sends numbered menu to bridge
    with patch("apps.messaging.client.httpx.Client.post", return_value=mock_resp) as mock_post:
        resp = c.post(
            "/bridge/inbound/",
            data=json.dumps(
                {"id": "m3", "jid": "987654321@lid", "phone": "2348011111111", "text": "hi"}
            ),
            content_type="application/json",
            HTTP_X_BRIDGE_SECRET="bridge-sec",
        )
        assert resp.status_code == 200
        assert mock_post.called
        sent_json = mock_post.call_args.kwargs["json"]
        assert sent_json["jid"] == "987654321@lid"
        assert "1. I Agree" in sent_json["text"]

        person = Person.objects.get(phone="+2348011111111")
        assert person.wa_jid == "987654321@lid"
        assert person.state == "ASK_CONSENT"
        assert person.state_data["_last_menu"][0]["id"] == "consent_yes"

        # 4. Replying "1" resolves via _last_menu -> consent_yes -> ASK_NAME
        resp2 = c.post(
            "/bridge/inbound/",
            data=json.dumps(
                {"id": "m4", "jid": "987654321@lid", "phone": "2348011111111", "text": "1"}
            ),
            content_type="application/json",
            HTTP_X_BRIDGE_SECRET="bridge-sec",
        )
        assert resp2.status_code == 200
        person.refresh_from_db()
        assert person.state == "ASK_NAME"
        assert person.opted_in is True


@pytest.mark.django_db
def test_baileys_bypasses_24h_window_but_honors_pause_all_and_stop(settings):
    settings.WA_TRANSPORT = "baileys"
    settings.BRIDGE_SECRET = "bridge-sec"
    settings.BAILEYS_ALLOWLIST = {"2348011111111"}
    settings.PAUSE_ALL = False

    person = Person.objects.create(
        phone="+2348011111111",
        wa_jid="2348011111111@s.whatsapp.net",
        name="Anthony",
        opted_in=True,
        window_expires_at=timezone.now() - timedelta(days=2),  # 24h window closed!
    )
    client = WhatsAppClient(transport="baileys")

    mock_resp = MagicMock(status_code=200, text='{"queued":true}')
    with patch("apps.messaging.client.httpx.Client.post", return_value=mock_resp):
        # Closed 24h window still succeeds in Baileys mode (no template block)
        log_ok = client.send_text(person, "Morning Q1 reminder!", allow_quiet_hours=True)
        assert log_ok.status == MessageLog.Status.SENT
        assert log_ok.billable is False

        # PAUSE_ALL kill-switch blocks immediately
        settings.PAUSE_ALL = True
        log_paused = client.send_text(person, "Should not send", allow_quiet_hours=True)
        assert log_paused.status == MessageLog.Status.BLOCKED
        assert log_paused.error_code == "pause_all"

        # Opted out still blocks
        settings.PAUSE_ALL = False
        person.opted_out = True
        person.save()
        log_stopped = client.send_text(person, "Should not send", allow_quiet_hours=True)
        assert log_stopped.status == MessageLog.Status.BLOCKED
        assert log_stopped.error_code == "opted_out"
