"""Day planning flow with Quarter System rule validation (PLAN & TODAY commands)."""
from __future__ import annotations

from datetime import date, timedelta

from apps.accounts.models import Person
from apps.engine import states
from apps.messaging.client import WhatsAppClient
from apps.quarters.rules import QUARTER_LABELS, QUARTER_ORDER, plannable_quarters, validate_day
from apps.quarters.services import format_day_plan, get_active_quarters, save_day_plan


def start_plan_flow(person: Person, client: WhatsAppClient, now=None) -> None:
    local_now = person.local_now(now)
    today = local_now.date()
    tomorrow = today + timedelta(days=1)
    active = get_active_quarters(person)
    today_plannable = plannable_quarters(local_now, today, active)

    if not today_plannable:
        # All active quarters for today have already started -> jump straight to tomorrow
        _begin_planning_for_date(person, tomorrow, active, active, client)
        return

    person.set_state(
        states.PLAN_CHOOSE_DAY,
        {"today": today.isoformat(), "tomorrow": tomorrow.isoformat()},
    )
    locked_note = ""
    if today_plannable != active:
        open_qs = ", ".join(f"Q{q}" for q in sorted(today_plannable))
        locked_note = f"\n(For today, remaining open quarters: *{open_qs}*)"

    client.send_buttons(
        person,
        f"Which day do you want to plan?{locked_note}",
        [
            ("plan_today", f"Today ({today.strftime('%d %b')})"),
            ("plan_tomorrow", f"Tomorrow ({tomorrow.strftime('%d %b')})"),
        ],
        allow_quiet_hours=True,
    )


def handle_choose_day(person: Person, choice: str, client: WhatsAppClient, now=None) -> None:
    local_now = person.local_now(now)
    today = local_now.date()
    tomorrow = today + timedelta(days=1)
    active = get_active_quarters(person)

    norm = choice.strip().lower()
    if norm in {"plan_today", "today", "1"}:
        editable = plannable_quarters(local_now, today, active)
        if not editable:
            client.send_text(
                person,
                "All of today's active quarters have already started — let's plan for tomorrow instead!",
                allow_quiet_hours=True,
            )
            _begin_planning_for_date(person, tomorrow, active, active, client)
            return
        _begin_planning_for_date(person, today, active, editable, client)
    elif norm in {"plan_tomorrow", "tomorrow", "2"}:
        _begin_planning_for_date(person, tomorrow, active, active, client)
    else:
        client.send_buttons(
            person,
            "Tap *Today* or *Tomorrow* to start planning:",
            [("plan_today", "Today"), ("plan_tomorrow", "Tomorrow")],
            allow_quiet_hours=True,
        )


def _begin_planning_for_date(
    person: Person, target_date: date, active: set[int], editable: set[int], client: WhatsAppClient
) -> None:
    ordered_editable = [q for q in QUARTER_ORDER if q in editable]
    data = {
        "target_date": target_date.isoformat(),
        "active": sorted(active),
        "editable": ordered_editable,
        "cursor": 0,
        "plan": {str(q): [] for q in ordered_editable},
    }
    person.set_state(states.PLAN_QUARTER_INPUT, data)
    _prompt_current_quarter(person, client)


def _prompt_current_quarter(person: Person, client: WhatsAppClient) -> None:
    data = person.state_data
    editable = data["editable"]
    cursor = data["cursor"]
    if cursor >= len(editable):
        _validate_and_show_summary(person, client)
        return

    q = editable[cursor]
    existing = data["plan"].get(str(q), [])
    slot = len(existing) + 1
    if slot == 1:
        client.send_buttons(
            person,
            (
                f"*{QUARTER_LABELS[q]}* ({data['target_date']})\n"
                "Type your *Priority #1* for this quarter (or tap Skip if you want 0 here).\n"
                "Tip: You can also enter 2 priorities separated by a semicolon (`;`)."
            ),
            [("plan_skip_q", "Skip Quarter"), ("plan_cancel", "Cancel")],
            allow_quiet_hours=True,
        )
    else:
        client.send_buttons(
            person,
            f"*{QUARTER_LABELS[q]}* — Priority #1 saved: _{existing[0]}_\n\nType your *Priority #2*, or move to the next quarter:",
            [("plan_next_q", "Next Quarter"), ("plan_cancel", "Cancel")],
            allow_quiet_hours=True,
        )


def handle_quarter_input(person: Person, text: str, client: WhatsAppClient) -> None:
    data = dict(person.state_data)
    norm = text.strip().lower()

    if norm in {"plan_cancel", "cancel"}:
        person.clear_state()
        client.send_text(person, "Planning cancelled. Reply *PLAN* anytime to start again.", allow_quiet_hours=True)
        return

    editable = data["editable"]
    cursor = data["cursor"]
    if cursor >= len(editable):
        _validate_and_show_summary(person, client)
        return

    q = editable[cursor]
    q_key = str(q)
    items = list(data["plan"].get(q_key, []))

    if norm in {"plan_skip_q", "skip quarter", "skip", "plan_next_q", "next quarter", "next", "done"}:
        data["cursor"] = cursor + 1
        person.set_state(states.PLAN_QUARTER_INPUT, data)
        _prompt_current_quarter(person, client)
        return

    # Allow semicolon-separated entry for both priorities in one message (saves messages!)
    new_entries = [part.strip() for part in text.split(";") if part.strip()]
    if not new_entries:
        client.send_text(person, "Please type a short priority title, or tap Skip/Next.", allow_quiet_hours=True)
        return

    for entry in new_entries:
        if len(items) < 2:
            items.append(entry[:160])

    data["plan"][q_key] = items
    if len(items) >= 2:
        data["cursor"] = cursor + 1
        person.set_state(states.PLAN_QUARTER_INPUT, data)
        _prompt_current_quarter(person, client)
    else:
        person.set_state(states.PLAN_QUARTER_NEXT, data)
        client.send_buttons(
            person,
            f"Saved for *Q{q}*: _{items[0]}_\nAdd a 2nd priority for Q{q} (max 2), or move on?",
            [("plan_add_2nd", "Add 2nd"), ("plan_next_q", "Next Quarter")],
            allow_quiet_hours=True,
        )


