# Study plans

[Handbook](README.md) · [Testing](TESTING.md) · [Modules](MODULES.md)

A study plan answers a different question than Mastery Path. Mastery Path
scores whether a learner may advance through a curriculum; a study plan decides
**when** they sit down. It takes a goal, a deadline, and the hours a learner can
actually commit, and lays out dated sessions that can be marked done or skipped.
The plan can point at the learner's real materials — books, mastery paths,
reading collections, course chapters, knowledge bases, practice banks,
notebooks, and Co-Writer documents — so a session is not just a free-text
goal.

The scheduler is deterministic. The model owns the conversation and supplies
parameters; `deeptutor/services/study_plan/scheduler.py` decides every date.
That keeps schedules auditable ("why is this session on a Friday?") and testable
without a clock, a database, or an LLM.

## Ownership

| Concern | Owner |
| --- | --- |
| Dataclasses and serialization | `deeptutor/services/study_plan/models.py` |
| Date arithmetic | `deeptutor/services/study_plan/scheduler.py` |
| Atomic JSON store | `deeptutor/services/study_plan/store.py` |
| Material discovery and resolution | `deeptutor/services/study_plan/materials.py` |
| Course catalog reader | `deeptutor/services/study_plan/courses.py` |
| Course catalog importer | `scripts/import_course_catalog.py` |
| Turn capability and prompt policy | `deeptutor/capabilities/study_plan/` |
| REST adapter | `deeptutor/api/routers/study_plan.py` |
| Module contract | `deeptutor/modules/manifests/study-plan.json` |
| Web surface | `web/features/study-plan/`, `web/app/(workspace)/study-plan/` |
| Regression tests | `tests/services/study_plan/`, `tests/api/test_study_plan.py`, `tests/tools/test_study_plan_tools.py` |

Plans live at `<workspace>/study_plan/plans.json` through
`PathService.get_study_plan_file()`, so switching workspace switches plans. The
file is the only durable state; there is no database migration.

## Data model

A plan stores `goal`, `start_date`, `deadline`, `weekly_hours`,
`session_minutes`, `weekdays`, `materials`, `created_at`, `updated_at`,
`archived`, and its `sessions`.

`materials` is a list of references, not copies:

| `kind` | Resolves to | Session link |
| --- | --- | --- |
| `knowledge` | Knowledge-base name | `/knowledge-bases/{name}` |
| `book` | Book id | `/learning/books/{id}` |
| `reading` | Immersive Reading workspace id | `/learning/reading/{id}` |
| `course` | `course_id/chapter_path` from the imported catalog | the published page (absolute URL) |
| `mastery` | Mastery-path topic id | `/learning/mastery/{id}` |
| `practice` | `source:material_id` from the review history | `/learning/practice?view=bank` |
| `notebook` | Notebook id | `/notebooks/{id}` |
| `document` | Co-Writer document id | `/co-writer/{id}` |
| `custom` | The plan's own free text | none |

Titles are always resolved from the owning feature at creation time and stored
with the ref, so a plan keeps reading sensibly after a source is renamed or
deleted. A request cannot pin its own title onto a library material: the REST
route and the chat tool both call `materials.resolve()`, which looks the record
up and returns the owner's title. Unknown refs are rejected — `400` over HTTP,
a failed tool result in chat — never silently dropped.

Each session is `{id, date, week, title, duration_minutes, status,
completed_at}` with `status` one of `pending | done | skipped`. Marking a
session records `completed_at`; resetting it clears the timestamp.

Free-text-only plans keep working: files written before materials existed carry
a `topics` list, which is read-migrated to `custom` materials.

## Scheduling rules

`build_schedule()` raises `ScheduleError` (a `ValueError`) for anything it
cannot satisfy, so the caller can show the learner a real reason instead of an
empty plan.

- The window is inclusive: `start_date` through `deadline`. A deadline before
  the start date is rejected, as is a span longer than ten years.
- Each calendar week gets `weekly_hours × 60` minutes of budget. Eligible
  weekdays are visited in date order; a session is placed whenever the
  remaining budget covers one whole `session_minutes` block.
