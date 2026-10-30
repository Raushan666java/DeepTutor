"""API contract tests for /api/study-plans."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from deeptutor.api.routers import study_plan as study_plan_router
from deeptutor.services.path_service import PathService
from deeptutor.services.study_plan import materials as materials_service
from deeptutor.services.study_plan.materials import MaterialDiscovery
from deeptutor.services.study_plan.models import StudyMaterial
from deeptutor.services.study_plan.store import StudyPlanStore

BOOK = StudyMaterial(
    kind="book",
    id="b1",
    title="Clean Code",
    detail="464 pages",
    href="/learning/books/b1",
)
NOTEBOOK = StudyMaterial(kind="notebook", id="n1", title="RAG notes", detail="12 records")


@pytest.fixture()
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    service = PathService(workspace_root=tmp_path / "data")
    monkeypatch.setattr("deeptutor.services.study_plan.store.get_path_service", lambda: service)
    store = StudyPlanStore()
    monkeypatch.setattr(study_plan_router, "get_study_plan_store", lambda: store)
    fixed = MaterialDiscovery(materials=[BOOK, NOTEBOOK])
    monkeypatch.setattr(
        study_plan_router,
        "resolve",
        lambda refs: materials_service.resolve(refs, discovery=fixed),
    )
    monkeypatch.setattr(study_plan_router, "discover", lambda query="", kinds=None: fixed)
    app = FastAPI()
    app.include_router(study_plan_router.router, prefix="/api/study-plans")
    app.state.store = store
    return TestClient(app)


def _payload(**overrides):
    body = {
        "goal": "Ship a RAG service",
        "deadline": "2026-11-30",
        "weekly_hours": 6,
        "start_date": "2026-11-02",
        "weekdays": [0, 2, 4],
    }
    body.update(overrides)
    return body


def test_materials_route_is_not_shadowed_by_plan_id(client: TestClient) -> None:
    response = client.get("/api/study-plans/materials")
    assert response.status_code == 200
    body = response.json()
    assert [row["title"] for row in body["materials"]] == ["Clean Code", "RAG notes"]
    assert body["unavailable"] == []
    assert {row["kind"] for row in body["kinds"]} >= {"book", "notebook"}


def test_materials_route_rejects_unknown_kind(client: TestClient) -> None:
    response = client.get("/api/study-plans/materials", params={"kinds": "book,video"})
    assert response.status_code == 400
    assert "video" in response.json()["detail"]


def test_create_plan_resolves_titles_server_side(client: TestClient) -> None:
    response = client.post(
        "/api/study-plans",
        json=_payload(
            materials=[
                {"kind": "book", "id": "b1", "title": "a stale browser title"},
                {"kind": "custom", "title": "  Chunking   strategies "},
            ]
        ),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert [(row["kind"], row["title"]) for row in body["materials"]] == [
        ("book", "Clean Code"),
        ("custom", "Chunking strategies"),
    ]
    assert body["materials"][0]["href"] == "/learning/books/b1"
    assert body["sessions"]


def test_create_plan_rejects_a_vanished_reference(client: TestClient) -> None:
    response = client.post(
        "/api/study-plans", json=_payload(materials=[{"kind": "book", "id": "gone"}])
    )
    assert response.status_code == 400
    assert "book:gone" in response.json()["detail"]


def test_create_plan_rejects_a_library_ref_without_an_id(client: TestClient) -> None:
    response = client.post(
        "/api/study-plans", json=_payload(materials=[{"kind": "book", "title": "x"}])
    )
    assert response.status_code == 400
    assert "needs an id" in response.json()["detail"]


def test_create_plan_rejects_an_empty_custom_title(client: TestClient) -> None:
    response = client.post(
        "/api/study-plans", json=_payload(materials=[{"kind": "custom", "title": "  "}])
    )
    assert response.status_code == 400
    assert "needs a title" in response.json()["detail"]


def test_legacy_topics_still_become_custom_materials(client: TestClient) -> None:
    response = client.post("/api/study-plans", json=_payload(topics=["Chunking"]))
    assert response.status_code == 201, response.text
    assert [(row["kind"], row["title"]) for row in response.json()["materials"]] == [
        ("custom", "Chunking")
    ]


def test_create_plan_rejects_an_impossible_window(client: TestClient) -> None:
    response = client.post(
        "/api/study-plans", json=_payload(start_date="2026-11-30", deadline="2026-11-01")
    )
    assert response.status_code == 400


def test_marking_a_session_updates_progress(client: TestClient) -> None:
    created = client.post("/api/study-plans", json=_payload()).json()
    session_id = created["sessions"][0]["id"]
    marked = client.post(
        f"/api/study-plans/{created['id']}/sessions/{session_id}/status",
        json={"status": "done"},
    )
    assert marked.status_code == 200, marked.text
    body = marked.json()
    assert body["sessions"][0]["status"] == "done"
    assert body["sessions"][0]["completed_at"]
    assert body["progress"]["done"] == 1
    assert body["progress"]["pending"] == len(body["sessions"]) - 1


def test_marking_an_unknown_session_is_404(client: TestClient) -> None:
    created = client.post("/api/study-plans", json=_payload()).json()
    response = client.post(
        f"/api/study-plans/{created['id']}/sessions/s999/status", json={"status": "done"}
    )
    assert response.status_code == 404


def test_read_and_archive_round_trip(client: TestClient) -> None:
    created = client.post("/api/study-plans", json=_payload()).json()
    assert client.get(f"/api/study-plans/{created['id']}").status_code == 200
    archived = client.patch(f"/api/study-plans/{created['id']}", json={"archived": True})
    assert archived.status_code == 200
    assert archived.json()["archived"] is True
    assert client.get("/api/study-plans").json()["plans"][0]["archived"] is True
    assert client.get("/api/study-plans/sp_9999").status_code == 404


def test_corrupt_file_is_preserved_and_409(client: TestClient) -> None:
    store: StudyPlanStore = client.app.state.store
    path = store.plans_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"{not json")
    assert client.get("/api/study-plans").status_code == 409
    assert path.read_bytes() == b"{not json"
