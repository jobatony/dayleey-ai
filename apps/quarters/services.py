from __future__ import annotations

from datetime import date, timedelta

from django.db import transaction

from apps.accounts.models import Person
from apps.quarters.models import Priority, QuarterConfig
from apps.quarters.rules import QUARTER_HOURS, QUARTER_ORDER


def ensure_quarter_configs(person: Person) -> list[QuarterConfig]:
    """Ensure Q1-Q4 configs exist for person (Q1-Q3 ON, Q4 OFF by default)."""
    existing = {qc.quarter: qc for qc in person.quarter_configs.all()}
    created = []
    for q in QUARTER_ORDER:
        if q not in existing:
            start_h, end_h = QUARTER_HOURS[q]
            qc = QuarterConfig.objects.create(
                person=person,
                quarter=q,
                active=(q != 4),
                start_hour=start_h,
                end_hour=end_h,
            )
            existing[q] = qc
            created.append(qc)
    return [existing[q] for q in QUARTER_ORDER]


def get_active_quarters(person: Person) -> set[int]:
    configs = ensure_quarter_configs(person)
    return {qc.quarter for qc in configs if qc.active}


def set_active_quarters(person: Person, active: set[int]) -> None:
    ensure_quarter_configs(person)
    for qc in person.quarter_configs.all():
        should_be_active = qc.quarter in active
        if qc.active != should_be_active:
            qc.active = should_be_active
            qc.save(update_fields=["active"])


@transaction.atomic
def save_day_plan(person: Person, target_date: date, plan: dict[int, list[str]], editable: set[int]) -> list[Priority]:
    """Replace priorities for editable quarters on target_date."""
    Priority.objects.filter(owner=person, date=target_date, quarter__in=editable).delete()
    saved: list[Priority] = []
    for q in QUARTER_ORDER:
        if q not in editable:
            continue
        titles = [t.strip() for t in plan.get(q, []) if t and t.strip()]
        for pos, title in enumerate(titles[:2], start=1):
            saved.append(
                Priority.objects.create(
                    owner=person,
                    date=target_date,
                    quarter=q,
                    position=pos,
                    title=title[:160],
                    status=Priority.Status.PLANNED,
                )
            )
    return saved


def format_day_plan(person: Person, target_date: date) -> str:
    items = list(Priority.objects.filter(owner=person, date=target_date).order_by("quarter", "position"))
    if not items:
        return f"No priorities planned for {target_date.isoformat()} yet. Reply PLAN to set them."
    by_q: dict[int, list[Priority]] = {}
    for item in items:
        by_q.setdefault(item.quarter, []).append(item)

    icons = {
        Priority.Status.PLANNED: "•",
        Priority.Status.DONE: "✅",
        Priority.Status.PARTIAL: "🟡",
        Priority.Status.MISSED: "❌",
        Priority.Status.UNCONFIRMED: "❓",
    }
    lines = [f"*Your plan for {target_date.strftime('%a, %d %b')}*"]
    for q in QUARTER_ORDER:
        if q in by_q:
            start_h, end_h = QUARTER_HOURS[q]
            lines.append(f"\n*Q{q}* ({start_h:02d}:00–{end_h:02d}:00)")
            for p in by_q[q]:
                lines.append(f"  {icons[p.status]} {p.title}")
    return "\n".join(lines)


def compute_user_stats(person: Person, today: date, days: int = 7) -> dict:
    start_date = today - timedelta(days=days - 1)
    qs = Priority.objects.filter(owner=person, date__gte=start_date, date__lte=today)
    total = qs.count()
    done = qs.filter(status=Priority.Status.DONE).count()
    partial = qs.filter(status=Priority.Status.PARTIAL).count()
    missed = qs.filter(status=Priority.Status.MISSED).count()
    unconfirmed = qs.filter(status=Priority.Status.UNCONFIRMED).count()

    # Streak: consecutive days ending today (or yesterday if today not finished) with >=1 Done priority
    streak = 0
    cursor = today
    for _ in range(60):
        day_qs = Priority.objects.filter(owner=person, date=cursor)
        if day_qs.filter(status=Priority.Status.DONE).exists():
            streak += 1
            cursor -= timedelta(days=1)
        elif cursor == today and day_qs.filter(status=Priority.Status.PLANNED).exists():
            cursor -= timedelta(days=1)
        else:
            break

    rate = round((done / total) * 100) if total else 0
    return {
        "days": days,
        "total": total,
        "done": done,
        "partial": partial,
        "missed": missed,
        "unconfirmed": unconfirmed,
        "completion_pct": rate,
        "streak_days": streak,
    }
