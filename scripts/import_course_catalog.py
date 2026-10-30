#!/usr/bin/env python
"""Build a study-plan course catalog from a Markdown course tree.

A "course" in a study plan is a published page: the plan schedules its
chapters, and each session links to the live site. This script turns a local
Docusaurus/MkDocs course directory into the small JSON index that
:mod:`deeptutor.services.study_plan.courses` reads. It never touches the
network, so the catalog is reproducible from the sources.

Only pages that declare a ``slug`` in front matter are published; drafts and
planning notes such as ``PLAN.md`` are skipped.

Usage::

    python scripts/import_course_catalog.py \\
        --docs site/docs/courses/ai-engineering-placement \\
        --base-url https://raushan666java.github.io/ai-engineering-journey \\
        --out data/courses/catalog.json

The output is merged by course id, so importing a second course keeps the
first. ``--dry-run`` prints the summary without writing.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

CATALOG_VERSION = 1


def parse_front_matter(text: str) -> dict[str, str]:
    """Read the flat ``key: value`` front matter this importer needs.

    A full YAML parser would accept nested structures this catalog cannot
    represent, so the narrow parser is deliberate: unknown keys are ignored and
    a malformed block simply yields no metadata.
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    data: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if value:
            data[key.strip()] = value.replace('\\"', '"').replace("''", "'")
    return data


def page_url(base_url: str, slug: str) -> str:
    """Join a site base with an absolute page slug, always ending in ``/``."""
    return base_url.rstrip("/") + "/" + slug.strip("/").strip() + "/"


def prettify(value: str) -> str:
    """A readable fallback when a page has no front-matter title."""
    words = value.replace("_", "-").split("-")
    text = " ".join(word for word in words if word)
    return text[:1].upper() + text[1:] if text else ""


def chapter_id(relative: Path) -> str:
    """Stable id: the page path, with a nested ``index`` collapsing to its dir."""
    without_suffix = relative.with_suffix("")
    parts = list(without_suffix.parts)
    if parts and parts[-1] == "index":
        parts = parts[:-1]
    return "/".join(parts)


def read_page(path: Path, docs_root: Path, base_url: str) -> dict[str, str] | None:
    text = path.read_text(encoding="utf-8")
    meta = parse_front_matter(text)
    slug = meta.get("slug", "").strip()
    if not slug:
        return None
    relative = path.relative_to(docs_root)
    identity = chapter_id(relative)
    if not identity:
        return None
    title = (meta.get("title") or meta.get("sidebar_label") or "").strip()
    return {
        "id": identity,
        "title": " ".join(title.split()) or prettify(identity.rsplit("/", 1)[-1]),
        "url": page_url(base_url, slug),
    }


def build_course(docs_root: Path, base_url: str) -> dict[str, Any]:
    """Build one course entry from a Markdown directory."""
    root_index = docs_root / "index.md"
    root_meta = (
        parse_front_matter(root_index.read_text(encoding="utf-8")) if root_index.exists() else {}
    )
    root_slug = root_meta.get("slug", "").strip()
    course_id = root_slug.strip("/").rsplit("/", 1)[-1] if root_slug else docs_root.name
    if not course_id:
        raise SystemExit(f"{docs_root} has no usable course id")
    title = (root_meta.get("title") or root_meta.get("sidebar_label") or "").strip()
    chapters = sorted(
        (
            chapter
            for path in docs_root.rglob("*.md")
            for chapter in [read_page(path, docs_root, base_url)]
            if chapter is not None
        ),
        key=lambda chapter: chapter["id"],
    )
    # ``foo.md`` and ``foo/index.md`` describe the same page id; keep the first.
    unique: list[dict[str, str]] = []
    seen: set[str] = set()
    for chapter in chapters:
        if chapter["id"] in seen:
            continue
        seen.add(chapter["id"])
        unique.append(chapter)
    chapters = unique
    if not chapters:
        raise SystemExit(f"{docs_root} has no published pages (front matter 'slug')")
    course: dict[str, Any] = {
        "id": course_id,
        "title": " ".join(title.split()) or prettify(course_id),
        "url": page_url(base_url, root_slug) if root_slug else "",
        "chapters": chapters,
    }
    return course


def merge_catalog(existing: Any, course: dict[str, Any]) -> dict[str, Any]:
    """Replace one course by id, keeping every other entry intact."""
    rows: list[dict[str, Any]] = []
    if isinstance(existing, dict) and isinstance(existing.get("courses"), list):
        rows = [row for row in existing["courses"] if isinstance(row, dict)]
    rows = [row for row in rows if str(row.get("id") or "") != course["id"]]
    rows.append(course)
    rows.sort(key=lambda row: str(row.get("id") or ""))
    return {"version": CATALOG_VERSION, "courses": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--docs", required=True, help="course directory with .md pages")
    parser.add_argument("--base-url", required=True, help="published site root")
    parser.add_argument(
        "--out",
        default="data/courses/catalog.json",
        help="catalog file to write (default: data/courses/catalog.json)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    docs_root = Path(args.docs)
    if not docs_root.is_dir():
        print(f"error: {docs_root} is not a directory", file=sys.stderr)
        return 2
    course = build_course(docs_root, args.base_url)
    out = Path(args.out)
    existing: Any = None
    if out.exists():
        try:
            existing = json.loads(out.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"error: {out} is not valid JSON: {exc.msg}", file=sys.stderr)
            return 2
    catalog = merge_catalog(existing, course)
    print(
        f"{course['id']}: {len(course['chapters'])} chapters "
        f"({len(catalog['courses'])} course(s) in catalog)"
    )
    if args.dry_run:
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
