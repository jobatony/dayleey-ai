"""Per-minute idempotent scheduler for quarter boundaries, check-ins, wrap-ups, partner daily reports, and fallbacks."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import Person
from apps.engine import states
from apps.messaging.budget import is_in_quiet_hours
from apps.messaging.client import WhatsAppClient
from apps.messaging.models import MessageLog, ScheduledEvent
from apps.network.models import Pairing
from apps.network.services import enable_self_report, get_due_self_report_fallbacks
from apps.quarters.models import Checkin, Priority
from apps.quarters.rules import QUARTER_LABELS, quarter_at_boundary_hour
from apps.quarters.services import compute_user_stats, get_active_quarters
from apps.reports.daily import (
    format_partner_daily_report,
    format_user_wrapup,
    format_weekly_report,
    upsert_daily_report,
)
from apps.reports.models import DailyReport

logger = logging.getLogger(__name__)


def _claim_event(
    event_key: str,
    person: Person,
    event_type: str,
    local_date: date,
    quarter: int | None = None,
) -> ScheduledEvent | None:
    try:
        with transaction.atomic():
            return ScheduledEvent.objects.create(
                event_key=event_key,
                person=person,
                event_type=event_type,
                local_date=local_date,
                quarter=quarter,
                status=ScheduledEvent.Status.SENT,
            )
    except IntegrityError:
        return None


def tick(now: datetime | None = None, *, client: WhatsAppClient | None = None) -> dict:
    """Execute one scheduler minute tick across all active users and partners."""
    ref = now or timezone.now()
    wa = client or WhatsAppClient()
    stats = {
        "boundaries": 0,
        "nudges": 0,
        "unconfirmed": 0,
        "wrapups": 0,
        "partner_reports": 0,
        "delayed_reports": 0,
        "fallbacks": 0,
        "weekly": 0,
    }

    active_users = Person.objects.filter(
        role__in=[Person.Role.USER, Person.Role.BOTH],
        opted_in=True,
        opted_out=False,
        paused=False,
    )

    for user in active_users:
        local_dt = user.local_now(ref)
        local_date = local_dt.date()
        active_qs = get_active_quarters(user)

        # 1. Quarter boundary messages (00:00, 06:00, 12:00, 18:00 within first 5 min window)
        boundary = quarter_at_boundary_hour(local_dt.hour)
        if boundary and local_dt.minute < 5:
            ended_q, starting_q = boundary
            if ended_q in active_qs or starting_q in active_qs:
                if _run_boundary_event(user, local_date, ended_q, starting_q, active_qs, ref, wa):
                    stats["boundaries"] += 1

        # 2. 21:00 daily wrap-up
        if local_dt.hour == 21 and local_dt.minute < 5:
            if _run_daily_wrapup(user, local_date, ref, wa):
                stats["wrapups"] += 1

        # 3. ~21:30 partner daily report
        if local_dt.hour == 21 and 30 <= local_dt.minute < 35:
            if _run_partner_daily_report(user, local_date, ref, wa):
                stats["partner_reports"] += 1

        # 4. Optional Sunday 19:00 weekly streak report
        if settings.WEEKLY_REPORT_ENABLED and local_dt.weekday() == 6 and local_dt.hour == 19 and local_dt.minute < 5:
            if _run_weekly_report(user, local_date, ref, wa):
                stats["weekly"] += 1

    # 5. Unanswered check-in 30-min reminders & 2-hour expiry -> unconfirmed
    nudges, unconfirmed = _process_pending_checkins(ref, wa)
    stats["nudges"] += nudges
    stats["unconfirmed"] += unconfirmed

    # 6. Deliver delayed partner reports at 08:00 partner-local time (from cross-TZ quiet hours)
    stats["delayed_reports"] += _deliver_delayed_partner_reports(ref, wa)

    # 7. Auto-switch unjoined partner invites to self-report after PARTNER_FALLBACK_DAYS
    stats["fallbacks"] += _process_self_report_fallbacks(ref, wa)

    return stats


def catchup(now: datetime | None = None, *, client: WhatsAppClient | None = None) -> dict:
    """Catch up only still-relevant events from the last 2 hours after a worker restart."""
    ref = now or timezone.now()
    wa = client or WhatsAppClient()
    totals: dict[str, int] = {}
    for minutes_ago in (90, 60, 30, 0):
        slot = ref - timedelta(minutes=minutes_ago)
        res = tick(now=slot, client=wa)
        for k, v in res.items():
            totals[k] = totals.get(k, 0) + v
    return totals


def _run_boundary_event(
    user: Person,
    local_date: date,
    ended_q: int,
    starting_q: int,
    active_qs: set[int],
    ref: datetime,
    wa: WhatsAppClient,
) -> bool:
    event_key = f"boundary:{user.id}:{local_date.isoformat()}:Q{starting_q}"
    ev = _claim_event(event_key, user, "boundary", local_date, quarter=starting_q)
    if ev is None:
        return False

    ended_date = local_date - timedelta(days=1) if ended_q == 3 and starting_q == 4 else local_date
    ended_items = (
        list(
            Priority.objects.filter(
                owner=user, date=ended_date, quarter=ended_q, status=Priority.Status.PLANNED
            ).order_by("position")
        )
        if ended_q in active_qs
        else []
    )
    starting_items = (
        list(
            Priority.objects.filter(
                owner=user, date=local_date, quarter=starting_q, status=Priority.Status.PLANNED
            ).order_by("position")
        )
        if starting_q in active_qs
        else []
    )

    if not ended_items and not starting_items:
        ev.status = ScheduledEvent.Status.SKIPPED
        ev.detail = "No planned priorities for ended or starting quarter."
        ev.save(update_fields=["status", "detail"])
        return False

    parts: list[str] = []
    if ended_items:
        parts.append(f"*Q{ended_q} Check-in ({QUARTER_LABELS[ended_q].split(' ')[1]}):*")
        for idx, p in enumerate(ended_items, start=1):
            parts.append(f"  {idx}. {p.title}")
    if starting_items:
        if parts:
            parts.append("")
        parts.append(f"*Up next — {QUARTER_LABELS[starting_q]}:*")
        for idx, p in enumerate(starting_items, start=1):
            parts.append(f"  • {p.title}")

    body = "\n".join(parts)
    template_summary = "; ".join(p.title for p in (starting_items or ended_items))
    fallback = (
        "quarter_boundary_reminder",
        [user.display_name, f"Q{ended_q}", f"Q{starting_q}", template_summary],
    )

    checkin_row = None
    if ended_items:
        checkin_row = Checkin.objects.create(
            person=user,
            date=ended_date,
            quarter=ended_q,
            kind=Checkin.Kind.BOUNDARY,
            sent_at=ref,
        )
        Priority.objects.filter(id__in=[p.id for p in ended_items]).update(checkin_sent_at=ref)

    if len(ended_items) >= 2:
        user.set_state(
            states.CHECKIN_SUMMARY,
            {"priority_ids": [p.id for p in ended_items], "checkin_id": checkin_row.id},
        )
        log = wa.send_buttons(
            user,
            body + "\n\nHow did your ended quarter go?",
            [
                ("chk_all_done", "All Done"),
                ("chk_review_each", "Review Each"),
                ("chk_missed_both", "Missed Both"),
            ],
            fallback_template=fallback,
            allow_quiet_hours=True,
            now=ref,
        )
    elif len(ended_items) == 1:
        user.set_state(
            states.CHECKIN_ITEM,
            {"priority_ids": [ended_items[0].id], "index": 0, "checkin_id": checkin_row.id},
        )
        log = wa.send_buttons(
            user,
            body + f"\n\nHow did *{ended_items[0].title}* go?",
            [("item_done", "Done"), ("item_partial", "Partial"), ("item_missed", "Missed")],
            fallback_template=fallback,
            allow_quiet_hours=True,
            now=ref,
        )
    else:
        log = wa.send_text(
            user,
            body + "\n\nYou've got this! I'll check back at the end of the quarter.",
            fallback_template=fallback,
            allow_quiet_hours=True,
            now=ref,
        )

    if log.status in {MessageLog.Status.BLOCKED, MessageLog.Status.FAILED}:
        ev.status = ScheduledEvent.Status.FAILED
        ev.detail = f"{log.error_code}: {log.error_message}"[:250]
        ev.save(update_fields=["status", "detail"])
        return False
    return True


def _process_pending_checkins(ref: datetime, wa: WhatsAppClient) -> tuple[int, int]:
    nudges = 0
    unconfirmed_count = 0

    pending = Checkin.objects.select_related("person").filter(
        kind=Checkin.Kind.BOUNDARY,
        responded_at__isnull=True,
        sent_at__lte=ref - timedelta(minutes=30),
        sent_at__gte=ref - timedelta(hours=6),
    )

    for chk in pending:
        user = chk.person
        if user.opted_out or user.paused:
            continue
        age = ref - chk.sent_at
        open_items = list(
            Priority.objects.filter(
                owner=user, date=chk.date, quarter=chk.quarter, status=Priority.Status.PLANNED
            )
        )
        if not open_items:
            chk.responded_at = ref
            chk.response = "resolved"
            chk.save(update_fields=["responded_at", "response"])
            continue

        if age >= timedelta(hours=2):
            # Mark unconfirmed (not missed!) so user can still confirm at 21:00 wrap-up
            Priority.objects.filter(id__in=[p.id for p in open_items]).update(
                status=Priority.Status.UNCONFIRMED
            )
            chk.responded_at = ref
            chk.response = "unconfirmed_auto"
            chk.save(update_fields=["responded_at", "response"])
            if user.state in {states.CHECKIN_SUMMARY, states.CHECKIN_ITEM}:
                user.clear_state()
            unconfirmed_count += len(open_items)
        elif chk.reminder_sent_at is None:
            # One gentle 30-min reminder (only if 24h window is open to avoid extra template spend)
            if user.is_window_open(ref):
                wa.send_text(
                    user,
                    f"Quick nudge: tap above to check in on *Q{chk.quarter}* before it's marked unconfirmed.",
                    allow_quiet_hours=False,
                    now=ref,
                )
            chk.reminder_sent_at = ref
            chk.save(update_fields=["reminder_sent_at"])
            nudges += 1

    return nudges, unconfirmed_count


def _run_daily_wrapup(user: Person, local_date: date, ref: datetime, wa: WhatsAppClient) -> bool:
    event_key = f"wrapup:{user.id}:{local_date.isoformat()}"
    ev = _claim_event(event_key, user, "wrapup", local_date)
    if ev is None:
        return False

    body, unconfirmed = format_user_wrapup(user, local_date)
    summary_line = (
        f"{len(unconfirmed)} item(s) still unconfirmed" if unconfirmed else "All items logged"
    )
    fallback = ("user_daily_wrapup", [user.display_name, local_date.isoformat(), summary_line])

    if unconfirmed:
        chk = Checkin.objects.create(
            person=user, date=local_date, kind=Checkin.Kind.WRAPUP, sent_at=ref
        )
        user.set_state(
            states.WRAPUP_CONFIRM,
            {"priority_ids": [p.id for p in unconfirmed], "checkin_id": chk.id},
        )
        log = wa.send_buttons(
            user,
            body,
            [
                ("wrap_all_done", "All Done"),
                ("wrap_review", "Review Each"),
                ("wrap_keep", "Leave As Is"),
            ],
            fallback_template=fallback,
            allow_quiet_hours=True,
            now=ref,
        )
    else:
        log = wa.send_text(
            user, body, fallback_template=fallback, allow_quiet_hours=True, now=ref
        )

    if log.status in {MessageLog.Status.BLOCKED, MessageLog.Status.FAILED}:
        ev.status = ScheduledEvent.Status.FAILED
        ev.detail = f"{log.error_code}: {log.error_message}"[:250]
        ev.save(update_fields=["status", "detail"])
        return False
    return True


def _run_partner_daily_report(
    user: Person, local_date: date, ref: datetime, wa: WhatsAppClient
) -> bool:
    pairing = Pairing.objects.select_related("partner").filter(user=user).first()
    if not pairing or not pairing.has_active_partner:
        return False

    partner = pairing.partner
    event_key = f"partner_report:{user.id}:{local_date.isoformat()}"
    ev = _claim_event(event_key, partner, "partner_report", local_date)
    if ev is None:
        return False

    report = upsert_daily_report(user, partner, local_date)

    # Respect partner quiet hours (e.g. cross-timezone pairing): defer to 08:00 partner-local
    if is_in_quiet_hours(partner, ref):
        ev.status = ScheduledEvent.Status.SKIPPED
        ev.detail = "Deferred to 08:00 partner-local due to partner quiet hours."
        ev.save(update_fields=["status", "detail"])
        return False

    return _send_partner_report_now(report, ref, wa, ev)


def _deliver_delayed_partner_reports(ref: datetime, wa: WhatsAppClient) -> int:
    sent = 0
    unsent_reports = DailyReport.objects.select_related("user", "partner").filter(
        sent_at__isnull=True, partner__isnull=False, date__gte=ref.date() - timedelta(days=2)
    )
    for report in unsent_reports:
        partner = report.partner
        if not partner or partner.opted_out or not partner.opted_in:
            continue
        p_local = partner.local_now(ref)
        if p_local.hour == 8 and p_local.minute < 5:
            event_key = f"partner_report_delayed:{report.id}"
            ev = _claim_event(event_key, partner, "partner_report_delayed", report.date)
            if ev and _send_partner_report_now(report, ref, wa, ev):
                sent += 1
    return sent


def _send_partner_report_now(
    report: DailyReport, ref: datetime, wa: WhatsAppClient, ev: ScheduledEvent
) -> bool:
    user = report.user
    partner = report.partner
    body = format_partner_daily_report(user, report.date, report.summary)
    summary = report.summary
    compact = (
        f"{summary.get('done', 0)}/{summary.get('total', 0)} done"
        + (f", {summary.get('missed', 0)} missed" if summary.get("missed") else "")
    )
    fallback = ("partner_daily_report", [user.display_name, report.date.isoformat(), compact])

    log = wa.send_buttons(
        partner,
        body,
        [
            (f"rep_cheer:{report.id}", "👏 Great job"),
            (f"rep_seen:{report.id}", "👀 Seen"),
            (f"rep_nudge:{report.id}", "💪 Nudge"),
        ],
        fallback_template=fallback,
        allow_quiet_hours=False,
        now=ref,
    )
    if log.status in {MessageLog.Status.BLOCKED, MessageLog.Status.FAILED}:
        ev.status = ScheduledEvent.Status.FAILED
        ev.detail = f"{log.error_code}: {log.error_message}"[:250]
        ev.save(update_fields=["status", "detail"])
        return False

    report.sent_at = ref
    report.save(update_fields=["sent_at", "updated_at"])
    return True


def _process_self_report_fallbacks(ref: datetime, wa: WhatsAppClient) -> int:
    count = 0
    for pairing in get_due_self_report_fallbacks(ref):
        enable_self_report(pairing.user)
        pairing.fallback_notified_at = ref
        pairing.save(update_fields=["fallback_notified_at", "updated_at"])
        wa.send_text(
            pairing.user,
            (
                f"It's been {settings.PARTNER_FALLBACK_DAYS} days and no partner joined your invite link yet. "
                "We've switched you to *self-report mode* so you can keep tracking smoothly — "
                "reply *PARTNER* anytime to invite someone new!"
            ),
            allow_quiet_hours=False,
            now=ref,
        )
        count += 1
    return count


def _run_weekly_report(user: Person, local_date: date, ref: datetime, wa: WhatsAppClient) -> bool:
    event_key = f"weekly:{user.id}:{local_date.isoformat()}"
    ev = _claim_event(event_key, user, "weekly_report", local_date)
    if ev is None:
        return False
    stats = compute_user_stats(user, today=local_date, days=7)
    body = format_weekly_report(user, local_date)
    fallback = (
        "weekly_report",
        [
            user.display_name,
            f"{stats['done']}/{stats['total']}",
            str(stats["completion_pct"]),
            str(stats["streak_days"]),
        ],
    )
    log = wa.send_text(user, body, fallback_template=fallback, allow_quiet_hours=False, now=ref)
    if log.status in {MessageLog.Status.BLOCKED, MessageLog.Status.FAILED}:
        ev.status = ScheduledEvent.Status.FAILED
        ev.detail = f"{log.error_code}: {log.error_message}"[:250]
        ev.save(update_fields=["status", "detail"])
        return False
    return True
