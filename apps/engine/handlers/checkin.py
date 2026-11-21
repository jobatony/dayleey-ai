"""Smart 3-button check-in handler (All Done / Review Each / Missed Both) + single-item + missed reason + wrap-up."""
from __future__ import annotations

from django.utils import timezone

from apps.accounts.models import Person
from apps.engine import states
from apps.messaging.client import WhatsAppClient
from apps.quarters.models import Checkin, Priority

REASON_MAP = {
    "reason_forgot": Priority.MissedReason.FORGOT,
    "forgot": Priority.MissedReason.FORGOT,
    "reason_time": Priority.MissedReason.TIME,
    "time": Priority.MissedReason.TIME,
    "ran out of time": Priority.MissedReason.TIME,
    "reason_emergency": Priority.MissedReason.EMERGENCY,
    "emergency": Priority.MissedReason.EMERGENCY,
    "reason_other": Priority.MissedReason.OTHER,
    "other": Priority.MissedReason.OTHER,
}


def handle_checkin_summary(person: Person, choice: str, client: WhatsAppClient) -> None:
    """Handle [All Done] [Review Each] [Missed Both] for a 2-priority quarter boundary."""
    data = dict(person.state_data)
    priority_ids: list[int] = data.get("priority_ids", [])
    checkin_id = data.get("checkin_id")
    priorities = list(Priority.objects.filter(id__in=priority_ids, owner=person).order_by("position"))
    if not priorities:
        person.clear_state()
        return

    norm = choice.strip().lower()
    now = timezone.now()

    if norm in {"chk_all_done", "all done", "done", "both done"}:
        for p in priorities:
            p.status = Priority.Status.DONE
            p.responded_at = now
            p.save(update_fields=["status", "responded_at", "updated_at"])
        _mark_checkin_responded(checkin_id, "all_done", now)
        person.clear_state()
        client.send_text(
            person,
            f"🔥 Awesome! Marked all {len(priorities)} priorities as *Done*. Keep the momentum going!",
            allow_quiet_hours=True,
        )
        return

    if norm in {"chk_missed_both", "missed both", "missed"}:
        for p in priorities:
            p.status = Priority.Status.MISSED
            p.responded_at = now
            p.save(update_fields=["status", "responded_at", "updated_at"])
        _mark_checkin_responded(checkin_id, "missed_both", now)
        person.set_state(
            states.CHECKIN_REASON,
            {"missed_ids": [p.id for p in priorities], "remaining_ids": [], "checkin_id": checkin_id},
        )
        _ask_missed_reason(person, "those priorities", client)
        return

    if norm in {"chk_review_each", "review each", "review"}:
        person.set_state(
            states.CHECKIN_ITEM,
            {"priority_ids": [p.id for p in priorities], "index": 0, "checkin_id": checkin_id},
        )
        _prompt_single_item(person, priorities[0], client)
        return

    client.send_buttons(
        person,
        "Tap one of the buttons to update your ended quarter:",
        [
            ("chk_all_done", "All Done"),
            ("chk_review_each", "Review Each"),
            ("chk_missed_both", "Missed Both"),
        ],
        allow_quiet_hours=True,
    )


def handle_checkin_item(person: Person, choice: str, client: WhatsAppClient) -> None:
    data = dict(person.state_data)
    priority_ids: list[int] = data.get("priority_ids", [])
    idx: int = data.get("index", 0)
    checkin_id = data.get("checkin_id")

    if idx >= len(priority_ids):
        person.clear_state()
        return

    priority = Priority.objects.filter(id=priority_ids[idx], owner=person).first()
    if not priority:
        person.clear_state()
        return

    norm = choice.strip().lower()
    now = timezone.now()

    if norm in {"item_done", "done"}:
        priority.status = Priority.Status.DONE
        priority.responded_at = now
        priority.save(update_fields=["status", "responded_at", "updated_at"])
    elif norm in {"item_partial", "partial"}:
        priority.status = Priority.Status.PARTIAL
        priority.responded_at = now
        priority.save(update_fields=["status", "responded_at", "updated_at"])
    elif norm in {"item_missed", "missed"}:
        priority.status = Priority.Status.MISSED
        priority.responded_at = now
        priority.save(update_fields=["status", "responded_at", "updated_at"])
        remaining = priority_ids[idx + 1 :]
        person.set_state(
            states.CHECKIN_REASON,
            {"missed_ids": [priority.id], "remaining_ids": remaining, "checkin_id": checkin_id},
        )
        _ask_missed_reason(person, f"'{priority.title}'", client)
        return
    else:
        _prompt_single_item(person, priority, client)
        return

    _advance_item_queue(person, priority_ids[idx + 1 :], checkin_id, client)


