"""Read-only discovery of the study materials a plan can schedule.

A plan's sessions should point at something the learner actually owns — a book,
a mastery path, a reading collection, a knowledge base, a notebook or a
co-writer document — not only at a goal string. This module lists those
surfaces without owning them: every provider reads another feature's store and
nothing here writes to it.

Two rules keep the layering honest:

* A provider is lazily imported and individually wrapped, so a deployment that
  disabled Books simply reports that kind as unavailable instead of breaking
  discovery for the others.
* Discovery never creates state. Providers check that the feature's directory
  already exists before constructing a store whose constructor would ``mkdir``
  it, so opening the picker in a fresh workspace leaves no empty directories.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Callable

from deeptutor.services.path_service import get_path_service
from deeptutor.services.study_plan.models import StudyMaterial

MATERIAL_KINDS: tuple[str, ...] = (
    "knowledge",
    "book",
    "reading",
    "course",
    "mastery",
    "practice",
    "notebook",
    "document",
)

KIND_LABELS: dict[str, str] = {
    "knowledge": "Knowledge base",
    "book": "Book",
    "reading": "Reading collection",
    "course": "Course chapter",
    "mastery": "Mastery path",
    "practice": "Practice bank",
    "notebook": "Notebook",
    "document": "Co-Writer document",
    "custom": "Custom topic",
}

MAX_MATERIALS_PER_KIND = 50
# A course catalog is browsed with the picker's filter, so a whole curriculum
# (60 courses, ~1,400 chapters) is a normal list rather than noise. Every other
# kind stays small.
KIND_LIMITS: dict[str, int] = {"course": 2_000}
MAX_TITLE_CHARS = 120
MAX_DETAIL_CHARS = 160


def _clip(text: Any, limit: int) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "…"


def _material(
    kind: str,
    material_id: str,
    title: Any,
    detail: str = "",
    href: str = "",
) -> StudyMaterial | None:
    name = _clip(title, MAX_TITLE_CHARS)
    if not name:
        return None
    return StudyMaterial(
        kind=kind,
        id=str(material_id or ""),
        title=name,
        detail=_clip(detail, MAX_DETAIL_CHARS),
        href=href,
    )


# ── providers ────────────────────────────────────────────────────────────────


def _knowledge_bases(query: str) -> list[StudyMaterial]:
    from deeptutor.multi_user.knowledge_access import current_kb_base_dir, current_kb_manager

    if not current_kb_base_dir().exists():
        return []
    needle = query.casefold()
    items: list[StudyMaterial] = []
    for name in current_kb_manager().list_knowledge_bases():
        if needle and needle not in name.casefold():
            continue
        item = _material(
            "knowledge",
            name,
            name,
            "Knowledge base",
            f"/knowledge-bases/{name}",
        )
        if item is not None:
            items.append(item)
    return items


def _books(query: str) -> list[StudyMaterial]:
    from deeptutor.book.storage import get_book_storage

    storage = get_book_storage()
    needle = query.casefold()
    items: list[StudyMaterial] = []
    for book_id in storage.list_book_ids():
        book = storage.load_book(book_id)
        if book is None:
            continue
        if needle and needle not in (book.title or "").casefold():
            continue
        detail = " · ".join(
            part
            for part in (
                f"{book.page_count} pages" if book.page_count else "",
                str(getattr(book.status, "value", book.status) or ""),
            )
            if part
        )
        item = _material(
            "book", book.id, book.title or book.id, detail, f"/learning/books/{book.id}"
        )
        if item is not None:
            items.append(item)
    return items


def _reading_collections(query: str) -> list[StudyMaterial]:
    from deeptutor.reading.catalog_store import ReadingCatalogStore

    # Constructing the catalog creates its database; skip a workspace that never
    # used Reading rather than turning a picker into a write.
    if not get_path_service().get_workspace_feature_dir("reading").exists():
        return []
    needle = query.casefold()
    items: list[StudyMaterial] = []
    for row in ReadingCatalogStore().list_workspaces(limit=MAX_MATERIALS_PER_KIND):
        title = str(getattr(row, "title", "") or "")
        if needle and needle not in title.casefold():
            continue
        item = _material(
            "reading",
            str(getattr(row, "id", "")),
            title,
            "Reading collection",
            f"/learning/reading/{getattr(row, 'id', '')}",
        )
        if item is not None:
            items.append(item)
    return items


def _course_chapters(query: str) -> list[StudyMaterial]:
    from deeptutor.services.study_plan.courses import load_catalog

    # Missing catalog means the learner never imported a course; an unreadable
    # one raises so the picker can report *why* the kind is unavailable.
    courses = load_catalog(get_path_service().get_course_catalog_file())
    needle = query.casefold()
    items: list[StudyMaterial] = []
    for course in courses:
        for chapter in course.chapters:
            if needle and needle not in f"{chapter.title} {course.title}".casefold():
                continue
            item = _material(
                "course",
                f"{course.id}/{chapter.id}",
                chapter.title,
                course.title,
                chapter.url,
            )
            if item is not None:
                items.append(item)
    return items


def _mastery_paths(query: str) -> list[StudyMaterial]:
    from deeptutor.learning.navigation import topic_cards

    if not (get_path_service().get_workspace_dir() / "learning").exists():
        return []
    items: list[StudyMaterial] = []
    for card in topic_cards(query=query).get("topics", []):
        objectives = int(card.get("objectives") or 0)
        mastered = int(card.get("mastered") or 0)
        detail = f"{mastered}/{objectives} mastered" if objectives else "Mastery path"
        item = _material(
            "mastery",
            str(card.get("path_id") or ""),
            card.get("name") or "",
            detail,
            f"/learning/mastery/{card.get('path_id')}",
        )
        if item is not None:
            items.append(item)
    return items


def _practice_banks(query: str) -> list[StudyMaterial]:
    from deeptutor.services.session.sqlite_store import get_sqlite_session_store

    # Opening the chat-history database creates it; skip a workspace that never
    # answered a question instead of turning discovery into a write.
    if not get_path_service().get_chat_history_db().exists():
        return []
    needle = query.casefold()
    items: list[StudyMaterial] = []
    store = get_sqlite_session_store()
    rows = store.list_question_bank_materials_sync()[:MAX_MATERIALS_PER_KIND]
    for row in rows:
        source = str(row.get("source") or "")
        material_id = str(row.get("material_id") or "")
        if not material_id:
            continue
        title = str(row.get("material_title") or "") or material_id
        if needle and needle not in title.casefold():
            continue
        count = int(row.get("entry_count") or 0)
        unresolved = int(row.get("unresolved_count") or 0)
        detail = f"{count} questions" if count else "Practice bank"
        if unresolved:
            detail += f" · {unresolved} to review"
        item = _material(
            "practice",
            f"{source}:{material_id}" if source else material_id,
            title,
            detail,
            "/learning/practice?view=bank",
        )
        if item is not None:
            items.append(item)
    return items


def _notebooks(query: str) -> list[StudyMaterial]:
    if not get_path_service().get_notebook_dir().exists():
        return []
    from deeptutor.services.notebook.service import get_notebook_manager

    needle = query.casefold()
    items: list[StudyMaterial] = []
    for row in get_notebook_manager().list_notebooks()[:MAX_MATERIALS_PER_KIND]:
        title = str(row.get("name") or "")
        if needle and needle not in title.casefold():
            continue
        count = int(row.get("record_count") or row.get("records") or 0)
        item = _material(
            "notebook",
            str(row.get("id") or ""),
            title or "Notebook",
            f"{count} records" if count else "Notebook",
            f"/notebooks/{row.get('id')}",
        )
        if item is not None:
            items.append(item)
    return items


def _documents(query: str) -> list[StudyMaterial]:
    if not get_path_service().get_co_writer_docs_dir().exists():
        return []
    from deeptutor.co_writer.storage import get_co_writer_storage

    needle = query.casefold()
    items: list[StudyMaterial] = []
    for doc in get_co_writer_storage().list_documents()[:MAX_MATERIALS_PER_KIND]:
        if needle and needle not in (doc.title or "").casefold():
            continue
        item = _material(
            "document",
            doc.id,
            doc.title or doc.id,
            "Co-Writer document",
            f"/co-writer/{doc.id}",
        )
        if item is not None:
            items.append(item)
    return items


_PROVIDERS: dict[str, Callable[[str], list[StudyMaterial]]] = {
    "knowledge": _knowledge_bases,
    "book": _books,
    "reading": _reading_collections,
    "course": _course_chapters,
    "mastery": _mastery_paths,
    "practice": _practice_banks,
    "notebook": _notebooks,
    "document": _documents,
}


@dataclass(slots=True)
class MaterialDiscovery:
    materials: list[StudyMaterial] = field(default_factory=list)
    unavailable: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "materials": [material.to_dict() for material in self.materials],
            "unavailable": list(self.unavailable),
            "kinds": [{"kind": kind, "label": KIND_LABELS[kind]} for kind in MATERIAL_KINDS],
        }

    def find(self, kind: str, material_id: str) -> StudyMaterial | None:
        for material in self.materials:
            if material.kind == kind and material.id == material_id:
                return material
        return None


def _reason(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    if len(text) > 160:
        text = text[:159].rstrip() + "…"
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def discover(
    *,
    query: str = "",
    kinds: Sequence[str] | None = None,
) -> MaterialDiscovery:
    """List the learner's materials, one failure-isolated provider at a time."""
    wanted = [kind for kind in (kinds or MATERIAL_KINDS) if kind in _PROVIDERS]
    needle = " ".join(str(query or "").split())
    discovery = MaterialDiscovery()
    for kind in wanted:
        try:
            rows = _PROVIDERS[kind](needle)
        except Exception as exc:  # a disabled or broken feature must not block the rest
            discovery.unavailable.append({"kind": kind, "reason": _reason(exc)})
            continue
        discovery.materials.extend(rows[: KIND_LIMITS.get(kind, MAX_MATERIALS_PER_KIND)])
    discovery.materials.sort(key=_sort_key)
    return discovery


