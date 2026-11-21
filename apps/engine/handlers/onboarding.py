"""Onboarding flow: consent -> name -> timezone -> active quarters -> partner choice."""
from __future__ import annotations

from zoneinfo import ZoneInfo

from django.conf import settings

from apps.accounts.models import Person
from apps.engine import states
from apps.messaging.client import WhatsAppClient
from apps.network.services import build_wa_me_link, create_or_refresh_invite, enable_self_report
from apps.quarters.services import ensure_quarter_configs, set_active_quarters

TZ_QUICK_MAP = {
    "tz_lagos": "Africa/Lagos",
    "africa/lagos": "Africa/Lagos",
    "lagos": "Africa/Lagos",
    "nigeria": "Africa/Lagos",
    "wat": "Africa/Lagos",
    "tz_london": "Europe/London",
    "europe/london": "Europe/London",
    "london": "Europe/London",
    "uk": "Europe/London",
    "tz_ny": "America/New_York",
    "america/new_york": "America/New_York",
    "new york": "America/New_York",
    "est": "America/New_York",
}


def start_onboarding(person: Person, client: WhatsAppClient) -> None:
    bot = settings.BOT_DISPLAY_NAME
    person.set_state(states.ASK_CONSENT, {})
    client.send_buttons(
        person,
        (
            f"Welcome to *{bot}* — your Quarter-System accountability assistant!\n\n"
            "How it works:\n"
            "• Every day has 4 quarters (Q1–Q4). You set up to 2 priorities per quarter.\n"
            "• I check in at quarter boundaries and send one daily summary to your accountability partner.\n\n"
            "By continuing, you agree to receive WhatsApp check-ins and share your daily priorities "
            "with the partner you choose. Reply STOP anytime to opt out."
        ),
        [("consent_yes", "I Agree"), ("consent_no", "Not Now")],
        bypass_opt_in=True,
        allow_quiet_hours=True,
    )


def handle_consent(person: Person, choice: str, client: WhatsAppClient) -> None:
    norm = choice.strip().lower()
    if norm in {"consent_yes", "i agree", "yes", "agree", "ok"}:
        person.opted_in = True
        person.opted_out = False
        person.consent_partner_sharing = True
        if person.role == Person.Role.PARTNER:
            person.role = Person.Role.BOTH
        else:
            person.role = Person.Role.USER
        person.save(
            update_fields=["opted_in", "opted_out", "consent_partner_sharing", "role", "updated_at"]
        )
        ensure_quarter_configs(person)
        if person.name:
            _ask_timezone(person, client)
        else:
            person.set_state(states.ASK_NAME, {})
            client.send_text(
                person,
                "Great! What first name should I call you (and show to your accountability partner)?",
                allow_quiet_hours=True,
            )
    elif norm in {"consent_no", "not now", "no"}:
        person.set_state(states.NEW, {})
        client.send_text(
            person,
            "No problem! Message me anytime if you want to try Dayleey later.",
            bypass_opt_in=True,
            allow_quiet_hours=True,
        )
    else:
        client.send_buttons(
            person,
            "Please tap *I Agree* to get started, or *Not Now* to cancel.",
            [("consent_yes", "I Agree"), ("consent_no", "Not Now")],
            bypass_opt_in=True,
            allow_quiet_hours=True,
        )


def handle_name(person: Person, text: str, client: WhatsAppClient) -> None:
    clean = text.strip()
    if len(clean) < 1 or len(clean) > 80:
        client.send_text(person, "Please enter a valid name (1–80 characters).", allow_quiet_hours=True)
        return
    person.name = clean
    person.save(update_fields=["name", "updated_at"])
    _ask_timezone(person, client)


def _ask_timezone(person: Person, client: WhatsAppClient) -> None:
    person.set_state(states.ASK_TZ, {})
    client.send_list(
        person,
        f"Nice to meet you, *{person.display_name}*! Select your timezone so quarter reminders arrive on time:",
        button_text="Select Timezone",
        rows=[
            ("tz_lagos", "Africa/Lagos (WAT)", "Nigeria / West Africa (UTC+1)"),
            ("tz_london", "Europe/London", "UK (GMT/BST)"),
            ("tz_ny", "America/New_York", "US Eastern (EST/EDT)"),
            ("tz_other", "Other timezone", "Type your IANA timezone e.g. Africa/Nairobi"),
        ],
        allow_quiet_hours=True,
    )


def handle_timezone(person: Person, text: str, client: WhatsAppClient) -> None:
    norm = text.strip().lower()
    if norm in {"tz_other", "other", "other timezone"}:
        person.set_state(states.ASK_TZ_CUSTOM, {})
        client.send_text(
            person,
            "Type your timezone in Area/City format (e.g. `Africa/Nairobi`, `Africa/Accra`, `Europe/Berlin`, `Asia/Dubai`):",
            allow_quiet_hours=True,
        )
        return

    tz_name = TZ_QUICK_MAP.get(norm)
    if not tz_name:
        # Maybe the user typed a valid IANA timezone directly
        tz_name = _validate_iana(text.strip())
    if not tz_name:
        client.send_text(
            person,
            "I didn't recognize that timezone. Pick from the list or type e.g. `Africa/Lagos` or `Europe/London`.",
            allow_quiet_hours=True,
        )
        return

    person.timezone = tz_name
    person.save(update_fields=["timezone", "updated_at"])
    _ask_quarters(person, client)