- Leftover minutes that cannot fit a session are dropped, never rolled into the
  next week, so a week cannot silently borrow study time.
- Weekdays default to Monday/Wednesday/Friday. Session length defaults to 60
  minutes and must be 5–480 minutes.
- Weekly hours must be enough for at least one session
  (`weekly_hours × 60 ≥ session_minutes`).
- At most 200 sessions are produced. A longer window is truncated at 200 and
  the result reports `truncated`.
- With no materials, session titles are `"{goal} — part N"`. With materials,
  titles cycle through the material titles in order.

Examples: 6 h/week on Mon/Wed/Fri at 60 minutes yields three sessions per week;
3 h/week at 90 minutes yields two (the third 90-minute block does not fit).

## Store semantics

`StudyPlanStore` mirrors the learning journal's contract:

- **Atomic writes.** Mutations go through `atomic_write_json`, so a crash never
  leaves a half-written file.
- **Serialized mutations.** A per-file thread lock plus a cross-process file
  lock make read-modify-write safe under the single-process server and under
  concurrent CLI calls. On Windows the lock probe uses `os.fstat` size rather
  than `read()` on a locked handle.
- **Deduplication.** Creating a plan with the same normalized goal, deadline,
  start, hours, session length, weekdays, and material key returns the existing
  plan (`deduplicated=True`) instead of a duplicate.
- **Corruption is preserved.** An unreadable or unsupported file raises; the
  REST route turns that into `409` and leaves the bytes untouched.
- **Limits.** 50 plans, 20 materials, 200 goal characters, 120 characters per
  material title. The chat injection carries the three most relevant active
  plans, not the whole file.

## Material discovery

`materials.discover()` lists what the learner owns, one provider per kind. Two
invariants keep it safe to call from a picker that may be opened in a fresh
workspace:

- **No side effects.** Constructors that would `mkdir` (knowledge bases,
  reading catalog, notebooks, co-writer docs) are only reached after the
  feature's directory already exists. A workspace that never used Reading
  reports nothing for it and stays empty.
- **Failure isolation.** Providers are imported lazily and wrapped
  individually. A disabled or broken feature lands in `unavailable` with a
  short reason; the other kinds still list.

| Kind | Source | Guard before construction |
| --- | --- | --- |
| `knowledge` | `current_kb_manager().list_knowledge_bases()` | `current_kb_base_dir().exists()` |
| `book` | `get_book_storage().list_book_ids()` then `load_book()` | storage self-guards |
| `reading` | `ReadingCatalogStore().list_workspaces(limit=…)` | `workspace/reading` exists |
| `course` | `courses.load_catalog()` | `courses/catalog.json` exists |
| `mastery` | `learning.navigation.topic_cards(query=…)` | `workspace/learning` exists |
| `practice` | `store.list_question_bank_materials_sync()` | `chat_history.db` exists |
| `notebook` | `get_notebook_manager().list_notebooks()` | notebook dir exists |
| `document` | `get_co_writer_storage().list_documents()` | co-writer docs dir exists |

