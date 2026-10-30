"""Tests for the dated study-plan store."""

from __future__ import annotations

from datetime import date
import multiprocessing
from pathlib import Path

import pytest

from deeptutor.services.path_service import PathService
from deeptutor.services.study_plan.materials import MATERIAL_KINDS
from deeptutor.services.study_plan.models import StudyMaterial
from deeptutor.services.study_plan.store import MAX_PLANS, StudyPlanStore


@pytest.fixture()
def plan_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StudyPlanStore:
    service = PathService(workspace_root=tmp_path / "data")
    monkeypatch.setattr(
        "deeptutor.services.study_plan.store.get_path_service",
        lambda: service,
    )
    return StudyPlanStore()


def _create(store: StudyPlanStore, **overrides):
    params = {
        "goal": "Ship a RAG service",
        "deadline": date(2026, 11, 30),
        "weekly_hours": 6,
        "start_date": date(2026, 11, 2),
    }
    params.update(overrides)
    return store.create_plan(**params)


def test_create_plan_writes_dated_sessions(plan_store: StudyPlanStore) -> None:
    result = _create(plan_store)
    assert result.accepted
    assert result.plan is not None
    plan = result.plan
    assert plan.id == "sp_0001"
    assert len(plan.sessions) == 13
    assert [session.id for session in plan.sessions[:3]] == ["s001", "s002", "s003"]
    assert plan.sessions[0].date == "2026-11-02"
    path = plan_store.plans_path()
    assert path.exists()
    again = plan_store.load()
    assert again.find("sp_0001") is not None
    assert again.plans[0].sessions[0].title.startswith("Ship a RAG service")


def test_empty_status_explains_how_to_start(plan_store: StudyPlanStore) -> None:
    text = plan_store.status_markdown()
    assert "No study plan yet" in text
    assert "study_plan_create" in text


def test_every_material_kind_round_trips(plan_store: StudyPlanStore) -> None:
    """Each schedulable kind survives a write, reload, and drives sessions."""
    materials = [
        StudyMaterial(
            kind=kind,
            id=f"{kind}-1",
            title=f"{kind} material",
            detail=f"{kind} detail",
            href=f"/{kind}/1",
        )
        for kind in MATERIAL_KINDS
    ]
    materials.append(StudyMaterial(kind="custom", id="", title="Write the eval harness"))
    created = _create(plan_store, materials=materials)
    assert created.accepted
    assert created.plan is not None
    assert [material.kind for material in created.plan.materials] == [
        *MATERIAL_KINDS,
        "custom",
    ]

    reloaded = plan_store.load().find(created.plan.id)
    assert reloaded is not None
    assert [material.to_dict() for material in reloaded.materials] == [
        material.to_dict() for material in materials
    ]
    # The scheduler reads material titles, so a plan is never generic text.
    scheduled = " ".join(session.title for session in reloaded.sessions)
    assert f"{MATERIAL_KINDS[0]} material" in scheduled
    assert "Write the eval harness" in scheduled


def test_identical_plan_is_deduplicated(plan_store: StudyPlanStore) -> None:
    first = _create(plan_store)
    second = _create(plan_store)
    assert first.accepted and second.accepted
    assert second.deduplicated
    assert second.plan is not None and first.plan is not None
    assert second.plan.id == first.plan.id
    assert len(plan_store.load().plans) == 1


def test_session_status_round_trip(plan_store: StudyPlanStore) -> None:
    created = _create(plan_store)
    assert created.plan is not None
    plan = created.plan
    done = plan_store.set_session_status(plan.id, "s001", "done")
    assert done.accepted
    session = done.plan.sessions[0] if done.plan else None
    assert session is not None
    assert session.status == "done"
    assert session.completed_at
    assert done.plan.progress()["percent_done"] == round(100 / len(done.plan.sessions))

    skipped = plan_store.set_session_status(plan.id, "s002", "skipped")
    assert skipped.plan is not None
    assert skipped.plan.find_session("s002").completed_at == ""

    reset = plan_store.set_session_status(plan.id, "s001", "pending")
    assert reset.plan.find_session("s001").completed_at == ""
    assert plan_store.load().find(plan.id).find_session("s001").status == "pending"


