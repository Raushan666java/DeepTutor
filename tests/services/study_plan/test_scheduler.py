"""Deterministic scheduling tests for dated study plans."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from deeptutor.services.study_plan.scheduler import (
    MAX_SESSIONS,
    PlanSchedule,
    ScheduleError,
    build_schedule,
)


def _schedule(**overrides) -> PlanSchedule:
    params = {
        "goal": "Ship a RAG service",
        "start_date": date(2026, 11, 2),  # a Monday
        "deadline": date(2026, 11, 15),  # two weeks later, Sunday
        "weekly_hours": 6,
    }
    params.update(overrides)
    return build_schedule(**params)


def test_six_hours_across_mon_wed_fri_fills_two_weeks() -> None:
    schedule = _schedule()
    days = [session.date for session in schedule.sessions]
    assert days == [
        date(2026, 11, 2),
        date(2026, 11, 4),
        date(2026, 11, 6),
        date(2026, 11, 9),
        date(2026, 11, 11),
        date(2026, 11, 13),
    ]
    assert [session.week for session in schedule.sessions] == [1, 1, 1, 2, 2, 2]
    assert schedule.weeks == 2
    assert schedule.total_minutes == 360


def test_weekly_budget_caps_the_days_it_can_fill() -> None:
    # 2 hours a week = two 60-minute sessions, so Friday is left empty.
    schedule = _schedule(weekly_hours=2)
    days = [session.date for session in schedule.sessions]
    assert days == [
        date(2026, 11, 2),
        date(2026, 11, 4),
        date(2026, 11, 9),
        date(2026, 11, 11),
    ]


def test_partial_weekly_budget_is_not_borrowed_by_the_next_week() -> None:
    # 2.5 hours = 150 minutes: two 60-minute sessions, 30 minutes dropped.
    schedule = _schedule(weekly_hours=2.5)
    per_week = {}
    for session in schedule.sessions:
        per_week[session.week] = per_week.get(session.week, 0) + 1
    assert per_week == {1: 2, 2: 2}


def test_deadline_day_is_inclusive() -> None:
    schedule = _schedule(start_date=date(2026, 11, 2), deadline=date(2026, 11, 2))
    assert [session.date for session in schedule.sessions] == [date(2026, 11, 2)]


def test_topics_cycle_across_sessions() -> None:
    schedule = _schedule(
        weekly_hours=6,
        topics=["Chunking", "Embeddings", "Retrieval"],
    )
    assert [session.title for session in schedule.sessions][:4] == [
        "Chunking",
        "Embeddings",
        "Retrieval",
        "Chunking",
    ]


def test_no_topics_names_sessions_by_goal() -> None:
    schedule = _schedule()
    assert schedule.sessions[0].title == "Ship a RAG service — part 1"


def test_custom_weekday_set_is_respected() -> None:
    schedule = _schedule(
        start_date=date(2026, 11, 2),
        deadline=date(2026, 11, 8),
        weekly_hours=2,
        weekdays=(5, 6),
    )
    assert [session.date for session in schedule.sessions] == [
        date(2026, 11, 7),
        date(2026, 11, 8),
    ]
    assert schedule.weekdays == (5, 6)


def test_session_minutes_change_the_weekly_budget_shape() -> None:
    schedule = _schedule(weekly_hours=1, session_minutes=30)
    # 60 minutes a week is two 30-minute sessions; Friday is left empty.
    assert [session.duration_minutes for session in schedule.sessions] == [30] * 4
    assert [session.date for session in schedule.sessions] == [
        date(2026, 11, 2),
        date(2026, 11, 4),
        date(2026, 11, 9),
        date(2026, 11, 11),
    ]


def test_truncates_at_the_session_limit() -> None:
    schedule = build_schedule(
        goal="A decade of study",
        start_date=date(2026, 1, 5),
        deadline=date(2035, 12, 31),
        weekly_hours=60,
    )
    assert schedule.truncated
    assert schedule.total_sessions == MAX_SESSIONS


@pytest.mark.parametrize(
    "overrides",
    [
        {"goal": "   "},
        {"deadline": date(2026, 10, 1)},
        {"weekly_hours": 0},
        {"weekly_hours": -2},
        {"weekly_hours": 0.5},
        {"session_minutes": 1},
        {"session_minutes": 1000},
        {"weekdays": ()},
        {"weekdays": (7,)},
        {"start_date": date(2026, 11, 3), "deadline": date(2026, 11, 4), "weekdays": (0,)},
    ],
)
def test_invalid_parameters_raise_schedule_error(overrides) -> None:
    with pytest.raises(ScheduleError):
        _schedule(**overrides)


def test_goal_whitespace_is_normalized() -> None:
    schedule = _schedule(goal="  Ship   a RAG service ")
    assert schedule.sessions[0].title.startswith("Ship a RAG service — part")


def test_long_span_is_rejected_before_walking_days() -> None:
    with pytest.raises(ScheduleError, match="ten years"):
        build_schedule(
            goal="Too far",
            start_date=date(2026, 1, 1),
            deadline=date(2026, 1, 1) + timedelta(days=4000),
            weekly_hours=3,
        )
