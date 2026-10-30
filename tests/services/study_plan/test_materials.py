"""Tests for read-only study-material discovery and resolution."""

from __future__ import annotations

import pytest

from deeptutor.services.study_plan import materials
from deeptutor.services.study_plan.materials import (
    KIND_LABELS,
    MATERIAL_KINDS,
    MAX_MATERIALS_PER_KIND,
    MaterialDiscovery,
    discover,
    resolve,
)
from deeptutor.services.study_plan.models import StudyMaterial, StudyPlan


def _rows(kind: str, count: int = 2) -> list[StudyMaterial]:
    return [
        StudyMaterial(kind=kind, id=f"{kind}_{index}", title=f"{kind} {index:02d}")
        for index in range(count)
    ]


@pytest.fixture()
def providers(monkeypatch: pytest.MonkeyPatch):
    """Replace every provider with a controllable fake; return the call log."""
    calls: list[tuple[str, str]] = []

    def install(overrides: dict | None = None) -> list[tuple[str, str]]:
        table = {}

        def make(kind: str, count: int = 2):
            def provider(query: str) -> list[StudyMaterial]:
                calls.append((kind, query))
                return _rows(kind, count)

            return provider

        for kind in MATERIAL_KINDS:
            table[kind] = make(kind)
        table.update(overrides or {})
        monkeypatch.setattr(materials, "_PROVIDERS", table)
        return calls

    return install


def test_discover_reads_every_kind_in_stable_order(providers) -> None:
    providers()
    found = discover()
    expected = [kind for kind in MATERIAL_KINDS for _ in range(2)]
    assert [item.kind for item in found.materials] == expected
    assert len(found.materials) == 2 * len(MATERIAL_KINDS)
    assert found.unavailable == []
    # Within one kind, titles sort case-insensitively.
    books = [item.title for item in found.materials if item.kind == "book"]
    assert books == sorted(books, key=str.casefold)


def test_discover_isolates_a_failing_provider(providers) -> None:
    def boom(query: str) -> list[StudyMaterial]:
        raise RuntimeError("books are disabled   on this install")

    providers({"book": boom})
    found = discover()
    assert found.unavailable == [
        {"kind": "book", "reason": "RuntimeError: books are disabled on this install"}
    ]
    assert {item.kind for item in found.materials} == set(MATERIAL_KINDS) - {"book"}


def test_discover_accepts_a_kind_subset(providers) -> None:
    calls = providers()
    found = discover(kinds=["book", "mastery"])
    assert [kind for kind, _ in calls] == ["book", "mastery"]
    assert {item.kind for item in found.materials} == {"book", "mastery"}


def test_discover_caps_rows_per_kind(providers) -> None:
    providers({"book": lambda query: _rows("book", MAX_MATERIALS_PER_KIND + 10)})
    found = discover(kinds=["book"])
    assert len(found.materials) == MAX_MATERIALS_PER_KIND


def test_discover_passes_the_normalized_query_through(providers) -> None:
    calls = providers()
    discover(query="  clean   code  ")
    assert {query for _, query in calls} == {"clean code"}


def test_resolve_uses_the_owner_title_and_reports_missing(providers) -> None:
    providers()
    found = discover()
    expected = next(item for item in found.materials if item.kind == "book" and item.id == "book_1")
    resolved, missing = resolve(
        [("book", "book_1"), ("book", "book_1"), ("book", "gone")], discovery=found
    )
    assert [item.title for item in resolved] == [expected.title]
    assert missing == ["book:gone"]


def test_resolve_discovers_only_the_wanted_kinds(providers) -> None:
    calls = providers()
    resolved, missing = resolve([("notebook", "notebook_0")])
    assert [kind for kind, _ in calls] == ["notebook"]
    assert [item.kind for item in resolved] == ["notebook"]
    assert missing == []


def test_resolve_of_nothing_touches_no_provider(providers) -> None:
    calls = providers()
    assert resolve([]) == ([], [])
    assert calls == []


def test_kind_labels_cover_every_kind_plus_custom() -> None:
    assert set(KIND_LABELS) == set(MATERIAL_KINDS) | {"custom"}


def test_material_parsing_trims_and_rejects_empty_titles() -> None:
    parsed = StudyMaterial.from_dict(
        {"kind": " book ", "id": " b1 ", "title": "  Clean\n  Code ", "detail": None}
    )
    assert parsed == StudyMaterial(kind="book", id="b1", title="Clean Code", detail="", href="")
    assert StudyMaterial.from_dict({"kind": "book", "title": "   "}) is None
    assert StudyMaterial.from_dict("book:b1") is None


def _plan(**overrides) -> StudyPlan | None:
    raw = {
        "id": "sp_0001",
        "goal": "Ship a RAG service",
        "start_date": "2026-11-02",
        "deadline": "2026-11-30",
        "weekly_hours": 6,
        "session_minutes": 60,
        "weekdays": [0, 2, 4],
        "sessions": [],
    }
    raw.update(overrides)
    return StudyPlan.from_dict(raw)


def test_legacy_topics_become_custom_materials() -> None:
    plan = _plan(topics=["Chunking", "  Retrieval  "])
    assert plan is not None
    assert [(item.kind, item.title) for item in plan.materials] == [
        ("custom", "Chunking"),
        ("custom", "Retrieval"),
    ]
    assert [item["title"] for item in plan.to_dict()["materials"]] == ["Chunking", "Retrieval"]


def test_materials_win_over_legacy_topics() -> None:
    plan = _plan(
        topics=["ignored"],
        materials=[{"kind": "book", "id": "b1", "title": "Clean Code"}],
    )
    assert plan is not None
    assert [item.title for item in plan.materials] == ["Clean Code"]


def test_practice_banks_read_the_review_history(tmp_path, monkeypatch) -> None:
    db = tmp_path / "chat_history.db"
    db.write_bytes(b"")

    class _Paths:
        def get_chat_history_db(self):
            return db

    class _Store:
        def list_question_bank_materials_sync(self):
            return [
                {
                    "source": "book",
                    "material_id": "b1",
                    "material_title": "Clean Code",
                    "entry_count": 4,
                    "unresolved_count": 2,
                },
                {
                    "source": "",
                    "material_id": "",
                    "material_title": "dropped",
                    "entry_count": 1,
                    "unresolved_count": 0,
                },
            ]

    monkeypatch.setattr(materials, "get_path_service", lambda: _Paths())
    monkeypatch.setattr(
        "deeptutor.services.session.sqlite_store.get_sqlite_session_store", lambda: _Store()
    )
    rows = materials._practice_banks("clean")
    assert len(rows) == 1
    row = rows[0]
    assert (row.kind, row.id) == ("practice", "book:b1")
    assert row.title == "Clean Code"
    assert row.detail == "4 questions · 2 to review"
    assert row.href == "/learning/practice?view=bank"


def test_practice_banks_do_not_create_a_database(tmp_path, monkeypatch) -> None:
    db = tmp_path / "chat_history.db"

    class _Paths:
        def get_chat_history_db(self):
            return db

    monkeypatch.setattr(materials, "get_path_service", lambda: _Paths())
    assert materials._practice_banks("") == []
    assert not db.exists()


def test_material_discovery_to_dict_reports_kinds() -> None:
    payload = MaterialDiscovery().to_dict()
    assert payload["materials"] == []
    assert payload["unavailable"] == []
    assert [row["kind"] for row in payload["kinds"]] == list(MATERIAL_KINDS)