def handle_timezone_custom(person: Person, text: str, client: WhatsAppClient) -> None:
    tz_name = TZ_QUICK_MAP.get(text.strip().lower()) or _validate_iana(text.strip())
    if not tz_name:
        client.send_text(
            person,
            "Couldn't find that timezone. Example valid values: `Africa/Lagos`, `Africa/Accra`, `America/Chicago`, `UTC`.",
            allow_quiet_hours=True,
        )
        return
    person.timezone = tz_name
    person.save(update_fields=["timezone", "updated_at"])
    _ask_quarters(person, client)


def _validate_iana(candidate: str) -> str | None:
    if not candidate:
        return None
    try:
        ZoneInfo(candidate)
        return candidate
    except Exception:
        return None


def _ask_quarters(person: Person, client: WhatsAppClient) -> None:
    person.set_state(states.ASK_QUARTERS, {})
    client.send_buttons(
        person,
        (
            f"Timezone set to *{person.timezone}*.\n\n"
            "Your day is split into 6-hour quarters:\n"
            "• *Q1* 06:00–12:00\n"
            "• *Q2* 12:00–18:00\n"
            "• *Q3* 18:00–24:00\n"
            "• *Q4* 00:00–06:00 (usually sleep — OFF by default)\n\n"
            "Which quarters do you want active?"
        ),
        [("q_q123", "Q1–Q3 (Recommended)"), ("q_all4", "All 4 (incl. Q4)")],
        allow_quiet_hours=True,
    )


def handle_quarters(person: Person, choice: str, client: WhatsAppClient) -> None:
    norm = choice.strip().lower()
    if norm in {"q_q123", "q1-q3", "q1–q3", "q1-q3 (recommended)", "1", "default"}:
        set_active_quarters(person, {1, 2, 3})
    elif norm in {"q_all4", "all 4", "all 4 (incl. q4)", "all", "2"}:
        set_active_quarters(person, {1, 2, 3, 4})
    else:
        client.send_buttons(
            person,
            "Please pick *Q1–Q3 (Recommended)* or *All 4 (incl. Q4)*.",
            [("q_q123", "Q1–Q3 (Recommended)"), ("q_all4", "All 4 (incl. Q4)")],
            allow_quiet_hours=True,
        )
        return

    _ask_partner(person, client)


def _ask_partner(person: Person, client: WhatsAppClient) -> None:
    person.set_state(states.ASK_PARTNER, {})
    client.send_buttons(
        person,
        (
            "Now for the accountability boost!\n"
            "You can invite *1 accountability partner* (free for them). They get *one daily summary* "
            "of your day and can cheer or nudge you.\n\n"
            "To respect WhatsApp rules, *they* opt in by tapping your invite link and sending the first message."
        ),
        [("partner_invite", "Get Invite Link"), ("partner_skip", "Skip (Self-report)")],
        allow_quiet_hours=True,
    )


def handle_partner_choice(person: Person, choice: str, client: WhatsAppClient) -> None:
    norm = choice.strip().lower()
    if norm in {"partner_invite", "get invite link", "invite", "yes"}:
        pairing = create_or_refresh_invite(person)
        link = build_wa_me_link(person, pairing.invite_token)
        person.clear_state()
        client.send_text(
            person,
            (
                "Forward this link to your accountability partner on WhatsApp:\n\n"
                f"{link}\n\n"
                f"Once they tap it and send the message, they'll be linked to you. "
                f"(If no one joins within {settings.PARTNER_FALLBACK_DAYS} days, we'll switch you to self-report mode automatically.)\n\n"
                "You're all set! Reply *PLAN* whenever you're ready to set your priorities."
            ),
            allow_quiet_hours=True,
        )
    elif norm in {"partner_skip", "skip", "skip (self-report)", "self-report"}:
        enable_self_report(person)
        person.clear_state()
        client.send_text(
            person,
            (
                "Got it — you're in *self-report mode* for now. You can invite a partner anytime by replying *PARTNER*.\n\n"
                "You're all set! Reply *PLAN* to set your priorities for today or tomorrow."
            ),
            allow_quiet_hours=True,
        )
    else:
        client.send_buttons(
            person,
            "Choose *Get Invite Link* to pair with a friend, or *Skip (Self-report)* to start solo:",
            [("partner_invite", "Get Invite Link"), ("partner_skip", "Skip (Self-report)")],
            allow_quiet_hours=True,
        )