def handle_quarter_next(person: Person, text: str, client: WhatsAppClient) -> None:
    data = dict(person.state_data)
    norm = text.strip().lower()

    if norm in {"plan_cancel", "cancel"}:
        person.clear_state()
        client.send_text(person, "Planning cancelled.", allow_quiet_hours=True)
        return

    if norm in {"plan_next_q", "next quarter", "next"}:
        data["cursor"] = data["cursor"] + 1
        person.set_state(states.PLAN_QUARTER_INPUT, data)
        _prompt_current_quarter(person, client)
        return

    if norm in {"plan_add_2nd", "add 2nd", "add"}:
        person.set_state(states.PLAN_QUARTER_INPUT, data)
        _prompt_current_quarter(person, client)
        return

    # If the user just typed the 2nd priority directly, accept it!
    q = data["editable"][data["cursor"]]
    items = list(data["plan"].get(str(q), []))
    items.append(text.strip()[:160])
    data["plan"][str(q)] = items[:2]
    data["cursor"] = data["cursor"] + 1
    person.set_state(states.PLAN_QUARTER_INPUT, data)
    _prompt_current_quarter(person, client)


def _validate_and_show_summary(person: Person, client: WhatsAppClient) -> None:
    data = person.state_data
    plan_int = {int(k): v for k, v in data["plan"].items()}
    active = set(data["active"])
    editable = set(data["editable"])

    violations = validate_day(plan_int, active=active, plannable=editable)
    if violations:
        lines = ["⚠️ *Your plan doesn't meet the Quarter System rules yet:*"]
        for v in violations:
            lines.append(f"• {v.message}")
        lines.append("\nLet's adjust it so every pair of consecutive active quarters has at least 2 priorities.")
        # Reset cursor to the first offending quarter so user can fix smoothly
        first_q = violations[0].quarters[0] if violations[0].quarters else data["editable"][0]
        if first_q in data["editable"]:
            data["cursor"] = data["editable"].index(first_q)
            data["plan"][str(first_q)] = []
        else:
            data["cursor"] = 0
        person.set_state(states.PLAN_CONFIRM, {**data, "valid": False})
        client.send_buttons(
            person,
            "\n".join(lines),
            [("plan_fix", f"Fix from Q{first_q}"), ("plan_cancel", "Cancel")],
            allow_quiet_hours=True,
        )
        return

    lines = [f"*Confirm your plan for {data['target_date']}:*"]
    for q in data["editable"]:
        items = plan_int.get(q, [])
        lines.append(f"\n*Q{q}* ({QUARTER_LABELS[q].split(' ')[1]})")
        if items:
            for idx, title in enumerate(items, start=1):
                lines.append(f"  {idx}. {title}")
        else:
            lines.append("  _(rest / no priority)_")

    person.set_state(states.PLAN_CONFIRM, {**data, "valid": True})
    client.send_buttons(
        person,
        "\n".join(lines),
        [("plan_save", "Confirm Plan"), ("plan_restart", "Edit"), ("plan_cancel", "Cancel")],
        allow_quiet_hours=True,
    )


def handle_plan_confirm(person: Person, choice: str, client: WhatsAppClient) -> None:
    data = dict(person.state_data)
    norm = choice.strip().lower()

    if norm in {"plan_cancel", "cancel"}:
        person.clear_state()
        client.send_text(person, "Plan discarded. Reply *PLAN* anytime.", allow_quiet_hours=True)
        return

    if norm in {"plan_fix", "plan_restart", "edit"} or norm.startswith("fix"):
        if norm in {"plan_restart", "edit"}:
            data["cursor"] = 0
            data["plan"] = {str(q): [] for q in data["editable"]}
        person.set_state(states.PLAN_QUARTER_INPUT, data)
        _prompt_current_quarter(person, client)
        return

    if norm in {"plan_save", "confirm plan", "confirm", "yes"} and data.get("valid"):
        target = date.fromisoformat(data["target_date"])
        plan_int = {int(k): v for k, v in data["plan"].items()}
        editable = set(data["editable"])
        save_day_plan(person, target, plan_int, editable)
        person.clear_state()
        client.send_text(
            person,
            f"✅ Plan saved for *{target.strftime('%a, %d %b')}*! I'll check in with you at each quarter boundary.",
            allow_quiet_hours=True,
        )
        return

    _validate_and_show_summary(person, client)


def handle_today_command(person: Person, client: WhatsAppClient, now=None) -> None:
    today = person.local_now(now).date()
    client.send_text(person, format_day_plan(person, today), allow_quiet_hours=True)
