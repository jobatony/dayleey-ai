"""End-to-end flow and time-travel scheduler tests covering onboarding, planning, partner pairing, check-ins, reports, STOP, and DELETE MY DATA."""
import re
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from freezegun import freeze_time

from apps.accounts.models import Person
from apps.engine.router import InboundMessage, handle_inbound
from apps.engine.scheduler import catchup, tick
from apps.messaging.client import WhatsAppClient, clear_dry_run_outbox, pop_dry_run_outbox
from apps.network.models import Pairing
from apps.quarters.models import Priority
from apps.reports.models import DailyReport

LAGOS = ZoneInfo("Africa/Lagos")


def _send_user(phone: str, text: str = "", button_id: str = "", now: datetime | None = None):
    msg = InboundMessage(
        wa_message_id=f"test_{uuid.uuid4().hex[:12]}",
        phone=phone,
        text=text,
        button_id=button_id,
    )
    handle_inbound(msg, client=WhatsAppClient(dry_run=True), now=now)
    return pop_dry_run_outbox()


@pytest.mark.django_db
def test_full_day_lifecycle_user_and_partner():
    clear_dry_run_outbox()
    user_phone = "+2348011111111"
    partner_phone = "+2348022222222"

    with freeze_time("2026-11-20 20:00:00+01:00"):
        # 1. Onboarding
        out = _send_user(user_phone, "Hi")
        assert "Welcome to *Dayleey*" in out[0]["body"]

        out = _send_user(user_phone, button_id="consent_yes")
        assert "first name" in out[0]["body"].lower()

        out = _send_user(user_phone, "Anthony")
        assert "timezone" in out[0]["body"].lower()

        out = _send_user(user_phone, button_id="tz_lagos")
        assert "6-hour quarters" in out[0]["body"]

        out = _send_user(user_phone, button_id="q_q123")
        assert "accountability partner" in out[0]["body"].lower()

        out = _send_user(user_phone, button_id="partner_invite")
        link_text = out[0]["body"]
        assert "https://wa.me/" in link_text
        token_match = re.search(r"JOIN-([A-Z0-9]+)", link_text)
        assert token_match is not None
        token = token_match.group(1)

        # 2. Self-pairing rejected
        out_self = _send_user(user_phone, f"Hi Dayleey [JOIN-{token}]")
        assert "own accountability partner" in out_self[0]["body"].lower()

        # 3. Partner joins via wa.me prefilled text
        out_p = _send_user(partner_phone, f"Hi Dayleey, I'm joining as Anthony's partner [JOIN-{token}]")
        assert "Accept this invite" in out_p[0]["body"]

        out_acc = _send_user(partner_phone, button_id="p_accept")
        # User gets notified + Partner gets mutual-pair offer
        assert any(m["to"] == user_phone and "accepted" in m["body"].lower() for m in out_acc)
        assert any(m["to"] == partner_phone and "mutual" in m["body"].lower() for m in out_acc)

        # Partner chooses partner-only
        out_po = _send_user(partner_phone, button_id="mutual_no")
        assert "partner-only" in out_po[0]["body"].lower()

        # 4. User plans tomorrow (2026-11-21) with an invalid plan first, then fixes it
        # At 20:00 WAT, all active quarters (Q1-Q3) for today have started -> jumps straight to tomorrow
        out_plan = _send_user(user_phone, "PLAN")
        assert "Q1 (06:00–12:00)" in out_plan[0]["body"]

        # Q1: 1 item; Q2: skip (0 items -> violates Q1+Q2 >= 2); Q3: 2 items
        _send_user(user_phone, "Morning run")
        _send_user(user_phone, button_id="plan_next_q")
        _send_user(user_phone, button_id="plan_skip_q")
        out_val = _send_user(user_phone, "Read book; Prep dinner")
        assert "Q1 + Q2" in out_val[0]["body"]

        # Fix from Q1: enter 2 priorities with semicolon shorthand!
        _send_user(user_phone, button_id="plan_fix")
        _send_user(user_phone, "Morning run; Write Django tests")
        _send_user(user_phone, button_id="plan_skip_q")
        out_confirm = _send_user(user_phone, "Read book; Prep dinner")
        assert "Confirm your plan" in out_confirm[0]["body"]

        out_saved = _send_user(user_phone, button_id="plan_save")
        assert "Plan saved" in out_saved[0]["body"]
        assert Priority.objects.filter(date=date(2026, 11, 21)).count() == 4

    # 5. Next day 06:00 WAT: Q1 start reminder
    with freeze_time("2026-11-21 06:00:00+01:00"):
        res = tick()
        assert res["boundaries"] == 1
        msgs = pop_dry_run_outbox()
        assert len(msgs) == 1
        assert "Morning run" in msgs[0]["body"]

        # Re-running tick & catchup sends zero duplicates
        assert tick()["boundaries"] == 0
        assert catchup()["boundaries"] == 0
        assert pop_dry_run_outbox() == []

    # 6. 12:00 WAT: Q1 (2 items) ends -> Smart 3-button summary!
    with freeze_time("2026-11-21 12:00:00+01:00"):
        res = tick()
        assert res["boundaries"] == 1
        msgs = pop_dry_run_outbox()
        assert msgs[0]["buttons"] == [
            ("chk_all_done", "All Done"),
            ("chk_review_each", "Review Each"),
            ("chk_missed_both", "Missed Both"),
        ]

        # Tap Review Each -> Item 1 Done, Item 2 Missed (Time)
        out_r = _send_user(user_phone, button_id="chk_review_each")
        assert "Morning run" in out_r[0]["body"]

        out_i2 = _send_user(user_phone, button_id="item_done")
        assert "Write Django tests" in out_i2[0]["body"]

        out_reason = _send_user(user_phone, button_id="item_missed")
        assert "What got in the way" in out_reason[0]["body"]

        out_done = _send_user(user_phone, button_id="reason_time")
        assert "Check-in logged" in out_done[0]["body"]

    # 7. 18:00 WAT: Q3 starts reminder
    with freeze_time("2026-11-21 18:00:00+01:00"):
        res = tick()
        assert res["boundaries"] == 1
        msgs = pop_dry_run_outbox()
        assert "Read book" in msgs[0]["body"]

    # 8. 21:00 WAT: Daily wrap-up lists Q3 items as unconfirmed/planned -> user taps All Done
    with freeze_time("2026-11-21 21:00:00+01:00"):
        res = tick()
        assert res["wrapups"] == 1
        msgs = pop_dry_run_outbox()
        assert "Daily Wrap-up" in msgs[0]["body"]
        assert msgs[0]["buttons"][0][0] == "wrap_all_done"

        _send_user(user_phone, button_id="wrap_all_done")

    # 9. 21:30 WAT: Partner daily report fires!
    with freeze_time("2026-11-21 21:30:00+01:00"):
        res = tick()
        assert res["partner_reports"] == 1
        msgs = pop_dry_run_outbox()
        assert len(msgs) == 1
        assert msgs[0]["to"] == partner_phone
        # Partner's 24h window is closed (>24h since 20:00 prev day!), so falls back to utility template!
        assert msgs[0]["kind"] == "template"
        assert msgs[0]["template_name"] == "partner_daily_report"

        # Partner replies with cheer button -> forwarded to user!
        report = DailyReport.objects.get(date=date(2026, 11, 21))
        out_cheer = _send_user(partner_phone, button_id=f"rep_cheer:{report.id}")
        report.refresh_from_db()
        assert report.partner_reply == DailyReport.PartnerReply.ACK
        assert any(m["to"] == user_phone and "cheer" in m["body"].lower() for m in out_cheer)