def test_unknown_plan_or_session_raises_key_error(plan_store: StudyPlanStore) -> None:
    _create(plan_store)
    with pytest.raises(KeyError):
        plan_store.set_session_status("sp_9999", "s001", "done")
    with pytest.raises(KeyError):
        plan_store.set_session_status("sp_0001", "s999", "done")


def test_invalid_status_is_rejected(plan_store: StudyPlanStore) -> None:
    _create(plan_store)
    with pytest.raises(ValueError):
        plan_store.set_session_status("sp_0001", "s001", "finished")


def test_archived_plans_leave_the_injection_slice(plan_store: StudyPlanStore) -> None:
    created = _create(plan_store)
    assert plan_store.injection_markdown().startswith("# Study plans")
    plan_store.set_plan_archived(created.plan.id, True)
    assert plan_store.injection_markdown() == ""
    assert plan_store.load().active_plans() == []
    plan_store.set_plan_archived(created.plan.id, False)
    assert "Ship a RAG service" in plan_store.injection_markdown()


def test_injection_reports_today_and_progress(plan_store: StudyPlanStore) -> None:
    created = _create(plan_store)
    plan_store.set_session_status(created.plan.id, "s001", "done")
    text = plan_store.injection_markdown(today=date(2026, 11, 2))
    assert "`s001`" in text
    assert "done" in text
    assert "2026-11-30" in text
    assert "1 done" in text


def test_reads_create_nothing(plan_store: StudyPlanStore) -> None:
    path = plan_store.plans_path()
    snapshot = plan_store.overview()
    assert snapshot["plans"] == []
    assert snapshot["injected_context"] == ""
    assert not path.parent.exists()


def test_invalid_parameters_save_nothing(plan_store: StudyPlanStore) -> None:
    result = _create(plan_store, deadline=date(2026, 10, 1))
    assert not result.accepted
    assert "Nothing was saved" in result.message
    assert not plan_store.plans_path().exists()


def test_plan_cap_is_enforced(plan_store: StudyPlanStore) -> None:
    for index in range(MAX_PLANS):
        assert _create(plan_store, goal=f"Goal {index}").accepted
    overflow = _create(plan_store, goal="One too many")
    assert not overflow.accepted
    assert str(MAX_PLANS) in overflow.message


@pytest.mark.parametrize(
    "content",
    [
        "{broken",
        "[]",
        '{"version": 2}',
        '{"plans": {}}',
        '{"plans": [{}]}',
        '{"plans": [{"id": "sp_0001", "goal": "x", "start_date": "2026-01-01", '
        '"deadline": "2026-02-01", "weekly_hours": 1, "session_minutes": 60, '
        '"sessions": [{"id": "s001"}]}]}',
    ],
)
def test_corrupt_file_is_preserved_on_read_and_write(plan_store, content) -> None:
    path = plan_store.plans_path()
    path.parent.mkdir(parents=True)
    path.write_text(content)
    with pytest.raises(ValueError):
        plan_store.overview()
    with pytest.raises(ValueError):
        _create(plan_store)
    assert path.read_text() == content


def test_plan_markdown_lists_every_session(plan_store: StudyPlanStore) -> None:
    created = _create(plan_store)
    text = plan_store.plan_markdown(created.plan, today=date(2026, 11, 2))
    assert "| `s001` | 2026-11-02 | 1 |" in text
    assert text.count("| `s") == len(created.plan.sessions)


def _create_many(root: str, prefix: str) -> None:
    import deeptutor.services.study_plan.store as module

    module.get_path_service = lambda: PathService(workspace_root=Path(root))
    store = module.StudyPlanStore()
    for index in range(5):
        store.create_plan(
            goal=f"{prefix}-{index}",
            deadline=date(2026, 11, 30),
            weekly_hours=3,
            start_date=date(2026, 11, 2),
        )


def test_two_workers_do_not_drop_plans(plan_store: StudyPlanStore) -> None:
    root = plan_store.plans_path().parent.parent
    context = multiprocessing.get_context("spawn")
    workers = [context.Process(target=_create_many, args=(str(root), p)) for p in ("a", "b")]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(20)
        assert worker.exitcode == 0
    plans = plan_store.load().plans
    assert len(plans) == 10
    assert len({plan.id for plan in plans}) == 10