Rows are capped at 50 per kind (500 for `course`, which is browsed with the
picker's filter), titles at 120 characters, details at 160. Courses sort by
chapter path so the curriculum order survives; every other kind sorts by title.
`resolve()` returns `(resolved, missing)`; the caller decides how to report the
missing refs.

## Course catalogs

A `course` material is a chapter of a site the learner already publishes, such
as a Docusaurus course. The catalog is a local JSON index — no request ever
fetches a URL, so discovery is offline, deterministic, and has no SSRF surface:

```json
{
  "version": 1,
  "courses": [
    {
      "id": "ai-engineering-placement",
      "title": "Backend + AI Engineer — Complete Placement Course",
      "url": "https://example.github.io/repo/ai-engineering-placement/",
      "chapters": [
        {
          "id": "01-python-programming/03-strings-and-formatting",
          "title": "Strings & Formatting",
          "url": "https://example.github.io/repo/ai-engineering-placement/01-python-programming/03-strings-and-formatting/"
        }
      ]
    }
  ]
}
```

Build or refresh it from the Markdown sources; only pages with a front-matter
`slug` are published, so drafts and `PLAN.md` are skipped. The output merges by
course id, so importing a second course keeps the first:

```bash
python scripts/import_course_catalog.py \
  --docs ../ai-engineering-journey/site/docs/courses/ai-engineering-placement \
  --base-url https://raushan666java.github.io/ai-engineering-journey \
  --out data/courses/catalog.json
```

The file lives at `<workspace>/courses/catalog.json` through
`PathService.get_course_catalog_file()`. Chapter titles, ids, and URLs come from
the catalog; a malformed catalog marks the kind `unavailable` instead of
returning a half-list, and a missing file simply means no course was imported.

## REST API

Mounted at `/api/study-plans` with the `learning` auth policy.

| Method and path | Body | Result |
| --- | --- | --- |
| `GET /api/study-plans` | — | `{version, updated_at, today, plans, injected_context}` |
| `GET /api/study-plans/materials?query=&kinds=` | — | `{materials, unavailable, kinds}` |
| `POST /api/study-plans` | `goal, deadline, weekly_hours, start_date?, session_minutes?, weekdays?, materials?, topics?` | `201` with the plan |
| `GET /api/study-plans/{plan_id}` | — | the plan, `404` when absent |
| `POST /api/study-plans/{plan_id}/sessions/{session_id}/status` | `{status}` | the updated plan |
| `PATCH /api/study-plans/{plan_id}` | `{archived}` | the updated plan |

`GET /materials` is declared before `/{plan_id}` so the literal segment can
never be parsed as a plan id. A `materials` entry is
`{kind, id?, title?}`: `id` is required for library kinds, `title` for
`custom`. `topics` remains a deprecated alias that becomes `custom` materials.

Errors: `400` for invalid dates, unknown kinds, unknown material refs, or
parameters the scheduler rejects; `409` for an unreadable/unsaveable file;
`404` for an unknown plan or session.

## Chat tools

| Tool | Purpose |
| --- | --- |
| `study_plan_materials` | List passable refs (`book:book_abc`) by kind, with unavailable kinds reported |
| `study_plan_status` | Active plans, today's sessions, next session, progress |
| `study_plan_create` | Build the schedule from goal, deadline, hours, and material refs |
| `study_plan_mark` | Mark one session `done`, `skipped`, or `pending` |

The capability is opt-in per turn through `study_plan_mode`, contributes a
system block, and seeds the loop with `injection_markdown()`.

## Web surface

`/study-plan` renders `features/study-plan/components/StudyPlanPage.tsx`:
a create form (goal, dates, weekly hours, session length, study days), a
material picker fed by `GET /materials`, and plan cards whose sessions can be
marked or reset and whose progress is a real `progressbar`. Archived plans are
collapsed behind a toggle. The sidebar entry is declared in
`web/components/sidebar/nav-entries.ts`; the manifest also advertises
`/study-plan` in `navigation`.

## Tests

```sh
.venv/Scripts/python.exe -m pytest tests/services/study_plan -q
.venv/Scripts/python.exe -m pytest tests/api/test_study_plan.py tests/tools/test_study_plan_tools.py -q
.venv/Scripts/python.exe scripts/check_architecture.py --root .
```

The suite covers: schedule arithmetic and validation, store atomicity and
deduplication, material discovery isolation and no-write behaviour, title
resolution, ref rejection, the REST contract (including route precedence and
corrupt-file preservation), the tool contract against the manifest, and the
legacy `topics` read-migration.

## Extending

To schedule a new material kind:

1. Add the kind to `MATERIAL_KINDS` and a label to `KIND_LABELS`.
2. Write a provider that returns `StudyMaterial` rows, guards any constructor
   that would create a directory, and register it in `_PROVIDERS`.
3. Add the link route to the web learner surfaces and, if the kind is gated,
   the manifest's `optional` list.
4. Extend `tests/services/study_plan/test_materials.py` with a fake provider
   for ordering, failure isolation, and missing-ref reporting.

To change scheduling, change `scheduler.py` and its tests first; the store and
REST layers deliberately contain no date logic.
