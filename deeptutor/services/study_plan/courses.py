"""Read-only course catalogs: a published course as schedulable chapters.

A ``course`` material is a page on a site the learner already publishes or
follows. The catalog is a small JSON index built by
``scripts/import_course_catalog.py`` from a Markdown course tree, so discovery
never fetches the network: no SSRF surface, no latency, and the same answer
every time. The live page stays the source of truth; this module only points at
it.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

CATALOG_VERSION = 1
MAX_CHAPTERS_PER_COURSE = 2_000


class CatalogError(ValueError):
    """The catalog exists but cannot be understood."""


@dataclass(frozen=True, slots=True)
class Chapter:
    id: str
    title: str
    url: str


@dataclass(frozen=True, slots=True)
class Course:
    id: str
    title: str
    url: str
    chapters: tuple[Chapter, ...]


def _text(value: Any, field_name: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise CatalogError(f"a {field_name} is empty")
    return text


def parse_catalog(raw: Any) -> list[Course]:
    """Validate a decoded catalog, dropping unusable rows.

    Structural problems raise :class:`CatalogError` so the picker can report
    the kind as unavailable; a single half-written chapter is skipped instead
    of hiding the rest of the course.
    """
    if not isinstance(raw, dict):
        raise CatalogError("the catalog must be a JSON object")
    version = raw.get("version", CATALOG_VERSION)
    if version != CATALOG_VERSION:
        raise CatalogError(f"unsupported catalog version {version!r}")
    rows = raw.get("courses")
    if not isinstance(rows, list):
        raise CatalogError("the catalog needs a courses list")

    courses: list[Course] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            course_id = _text(row.get("id"), "course id")
            title = _text(row.get("title"), "course title")
        except CatalogError:
            continue
        chapters: list[Chapter] = []
        for item in row.get("chapters") or []:
            if not isinstance(item, dict):
                continue
            try:
                chapters.append(
                    Chapter(
                        id=_text(item.get("id"), "chapter id"),
                        title=_text(item.get("title"), "chapter title"),
                        url=_text(item.get("url"), "chapter url"),
                    )
                )
            except CatalogError:
                continue
        if not chapters:
            continue
        courses.append(
            Course(
                id=course_id,
                title=title,
                url=str(row.get("url") or "").strip(),
                chapters=tuple(chapters[:MAX_CHAPTERS_PER_COURSE]),
            )
        )
    return courses


def load_catalog(path: Path) -> list[Course]:
    """Read a catalog file; a missing file means "no courses yet"."""
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CatalogError(f"cannot read {path.name}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogError(f"{path.name} is not valid JSON: {exc.msg}") from exc
    return parse_catalog(raw)


__all__ = [
    "CATALOG_VERSION",
    "MAX_CHAPTERS_PER_COURSE",
    "CatalogError",
    "Chapter",
    "Course",
    "load_catalog",
    "parse_catalog",
]
