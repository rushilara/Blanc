# Backend (Owner B)

FastAPI service: case/record persistence (SQLite + vector index), hybrid retrieval with exclusion filtering, structured LLM reconstruction, follow-up and exclusion endpoints.

See `../docs/02_ARCHITECTURE_AND_DATA_CONTRACTS.md` and `../docs/05_CLAUDE_CODE_KICKOFF.md` (tasks B1–B5). Validate all output against `../contracts/*.schema.json`.

## Stack

Python 3.12, FastAPI, Pydantic v2, pydantic-settings, uvicorn, SQLAlchemy 2.0 over SQLite. LLM and embeddings: Google Gemini API (`google-genai`). Vector index: [sqlite-vec](https://github.com/asg017/sqlite-vec) (`vec0` virtual table); keyword index: SQLite FTS5. Both live in the same SQLite file as the tables.

## Run

From the repo root: `make backend` (FastAPI with reload on `BACKEND_PORT`, default 8000) and `make test`.

Or manually from `backend/`:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --reload --port 8000
.venv/bin/pytest -q
.venv/bin/ruff check .
```

### Tests and live tests

`make test` / CI run fully offline: every test embeds with the deterministic `FakeEmbedder` (`app/embeddings.py`). Tests that call a real API are marked `@pytest.mark.live`; `pytest.ini` deselects them by default (and so in CI). Run them on demand with `make test-live` from the repo root: it runs only the live tests, which read `GEMINI_API_KEY` from the root `.env` and skip if it is unset.

### sqlite-vec needs a Python that can load SQLite extensions

Every DB connection loads the sqlite-vec extension (`app/db.py`). If startup fails with "cannot load SQLite extensions" (or `AttributeError: ... enable_load_extension`), your Python was built without loadable extension support (common with the macOS system Python and some pyenv builds). Use a Python 3.12 from python.org or Homebrew (`brew install python@3.12`), or rebuild with pyenv: `PYTHON_CONFIGURE_OPTS="--enable-loadable-sqlite-extensions" pyenv install 3.12`. Then recreate the venv (`make clean && make backend`, or `make backend PYTHON=/path/to/python3.12`). GitHub's `setup-python` builds (CI) support it.

#### Apple Silicon: "Fatal Python error: Illegal instruction"

On an Apple Silicon Mac, an x86_64 (Intel) Python running under Rosetta, such as some Miniconda installs, can crash outright when loading sqlite-vec with `Fatal Python error: Illegal instruction`. It is a hard crash, so the friendly "cannot load SQLite extensions" error above never prints. Check the Python your venv uses:

```bash
backend/.venv/bin/python -c "import platform; print(platform.machine())"   # must print arm64
```

If it prints `x86_64`, install a native arm64 Python 3.12 (the python.org installer, or Homebrew under `/opt/homebrew`: `brew install python@3.12`) and recreate the venv: `make clean && make backend PYTHON=/opt/homebrew/bin/python3.12`. The app also logs a warning at startup (logger `app.db`) when it detects an x86_64 Python on an arm64 Mac, just before the crash would happen. A native Python that lacks `enable_load_extension` does not crash: it shows the error described above.

## Config

`app/config.py` defines `Settings` (pydantic-settings), loaded from the repo-root `.env`; real environment variables override it. Fields: `GEMINI_API_KEY` (LLM and embeddings), `GEMINI_MODEL`, `EMBEDDING_MODEL` (default `gemini-embedding-001`), `EMBEDDING_DIM` (default 768; the Gemini `output_dimensionality` and the vector index size), `DATABASE_URL`, `CORS_ORIGINS` (comma-separated). Keys are optional, so the app starts without them. Use `get_settings()` to access config.

## Layout

- `app/main.py`: app factory (`create_app`), CORS, lifespan (opens the DB and creates tables), `GET /health` (status plus a non-secret config summary).
- `app/config.py`: settings.
- `app/db.py`: SQLAlchemy 2.0 engine/session factory, `Base`, `UTCDateTime`, `get_session` dependency. SQLite foreign keys are enabled on every connection, and the DB file's parent directory is created on startup.
- `app/models.py`: ORM tables: `Case`, `EvidenceRecordRow` (`evidence_records`), `EvidenceChunk` (`evidence_chunks`), `BoardSnapshotRow` (`board_snapshots`, append-only).
- `app/evidence_store.py`: `store_records` (persist records + chunks + vectors + keyword index), `reembed_stale_chunks`, case-scoped reads. See "Evidence storage" below.
- `app/search_index.py`: sqlite-vec and FTS5 virtual tables, their cleanup trigger, and case-scoped `vector_search` / `keyword_search` primitives (ranking/fusion is BLA-13).
- `app/chunking.py`: `chunk_spans`, the ~400-token / ~50-token-overlap splitter.
- `app/embeddings.py`: `Embedder` protocol, `GeminiEmbedder`, `FakeEmbedder` (tests), `make_embedder(settings)`.
- `app/schemas/`: Pydantic request/response models. Kept separate from ORM tables.
- `app/deps.py`: shared dependencies: `SessionDep`, `require_case` / `CaseDep`, `get_case_or_404`, `EmbedderDep` (the app's embedder, `app.state.embedder`).
- `app/ids.py`: `new_id(prefix)` returns prefixed ULIDs (`case_01J...`). Use it for every server-generated ID (`analysis_`, `board_`, `rec_`, ...).
- `app/contracts.py`: Pydantic models for the shared contracts in `../contracts/` (`EvidenceRecord`, `AnalysisRevision`, `InvestigationBoard`, `RevisionComparison`). Kept in sync with the JSON Schemas by `tests/test_contracts.py`.
- `app/validation.py`: cross-reference checks (`validate_revision`, `validate_board`, `validate_comparison`) plus `schema_errors` for parsing untrusted JSON. Each returns readable error strings; an empty list means valid.
- `app/routers/`: HTTP routers (`cases.py`: `POST /cases`, `GET /cases`, `GET /cases/{case_id}`; `records.py`: `GET /cases/{case_id}/records[/{record_id}]`; `boards.py`: `/cases/{case_id}/boards`).
- `app/boards.py`: investigation boards: create, pin/unpin, notes, append-only snapshots. See "Boards" below.
- `app/records.py`: case-scoped record + exact excerpt lookup. See "Source records" below.
- `tests/`: pytest suite (`TestClient`). `conftest.py` gives each test its own temp SQLite file.

## Persistence

SQLite via SQLAlchemy 2.0 (typed `Mapped[...]` ORM) at `DATABASE_URL` (default `sqlite:///./data/blanc.db`, relative to `backend/`; `data/` is gitignored). On Railway, mount the volume at `/data` and set `DATABASE_URL=sqlite:////data/blanc.db` (four slashes: absolute path); run a single instance, since SQLite is a local file. Tables are created with `Base.metadata.create_all` at startup. There are no migrations yet (Alembic can come later), so during development delete `backend/data/blanc.db` after changing a table.

The DB is opened in the lifespan, not at import. In tests, enter the client (`with TestClient(app) as client:`) so the lifespan runs.

## Evidence storage (BLA-12)

The importer (BLA-9) turns files into `EvidenceRecord` objects and calls:

```python
from app.evidence_store import store_records
result = store_records(session, case_id, records, embedder)  # embedder: EmbedderDep / make_embedder(settings)
session.commit()  # store_records flushes; the caller owns the transaction
# result.inserted, result.unchanged, result.chunks_created, result.rejected: [RejectedRecord(record_id, reason)],
# result.source_changed: [record_id] (a subset of unchanged; see "Re-import" below)
```

- **Tables.** `evidence_records`: PK `(case_id, record_id)` (record IDs are only unique within a case), all contract fields, plus `content_hash`. `evidence_chunks`: `chunk_id` (`chk_` ULID), `case_id`, `record_id` (composite FK to the record, `ON DELETE CASCADE`), `chunk_index`, `char_start`/`char_end` (so `record.content[char_start:char_end] == chunk.text`), `text`, `embedding_model`, `embedding_dim`. Both cascade from `cases`.
- **Indexes.** `evidence_chunk_vectors` (sqlite-vec `vec0`, cosine distance): `chunk_id`, `case_id` as a **partition key**, `record_id` as a metadata column, `embedding float[EMBEDDING_DIM]`. `evidence_chunks_fts` (FTS5): `text` plus `UNINDEXED` `chunk_id`/`case_id`/`record_id`. Virtual tables cannot have FKs, so `evidence_chunks` is the source of truth and an `AFTER DELETE` trigger on it deletes the matching vector and FTS rows: deleting a chunk, record or case never leaves orphans.
- **Case scoping in search.** `vector_search(session, case_id, vector, k)` puts `case_id = ?` in the KNN query, so the partition key restricts the scan *before* the `k` nearest are picked. `keyword_search(session, case_id, match, k)` filters `case_id = ?` in the same statement as `MATCH`, before `ORDER BY bm25 LIMIT k`. Never query the virtual tables without a `case_id` filter.
- **Re-import.** Identical records (same SHA-256 of the canonical contract JSON) are a no-op: no new rows, no embedding calls. A stored `record_id` that differs **only** in `original_sha256` (the hash of the whole source file, so it changes when, say, messages are appended to a chat export) counts as unchanged: the stored version is kept with its old hash, nothing is embedded, and the ID is listed in `result.source_changed` (also counted in `unchanged`) so the importer can tell the user the file changed. A stored `record_id` arriving with any **other** difference is rejected and the stored version kept (records are immutable); the reason names the differing fields, e.g. `(fields: content, timestamp)`; so is a record whose `case_id` is not the target case. Other records in the call are still stored. An unknown case raises `CaseNotFoundError`. Records are embedded before anything is written, so an embedding failure writes nothing.
- **Chunking.** ~4 characters per token (no tokenizer dependency). Content up to 400 tokens (1600 chars) is one chunk; longer content is split into 1600-char windows overlapping by ~200 chars, with boundaries moved to whitespace. Empty or whitespace-only content is stored with no chunks.
- **Embeddings.** `GeminiEmbedder` calls `gemini-embedding-001` with `output_dimensionality=EMBEDDING_DIM`, task type `RETRIEVAL_DOCUMENT` for chunks and `RETRIEVAL_QUERY` for queries (`embed_query`, for BLA-13), in batches of 100, with SDK retries on 429/5xx. Each chunk stores the model and dimension. After changing `EMBEDDING_MODEL`, run `reembed_stale_chunks(session, case_id, embedder)` per case. After changing `EMBEDDING_DIM`, the vector table is recreated empty at startup (a warning is logged) and the same call rebuilds it; `store_records` refuses an embedder whose dimension does not match the index.

## Case isolation (required for every new table and query)

Every piece of evidence belongs to exactly one case. Search for one case must never return another case's records. To guarantee this:

1. **Every table that holds evidence or anything derived from it** (records, chunks, embeddings, analyses, boards, ...) has a `case_id` column declared `ForeignKey("cases.case_id"), nullable=False`, plus an index on it. Child rows (for example chunks of a record) carry their own `case_id` too, so they can be filtered without a join.
2. **Every route under `/cases/{case_id}/...` resolves the case with `CaseDep`** (or `require_case`). An unknown case returns 404 before any other query runs.
3. **Every query is scoped by the resolved `case.case_id`.** Never look up a record, chunk or analysis by its own ID alone. Filter by `(case_id, id)`, so an ID from another case behaves as not found (404). The same applies to vector-index searches: filter by `case_id` inside the search, not after it.
4. Tests for each new table or endpoint must create two cases and assert that nothing from one case is visible through the other.

## Returning contract models

Serialize contract models with `to_contract_json(model)` (or `model.to_contract_json()`) from `app.contracts`. If a route returns the model directly, set `response_model_exclude_unset=True`.

Plain `model_dump()` / `model_dump_json()` is **not** schema-valid. Some fields may be left out but must not be `null` (edge `description`, a node board item's `status`, `needs_review`, ...). These default to `None` internally, so a plain dump writes them as `null`, which `../contracts/*.schema.json` rejects. `to_contract_json` drops them and keeps the fields the schema allows to be `null` (`parent_analysis_id`, `base_ref_id`, ...).

## Source records (BLA-59)

The evidence drawer (BLA-40) resolves a citation to its source text with
`GET /cases/{case_id}/records/{record_id}`. Optional query params select an exact excerpt, **at most one mode**:

- `chunk_id=chk_...`: the excerpt is that chunk's stored text and offsets. The chunk must belong to this case **and** this record.
- `char_start=N&char_end=M` (both required): the excerpt is `content[N:M]`. Offsets are Python `str` indices (Unicode code points, not UTF-8 bytes or UTF-16 units), the same as `evidence_chunks.char_start`/`char_end`. Requires `0 <= N < M <= len(content)`.

```json
{
  "record": {"record_id": "rec-01", "case_id": "case_01J...", "source_file": "mailbox.eml",
             "source_locator": "message 1, line 12", "content": "Alex emails Jordan ...",
             "timestamp": "2026-04-10T09:15:00Z", "timestamp_kind": "sent",
             "original_sha256": null, "metadata": {"sender": "Alex"}},
  "excerpt": {"text": "Alex emails", "char_start": 0, "char_end": 11, "chunk_id": null}
}
```

`record` is exactly the `EvidenceRecord` contract JSON (`to_contract_json`: omitted `timestamp_kind`/`metadata` are absent, never null). `excerpt` is `null` when no mode is given; `excerpt.chunk_id` is null in offset mode.

Errors: unknown case, a record not in this case (even if another case has that `record_id`), or a chunk not in this case and record: **404** (`{"detail": "Case not found" | "Record not found" | "Chunk not found"}`). Both modes at once, a lone `char_start`/`char_end`, negative, empty/reversed or past-the-end offsets, or an empty `chunk_id`: **422**. The record is resolved before offsets are checked, so offsets cannot probe another case's content length.

`GET /cases/{case_id}/records?limit=50&offset=0` lists the case's records ordered by `(source_file, record_id)`: `{"items": [EvidenceRecord, ...], "total", "limit", "offset"}` (`limit` 1-200).

The logic lives in `app/records.py` (`get_record_with_excerpt`, `chunk_excerpt`, `span_excerpt`, `page_records`) for reuse by reconstruction and citation checks; it raises `RecordNotFoundError` / `ChunkNotFoundError` / `InvalidExcerptError`. Stored content is untrusted evidence and is returned verbatim; the UI must render it as text, never as HTML.

## Boards (BLA-31)

Every response is `InvestigationBoard` contract JSON. A board is a series of append-only snapshots: creating one writes revision 1, and every change writes `revision + 1` with `parent_revision` set. Rows are never updated (a SQLite trigger rejects `UPDATE` on `board_snapshots`).

| Endpoint | Does |
|---|---|
| `POST /cases/{case_id}/boards` | `{"title", "active_analysis_id"?}` creates an empty board (201). |
| `GET /cases/{case_id}/boards` | Latest snapshot of every board, most recently updated first. |
| `GET /cases/{case_id}/boards/{board_id}` | Latest snapshot. |
| `GET /cases/{case_id}/boards/{board_id}/revisions[/{n}]` | Every snapshot, oldest first, or snapshot `n`. |
| `PATCH /cases/{case_id}/boards/{board_id}` | Unpin, pin, add notes, rename, set the active revision; appends one snapshot. |

`PATCH` body (every field optional, applied in this order):

```json
{
  "expected_revision": 3,
  "unpin": ["item_..."],
  "add_reconstruction": {"analysis_id": "analysis_..."},
  "pin": [{"kind": "claim", "source_analysis_id": "analysis_...", "ref_id": "claim-2"}],
  "notes": [{"text": "Check whether the meeting happened.", "target_item_id": "item_...", "author": "investigator"}],
  "title": "New title",
  "active_analysis_id": "analysis_..."
}
```

- **A pin names a finding; the server copies its `evidence_ids` and `status`** from the saved revision. The request cannot set either, so pinning never changes a status or marks anything verified.
- **"Add this reconstruction to board"** (BLA-30): `"add_reconstruction": {"analysis_id": "analysis_...", "kinds": ["claim", "node", "edge", "event"]}` pins every finding of that revision in one change (`kinds` optional, default all), through the same checks as single pins. Nodes that cite no evidence are skipped. Adding the same revision again changes nothing; adding a second revision appends its findings next to the first.
- **Nodes are never merged.** A node is identified by its revision and ID, so the same or a similar name from another revision (or the same revision) stays a separate item; deciding two are the same person is left to the investigator.
- **Stable item IDs.** `item_id` is derived from `(source_analysis_id, kind, ref_id)` (`app.boards.item_id_for`), so pinning a finding twice is a no-op, and re-pinning after an unpin gives the same ID. A note can target an item pinned in the same request by that ID.
- **Notes are investigator-only text**, kept in `notes`, never in items. Notes are never removed, so an item with notes cannot be unpinned (422).
- **Validated before saving.** The new snapshot goes through `validate_board`. Any error is a 422 listing every problem, and nothing is saved.
- **Errors:** 404 for an unknown case or board, or a board of another case; 409 when `expected_revision` is stale or another save took that revision number; 422 for an invalid change. A change that alters nothing returns the current snapshot and writes nothing.
- **Revision lookup.** Boards find saved revisions through the `get_revision_lookup` dependency in `app/routers/boards.py`. Saved revisions are BLA-28, so for now it knows none: boards can be created, renamed and annotated, but every pin is a 422 (`no saved revision ... in this case`). BLA-28 should return its lookup there.