@pytest.mark.django_db
def test_unanswered_checkin_nudge_and_unconfirmed_not_missed():
    clear_dry_run_outbox()
    user = Person.objects.create(
        phone="+2348033333333",
        name="Kemi",
        timezone="Africa/Lagos",
        opted_in=True,
        window_expires_at=datetime(2026, 11, 22, 12, 0, tzinfo=LAGOS),
    )
    p = Priority.objects.create(
        owner=user, date=date(2026, 11, 21), quarter=1, position=1, title="Study SQL"
    )

    with freeze_time("2026-11-21 12:00:00+01:00"):
        assert tick()["boundaries"] == 1
        pop_dry_run_outbox()

    # 35 minutes later -> 1 gentle nudge
    with freeze_time("2026-11-21 12:35:00+01:00"):
        res = tick()
        assert res["nudges"] == 1
        assert res["unconfirmed"] == 0
        assert len(pop_dry_run_outbox()) == 1

    # 2 hours 5 minutes later -> marked UNCONFIRMED (NOT missed!)
    with freeze_time("2026-11-21 14:05:00+01:00"):
        res = tick()
        assert res["unconfirmed"] == 1
        p.refresh_from_db()
        assert p.status == Priority.Status.UNCONFIRMED


@pytest.mark.django_db
def test_cross_timezone_partner_quiet_hours_defers_to_8am():
    clear_dry_run_outbox()
    user = Person.objects.create(
        phone="+2348044444444",
        name="Yemi",
        timezone="Africa/Lagos",  # UTC+1
        opted_in=True,
        window_expires_at=datetime(2026, 11, 23, 0, 0, tzinfo=LAGOS),
    )
    # Partner in Tokyo (UTC+9): when it's 21:30 Lagos, it's 05:30 Tokyo (in quiet hours 22-06!)
    partner = Person.objects.create(
        phone="+819011112222",
        name="Kenji",
        timezone="Asia/Tokyo",
        role=Person.Role.PARTNER,
        opted_in=True,
        window_expires_at=datetime(2026, 11, 23, 0, 0, tzinfo=LAGOS),
    )
    Pairing.objects.create(user=user, partner=partner, status=Pairing.Status.JOINED, mode=Pairing.Mode.PARTNER)
    Priority.objects.create(
        owner=user, date=date(2026, 11, 21), quarter=1, position=1, title="Ship MVP", status=Priority.Status.DONE
    )

    # At 21:30 Lagos (05:30 Tokyo), report is deferred (NOT sent during Tokyo quiet hours)
    with freeze_time("2026-11-21 21:30:00+01:00"):
        res = tick()
        assert res["partner_reports"] == 0
        assert pop_dry_run_outbox() == []
        report = DailyReport.objects.get(user=user, date=date(2026, 11, 21))
        assert report.sent_at is None

    # At 08:00 Tokyo (2026-11-22 08:00+09:00), delayed report is delivered!
    with freeze_time("2026-11-22 08:00:00+09:00"):
        res = tick()
        assert res["delayed_reports"] == 1
        msgs = pop_dry_run_outbox()
        assert len(msgs) == 1 and msgs[0]["to"] == partner.phone
        report.refresh_from_db()
        assert report.sent_at is not None


