"""Chat tools for dated study plans (#study-plan).

Four tools, mirroring the journal's read/write split: one lists the materials a
plan can study, one reads plans and today's work, one creates a dated schedule
from a goal, materials, and a deadline, and one marks a session done or
skipped. Scheduling itself is deterministic — the model supplies parameters,
:mod:`deeptutor.services.study_plan.scheduler` decides every session date.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from deeptutor.core.tool_protocol import BaseTool, ToolDefinition, ToolParameter, ToolResult
from deeptutor.services.study_plan.models import StudyMaterial

STUDY_PLAN_TOOL_NAMES: tuple[str, ...] = (
    "study_plan_materials",
    "study_plan_status",
    "study_plan_create",
    "study_plan_mark",
)

_WEEKDAY_NAMES = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "weds": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}


def _parse_day(value: Any) -> date:
    text = str(value or "").strip()
    if not text:
        raise ValueError("a date is required (YYYY-MM-DD)")
    return datetime.strptime(text[:10], "%Y-%m-%d").date()


def _parse_weekdays(value: Any) -> list[int] | None:
    """Accept "Mon,Wed,Fri", "0,2,4", or a list of either; None means default."""
    if value is None:
        return None
    items: list[Any]
    if isinstance(value, list | tuple | set):
        items = list(value)
    else:
        items = [part for part in str(value).replace(";", ",").split(",")]
    days: list[int] = []
    for item in items:
        token = str(item).strip().lower()
        if not token:
            continue
        if token.isdigit():
            day = int(token)
        elif token in _WEEKDAY_NAMES:
            day = _WEEKDAY_NAMES[token]
        else:
            raise ValueError(f"unknown weekday {token!r}; use names like Mon or numbers 0-6")
        if day < 0 or day > 6:
            raise ValueError("weekday numbers run from 0 (Monday) to 6 (Sunday)")
        if day not in days:
            days.append(day)
    return days or None


def _split_topics(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list | tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    parts: list[str] = []
    for line in str(value).splitlines():
        parts.extend(chunk for chunk in line.split(";"))
    return [part.strip() for part in parts if part.strip()]


def _material_kinds() -> tuple[str, ...]:
    from deeptutor.services.study_plan.materials import MATERIAL_KINDS

    return MATERIAL_KINDS


def _split_materials(value: Any) -> tuple[list[tuple[str, str]], list[str]]:
    """Split ``kind:id`` references from free-text topics.

    ``"book:book_abc"`` is a reference to an existing record; ``"Chapter 3"``
    is a custom topic owned by the plan itself.
    """
    refs: list[tuple[str, str]] = []
    topics: list[str] = []
    if value is None:
        return refs, topics
    if isinstance(value, list | tuple):
        items = [str(item).strip() for item in value]
    else:
        items = [line.strip() for line in str(value).splitlines()]
    kinds = _material_kinds()
    for text in items:
        if not text:
            continue
        head, sep, rest = text.partition(":")
        kind = head.strip().lower()
        if sep and kind in kinds and rest.strip():
            refs.append((kind, rest.strip()))
        else:
            topics.append(text)
    return refs, topics


class StudyPlanMaterialsTool(BaseTool):
    """List the learner's books, paths, collections, and other materials."""

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="study_plan_materials",
            description=(
                "List the study materials a plan can schedule: knowledge bases, "
                "books, reading collections, course chapters, mastery paths, "
                "practice banks, notebooks, and Co-Writer documents. Call this "
                "before creating a plan from the learner's own content. Each row "
                "reports the reference to pass in "
                "study_plan_create.materials, e.g. 'book:book_abc'. A kind that is "
                "unavailable on this install is reported, not fatal."
            ),
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="Optional text to match against material titles.",
                    required=False,
                ),
                ToolParameter(
                    name="kind",
                    type="string",
                    description=(
                        "Optional single kind: knowledge | book | reading | "
                        "course | mastery | practice | notebook | document."
                    ),
                    required=False,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        from deeptutor.services.study_plan import KIND_LABELS, discover

        query = str(kwargs.get("query") or "").strip()
        kind = str(kwargs.get("kind") or "").strip().lower()
        if kind and kind not in _material_kinds():
            return ToolResult(
                content=(
                    f"Error: unknown material kind {kind!r}. Use one of "
                    + ", ".join(_material_kinds())
                    + "."
                ),
                success=False,
            )
        discovery = discover(query=query, kinds=[kind] if kind else None)
        lines: list[str] = []
        for material_kind in _material_kinds():
            rows = [item for item in discovery.materials if item.kind == material_kind]
            if not rows:
                continue
            lines.append(f"{KIND_LABELS[material_kind]}:")
            for item in rows:
                detail = f" ({item.detail})" if item.detail else ""
                ref = f"{item.kind}:{item.id}" if item.id else item.title
                lines.append(f"- {ref} — {item.title}{detail}")
        if discovery.unavailable:
            lines.append("Unavailable on this install:")
            for row in discovery.unavailable:
                lines.append(f"- {row['kind']}: {row['reason']}")
        if not lines:
            content = (
                "No study materials found"
                + (f" matching {query!r}" if query else "")
                + ". The learner can still plan a custom topic from a goal."
            )
        else:
            lines.insert(
                0,
                "Pass these references in study_plan_create.materials "
                "(e.g. book:book_abc). Anything else is treated as a custom topic.",
            )
            content = "\n".join(lines)
        return ToolResult(
            content=content,
            metadata={
                "count": len(discovery.materials),
                "unavailable": discovery.unavailable,
            },
        )


class StudyPlanStatusTool(BaseTool):
    """Read active plans, today's sessions, and per-plan progress."""

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="study_plan_status",
            description=(
                "Read the learner's dated study plans: goal, deadline, weekly "
                "hours, materials, today's sessions, next session, and progress. "
                "Call this before answering 'what should I study today?' or before "
                "creating a plan that may already exist. This schedules study "
                "time; Mastery Path (mastery_status) scores curriculum progress."
            ),
            parameters=[],
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        from deeptutor.services.study_plan import get_study_plan_store

        _ = kwargs
        try:
            text = get_study_plan_store().status_markdown()
        except (OSError, ValueError):
            return ToolResult(
                content="Study plans could not be read. Original data preserved.",
                success=False,
            )
        return ToolResult(content=text, metadata={"char_count": len(text)})


class StudyPlanCreateTool(BaseTool):
    """Turn a goal, materials, and a deadline into dated, markable sessions."""

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="study_plan_create",
            description=(
                "Create a dated study plan: a goal, materials, a deadline, and the "
                "weekly hours the learner can actually commit. The scheduler lays "
                "out sessions between the start date and the deadline; every "
                "session is markable done/skipped. Confirm the goal, deadline, and "
                "weekly hours with the learner first, then create it once. Pass "
                "materials as refs from study_plan_materials ('book:book_abc'); "
                "free text becomes a custom topic. Without materials, sessions are "
                "numbered parts of the goal."
            ),
            parameters=[
                ToolParameter(
                    name="goal",
                    type="string",
                    description="What the learner wants to be able to do by the deadline.",
                    required=True,
                ),
                ToolParameter(
                    name="deadline",
                    type="string",
                    description="Last study day, YYYY-MM-DD.",
                    required=True,
                ),
                ToolParameter(
                    name="weekly_hours",
                    type="number",
                    description="Hours per week the learner can study (e.g. 6).",
                    required=True,
                    minimum=0.1,
                    maximum=60,
                ),
                ToolParameter(
                    name="start_date",
                    type="string",
                    description="First study day, YYYY-MM-DD. Defaults to today.",
                    required=False,
                ),
                ToolParameter(
                    name="session_minutes",
                    type="integer",
                    description="Length of one session in minutes (default 60).",
                    required=False,
                    default=60,
                    minimum=5,
                    maximum=480,
                ),
                ToolParameter(
                    name="weekdays",
                    type="string",
                    description=(
                        "Preferred days, e.g. 'Mon,Wed,Fri' or '0,2,4'. Defaults to Mon/Wed/Fri."
                    ),
                    required=False,
                ),
                ToolParameter(
                    name="materials",
                    type="array",
                    description=(
                        "Study materials, one per session (cycles when the plan has "
                        "more sessions than materials). Use refs from "
                        "study_plan_materials such as 'book:book_abc', "
                        "'mastery:python-basics', 'knowledge:notes'. Free text is a "
                        "custom topic."
                    ),
                    required=False,
                    items={"type": "string"},
                    max_items=20,
                ),
                ToolParameter(
                    name="topics",
                    type="array",
                    description=("Deprecated alias for free-text custom topics; prefer materials."),
                    required=False,
                    items={"type": "string"},
                    max_items=20,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        from deeptutor.services.study_plan import get_study_plan_store, resolve

        try:
            deadline = _parse_day(kwargs.get("deadline"))
            start_date = _parse_day(kwargs.get("start_date")) if kwargs.get("start_date") else None
            weekdays = _parse_weekdays(kwargs.get("weekdays"))
            weekly_hours = float(kwargs.get("weekly_hours") or 0)
            session_minutes = int(kwargs.get("session_minutes") or 60)
        except (TypeError, ValueError) as exc:
            return ToolResult(content=f"Error: {exc}.", success=False)

        refs, free_text = _split_materials(kwargs.get("materials"))
        materials: list[StudyMaterial] = []
        if refs:
            resolved, missing = resolve(refs)
            if missing:
                return ToolResult(
                    content=(
                        "Error: these materials no longer exist: "
                        + ", ".join(missing)
                        + ". Call study_plan_materials to list what is available."
                    ),
                    success=False,
                )
            materials.extend(resolved)
        for title in [*free_text, *_split_topics(kwargs.get("topics"))]:
            materials.append(StudyMaterial(kind="custom", id="", title=title))

        store = get_study_plan_store()
        try:
            result = store.create_plan(
                goal=str(kwargs.get("goal") or ""),
                deadline=deadline,
                weekly_hours=weekly_hours,
                start_date=start_date,
                session_minutes=session_minutes,
                weekdays=weekdays if weekdays is not None else (0, 2, 4),
                materials=materials,
            )
        except (OSError, ValueError):
            return ToolResult(
                content="Study plan could not be saved. Original data preserved.",
                success=False,
            )
        metadata: dict[str, Any] = {"deduplicated": result.deduplicated}
        if result.plan is not None:
            metadata.update(
                {
                    "plan_id": result.plan.id,
                    "sessions": len(result.plan.sessions),
                    "materials": [item.title for item in result.plan.materials],
                    "progress": result.plan.progress(),
                }
            )
        return ToolResult(content=result.message, success=result.accepted, metadata=metadata)


class StudyPlanMarkTool(BaseTool):
    """Mark one session of a plan done or skipped."""

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="study_plan_mark",
            description=(
                "Mark one study session done or skipped, or reset it to pending. "
                "Use the plan id and session id reported by study_plan_status or "
                "study_plan_create. Only mark what the learner says actually "
                "happened; a skipped session stays visible so the plan stays honest."
            ),
            parameters=[
                ToolParameter(
                    name="plan_id",
                    type="string",
                    description="Plan id such as sp_0001.",
                    required=True,
                ),
                ToolParameter(
                    name="session_id",
                    type="string",
                    description="Session id such as s003.",
                    required=True,
                ),
                ToolParameter(
                    name="status",
                    type="string",
                    description="done | skipped | pending.",
                    enum=["done", "skipped", "pending"],
                    required=True,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        from deeptutor.services.study_plan import get_study_plan_store

        plan_id = str(kwargs.get("plan_id") or "").strip()
        session_id = str(kwargs.get("session_id") or "").strip()
        status = str(kwargs.get("status") or "").strip().lower()
        if not plan_id or not session_id:
            return ToolResult(content="Error: plan_id and session_id are required.", success=False)
        try:
            result = get_study_plan_store().set_session_status(plan_id, session_id, status)
        except KeyError as exc:
            missing = str(exc.args[0] if exc.args else "id")
            return ToolResult(
                content=f"Error: no plan or session with id {missing!r}.", success=False
            )
        except ValueError as exc:
            return ToolResult(content=f"Error: {exc}.", success=False)
        except OSError:
            return ToolResult(
                content="Study plan could not be updated. Original data preserved.",
                success=False,
            )
        return ToolResult(
            content=result.message,
            success=result.accepted,
            metadata={
                "plan_id": plan_id,
                "session_id": session_id,
                "status": status,
                "progress": result.plan.progress() if result.plan else {},
            },
        )


__all__ = [
    "STUDY_PLAN_TOOL_NAMES",
    "StudyPlanCreateTool",
    "StudyPlanMarkTool",
    "StudyPlanMaterialsTool",
    "StudyPlanStatusTool",
]
