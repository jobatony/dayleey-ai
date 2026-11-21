"""Inbound WhatsApp message parser, deduplicator, and state-machine router."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import Person
from apps.engine import states
from apps.engine.handlers import checkin, commands, onboarding, partner, plan
from apps.messaging.client import WhatsAppClient
from apps.messaging.models import MessageLog
from apps.network.services import extract_join_token

logger = logging.getLogger(__name__)


@dataclass
class InboundMessage:
    wa_message_id: str
    phone: str
    text: str
    button_id: str = ""
    wa_jid: str = ""
    profile_name: str = ""
    raw: dict | None = None

    @property
    def choice(self) -> str:
        return self.button_id or self.text


def normalize_phone_digits(raw: str) -> str:
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    # Convert 11-digit Nigerian local mobile numbers (070/080/081/090/091...) to international 234...
    if len(digits) == 11 and digits[0] == "0" and digits[1] in "789" and digits[2] in "01":
        return "234" + digits[1:]
    return digits


def normalize_phone(raw: str) -> str:
    digits = normalize_phone_digits(raw)
    return f"+{digits}" if digits else ""


def parse_webhook_messages(payload: dict) -> list[InboundMessage]:
    results: list[InboundMessage] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            contacts = {
                c.get("wa_id"): (c.get("profile") or {}).get("name", "")
                for c in value.get("contacts", [])
            }
            for msg in value.get("messages", []):
                wa_id = msg.get("id", "")
                from_raw = msg.get("from", "")
                phone = normalize_phone(from_raw)
                if not wa_id or not phone:
                    continue

                msg_type = msg.get("type", "")
                text = ""
                button_id = ""
                if msg_type == "text":
                    text = (msg.get("text") or {}).get("body", "")
                elif msg_type == "button":
                    btn = msg.get("button") or {}
                    text = btn.get("text", "")
                    button_id = btn.get("payload", "")
                elif msg_type == "interactive":
                    inter = msg.get("interactive") or {}
                    itype = inter.get("type")
                    if itype == "button_reply":
                        br = inter.get("button_reply") or {}
                        button_id = br.get("id", "")
                        text = br.get("title", "")
                    elif itype == "list_reply":
                        lr = inter.get("list_reply") or {}
                        button_id = lr.get("id", "")
                        text = lr.get("title", "")

                results.append(
                    InboundMessage(
                        wa_message_id=wa_id,
                        phone=phone,
                        text=text.strip(),
                        button_id=button_id.strip(),
                        profile_name=contacts.get(from_raw, ""),
                        raw=msg,
                    )
                )
    return results


def handle_inbound(
    msg: InboundMessage, *, client: WhatsAppClient | None = None, now: datetime | None = None
) -> bool:
    """Process one inbound WhatsApp message. Returns False if duplicate/ignored."""
    ref = now or timezone.now()
    wa = client or WhatsAppClient()

    # 1. Idempotency check by wa_message_id
    if MessageLog.objects.filter(wa_message_id=msg.wa_message_id).exists():
        logger.info("Ignoring duplicate inbound message %s", msg.wa_message_id)
        return False

    person = None
    created = False
    if msg.wa_jid:
        person = Person.objects.filter(wa_jid=msg.wa_jid).first()
    if person is None:
        person, created = Person.objects.get_or_create(
            phone=msg.phone,
            defaults={
                "name": "",
                "wa_jid": msg.wa_jid,
                "timezone": settings.DEFAULT_TIMEZONE,
                "is_allowlisted": not settings.BETA_ALLOWLIST_ENABLED,
            },
        )
    if msg.wa_jid and person.wa_jid != msg.wa_jid:
        person.wa_jid = msg.wa_jid
        person.save(update_fields=["wa_jid", "updated_at"])

    try:
        with transaction.atomic():
            MessageLog.objects.create(
                person=person,
                phone=person.phone,
                direction=MessageLog.Direction.INBOUND,
                wa_message_id=msg.wa_message_id,
                msg_type=MessageLog.MsgType.TEXT,
                payload=msg.raw or {"text": msg.text, "button_id": msg.button_id},
                status=MessageLog.Status.RECEIVED,
            )
    except IntegrityError:
        return False

    person.touch_inbound(ref)

    # Resolve numbered menu replies (e.g. "1", "2", "3") from Baileys text menus
    if not msg.button_id and msg.text.strip().isdigit():
        last_menu = (person.state_data or {}).get("_last_menu") or []
        idx = int(msg.text.strip()) - 1
        if 0 <= idx < len(last_menu):
            selected = last_menu[idx]
            msg.button_id = str(selected.get("id") or "")
            msg.text = str(selected.get("label") or msg.text)

    choice = msg.choice
    upper_cmd = msg.text.strip().upper()

    # 2. Highest-priority compliance commands
    if upper_cmd == "STOP":
        commands.handle_stop(person, wa)
        return True
    if upper_cmd == "DELETE MY DATA":
        commands.handle_delete_request(person, wa)
        return True
    if person.state == states.DELETE_CONFIRM:
        commands.handle_delete_confirm(person, choice, wa)
        return True

    # 3. Partner invite join token (works even for non-allowlisted people joining an active user)
    token = extract_join_token(msg.text)
    if token:
        partner.handle_join_token(person, token, wa)
        return True

    # 4. Closed beta allowlist check for non-partner newcomers
    if settings.BETA_ALLOWLIST_ENABLED and not person.is_allowlisted and person.role == Person.Role.USER:
        wa.send_text(
            person,
            f"Thanks for messaging *{settings.BOT_DISPLAY_NAME}*! We're currently in a closed beta. "
            "Ask your invite contact to allowlist your number.",
            bypass_opt_in=True,
            allow_quiet_hours=True,
            now=ref,
        )
        return True

    # 5. Resume from opt-out / pause
    if upper_cmd == "RESUME":
        commands.handle_resume(person, wa)
        return True

    if person.opted_out:
        wa.send_text(
            person,
            "You're currently opted out of Dayleey. Reply *RESUME* anytime to rejoin.",
            bypass_opt_in=True,
            allow_quiet_hours=True,
            now=ref,
        )
        return True

    # 6. Partner pending acceptance / mutual offer
    if person.state == states.PARTNER_PENDING_ACCEPT:
        partner.handle_partner_accept_response(person, choice, wa)
        return True
    if person.state == states.PARTNER_OFFER_MUTUAL:
        partner.handle_mutual_offer(person, choice, wa)
        return True

    # 7. Partner daily report reply buttons (can arrive anytime while partner is IDLE)
    if partner.handle_partner_report_reply(person, choice, wa):
        return True

    # 8. Onboarding states
    if person.state in {states.NEW, states.DELETED}:
        if created and msg.profile_name and not person.name:
            # Keep asking for explicit name during onboarding so user controls what partner sees
            pass
        onboarding.start_onboarding(person, wa)
        return True
    if person.state == states.ASK_CONSENT:
        onboarding.handle_consent(person, choice, wa)
        return True
    if person.state == states.ASK_NAME:
        onboarding.handle_name(person, msg.text, wa)
        return True
    if person.state == states.ASK_TZ:
        onboarding.handle_timezone(person, choice, wa)
        return True
    if person.state == states.ASK_TZ_CUSTOM:
        onboarding.handle_timezone_custom(person, msg.text, wa)
        return True
    if person.state == states.ASK_QUARTERS:
        onboarding.handle_quarters(person, choice, wa)
        return True
    if person.state == states.ASK_PARTNER:
        onboarding.handle_partner_choice(person, choice, wa)
        return True

    # 9. Global user commands (accessible from IDLE or interrupting active flows)
    if upper_cmd == "HELP":
        commands.handle_help(person, wa)
        return True
    if upper_cmd == "PAUSE":
        commands.handle_pause(person, wa)
        return True
    if upper_cmd == "PLAN":
        plan.start_plan_flow(person, wa, now=ref)
        return True
    if upper_cmd == "TODAY":
        plan.handle_today_command(person, wa, now=ref)
        return True
    if upper_cmd == "PARTNER":
        partner.handle_partner_command(person, wa)
        return True
    if upper_cmd == "STATS":
        commands.handle_stats(person, wa, now=ref)
        return True

    # 10. Active conversational sub-flows
    if person.state == states.PLAN_CHOOSE_DAY:
        plan.handle_choose_day(person, choice, wa, now=ref)
        return True
    if person.state == states.PLAN_QUARTER_INPUT:
        plan.handle_quarter_input(person, choice, wa)
        return True
    if person.state == states.PLAN_QUARTER_NEXT:
        plan.handle_quarter_next(person, choice, wa)
        return True
    if person.state == states.PLAN_CONFIRM:
        plan.handle_plan_confirm(person, choice, wa)
        return True
    if person.state == states.CHECKIN_SUMMARY:
        checkin.handle_checkin_summary(person, choice, wa)
        return True
    if person.state == states.CHECKIN_ITEM:
        checkin.handle_checkin_item(person, choice, wa)
        return True
    if person.state == states.CHECKIN_REASON:
        checkin.handle_checkin_reason(person, choice, wa)
        return True
    if person.state == states.WRAPUP_CONFIRM:
        checkin.handle_wrapup_confirm(person, choice, wa)
        return True

    # 11. Partner-only person messaging in IDLE
    if person.role == Person.Role.PARTNER:
        person.set_state(states.PARTNER_OFFER_MUTUAL, {})
        wa.send_buttons(
            person,
            (
                "You're currently set up as an accountability partner only. "
                "Want to start tracking your own daily quarters too?"
            ),
            [("mutual_yes", "Yes, track mine"), ("mutual_no", "Stay partner only")],
            allow_quiet_hours=True,
            now=ref,
        )
        return True

    # 12. Fallback in IDLE
    wa.send_text(
        person,
        "I didn't recognize that command. Reply *PLAN*, *TODAY*, *PARTNER*, *STATS*, or *HELP*.",
        allow_quiet_hours=True,
        now=ref,
    )
    return True