def _sort_key(item: StudyMaterial) -> tuple[int, str, str]:
    """Curriculum order for courses, alphabetical elsewhere.

    A course's chapter ids are its path on disk (``00-core/01-networks``), so
    sorting by id keeps the learner's own reading order instead of shuffling
    "Chapter 10" before "Chapter 2".
    """
    position = MATERIAL_KINDS.index(item.kind)
    if item.kind == "course":
        return (position, item.id.casefold(), item.title.casefold())
    return (position, item.title.casefold(), "")


def resolve(
    refs: Iterable[tuple[str, str]],
    *,
    discovery: MaterialDiscovery | None = None,
) -> tuple[list[StudyMaterial], list[str]]:
    """Resolve ``(kind, id)`` pairs to canonical materials.

    Returns ``(resolved, missing)`` where ``missing`` holds ``kind:id`` strings
    the caller asked for but which no longer exist. Titles always come from the
    owning feature, never from the request, so a stale browser tab cannot pin a
    renamed book under its old name.
    """
    pairs = [(str(kind), str(material_id)) for kind, material_id in refs]
    if not pairs:
        return [], []
    wanted_kinds = sorted({kind for kind, _ in pairs})
    found = discovery or discover(kinds=wanted_kinds)
    resolved: list[StudyMaterial] = []
    missing: list[str] = []
    for kind, material_id in pairs:
        material = found.find(kind, material_id)
        if material is None:
            missing.append(f"{kind}:{material_id}")
        elif material not in resolved:
            resolved.append(material)
    return resolved, missing


__all__ = [
    "KIND_LABELS",
    "MATERIAL_KINDS",
    "MAX_MATERIALS_PER_KIND",
    "MaterialDiscovery",
    "discover",
    "resolve",
]
