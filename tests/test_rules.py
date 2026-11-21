"""Unit tests for Dayleey's pure Quarter System rules."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from apps.quarters.rules import plannable_quarters, quarter_at_boundary_hour, validate_day

LAGOS = ZoneInfo("Africa/Lagos")


def test_valid_q1_to_q3_plan_passes():
    # Q1: 1, Q2: 1, Q3: 1 -> (Q1+Q2=2), (Q2+Q3=2) -> valid
    violations = validate_day(
        plan={1: ["Gym"], 2: ["Ship API"], 3: ["Read 20 pages"]},
        active={1, 2, 3},
    )
    assert violations == []


def test_valid_alternating_2_0_2_passes():
    # Q1: 2, Q2: 0, Q3: 2 -> (Q1+Q2=2), (Q2+Q3=2) -> valid
    violations = validate_day(
        plan={1: ["Gym", "Deep work"], 2: [], 3: ["Family dinner", "Plan tomorrow"]},
        active={1, 2, 3},
    )
    assert violations == []


def test_consecutive_pair_under_two_fails_with_specific_pair():
    violations = validate_day(
        plan={1: ["Gym"], 2: [], 3: ["Read", "Journal"]},
        active={1, 2, 3},
    )
    assert len(violations) == 1
    assert violations[0].code == "CONSECUTIVE_MIN"
    assert violations[0].quarters == (1, 2)


def test_three_priorities_in_one_quarter_fails():
    violations = validate_day(
        plan={1: ["A", "B", "C"], 2: ["D"], 3: ["E"]},
        active={1, 2, 3},
    )
    assert any(v.code == "MAX_EXCEEDED" and v.quarters == (1,) for v in violations)


def test_q4_off_does_not_wrap_q3_to_q1():
    # Q4 is off, so only (1,2) and (2,3) are checked
    violations = validate_day(
        plan={1: [], 2: ["A", "B"], 3: []},
        active={1, 2, 3},
    )
    assert violations == []


def test_q4_active_enforces_wrap_q4_to_q1():
    # All 4 active: Q1=0, Q2=2, Q3=2, Q4=1 -> Q4+Q1 = 1 < 2 -> fails!
    violations = validate_day(
        plan={1: [], 2: ["A", "B"], 3: ["C", "D"], 4: ["Night shift"]},
        active={1, 2, 3, 4},
    )
    assert any(v.code == "CONSECUTIVE_MIN" and v.quarters == (4, 1) for v in violations)


def test_midday_planning_locks_started_quarters_and_validates_remaining():
    # At 08:30 WAT, Q1 (06-12) has started; Q2 and Q3 remain plannable
    now = datetime(2026, 11, 20, 8, 30, tzinfo=LAGOS)
    editable = plannable_quarters(now, date(2026, 11, 20), active={1, 2, 3})
    assert editable == {2, 3}

    # Q2+Q3 = 1 -> fails
    violations = validate_day(plan={2: ["Task 1"], 3: []}, active={1, 2, 3}, plannable=editable)
    assert any(v.code == "CONSECUTIVE_MIN" and v.quarters == (2, 3) for v in violations)

    # Q2+Q3 = 2 -> passes
    assert validate_day(plan={2: ["Task 1"], 3: ["Task 2"]}, active={1, 2, 3}, plannable=editable) == []


def test_midday_single_remaining_quarter_requires_at_least_one():
    # At 14:00 WAT, only Q3 (18-24) remains plannable
    now = datetime(2026, 11, 20, 14, 0, tzinfo=LAGOS)
    editable = plannable_quarters(now, date(2026, 11, 20), active={1, 2, 3})
    assert editable == {3}

    violations = validate_day(plan={3: []}, active={1, 2, 3}, plannable=editable)
    assert len(violations) == 1
    assert violations[0].code == "SINGLE_QUARTER_MIN"

    assert validate_day(plan={3: ["Evening workout"]}, active={1, 2, 3}, plannable=editable) == []


def test_tomorrow_has_all_active_quarters_plannable():
    now = datetime(2026, 11, 20, 21, 0, tzinfo=LAGOS)
    assert plannable_quarters(now, date(2026, 11, 21), active={1, 2, 3}) == {1, 2, 3}
    assert plannable_quarters(now, date(2026, 11, 20), active={1, 2, 3}) == set()


def test_boundary_hours_mapping():
    assert quarter_at_boundary_hour(6) == (4, 1)
    assert quarter_at_boundary_hour(12) == (1, 2)
    assert quarter_at_boundary_hour(18) == (2, 3)
    assert quarter_at_boundary_hour(0) == (3, 4)
    assert quarter_at_boundary_hour(15) is None