@pytest.mark.django_db
def test_self_report_fallback_partner_stop_and_delete_my_data():
    clear_dry_run_outbox()
    user = Person.objects.create(
        phone="+2348055555555",
        name="Sola",
        timezone="Africa/Lagos",
        opted_in=True,
        window_expires_at=datetime(2026, 11, 25, 12, 0, tzinfo=LAGOS),
    )
    pairing = Pairing.objects.create(user=user, status=Pairing.Status.INVITED, mode=Pairing.Mode.PARTNER)
    Pairing.objects.filter(id=pairing.id).update(invited_at=datetime(2026, 11, 17, 10, 0, tzinfo=LAGOS))

    # 3+ days later -> auto-switched to self-report
    with freeze_time("2026-11-21 10:00:00+01:00"):
        res = tick()
        assert res["fallbacks"] == 1
        pairing.refresh_from_db()
        assert pairing.mode == Pairing.Mode.SELF_REPORT
        msgs = pop_dry_run_outbox()
        assert "self-report mode" in msgs[0]["body"].lower()

    # Partner STOP removes pairing and informs user
    partner = Person.objects.create(
        phone="+2348066666666", name="Femi", role=Person.Role.PARTNER, opted_in=True
    )
    pairing.partner = partner
    pairing.status = Pairing.Status.JOINED
    pairing.mode = Pairing.Mode.PARTNER
    pairing.save()

    with freeze_time("2026-11-21 11:00:00+01:00"):
        out = _send_user(partner.phone, "STOP")
        assert any(m["to"] == partner.phone and "opted out" in m["body"].lower() for m in out)
        assert any(m["to"] == user.phone and "opted out" in m["body"].lower() for m in out)
        partner.refresh_from_db()
        pairing.refresh_from_db()
        assert partner.opted_out is True
        assert pairing.mode == Pairing.Mode.SELF_REPORT

        # User DELETE MY DATA erases personal data
        Priority.objects.create(owner=user, date=date(2026, 11, 21), quarter=1, position=1, title="Secret task")
        out_req = _send_user(user.phone, "DELETE MY DATA")
        assert "Delete My Data" in out_req[0]["body"]
        out_del = _send_user(user.phone, button_id="del_yes")
        assert "permanently erased" in out_del[0]["body"].lower()
        user.refresh_from_db()
        assert user.name == ""
        assert user.opted_out is True
        assert Priority.objects.filter(owner=user).count() == 0
