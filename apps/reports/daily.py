from __future__ import annotations

from datetime import date

from apps.accounts.models import Person
from apps.quarters.models import Priority
from apps.quarters.services import compute_user_stats
from apps.reports.models import DailyReport


def summarize_day(user: Person, target_date: date) -> dict:
    items = list(Priority.objects.filter(owner=user, date=target_date).order_by("quarter", "position"))
    counts = {
        "total": len(items),
        "done": sum(1 for p in items if p.status == Priority.Status.DONE),
        "partial": sum(1 for p in items if p.status == Priority.Status.PARTIAL),
        "missed": sum(1 for p in items if p.status == Priority.Status.MISSED),
        "unconfirmed": sum(1 for p in items if p.status == Priority.Status.UNCONFIRMED),
        "planned": sum(1 for p in items if p.status == Priority.Status.PLANNED),
    }
    counts["items"] = [
        {"quarter": p.quarter, "title": p.title, "status": p.status} for p in items
    ]
    return counts


def upsert_daily_report(user: Person, partner: Person | None, target_date: date) -> DailyReport:
    summary = summarize_day(user, target_date)
    report, _ = DailyReport.objects.update_or_create(
        user=user,
        date=target_date,
        defaults={"partner": partner, "summary": summary},
    )
    return report


def format_user_wrapup(user: Person, target_date: date) -> tuple[str, list[Priority]]:
    summary = summarize_day(user, target_date)
    unconfirmed = list(
        Priority.objects.filter(
            owner=user,
            date=target_date,
            status__in=[Priority.Status.UNCONFIRMED, Priority.Status.PLANNED],
        ).order_by("quarter", "position")
    )
    if summary["total"] == 0:
        return (
            f"Evening wrap-up ({target_date.strftime('%a %d %b')}): You had no priorities planned today. "
            "Reply PLAN to set tomorrow's quarters!",
            [],
        )

    lines = [
        f"*Daily Wrap-up ({target_date.strftime('%a %d %b')})*",
        f"✅ Done: {summary['done']}/{summary['total']} | 🟡 Partial: {summary['partial']} | ❌ Missed: {summary['missed']}",
    ]
    if unconfirmed:
        lines.append(f"\n❓ You still have {len(unconfirmed)} unconfirmed item(s):")
        for idx, p in enumerate(unconfirmed, start=1):
            lines.append(f"  {idx}. Q{p.quarter}: {p.title}")
        lines.append("\nTap below to confirm them before your daily report goes out.")
    else:
        lines.append("\nReply PLAN anytime to set tomorrow's quarters.")
    return "\n".join(lines), unconfirmed


def format_partner_daily_report(user: Person, target_date: date, summary: dict) -> str:
    if summary["total"] == 0:
        return (
            f"*Dayleey Daily Report — {user.display_name} ({target_date.strftime('%a %d %b')})*\n"
            f"{user.display_name} did not set any priorities today.\n\n"
            "Send them a quick tap below!"
        )

    icons = {
        "done": "✅",
        "partial": "🟡",
        "missed": "❌",
        "unconfirmed": "❓",
        "planned": "•",
    }
    lines = [
        f"*Dayleey Daily Report — {user.display_name} ({target_date.strftime('%a %d %b')})*",
        f"Score: {summary['done']}/{summary['total']} done"
        + (f", {summary['partial']} partial" if summary["partial"] else "")
        + (f", {summary['missed']} missed" if summary["missed"] else "")
        + (f", {summary['unconfirmed']} unconfirmed" if summary["unconfirmed"] else ""),
        "",
    ]
    for item in summary.get("items", []):
        lines.append(f"{icons.get(item['status'], '•')} Q{item['quarter']}: {item['title']}")
    lines.append("\nTap a quick response to encourage or nudge them (or reply STOP to opt out).")
    return "\n".join(lines)


def format_weekly_report(user: Person, today: date) -> str:
    stats = compute_user_stats(user, today=today, days=7)
    return (
        f"*Dayleey Weekly Report — {user.display_name}*\n"
        f"Last 7 days: {stats['done']}/{stats['total']} priorities done ({stats['completion_pct']}%)\n"
        f"🟡 Partial: {stats['partial']} | ❌ Missed: {stats['missed']} | ❓ Unconfirmed: {stats['unconfirmed']}\n"
        f"🔥 Current streak: {stats['streak_days']} day(s)\n\n"
        "Reply PLAN to line up next week!"
    )
