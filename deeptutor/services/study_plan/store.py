"""File-backed study-plan store (atomic JSON under the active workspace).

Follows the learning journal's contract: the path resolves through the active
``PathService`` (so a workspace scope owns its own plans), writes are atomic,
mutations are serialized across threads and processes, and a corrupt file is
reported rather than silently replaced with an empty aggregate.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
import json
import logging
import os
from pathlib import Path
import sys
import threading

from deeptutor.services.file_io import atomic_write_json
from deeptutor.services.path_service import get_path_service
from deeptutor.services.study_plan.models import (
    SESSION_STATUSES,
    StudyMaterial,
    StudyPlan,
    StudyPlanCollection,
    StudySession,
)
from deeptutor.services.study_plan.scheduler import (
    DEFAULT_SESSION_MINUTES,
    DEFAULT_WEEKDAYS,
    ScheduleError,
    build_schedule,
)

logger = logging.getLogger(__name__)

PLAN_VERSION = 1
MAX_PLANS = 50
MAX_GOAL_CHARS = 200
MAX_TOPIC_CHARS = 120
MAX_TOPICS = 20
MAX_INJECTED_PLANS = 3

WEEKDAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _clip(text: str, limit: int) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "…"


def _normalize_key(*parts: object) -> str:
    return " ".join(" ".join(str(part) for part in parts).split()).casefold()


def _weekday_labels(weekdays: Sequence[int]) -> str:
    labels = [WEEKDAY_LABELS[day] for day in sorted(set(weekdays)) if 0 <= day <= 6]
    return "/".join(labels) or "no days"


@dataclass(frozen=True, slots=True)
class PlanWriteResult:
    accepted: bool
    message: str
    collection: StudyPlanCollection
    plan: StudyPlan | None = None
    deduplicated: bool = False


class StudyPlanStore:
    """Stateless facade; path resolves via the active PathService."""

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}

    def plans_path(self) -> Path:
        return get_path_service().get_study_plan_file()

    def load(self) -> StudyPlanCollection:
        path = self.plans_path()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return StudyPlanCollection(version=PLAN_VERSION)
        if not isinstance(raw, dict) or raw.get("version", 1) != PLAN_VERSION:
            raise ValueError("Invalid or unsupported study plan file; original preserved")
        if not isinstance(raw.get("plans", []), list):
            raise ValueError("Invalid study plan list; original preserved")
        for item in raw.get("plans", []):
            if StudyPlan.from_dict(item) is None:
                raise ValueError("Invalid study plan entry; original preserved")
        collection = StudyPlanCollection.from_dict(raw)
        collection.version = PLAN_VERSION
        return collection

    def overview(self, *, today: date | None = None) -> dict:
        """Read-only snapshot for the panel and the REST route."""
        collection = self.load()
        snapshot = collection.to_dict()
        snapshot["today"] = (today or date.today()).isoformat()
        snapshot["injected_context"] = self.injection_markdown(collection=collection, today=today)
        return snapshot

    def injection_markdown(
        self,
        *,
        collection: StudyPlanCollection | None = None,
        today: date | None = None,
    ) -> str:
        """The per-turn slice: active plans, today's sessions, progress."""
        collection = collection if collection is not None else self.load()
        plans = collection.active_plans()
        if not plans:
            return ""
        day = (today or date.today()).isoformat()
        lines = ["# Study plans (dated sessions the learner committed to)", ""]
        for plan in plans[:MAX_INJECTED_PLANS]:
            lines.extend(self._plan_lines(plan, today=day))
            lines.append("")
        hidden = len(plans) - MAX_INJECTED_PLANS
        if hidden > 0:
            lines.append(f"_{hidden} more plan(s) available via `study_plan_status`._")
        lines.append("_This plan schedules study; use Mastery Path for scored curriculum gates._")
        return "\n".join(lines).rstrip() + "\n"

    def plan_markdown(self, plan: StudyPlan, *, today: date | None = None) -> str:
        day = (today or date.today()).isoformat()
        lines = ["# Study plan", "", *self._plan_lines(plan, today=day), ""]
        lines.append("| Session | Date | Week | Topic | Status |")
        lines.append("| --- | --- | --- | --- | --- |")
        for session in plan.sessions:
            lines.append(
                f"| `{session.id}` | {session.date} | {session.week} | "
                f"{session.title} | {session.status} |"
            )
        return "\n".join(lines).rstrip() + "\n"

    def status_markdown(self, *, today: date | None = None) -> str:
        collection = self.load()
        plans = collection.active_plans()
        if not plans:
            return (
                "(No study plan yet. When the learner names a goal and a deadline, "
                "call study_plan_create with the goal, deadline, and weekly hours; "
                "then mark sessions with study_plan_mark as they finish. Use Mastery "
                "Path for scored curriculum progress instead.)\n"
            )
        day = (today or date.today()).isoformat()
        lines = ["# Study plans", ""]
        for plan in plans:
            lines.extend(self._plan_lines(plan, today=day))
            lines.append("")
        lines.append(
            "_Mark sessions with `study_plan_mark`; this plan schedules, Mastery Path scores._"
        )
        return "\n".join(lines).rstrip() + "\n"

    def _plan_lines(self, plan: StudyPlan, *, today: str) -> list[str]:
        progress = plan.progress()
        lines = [
            f"## {plan.goal}  (`{plan.id}`)",
            (
                f"- Deadline {plan.deadline} · {plan.weekly_hours:g} h/week · "
                f"{_weekday_labels(plan.weekdays)} · {progress['total']} sessions · "
                f"{progress['done']} done · {progress['percent_done']}%"
            ),
        ]
        if plan.materials:
            names = ", ".join(material.title for material in plan.materials[:5])
            extra = len(plan.materials) - 5
            suffix = f" (+{extra} more)" if extra > 0 else ""
            lines.append(f"- Materials: {names}{suffix}")
        today_sessions = [session for session in plan.sessions if session.date == today]
        if today_sessions:
            for session in today_sessions:
                lines.append(
                    f"- Today: `{session.id}` {session.title} "
                    f"({session.duration_minutes} min, {session.status})"
                )
        else:
            lines.append("- Today: no session scheduled")
        upcoming = next(
            (
                session
                for session in plan.sessions
                if session.date > today and session.status == "pending"
            ),
            None,
        )
        if upcoming is not None:
            lines.append(
                f"- Next: `{upcoming.id}` {upcoming.date} {upcoming.title} "
                f"({upcoming.duration_minutes} min)"
            )
        if plan.archived:
            lines.append("- Archived")
        return lines

    def create_plan(
        self,
        *,
        goal: str,
        deadline: date,
        weekly_hours: float,
        start_date: date | None = None,
        session_minutes: int = DEFAULT_SESSION_MINUTES,
        weekdays: Sequence[int] = DEFAULT_WEEKDAYS,
        materials: Sequence[StudyMaterial] = (),
    ) -> PlanWriteResult:
        goal_c = _clip(goal, MAX_GOAL_CHARS)
        if not goal_c:
            return PlanWriteResult(
                accepted=False,
                message="A study plan needs a non-empty goal.",
                collection=self.load(),
            )
        materials_c: list[StudyMaterial] = []
        for item in materials:
            title = _clip(item.title, MAX_TOPIC_CHARS)
            if not title:
                continue
            candidate = StudyMaterial(
                kind=_clip(item.kind, 32) or "custom",
                id=_clip(item.id, 200),
                title=title,
                detail=_clip(item.detail, 160),
                href=_clip(item.href, 400),
            )
            if candidate not in materials_c:
                materials_c.append(candidate)
        materials_c = materials_c[:MAX_TOPICS]
        first_day = start_date or date.today()
        try:
            schedule = build_schedule(
                goal=goal_c,
                start_date=first_day,
                deadline=deadline,
                weekly_hours=weekly_hours,
                session_minutes=session_minutes,
                weekdays=weekdays,
                topics=[material.title for material in materials_c],
            )
        except ScheduleError as exc:
            return PlanWriteResult(
                accepted=False,
                message=f"{exc} Nothing was saved.",
                collection=self.load(),
            )

        material_key = "|".join(
            f"{material.kind}:{material.id or material.title}" for material in materials_c
        )
        key = _normalize_key(
            goal_c,
            deadline.isoformat(),
            first_day.isoformat(),
            f"{float(weekly_hours):g}",
            str(schedule.session_minutes),
            ",".join(str(day) for day in schedule.weekdays),
            material_key,
        )

        def mutate(collection: StudyPlanCollection) -> PlanWriteResult:
            for existing in collection.active_plans():
                existing_key = _normalize_key(
                    existing.goal,
                    existing.deadline,
                    existing.start_date,
                    f"{float(existing.weekly_hours):g}",
                    str(existing.session_minutes),
                    ",".join(str(day) for day in existing.weekdays),
                    "|".join(
                        f"{material.kind}:{material.id or material.title}"
                        for material in existing.materials
                    ),
                )
                if existing_key == key:
                    return PlanWriteResult(
                        accepted=True,
                        message=(f"plan already exists (id={existing.id}); nothing new was saved."),
                        collection=collection,
                        plan=existing,
                        deduplicated=True,
                    )
            if len(collection.plans) >= MAX_PLANS:
                return PlanWriteResult(
                    accepted=False,
                    message=(
                        f"At most {MAX_PLANS} plans are kept; archive an old plan "
                        "before creating another."
                    ),
                    collection=collection,
                )
            plan_id = self._next_plan_id(collection)
            sessions = [
                StudySession(
                    id=f"s{index:03d}",
                    date=entry.date.isoformat(),
                    week=entry.week,
                    title=entry.title,
                    duration_minutes=entry.duration_minutes,
                )
                for index, entry in enumerate(schedule.sessions, start=1)
            ]
            plan = StudyPlan(
                id=plan_id,
                goal=goal_c,
                start_date=first_day.isoformat(),
                deadline=deadline.isoformat(),
                weekly_hours=float(weekly_hours),
                session_minutes=schedule.session_minutes,
                weekdays=list(schedule.weekdays),
                materials=materials_c,
                created_at=collection.updated_at,
                updated_at=collection.updated_at,
                sessions=sessions,
            )
            collection.plans.append(plan)
            note = " (schedule truncated at the session limit)" if schedule.truncated else ""
            return PlanWriteResult(
                accepted=True,
                message=(
                    f"plan {plan_id} created: {len(sessions)} sessions across "
                    f"{schedule.weeks} week(s) to {plan.deadline}{note}."
                ),
                collection=collection,
                plan=plan,
            )

        return self._mutate(mutate)

    def set_session_status(
        self,
        plan_id: str,
        session_id: str,
        status: str,
    ) -> PlanWriteResult:
        wanted = str(status or "").strip().lower()
        if wanted not in SESSION_STATUSES:
            raise ValueError(f"status must be one of {', '.join(SESSION_STATUSES)}; got {status!r}")

        def mutate(collection: StudyPlanCollection) -> PlanWriteResult:
            plan = collection.find(plan_id)
            if plan is None:
                raise KeyError(plan_id)
            session = plan.find_session(session_id)
            if session is None:
                raise KeyError(session_id)
            session.status = wanted  # type: ignore[assignment]
            session.completed_at = collection.updated_at if wanted == "done" else ""
            plan.updated_at = collection.updated_at
            return PlanWriteResult(
                accepted=True,
                message=(
                    f"session {session_id} in {plan_id} marked {wanted} "
                    f"({plan.progress()['percent_done']}% of sessions done)."
                ),
                collection=collection,
                plan=plan,
            )

        return self._mutate(mutate)

    def set_plan_archived(self, plan_id: str, archived: bool = True) -> PlanWriteResult:
        def mutate(collection: StudyPlanCollection) -> PlanWriteResult:
            plan = collection.find(plan_id)
            if plan is None:
                raise KeyError(plan_id)
            plan.archived = bool(archived)
            plan.updated_at = collection.updated_at
            state = "archived" if plan.archived else "restored"
            return PlanWriteResult(
                accepted=True,
                message=f"plan {plan_id} {state}.",
                collection=collection,
                plan=plan,
            )

        return self._mutate(mutate)

    def _next_plan_id(self, collection: StudyPlanCollection) -> str:
        highest = 0
        for plan in collection.plans:
            suffix = plan.id.removeprefix("sp_")
            if suffix.isdigit():
                highest = max(highest, int(suffix))
        return f"sp_{highest + 1:04d}"

    def _mutate(self, mutator) -> PlanWriteResult:  # noqa: ANN001
        path = self.plans_path()
        lock = self._lock_for(path)
        with lock, self._file_lock(path):
            collection = self.load()
            collection.touch()
            result = mutator(collection)
            if not result.accepted:
                return result
            if not result.deduplicated:
                atomic_write_json(path, result.collection.to_dict())
            return result

    def _lock_for(self, path: Path) -> threading.Lock:
        key = str(path.resolve())
        lock = self._locks.get(key)
        if lock is None:
            lock = threading.Lock()
            self._locks[key] = lock
        return lock

    @staticmethod
    @contextmanager
    def _file_lock(path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_suffix(".lock").open("a+b") as handle:
            handle.seek(0)
            if sys.platform == "win32":
                import msvcrt

                # Size check instead of read(): msvcrt locks byte ranges, and a
                # read on a handle another process is holding is not reliable on
                # Windows. The byte only has to exist to be locked.
                if os.fstat(handle.fileno()).st_size == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                handle.seek(0)
                if sys.platform == "win32":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


_STORE: StudyPlanStore | None = None


def get_study_plan_store() -> StudyPlanStore:
    global _STORE
    if _STORE is None:
        _STORE = StudyPlanStore()
    return _STORE


def parse_day(value: str) -> date:
    """Parse a wire date (``YYYY-MM-DD``) or raise ``ValueError`` for the API."""
    return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()


__all__ = [
    "MAX_PLANS",
    "PLAN_VERSION",
    "PlanWriteResult",
    "StudyPlanStore",
    "get_study_plan_store",
    "parse_day",
]