def handle_checkin_reason(person: Person, choice: str, client: WhatsAppClient) -> None:
    data = dict(person.state_data)
    missed_ids: list[int] = data.get("missed_ids", [])
    remaining_ids: list[int] = data.get("remaining_ids", [])
    checkin_id = data.get("checkin_id")

    reason = REASON_MAP.get(choice.strip().lower(), Priority.MissedReason.OTHER)
    Priority.objects.filter(id__in=missed_ids, owner=person).update(missed_reason=reason)

    _advance_item_queue(person, remaining_ids, checkin_id, client)


def handle_wrapup_confirm(person: Person, choice: str, client: WhatsAppClient) -> None:
    """Confirm remaining unconfirmed items during the 21:00 daily wrap-up."""
    data = dict(person.state_data)
    priority_ids: list[int] = data.get("priority_ids", [])
    checkin_id = data.get("checkin_id")
    norm = choice.strip().lower()
    now = timezone.now()

    if norm in {"wrap_all_done", "all done", "done"}:
        Priority.objects.filter(id__in=priority_ids, owner=person).update(
            status=Priority.Status.DONE, responded_at=now
        )
        _mark_checkin_responded(checkin_id, "wrap_all_done", now)
        person.clear_state()
        client.send_text(
            person,
            "✅ Updated all remaining items to *Done*! Your daily report will go out shortly.",
            allow_quiet_hours=True,
        )
    elif norm in {"wrap_review", "review each", "review"}:
        first = Priority.objects.filter(id=priority_ids[0], owner=person).first() if priority_ids else None
        if not first:
            person.clear_state()
            return
        person.set_state(
            states.CHECKIN_ITEM,
            {"priority_ids": priority_ids, "index": 0, "checkin_id": checkin_id},
        )
        _prompt_single_item(person, first, client)
    elif norm in {"wrap_keep", "leave as is", "skip"}:
        _mark_checkin_responded(checkin_id, "wrap_keep", now)
        person.clear_state()
        client.send_text(
            person,
            "Got it — leaving those as unconfirmed. Reply *PLAN* whenever you're ready for tomorrow!",
            allow_quiet_hours=True,
        )
    else:
        client.send_buttons(
            person,
            "Confirm your unconfirmed items before tonight's report:",
            [
                ("wrap_all_done", "All Done"),
                ("wrap_review", "Review Each"),
                ("wrap_keep", "Leave As Is"),
            ],
            allow_quiet_hours=True,
        )


def _prompt_single_item(person: Person, priority: Priority, client: WhatsAppClient) -> None:
    client.send_buttons(
        person,
        f"How did *Q{priority.quarter}: {priority.title}* go?",
        [("item_done", "Done"), ("item_partial", "Partial"), ("item_missed", "Missed")],
        allow_quiet_hours=True,
    )


def _ask_missed_reason(person: Person, label: str, client: WhatsAppClient) -> None:
    client.send_list(
        person,
        f"What got in the way of {label}?",
        button_text="Select Reason",
        rows=[
            ("reason_forgot", "Forgot", "Slipped my mind"),
            ("reason_time", "Ran out of time", "Took longer than planned"),
            ("reason_emergency", "Emergency", "Something unexpected came up"),
            ("reason_other", "Other", "Another reason"),
        ],
        allow_quiet_hours=True,
    )


def _advance_item_queue(
    person: Person, remaining_ids: list[int], checkin_id: int | None, client: WhatsAppClient
) -> None:
    if remaining_ids:
        next_p = Priority.objects.filter(id=remaining_ids[0], owner=person).first()
        if next_p:
            person.set_state(
                states.CHECKIN_ITEM,
                {"priority_ids": remaining_ids, "index": 0, "checkin_id": checkin_id},
            )
            _prompt_single_item(person, next_p, client)
            return

    _mark_checkin_responded(checkin_id, "reviewed", timezone.now())
    person.clear_state()
    client.send_text(person, "✅ Check-in logged! Stay focused for the next quarter.", allow_quiet_hours=True)


def _mark_checkin_responded(checkin_id: int | None, response: str, now) -> None:
    if not checkin_id:
        return
    Checkin.objects.filter(id=checkin_id).update(response=response[:64], responded_at=now)
