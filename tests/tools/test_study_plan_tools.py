"""Tests for the study-plan chat tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deeptutor.capabilities.study_plan.tools import (
    STUDY_PLAN_TOOL_NAMES,
    StudyPlanCreateTool,
    StudyPlanMarkTool,
    StudyPlanMaterialsTool,
    StudyPlanStatusTool,
)
from deeptutor.services.path_service import PathService
from deeptutor.services.study_plan import materials as materials_service
from deeptutor.services.study_plan.materials import MaterialDiscovery
from deeptutor.services.study_plan.models import StudyMaterial
from deeptutor.services.study_plan.store import StudyPlanStore

BOOK = StudyMaterial(kind="book", id="b1", title="Clean Code", detail="464 pages")


@pytest.fixture()
def wired(tmp_path, monkeypatch: pytest.MonkeyPatch) -> StudyPlanStore:
    monkeypatch.setattr(
        "deeptutor.services.study_plan.store.get_path_service",
        lambda: PathService(workspace_root=tmp_path / "data"),
    )
    store = StudyPlanStore()
    fixed = MaterialDiscovery(
        materials=[BOOK],
        unavailable=[{"kind": "reading", "reason": "RuntimeError: disabled"}],
    )
    monkeypatch.setattr("deeptutor.services.study_plan.get_study_plan_store", lambda: store)
    monkeypatch.setattr(
        "deeptutor.services.study_plan.resolve",
        lambda refs: materials_service.resolve(refs, discovery=fixed),
    )
    monkeypatch.setattr(
        "deeptutor.services.study_plan.discover", lambda query="", kinds=None: fixed
    )
    return store


def test_tool_names_match_the_manifest() -> None:
    manifest = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "deeptutor"
            / "modules"
            / "manifests"
            / "study-plan.json"
        ).read_text(encoding="utf-8")
    )
    declared = manifest["capabilities"]["study_plan"]["manifest"]["tools_used"]
    assert declared == list(STUDY_PLAN_TOOL_NAMES)
    assert set(manifest["tools"]) == set(STUDY_PLAN_TOOL_NAMES)


def test_tool_classes_report_their_names() -> None:
    assert StudyPlanMaterialsTool().name == "study_plan_materials"
    assert StudyPlanStatusTool().name == "study_plan_status"
    assert StudyPlanCreateTool().name == "study_plan_create"
    assert StudyPlanMarkTool().name == "study_plan_mark"


@pytest.mark.asyncio
async def test_materials_tool_lists_passable_refs(wired: StudyPlanStore) -> None:
    result = await StudyPlanMaterialsTool().execute()
    assert result.success
    assert "book:b1" in result.content
    assert "Clean Code" in result.content
    assert "464 pages" in result.content
    assert "reading: RuntimeError: disabled" in result.content
    assert "Notebook:" not in result.content
    assert result.metadata["count"] == 1


@pytest.mark.asyncio
async def test_materials_tool_rejects_an_unknown_kind(wired: StudyPlanStore) -> None:
    result = await StudyPlanMaterialsTool().execute(kind="video")
    assert not result.success
    assert "unknown material kind" in result.content


@pytest.mark.asyncio
async def test_materials_tool_says_so_when_empty(wired: StudyPlanStore, monkeypatch) -> None:
    monkeypatch.setattr(
        "deeptutor.services.study_plan.discover",
        lambda query="", kinds=None: MaterialDiscovery(),
    )
    result = await StudyPlanMaterialsTool().execute(query="kubernetes")
    assert "No study materials found" in result.content
    assert "kubernetes" in result.content


@pytest.mark.asyncio
async def test_create_tool_resolves_refs_and_keeps_free_text_custom(
    wired: StudyPlanStore,
) -> None:
    result = await StudyPlanCreateTool().execute(
        goal="Ship a RAG service",
        deadline="2026-11-30",
        start_date="2026-11-02",
        weekly_hours=6,
        materials=["book:b1", "Chapter 3: chunking"],
    )
    assert result.success, result.content
    plan = wired.load().find(result.metadata["plan_id"])
    assert plan is not None
    assert [(item.kind, item.title) for item in plan.materials] == [
        ("book", "Clean Code"),
        ("custom", "Chapter 3: chunking"),
    ]
    assert result.metadata["materials"] == ["Clean Code", "Chapter 3: chunking"]


@pytest.mark.asyncio
async def test_create_tool_reports_a_vanished_reference(wired: StudyPlanStore) -> None:
    result = await StudyPlanCreateTool().execute(
        goal="Ship a RAG service",
        deadline="2026-11-30",
        weekly_hours=6,
        materials=["book:gone"],
    )
    assert not result.success
    assert "book:gone" in result.content
    assert wired.load().plans == []


@pytest.mark.asyncio
async def test_create_tool_surfaces_a_bad_schedule(wired: StudyPlanStore) -> None:
    result = await StudyPlanCreateTool().execute(
        goal="Ship a RAG service",
        deadline="2026-11-01",
        start_date="2026-11-30",
        weekly_hours=6,
    )
    assert not result.success
    assert "Nothing was saved" in result.content


@pytest.mark.asyncio
async def test_create_and_mark_tools_round_trip(wired: StudyPlanStore) -> None:
    created = await StudyPlanCreateTool().execute(
        goal="Ship a RAG service",
        deadline="2026-11-30",
        start_date="2026-11-02",
        weekly_hours=6,
        weekdays="Mon,Wed,Fri",
    )
    assert created.success, created.content
    plan = wired.load().find(created.metadata["plan_id"])
    assert plan is not None
    assert plan.weekdays == [0, 2, 4]
    session_id = plan.sessions[0].id

    marked = await StudyPlanMarkTool().execute(
        plan_id=plan.id, session_id=session_id, status="done"
    )
    assert marked.success, marked.content
    assert marked.metadata["progress"]["done"] == 1
    assert wired.load().find(plan.id).sessions[0].status == "done"  # type: ignore[union-attr]

    missing = await StudyPlanMarkTool().execute(plan_id=plan.id, session_id="s999", status="done")
    assert not missing.success
    assert "s999" in missing.content


@pytest.mark.asyncio
async def test_status_tool_reads_the_plan(wired: StudyPlanStore) -> None:
    created = await StudyPlanCreateTool().execute(
        goal="Ship a RAG service",
        deadline="2026-11-30",
        start_date="2026-11-02",
        weekly_hours=6,
        materials=["book:b1"],
    )
    assert created.success
    status = await StudyPlanStatusTool().execute()
    assert status.success
    assert "Ship a RAG service" in status.content
    assert "Clean Code" in status.content
