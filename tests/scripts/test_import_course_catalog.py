"""Tests for ``scripts/import_course_catalog.py``."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

BASE = "https://example.test/site"


def _load_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "import_course_catalog.py"
    name = "import_course_catalog_under_test"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _write_course(root: Path) -> Path:
    docs = root / "placement"
    (docs / "01-python").mkdir(parents=True)
    (docs / "index.md").write_text(
        '---\nid: index\nslug: /placement\ntitle: "Placement \\"Course\\""\n---\n\n# Placement\n',
        encoding="utf-8",
    )
    (docs / "01-python" / "01-basics.md").write_text(
        "---\nid: 01-basics\nslug: /placement/01-python/01-basics\ntitle: Python Basics\n---\n\n# Basics\n",
        encoding="utf-8",
    )
    (docs / "01-python" / "index.md").write_text(
        "---\nslug: /placement/01-python\ntitle: Python Programming\n---\n",
        encoding="utf-8",
    )
    (docs / "PLAN.md").write_text("# Internal plan\n", encoding="utf-8")
    return docs


def test_parse_front_matter_handles_quotes_and_ignores_noise() -> None:
    module = _load_module()
    meta = module.parse_front_matter(
        '---\ntitle: "A \\"quoted\\" title"\nslug: /x\n# note\nnested:\n---\nbody'
    )
    assert meta == {"title": 'A "quoted" title', "slug": "/x"}
    assert module.parse_front_matter("no front matter") == {}
    assert module.parse_front_matter("---\nunterminated: yes\n") == {}


def test_page_url_and_chapter_id_are_stable() -> None:
    module = _load_module()
    assert module.page_url(BASE, "/placement") == f"{BASE}/placement/"
    assert module.page_url(BASE + "/", "placement/01/") == f"{BASE}/placement/01/"
    assert module.chapter_id(Path("01-python/01-basics.md")) == "01-python/01-basics"
    assert module.chapter_id(Path("01-python/index.md")) == "01-python"


def test_build_course_uses_published_pages_only(tmp_path: Path) -> None:
    module = _load_module()
    docs = _write_course(tmp_path)
    course = module.build_course(docs, BASE)

    assert (course["id"], course["title"]) == ("placement", 'Placement "Course"')
    assert course["url"] == f"{BASE}/placement/"
    assert [(row["id"], row["title"]) for row in course["chapters"]] == [
        ("01-python", "Python Programming"),
        ("01-python/01-basics", "Python Basics"),
    ]
    assert all(row["url"].startswith(f"{BASE}/placement/") for row in course["chapters"])
    assert all("PLAN" not in row["id"] for row in course["chapters"])


def test_build_course_rejects_a_directory_without_published_pages(tmp_path: Path) -> None:
    module = _load_module()
    docs = tmp_path / "draft"
    docs.mkdir()
    (docs / "PLAN.md").write_text("# draft\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        module.build_course(docs, BASE)


def test_merge_catalog_replaces_one_course_and_keeps_the_rest() -> None:
    module = _load_module()
    existing = {
        "version": 1,
        "courses": [
            {"id": "other", "title": "Other", "chapters": []},
            {"id": "placement", "title": "Old", "chapters": [{"id": "x"}]},
        ],
    }
    merged = module.merge_catalog(
        existing,
        {"id": "placement", "title": "New", "url": "", "chapters": [{"id": "y"}]},
    )
    assert [row["id"] for row in merged["courses"]] == ["other", "placement"]
    assert merged["courses"][1]["title"] == "New"


def test_main_writes_then_merges_a_catalog(tmp_path: Path, capsys) -> None:
    module = _load_module()
    docs = _write_course(tmp_path)
    out = tmp_path / "data" / "courses" / "catalog.json"

    assert module.main(["--docs", str(docs), "--base-url", BASE, "--out", str(out)]) == 0
    first = json.loads(out.read_text(encoding="utf-8"))
    assert [row["id"] for row in first["courses"]] == ["placement"]

    assert module.main(["--docs", str(docs), "--base-url", BASE, "--out", str(out)]) == 0
    second = json.loads(out.read_text(encoding="utf-8"))
    assert len(second["courses"]) == 1
    assert "2 chapters" in capsys.readouterr().out


def test_dry_run_does_not_write(tmp_path: Path) -> None:
    module = _load_module()
    docs = _write_course(tmp_path)
    out = tmp_path / "catalog.json"
    assert (
        module.main(["--docs", str(docs), "--base-url", BASE, "--out", str(out), "--dry-run"]) == 0
    )
    assert not out.exists()
