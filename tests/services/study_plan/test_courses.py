"""Tests for course catalogs and the ``course`` material provider."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deeptutor.services.study_plan import materials
from deeptutor.services.study_plan.courses import (
    CATALOG_VERSION,
    CatalogError,
    load_catalog,
    parse_catalog,
)


def _catalog(chapters: list[dict[str, str]] | None = None) -> dict:
    return {
        "version": CATALOG_VERSION,
        "courses": [
            {
                "id": "placement",
                "title": "Placement Course",
                "url": "https://example.test/placement/",
                "chapters": chapters
                if chapters is not None
                else [
                    {
                        "id": "01-python/01-basics",
                        "title": "Python basics",
                        "url": "https://example.test/placement/01-python/01-basics/",
                    },
                    {
                        "id": "01-python/02-control-flow",
                        "title": "Control flow",
                        "url": "https://example.test/placement/01-python/02-control-flow/",
                    },
                ],
            }
        ],
    }


class _Paths:
    def __init__(self, root: Path) -> None:
        self.root = root

    def get_course_catalog_file(self) -> Path:
        return self.root / "courses" / "catalog.json"


def _install_catalog(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "courses" / "catalog.json"
    path.parent.mkdir(parents=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


def test_parse_catalog_reads_courses_and_chapters() -> None:
    courses = parse_catalog(_catalog())
    assert len(courses) == 1
    course = courses[0]
    assert (course.id, course.title) == ("placement", "Placement Course")
    assert [chapter.id for chapter in course.chapters] == [
        "01-python/01-basics",
        "01-python/02-control-flow",
    ]
    assert course.chapters[0].url.startswith("https://example.test/")


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"version": 99, "courses": []},
        {"version": CATALOG_VERSION, "courses": "nope"},
    ],
)
def test_parse_catalog_rejects_broken_structure(payload: object) -> None:
    with pytest.raises(CatalogError):
        parse_catalog(payload)


def test_parse_catalog_skips_unusable_rows_without_hiding_the_rest() -> None:
    raw = _catalog(
        [
            {"id": "ok", "title": "Usable", "url": "https://example.test/ok/"},
            {"id": "", "title": "No id", "url": "https://example.test/x/"},
            {"id": "no-url", "title": "No url"},
            "not a chapter",
        ]
    )
    raw["courses"].append({"id": "empty", "title": "Empty", "chapters": []})
    raw["courses"].append(
        {"title": "No id at all", "chapters": [{"id": "a", "title": "A", "url": "u"}]}
    )
    courses = parse_catalog(raw)
    assert [course.id for course in courses] == ["placement"]
    assert [chapter.id for chapter in courses[0].chapters] == ["ok"]


def test_load_catalog_returns_empty_when_missing(tmp_path: Path) -> None:
    assert load_catalog(tmp_path / "missing.json") == []


def test_load_catalog_reports_invalid_json(tmp_path: Path) -> None:
    path = _install_catalog(tmp_path, "{not json")
    with pytest.raises(CatalogError, match="not valid JSON"):
        load_catalog(path)


def test_course_provider_maps_chapters_to_absolute_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_catalog(tmp_path, _catalog())
    monkeypatch.setattr(materials, "get_path_service", lambda: _Paths(tmp_path))

    found = materials.discover(kinds=["course"])
    assert found.unavailable == []
    assert {(row.kind, row.id) for row in found.materials} == {
        ("course", "placement/01-python/01-basics"),
        ("course", "placement/01-python/02-control-flow"),
    }
    first = next(row for row in found.materials if row.id.endswith("01-basics"))
    assert first.title == "Python basics"
    assert first.detail == "Placement Course"
    assert first.href == "https://example.test/placement/01-python/01-basics/"


def test_course_provider_filters_on_chapter_and_course_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_catalog(tmp_path, _catalog())
    monkeypatch.setattr(materials, "get_path_service", lambda: _Paths(tmp_path))

    by_chapter = materials.discover(query="control", kinds=["course"])
    assert [row.title for row in by_chapter.materials] == ["Control flow"]
    by_course = materials.discover(query="placement", kinds=["course"])
    assert len(by_course.materials) == 2
    assert materials.discover(query="nothing", kinds=["course"]).materials == []


def test_course_provider_keeps_curriculum_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_catalog(tmp_path, _catalog())
    monkeypatch.setattr(materials, "get_path_service", lambda: _Paths(tmp_path))

    found = materials.discover(kinds=["course"])
    assert [row.id for row in found.materials] == [
        "placement/01-python/01-basics",
        "placement/01-python/02-control-flow",
    ]


def test_course_provider_reports_a_broken_catalog_as_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_catalog(tmp_path, {"version": CATALOG_VERSION, "courses": "nope"})
    monkeypatch.setattr(materials, "get_path_service", lambda: _Paths(tmp_path))

    found = materials.discover(kinds=["course"])
    assert found.materials == []
    assert found.unavailable[0]["kind"] == "course"
    assert "CatalogError" in found.unavailable[0]["reason"]


def test_course_kind_has_its_own_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    chapters = [
        {
            "id": f"ch-{index:04d}",
            "title": f"Chapter {index:04d}",
            "url": f"https://e.test/{index}/",
        }
        for index in range(materials.KIND_LIMITS["course"] + 25)
    ]
    _install_catalog(tmp_path, _catalog(chapters))
    monkeypatch.setattr(materials, "get_path_service", lambda: _Paths(tmp_path))

    found = materials.discover(kinds=["course"])
    assert len(found.materials) == materials.KIND_LIMITS["course"]
    assert materials.KIND_LIMITS["course"] > materials.MAX_MATERIALS_PER_KIND
