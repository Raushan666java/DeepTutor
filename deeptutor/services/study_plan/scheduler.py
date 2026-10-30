"""Deterministic session scheduler for dated study plans.

Every session in a plan comes from this pure function, not from the model:
same goal, dates, and hours always produce the same sessions. That keeps the
schedule auditable ("why is this session on a Friday?") and lets the plan
logic be unit-tested without a database, a clock, or an LLM.

Budgeting rule: each calendar week gets ``weekly_hours * 60`` minutes of
budget. Eligible weekdays are visited in date order and a session is placed
whenever the remaining budget covers one whole session. Leftover minutes that
cannot fit a session are dropped rather than rolled over, so a week never
silently borrows from the next.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

DEFAULT_WEEKDAYS: tuple[int, ...] = (0, 2, 4)  # Monday, Wednesday, Friday
DEFAULT_SESSION_MINUTES = 60
MIN_SESSION_MINUTES = 5
MAX_SESSION_MINUTES = 480
MAX_SESSIONS = 200
MAX_SPAN_DAYS = 3650


class ScheduleError(ValueError):
    """Invalid plan parameters; the message is safe to show the learner."""


@dataclass(frozen=True, slots=True)
class ScheduledSession:
    date: date
    week: int
    title: str
    duration_minutes: int


@dataclass(frozen=True, slots=True)
class PlanSchedule:
    weekdays: tuple[int, ...]
    session_minutes: int
    sessions: tuple[ScheduledSession, ...] = ()
    weeks: int = 0
    total_minutes: int = 0
    truncated: bool = False

    @property
    def total_sessions(self) -> int:
        return len(self.sessions)

    @property
    def starts_on(self) -> date | None:
        return self.sessions[0].date if self.sessions else None

    @property
    def ends_on(self) -> date | None:
        return self.sessions[-1].date if self.sessions else None


def _normalize_weekdays(raw: Iterable[int]) -> tuple[int, ...]:
    try:
        values = {int(item) for item in raw}
    except (TypeError, ValueError) as exc:
        raise ScheduleError("Weekdays must be integers from 0 (Monday) to 6 (Sunday).") from exc
    if not values:
        raise ScheduleError("Pick at least one weekday for study sessions.")
    if any(value < 0 or value > 6 for value in values):
        raise ScheduleError("Weekdays must be between 0 (Monday) and 6 (Sunday).")
    return tuple(sorted(values))


def _normalize_session_minutes(raw: int) -> int:
    try:
        minutes = int(raw)
    except (TypeError, ValueError) as exc:
        raise ScheduleError("Session length must be a whole number of minutes.") from exc
    if minutes < MIN_SESSION_MINUTES or minutes > MAX_SESSION_MINUTES:
        raise ScheduleError(
            f"Session length must be between {MIN_SESSION_MINUTES} "
            f"and {MAX_SESSION_MINUTES} minutes."
        )
    return minutes


def _normalize_topics(raw: Sequence[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for item in raw:
        text = " ".join(str(item or "").split())
        if text and text not in seen:
            seen.append(text)
    return tuple(seen)


def build_schedule(
    *,
    goal: str,
    start_date: date,
    deadline: date,
    weekly_hours: float,
    session_minutes: int = DEFAULT_SESSION_MINUTES,
    weekdays: Sequence[int] = DEFAULT_WEEKDAYS,
    topics: Sequence[str] = (),
) -> PlanSchedule:
    """Lay out dated sessions between ``start_date`` and ``deadline`` (inclusive)."""
    goal_text = " ".join(str(goal or "").split())
    if not goal_text:
        raise ScheduleError("A study plan needs a goal.")
    if deadline < start_date:
        raise ScheduleError("The deadline must be on or after the start date.")
    if (deadline - start_date).days > MAX_SPAN_DAYS:
        raise ScheduleError("The deadline must be within ten years of the start date.")
    try:
        hours = float(weekly_hours)
    except (TypeError, ValueError) as exc:
        raise ScheduleError("Weekly hours must be a number.") from exc
    if hours <= 0:
        raise ScheduleError("Weekly hours must be greater than zero.")

    minutes = _normalize_session_minutes(session_minutes)
    weekday_set = set(_normalize_weekdays(weekdays))
    minutes_per_week = int(round(hours * 60))
    if minutes_per_week < minutes:
        raise ScheduleError(f"{hours:g} weekly hours cannot fit a single {minutes}-minute session.")

    topic_cycle = _normalize_topics(topics)
    budget: dict[date, int] = {}
    sessions: list[ScheduledSession] = []
    first_week = start_date - timedelta(days=start_date.weekday())
    day = start_date
    truncated = False

    while day <= deadline:
        if day.weekday() in weekday_set:
            week_start = day - timedelta(days=day.weekday())
            remaining = budget.setdefault(week_start, minutes_per_week)
            if remaining >= minutes:
                index = len(sessions)
                title = (
                    topic_cycle[index % len(topic_cycle)]
                    if topic_cycle
                    else f"{goal_text} — part {index + 1}"
                )
                sessions.append(
                    ScheduledSession(
                        date=day,
                        week=(week_start - first_week).days // 7 + 1,
                        title=title,
                        duration_minutes=minutes,
                    )
                )
                budget[week_start] = remaining - minutes
                if len(sessions) >= MAX_SESSIONS:
                    truncated = day < deadline
                    break
        day += timedelta(days=1)

    if not sessions:
        raise ScheduleError(
            "None of the chosen weekdays fall inside the plan window, "
            "or the hours are too low for one session."
        )

    weeks = len({session.week for session in sessions})
    return PlanSchedule(
        weekdays=tuple(sorted(weekday_set)),
        session_minutes=minutes,
        sessions=tuple(sessions),
        weeks=weeks,
        total_minutes=sum(session.duration_minutes for session in sessions),
        truncated=truncated,
    )


__all__ = [
    "DEFAULT_SESSION_MINUTES",
    "DEFAULT_WEEKDAYS",
    "MAX_SESSIONS",
    "MAX_SESSION_MINUTES",
    "MAX_SPAN_DAYS",
    "MIN_SESSION_MINUTES",
    "PlanSchedule",
    "ScheduleError",
    "ScheduledSession",
    "build_schedule",
]
