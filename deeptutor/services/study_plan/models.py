"""Dataclasses for dated study plans.

A plan is deliberately small and deterministic: a goal, a deadline, weekly
hours, and the dated sessions a scheduler produced from them. The dataclasses
own serialization only — generation lives in :mod:`scheduler` and persistence
in :mod:`store`, so plan arithmetic stays testable without files or an LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

SessionStatus = Literal["pending", "done", "skipped"]

SESSION_STATUSES: tuple[SessionStatus, ...] = ("pending", "done", "skipped")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class StudyMaterial:
    """One thing a plan studies: a book, path, collection, or custom topic.

    ``kind`` names the owning feature and ``id`` its record there; ``title`` is
    always the owner's title at the time the plan was created, so a plan still
    reads sensibly after the source is renamed or removed.
    """

    kind: str
    id: str
    title: str
    detail: str = ""
    href: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "title": self.title,
            "detail": self.detail,
            "href": self.href,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> StudyMaterial | None:
        if not isinstance(raw, dict):
            return None
        kind = str(raw.get("kind") or "").strip()
        title = " ".join(str(raw.get("title") or "").split())
        if not kind or not title:
            return None
        return cls(
            kind=kind,
            id=str(raw.get("id") or "").strip(),
            title=title,
            detail=str(raw.get("detail") or "").strip(),
            href=str(raw.get("href") or "").strip(),
        )


@dataclass(slots=True)
class StudySession:
    """One dated study block the learner can mark done or skip."""

    id: str
    date: str
    week: int
    title: str
    duration_minutes: int
    status: SessionStatus = "pending"
    completed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "date": self.date,
            "week": self.week,
            "title": self.title,
            "duration_minutes": self.duration_minutes,
            "status": self.status,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> StudySession | None:
        if not isinstance(raw, dict):
            return None
        session_id = str(raw.get("id") or "").strip()
        day = str(raw.get("date") or "").strip()
        title = str(raw.get("title") or "").strip()
        if not session_id or not day or not title:
            return None
        status = str(raw.get("status") or "pending").strip()
        if status not in SESSION_STATUSES:
            return None
        try:
            week = int(raw.get("week") or 0)
            duration = int(raw.get("duration_minutes") or 0)
        except (TypeError, ValueError):
            return None
        return cls(
            id=session_id,
            date=day,
            week=week,
            title=title,
            duration_minutes=duration,
            status=status,  # type: ignore[arg-type]
            completed_at=str(raw.get("completed_at") or "").strip(),
        )


@dataclass(slots=True)
class StudyPlan:
    """A goal plus the dated sessions that reach it."""

    id: str
    goal: str
    start_date: str
    deadline: str
    weekly_hours: float
    session_minutes: int
    weekdays: list[int] = field(default_factory=list)
    materials: list[StudyMaterial] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    archived: bool = False
    sessions: list[StudySession] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.goal.strip()

    def find_session(self, session_id: str) -> StudySession | None:
        for session in self.sessions:
            if session.id == session_id:
                return session
        return None

    def progress(self) -> dict[str, int]:
        done = sum(1 for session in self.sessions if session.status == "done")
        skipped = sum(1 for session in self.sessions if session.status == "skipped")
        total = len(self.sessions)
        pending = total - done - skipped
        percent = round(done * 100 / total) if total else 0
        return {
            "total": total,
            "done": done,
            "skipped": skipped,
            "pending": pending,
            "percent_done": percent,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "start_date": self.start_date,
            "deadline": self.deadline,
            "weekly_hours": self.weekly_hours,
            "session_minutes": self.session_minutes,
            "weekdays": list(self.weekdays),
            "materials": [material.to_dict() for material in self.materials],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "archived": self.archived,
            "sessions": [session.to_dict() for session in self.sessions],
            "progress": self.progress(),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> StudyPlan | None:
        if not isinstance(raw, dict):
            return None
        plan_id = str(raw.get("id") or "").strip()
        goal = str(raw.get("goal") or "").strip()
        start_date = str(raw.get("start_date") or "").strip()
        deadline = str(raw.get("deadline") or "").strip()
        if not plan_id or not goal or not start_date or not deadline:
            return None
        try:
            weekly_hours = float(raw.get("weekly_hours") or 0)
            session_minutes = int(raw.get("session_minutes") or 0)
        except (TypeError, ValueError):
            return None
        weekdays: list[int] = []
        for item in raw.get("weekdays") or []:
            try:
                weekdays.append(int(item))
            except (TypeError, ValueError):
                return None
        topics = [str(item).strip() for item in raw.get("topics") or [] if str(item).strip()]
        materials: list[StudyMaterial] = []
        for item in raw.get("materials") or []:
            parsed = StudyMaterial.from_dict(item)
            if parsed is not None:
                materials.append(parsed)
        # Files written before materials existed carried only free-text topics.
        if not materials:
            materials = [StudyMaterial(kind="custom", id="", title=title) for title in topics]
        sessions: list[StudySession] = []
        for item in raw.get("sessions") or []:
            parsed = StudySession.from_dict(item)
            if parsed is None:
                return None
            sessions.append(parsed)
        return cls(
            id=plan_id,
            goal=goal,
            start_date=start_date,
            deadline=deadline,
            weekly_hours=weekly_hours,
            session_minutes=session_minutes,
            weekdays=weekdays,
            materials=materials,
            created_at=str(raw.get("created_at") or "").strip(),
            updated_at=str(raw.get("updated_at") or "").strip(),
            archived=bool(raw.get("archived", False)),
            sessions=sessions,
        )


@dataclass(slots=True)
class StudyPlanCollection:
    """The per-workspace plan file: every plan the learner has created."""

    version: int = 1
    updated_at: str = ""
    plans: list[StudyPlan] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.plans

    def active_plans(self) -> list[StudyPlan]:
        return [plan for plan in self.plans if not plan.archived]

    def find(self, plan_id: str) -> StudyPlan | None:
        for plan in self.plans:
            if plan.id == plan_id:
                return plan
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "updated_at": self.updated_at,
            "plans": [plan.to_dict() for plan in self.plans],
        }

    @classmethod
    def from_dict(cls, raw: Any) -> StudyPlanCollection:
        if not isinstance(raw, dict):
            return cls()
        plans: list[StudyPlan] = []
        for item in raw.get("plans") or []:
            parsed = StudyPlan.from_dict(item)
            if parsed is not None:
                plans.append(parsed)
        return cls(
            version=int(raw.get("version") or 1),
            updated_at=str(raw.get("updated_at") or "").strip(),
            plans=plans,
        )

    def touch(self) -> None:
        self.updated_at = utc_now()


__all__ = [
    "SESSION_STATUSES",
    "SessionStatus",
    "StudyMaterial",
    "StudyPlan",
    "StudyPlanCollection",
    "StudySession",
    "utc_now",
]
