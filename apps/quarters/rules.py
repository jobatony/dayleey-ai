"""Pure business-rule logic for the Dayleey Quarter System (no DB access)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

# Fixed 6-hour quarters in user's local timezone:
# Q1: 06:00-12:00, Q2: 12:00-18:00, Q3: 18:00-24:00, Q4: 00:00-06:00 (default off)
QUARTER_HOURS: dict[int, tuple[int, int]] = {
    1: (6, 12),
    2: (12, 18),
    3: (18, 24),
    4: (0, 6),
}

QUARTER_LABELS: dict[int, str] = {
    1: "Q1 (06:00–12:00)",
    2: "Q2 (12:00–18:00)",
    3: "Q3 (18:00–24:00)",
    4: "Q4 (00:00–06:00)",
}

QUARTER_ORDER: tuple[int, ...] = (1, 2, 3, 4)
MAX_PRIORITIES_PER_QUARTER = 2
MIN_PRIORITIES_PER_CONSECUTIVE_PAIR = 2


@dataclass(frozen=True)
class RuleViolation:
    code: str
    message: str
    quarters: tuple[int, ...] = ()


def quarter_for_hour(hour: int) -> int:
    """Return quarter number (1-4) for a local hour 0..23."""
    if 6 <= hour < 12:
        return 1
    if 12 <= hour < 18:
        return 2
    if 18 <= hour < 24:
        return 3
    return 4


def previous_quarter(quarter: int) -> int:
    """Return the chronologically preceding quarter (Q1 <- Q4, Q2 <- Q1, Q3 <- Q2, Q4 <- Q3)."""
    return {1: 4, 2: 1, 3: 2, 4: 3}[quarter]


def quarter_at_boundary_hour(hour: int) -> tuple[int, int] | None:
    """For a boundary hour (0, 6, 12, 18), return (ended_quarter, starting_quarter)."""
    mapping = {
        6: (4, 1),
        12: (1, 2),
        18: (2, 3),
        0: (3, 4),
    }
    return mapping.get(hour)


def plannable_quarters(local_now: datetime, target_date: date, active: set[int]) -> set[int]:
    """Return the subset of active quarters that have not started yet for target_date.

    - For a future date: all active quarters are plannable.
    - For a past date: none are plannable.
    - For today: a quarter is plannable only if local_now is strictly before its start hour.
      (Note: Q4 starts at 00:00, so once today has begun, today's Q4 has already started.)
    """
    today = local_now.date()
    if target_date < today:
        return set()
    if target_date > today:
        return set(active)

    result: set[int] = set()
    for q in active:
        start_hour, _ = QUARTER_HOURS[q]
        if local_now.hour < start_hour:
            result.add(q)
    return result


def consecutive_active_pairs(eligible: set[int], allow_wrap: bool = True) -> list[tuple[int, int]]:
    """Build ordered consecutive pairs across eligible active quarters.

    - Standard daytime order: Q1 -> Q2 -> Q3 -> Q4.
    - Q4 -> Q1 wraps across days only when both Q4 and Q1 are eligible and allow_wrap is True.
    """
    ordered = [q for q in QUARTER_ORDER if q in eligible]
    if len(ordered) < 2:
        return []
    pairs = [(ordered[i], ordered[i + 1]) for i in range(len(ordered) - 1)]
    if allow_wrap and 4 in eligible and 1 in eligible and len(ordered) > 2:
        pairs.append((4, 1))
    return pairs


def validate_day(
    plan: dict[int, list[str]],
    active: set[int],
    plannable: set[int] | None = None,
) -> list[RuleViolation]:
    """Validate a day's priority plan against Dayleey's quarter rules.

    Rules:
    1. Only active & plannable quarters may receive priorities.
    2. Max 2 priorities per active quarter (non-empty titles).
    3. Every pair of consecutive eligible active quarters must have >= 2 priorities combined.
       (Q4->Q1 wraps across days only if both Q4 and Q1 are active and full-day planning applies.)
    4. Mid-day edge case: if only 1 eligible active quarter remains, require >= 1 priority in it.
    """
    violations: list[RuleViolation] = []
    clean_active = {q for q in active if q in QUARTER_HOURS}
    eligible = clean_active if plannable is None else (clean_active & set(plannable))

    if not clean_active:
        return [
            RuleViolation(
                code="NO_ACTIVE_QUARTERS",
                message="You have no active quarters configured. Enable at least one quarter first.",
            )
        ]

    if not eligible:
        return [
            RuleViolation(
                code="NO_PLANNABLE_QUARTERS",
                message="All active quarters for that day have already started. Try planning for tomorrow!",
            )
        ]

    cleaned_plan: dict[int, list[str]] = {}
    for q, items in plan.items():
        titles = [t.strip() for t in items if t and t.strip()]
        if titles and q not in clean_active:
            violations.append(
                RuleViolation(
                    code="INACTIVE_QUARTER",
                    message=f"{QUARTER_LABELS.get(q, f'Q{q}')} is turned off in your settings.",
                    quarters=(q,),
                )
            )
            continue
        if titles and q not in eligible:
            violations.append(
                RuleViolation(
                    code="LOCKED_QUARTER",
                    message=f"{QUARTER_LABELS.get(q, f'Q{q}')} has already started and can't be edited.",
                    quarters=(q,),
                )
            )
            continue
        if len(titles) > MAX_PRIORITIES_PER_QUARTER:
            violations.append(
                RuleViolation(
                    code="MAX_EXCEEDED",
                    message=(
                        f"{QUARTER_LABELS[q]} has {len(titles)} priorities. "
                        f"Maximum allowed is {MAX_PRIORITIES_PER_QUARTER} per quarter."
                    ),
                    quarters=(q,),
                )
            )
        cleaned_plan[q] = titles

    if len(eligible) == 1:
        only_q = next(iter(eligible))
        if len(cleaned_plan.get(only_q, [])) < 1:
            violations.append(
                RuleViolation(
                    code="SINGLE_QUARTER_MIN",
                    message=f"Add at least 1 priority for {QUARTER_LABELS[only_q]} (your only remaining active quarter).",
                    quarters=(only_q,),
                )
            )
        return violations

    is_full_day = eligible == clean_active
    for q_a, q_b in consecutive_active_pairs(eligible, allow_wrap=is_full_day):
        total = len(cleaned_plan.get(q_a, [])) + len(cleaned_plan.get(q_b, []))
        if total < MIN_PRIORITIES_PER_CONSECUTIVE_PAIR:
            violations.append(
                RuleViolation(
                    code="CONSECUTIVE_MIN",
                    message=(
                        f"Q{q_a} + Q{q_b} have only {total} priority total — "
                        f"any two consecutive active quarters need at least {MIN_PRIORITIES_PER_CONSECUTIVE_PAIR}."
                    ),
                    quarters=(q_a, q_b),
                )
            )

    return violations
