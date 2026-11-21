"""Global commands: HELP, STATS, PAUSE, RESUME, STOP, DELETE MY DATA."""
from __future__ import annotations

from apps.accounts.models import Person
from apps.engine import states
from apps.messaging.client import WhatsAppClient
from apps.network.services import remove_pairings_for_partner
from apps.quarters.services import compute_user_stats


def handle_help(person: Person, client: WhatsAppClient) -> None:
    client.send_text(
        person,
        (
            "*Dayleey Commands:*\n"
            "• *PLAN* — Set priorities for today or tomorrow\n"
            "• *TODAY* — View today's priorities & progress\n"
            "• *PARTNER* — Manage your accountability partner\n"
            "• *STATS* — 7-day completion rate & streak\n"
            "• *PAUSE* / *RESUME* — Pause or resume reminders\n"
            "• *STOP* — Opt out of all messages\n"
            "• *DELETE MY DATA* — Permanently erase your data"
        ),
        allow_quiet_hours=True,
    )


def handle_stats(person: Person, client: WhatsAppClient, now=None) -> None:
    today = person.local_now(now).date()
    s = compute_user_stats(person, today=today, days=7)
    client.send_text(
        person,
        (
            f"*Your 7-Day Stats ({person.display_name})*\n"
            f"✅ Done: {s['done']}/{s['total']} ({s['completion_pct']}%)\n"
            f"🟡 Partial: {s['partial']} | ❌ Missed: {s['missed']} | ❓ Unconfirmed: {s['unconfirmed']}\n"
            f"🔥 Current streak: {s['streak_days']} day(s)"
        ),
        allow_quiet_hours=True,
    )


def handle_pause(person: Person, client: WhatsAppClient) -> None:
    person.paused = True
    person.clear_state()
    person.save(update_fields=["paused", "updated_at"])
    client.send_text(
        person,
        "⏸️ Reminders paused. Reply *RESUME* anytime when you're ready to pick back up.",
        allow_quiet_hours=True,
    )


def handle_resume(person: Person, client: WhatsAppClient) -> None:
    person.paused = False
    person.opted_out = False
    person.opted_in = True
    person.clear_state()
    person.save(update_fields=["paused", "opted_out", "opted_in", "updated_at"])
    client.send_text(
        person,
        "▶️ Welcome back! Your quarter reminders are active again. Reply *PLAN* to set your priorities.",
        allow_quiet_hours=True,
    )


def handle_stop(person: Person, client: WhatsAppClient) -> None:
    affected_users = remove_pairings_for_partner(person)

    # Send final opt-out confirmation before setting opted_out=True
    client.send_text(
        person,
        "You've been opted out of Dayleey and won't receive further messages. Reply *RESUME* anytime to rejoin.",
        bypass_opt_in=True,
        allow_quiet_hours=True,
    )

    person.opted_out = True
    person.opted_in = False
    person.paused = True
    person.clear_state()
    person.save(update_fields=["opted_out", "opted_in", "paused", "updated_at"])

    # Inform any users whose accountability partner just opted out
    for user in affected_users:
        client.send_text(
            user,
            (
                f"Heads up: your accountability partner ({person.display_name}) opted out. "
                "We've switched you to self-report mode — reply *PARTNER* anytime to invite someone else."
            ),
            allow_quiet_hours=False,
        )


def handle_delete_request(person: Person, client: WhatsAppClient) -> None:
    person.set_state(states.DELETE_CONFIRM, {})
    client.send_buttons(
        person,
        (
            "⚠️ *Delete My Data*\n"
            "This will permanently erase your name, priorities, check-ins, and partner pairings, "
            "and opt you out of Dayleey. Are you sure?"
        ),
        [("del_yes", "Yes, Delete All"), ("del_no", "Cancel")],
        allow_quiet_hours=True,
    )


def handle_delete_confirm(person: Person, choice: str, client: WhatsAppClient) -> None:
    norm = choice.strip().lower()
    if norm in {"del_yes", "yes, delete all", "yes", "delete"}:
        affected_users = remove_pairings_for_partner(person)
        client.send_text(
            person,
            "Your Dayleey data has been permanently erased and you have been opted out.",
            bypass_opt_in=True,
            allow_quiet_hours=True,
        )
        person.anonymize_and_wipe()
        for user in affected_users:
            client.send_text(
                user,
                "Your accountability partner has left Dayleey. We've switched you to self-report mode; reply *PARTNER* to invite someone new.",
                allow_quiet_hours=False,
            )
    else:
        person.clear_state()
        client.send_text(person, "Cancelled — your data was not deleted.", allow_quiet_hours=True)
