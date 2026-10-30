"""Dated study plans: create, read, mark sessions, archive, list materials.

The schedule is produced by ``deeptutor.services.study_plan`` — this router is
a thin, validating adapter. Every response is workspace-scoped through the
active ``PathService``; a corrupt plan file returns 409 and is left untouched.

Materials are references to records owned by other features (a book id, a
mastery path id, …). The request carries only ``kind`` + ``id``; the title is
resolved server-side from the owning feature, so a stale tab cannot pin a
renamed book under its old name. Unknown references are a 400, never a silent
drop.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from deeptutor.services.study_plan import (
    MATERIAL_KINDS,
    StudyMaterial,
    StudyPlan,
    discover,
    get_study_plan_store,
    parse_day,
    resolve,
)
from deeptutor.services.study_plan.store import MAX_TOPICS

router = APIRouter()

Goal = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
DayText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=10)]
Topic = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
MaterialKind = Literal[
    "knowledge",
    "book",
    "reading",
    "course",
    "mastery",
    "practice",
    "notebook",
    "document",
    "custom",
]
SessionStatus = Literal["pending", "done", "skipped"]


class SessionOut(BaseModel):
    id: str
    date: str
    week: int
    title: str
    duration_minutes: int
    status: SessionStatus
    completed_at: str


class MaterialOut(BaseModel):
    kind: str
    id: str
    title: str
    detail: str
    href: str


class MaterialKindOut(BaseModel):
    kind: str
    label: str


class MaterialUnavailableOut(BaseModel):
    kind: str
    reason: str


class MaterialListOut(BaseModel):
    materials: list[MaterialOut]
    unavailable: list[MaterialUnavailableOut]
    kinds: list[MaterialKindOut]


class ProgressOut(BaseModel):
    total: int
    done: int
    skipped: int
    pending: int
    percent_done: int


class PlanOut(BaseModel):
    id: str
    goal: str
    start_date: str
    deadline: str
    weekly_hours: float
    session_minutes: int
    weekdays: list[int]
    materials: list[MaterialOut]
    created_at: str
    updated_at: str
    archived: bool
    sessions: list[SessionOut]
    progress: ProgressOut


class PlanOverview(BaseModel):
    version: int
    updated_at: str
    today: str
    plans: list[PlanOut]
    injected_context: str


class MaterialRef(BaseModel):
    """A pointer to a study material, resolved server-side to a title."""

    model_config = ConfigDict(extra="forbid")

    kind: MaterialKind
    id: str = Field(default="", max_length=200)
    title: str = Field(default="", max_length=120)


class CreatePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: Goal
    deadline: DayText
    weekly_hours: float = Field(gt=0, le=60)
    start_date: DayText | None = None
    session_minutes: int = Field(default=60, ge=5, le=480)
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    materials: list[MaterialRef] = Field(default_factory=list, max_length=MAX_TOPICS)
    #: Deprecated alias kept for older clients: each entry becomes a custom material.
    topics: list[Topic] = Field(default_factory=list, max_length=MAX_TOPICS)


class SessionStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: SessionStatus


class PlanPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    archived: bool


def _store():
    return get_study_plan_store()


def _day(value: str, field: str) -> date:
    try:
        return parse_day(value)
    except ValueError as exc:
        raise HTTPException(400, f"{field} must be a date like 2026-11-30.") from exc


def _plan_payload(plan: StudyPlan) -> dict[str, Any]:
    return plan.to_dict()


def _resolve_materials(
    refs: list[MaterialRef],
    legacy_topics: list[str],
) -> list[StudyMaterial]:
    """Turn request refs into canonical materials, or fail the request."""
    library: list[tuple[str, str]] = []
    custom: list[StudyMaterial] = []
    for ref in refs:
        if ref.kind == "custom":
            title = " ".join((ref.title or ref.id).split())
            if not title:
                raise HTTPException(400, "A custom material needs a title.")
            custom.append(StudyMaterial(kind="custom", id="", title=title))
            continue
        material_id = ref.id.strip()
        if not material_id:
            raise HTTPException(400, f"A {ref.kind} material needs an id.")
        library.append((ref.kind, material_id))
    for title in legacy_topics:
        custom.append(StudyMaterial(kind="custom", id="", title=title))
    resolved, missing = resolve(library)
    if missing:
        raise HTTPException(
            400,
            "These materials no longer exist: "
            + ", ".join(missing)
            + ". Refresh the picker and choose again.",
        )
    return resolved + custom


@router.get("", response_model=PlanOverview)
def overview():
    try:
        return _store().overview()
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail="Study plans could not be read. The original file has been preserved.",
        ) from exc


@router.get("/materials", response_model=MaterialListOut)
def list_materials(query: str = "", kinds: str = ""):
    """List the learner's books, paths, collections, and so on.

    Declared before ``/{plan_id}`` so "materials" is never read as a plan id.
    """
    wanted = [part.strip() for part in kinds.split(",") if part.strip()] or None
    if wanted:
        unknown = sorted({kind for kind in wanted if kind not in MATERIAL_KINDS})
        if unknown:
            raise HTTPException(
                400,
                "Unknown material kind(s): "
                + ", ".join(unknown)
                + ". Known kinds: "
                + ", ".join(MATERIAL_KINDS)
                + ".",
            )
    return discover(query=query, kinds=wanted).to_dict()


@router.post("", response_model=PlanOut, status_code=201)
def create_plan(payload: CreatePlan):
    deadline = _day(payload.deadline, "deadline")
    start_date = _day(payload.start_date, "start_date") if payload.start_date else None
    if any(day < 0 or day > 6 for day in payload.weekdays):
        raise HTTPException(400, "weekdays must be numbers from 0 (Mon) to 6 (Sun).")
    weekdays = sorted(set(payload.weekdays))
    materials = _resolve_materials(payload.materials, payload.topics)
    try:
        result = _store().create_plan(
            goal=payload.goal,
            deadline=deadline,
            weekly_hours=payload.weekly_hours,
            start_date=start_date,
            session_minutes=payload.session_minutes,
            weekdays=weekdays or (0, 2, 4),
            materials=materials,
        )
    except OSError as exc:
        raise HTTPException(409, "Study plans could not be saved.") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not result.accepted or result.plan is None:
        # Rejected scheduling parameters are a client error, not a server fault.
        raise HTTPException(400, result.message)
    return _plan_payload(result.plan)


@router.get("/{plan_id}", response_model=PlanOut)
def get_plan(plan_id: str):
    try:
        collection = _store().load()
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail="Study plans could not be read. The original file has been preserved.",
        ) from exc
    plan = collection.find(plan_id)
    if plan is None:
        raise HTTPException(404, "Study plan not found.")
    return _plan_payload(plan)


@router.post("/{plan_id}/sessions/{session_id}/status", response_model=PlanOut)
def set_session_status(plan_id: str, session_id: str, payload: SessionStatusUpdate):
    try:
        result = _store().set_session_status(plan_id, session_id, payload.status)
    except KeyError as exc:
        missing = str(exc.args[0] if exc.args else "id")
        raise HTTPException(404, f"No plan or session with id {missing!r}.") from exc
    except OSError as exc:
        raise HTTPException(409, "Study plans could not be updated.") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if result.plan is None:
        raise HTTPException(404, "Study plan not found.")
    return _plan_payload(result.plan)


@router.patch("/{plan_id}", response_model=PlanOut)
def update_plan(plan_id: str, payload: PlanPatch):
    try:
        result = _store().set_plan_archived(plan_id, payload.archived)
    except KeyError as exc:
        raise HTTPException(404, "Study plan not found.") from exc
    except OSError as exc:
        raise HTTPException(409, "Study plans could not be updated.") from exc
    if result.plan is None:
        raise HTTPException(404, "Study plan not found.")
    return _plan_payload(result.plan)


__all__ = ["router"]
