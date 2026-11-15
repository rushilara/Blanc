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

`app/config.py` defines `Settings` (pydantic-settings), loaded from the repo-root `.env`; real environment variables override it. Fields: `GEMINI_API_KEY` (LLM and embeddings), `GEMINI_MODEL`, `EMBEDDING_MODEL` (default `gemini-embedding-001`), `EMBEDDING_DIM` (default 768; the Gemini `output_dimensionality` and the vector index size), `DATABASE_URL`, `CASES_DIR` (default `./data/cases`; case-local image/PDF bytes under `{CASES_DIR}/{case_id}/media/`), `CORS_ORIGINS` (comma-separated), and the `RETRIEVAL_*` tuning settings (see "Retrieval" below). Keys are optional, so the app starts without them. Use `get_settings()` to access config.

## Layout

- `app/main.py`: app factory (`create_app`), CORS, lifespan (opens the DB and creates tables), `GET /health` (status plus a non-secret config summary).
- `app/config.py`: settings.
- `app/db.py`: SQLAlchemy 2.0 engine/session factory, `Base`, `UTCDateTime`, `get_session` dependency. SQLite foreign keys are enabled on every connection, and the DB file's parent directory is created on startup.
- `app/models.py`: ORM tables: `Case`, `EvidenceRecordRow` (`evidence_records`), `EvidenceChunk` (`evidence_chunks`), `BoardSnapshotRow` (`board_snapshots`, append-only), `AnalysisRevisionRow` (`analysis_revisions`, immutable), `RevisionRestoreRow` (`revision_restores`, insert-only).
- `app/evidence_store.py`: `store_records` (persist records + chunks + vectors + keyword index), `reembed_stale_chunks`, case-scoped reads. See "Evidence storage" below.
- `app/search_index.py`: sqlite-vec and FTS5 virtual tables, their cleanup trigger, and case-scoped `vector_search` / `keyword_search` primitives, and `to_fts_query` (free text to a safe FTS5 query).
- `app/retrieval.py`: `retrieve`, hybrid vector + keyword retrieval with RRF fusion and the capped evidence window. See "Retrieval" below.
- `app/chunking.py`: `chunk_spans`, the ~400-token / ~50-token-overlap splitter.
- `app/embeddings.py`: `Embedder` protocol, `GeminiEmbedder`, `FakeEmbedder` (tests), `make_embedder(settings)`.
- `app/schemas/`: Pydantic request/response models. Kept separate from ORM tables.
- `app/deps.py`: shared dependencies: `SessionDep`, `require_case` / `CaseDep`, `get_case_or_404`, `EmbedderDep` (the app's embedder, `app.state.embedder`).
- `app/ids.py`: `new_id(prefix)` returns prefixed ULIDs (`case_01J...`). Use it for every server-generated ID (`analysis_`, `board_`, `rec_`, ...).
- `app/contracts.py`: Pydantic models for the shared contracts in `../contracts/` (`EvidenceRecord`, `AnalysisRevision`, `InvestigationBoard`, `RevisionComparison`). Kept in sync with the JSON Schemas by `tests/test_contracts.py`.
- `app/comparison.py`: `compare_revisions`, the deterministic support diff between a revision and its exclusion child (BLA-34). Findings are matched by structure only, never by reused LLM IDs; when a finding lost at least one cited source to the exclusion, a reworded finding that kept only records it cited gets an advisory `possible_matches` link on both items (BLA-63), which never changes `support_status`. See the module docstring and `../contracts/README.md`.
- `app/validation.py`: cross-reference checks (`validate_revision`, `validate_board`, `validate_comparison`) plus `schema_errors` for parsing untrusted JSON. Each returns readable error strings; an empty list means valid.
- `app/routers/`: HTTP routers (`cases.py`: `POST /cases`, `GET /cases`, `GET /cases/{case_id}`; `records.py`: `GET /cases/{case_id}/records[/{record_id}]`; `search.py`: `POST /cases/{case_id}/search`; `boards.py`: `/cases/{case_id}/boards`).
- `app/boards.py`: investigation boards: create, pin/unpin, notes, append-only snapshots. See "Boards" below.
- `app/records.py`: case-scoped record + exact excerpt lookup. See "Source records" below.
- `tests/`: pytest suite (`TestClient`). `conftest.py` gives each test its own temp SQLite file.

## Persistence

SQLite via SQLAlchemy 2.0 (typed `Mapped[...]` ORM) at `DATABASE_URL` (default `sqlite:///./data/blanc.db`, relative to `backend/`; `data/` is gitignored). **MVP is local-first:** keep the DB file on the investigator’s machine and run a single local uvicorn process. Hosted volumes (Railway/Render/etc.) are not part of the MVP evidence path; optional cloud deploy of the app can come later. Tables are created with `Base.metadata.create_all` at startup. There are no migrations yet (Alembic can come later), so during development delete `backend/data/blanc.db` after changing a table.

The DB is opened in the lifespan, not at import. In tests, enter the client (`with TestClient(app) as client:`) so the lifespan runs.

## Original images (`EVIDENCE_ROOT`)

`GET /cases/{case_id}/records/{record_id}/media` returns the original image behind a record (e.g. a Chrome download's saved file) when it was extracted into that case's folder under `EVIDENCE_ROOT/{case_id}/`. Each case has its own extracted files and `artifact_manifest.jsonl` (one JSON object per line: `source_path`, `extracted_file`, `sha256`); without an entry the file is looked up at its original relative path inside the case folder. An entry may also list `record_ids`, linking a file to records by evidence gathered outside the app (for example a download row whose file was later moved, matched by exact name and size, or a Zone.Identifier `HostUrl` equal to the download URL). A record's own paths are tried first, and a linked file goes through the same checks. Only PNG, JPEG, GIF and WebP under 25 MB are served, the leading bytes must match the type, a manifest SHA-256 must match, and paths may not leave the case folder (`..`, absolute paths and escaping symlinks are refused). `X-Source-Path` is percent-encoded (latin-1 safe) and omitted if the path contains CR/LF. 404 means not available locally, 409 a type or hash mismatch. Nothing is fetched from the network.

This path is separate from BLA-68 ingest media under `CASES_DIR/{case_id}/media/` (imported images/PDFs stored with the case DB). `EVIDENCE_ROOT` is for investigator-extracted disk images / path-mapped originals shown in the source panel. See `app/media.py`.

## Evidence storage (BLA-12)

From the command line, `python -m app.evidence PATH --store --title "Case title"` (or `--case-id case_...`) imports and stores in one transaction; `make import` wraps it (see the root README). With `--title` the case is created in the same transaction and its `case_id` is printed on stdout, so a failed parse or embedding leaves no empty case behind (and removes media files that run newly wrote). `--database-url` overrides `DATABASE_URL`. With `--store`, image/PDF bytes go under `{CASES_DIR}/{case_id}/media/` unless you pass `--media-dir`. Without `--store`, the command only parses (`--out records.json`); loose PNG/JPEG/PDF files are skipped unless you pass `--media-dir`. Media records store empty `content` and no chunks until multimodal embedding (BLA-69), so they are not retrieved yet.

The importer (BLA-9) turns files into `EvidenceRecord` objects and calls:

```python
from app.evidence_store import store_records
result = store_records(session, case_id, records, embedder)  # embedder: EmbedderDep / make_embedder(settings)
session.commit()  # store_records flushes; the caller owns the transaction
# result.inserted, result.unchanged, result.chunks_created, result.rejected: [RejectedRecord(record_id, reason)],
# result.source_changed: [record_id] (a subset of unchanged; see "Re-import" below)
```

- **Tables.** `evidence_records`: PK `(case_id, record_id)` (record IDs are only unique within a case), all contract fields, plus `content_hash`. `evidence_chunks`: `chunk_id` (`chk_` ULID), `case_id`, `record_id` (composite FK to the record, `ON DELETE CASCADE`), `chunk_index`, `char_start`/`char_end` (so `record.content[char_start:char_end] == chunk.text`), `text`, `embedding_model`, `embedding_dim`. Both cascade from `cases`.
- **Indexes.** `evidence_chunk_vectors` (sqlite-vec `vec0`, cosine distance): `chunk_id`, `case_id` as a **partition key**, `record_id` as a metadata column, `embedding float[EMBEDDING_DIM]`. `evidence_chunks_fts` (FTS5, `tokenize='porter unicode61 remove_diacritics 2'`): `text` plus `UNINDEXED` `chunk_id`/`case_id`/`record_id`. If an existing database's FTS table has a different tokenizer (databases created before BLA-13 used plain `unicode61`), startup detects it from the table's SQL in `sqlite_master`, logs a warning, drops it and refills it from `evidence_chunks` (no embedding calls). Virtual tables cannot have FKs, so `evidence_chunks` is the source of truth and an `AFTER DELETE` trigger on it deletes the matching vector and FTS rows: deleting a chunk, record or case never leaves orphans.
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

## Retrieval (BLA-13)

`app/retrieval.py` turns a question into the evidence window for analysis (BLA-15):

```python
from app.retrieval import RetrievalConfig, retrieve
result = retrieve(session, case_id, question, embedder, config=RetrievalConfig.from_settings(settings))
# optional per call: limit=..., max_excerpt_chars=..., excluded_record_ids={...}
for rec in result.records:  # best first
    rec.record_id, rec.source_file, rec.source_locator, rec.timestamp, rec.timestamp_kind
    rec.score, rec.matched_by  # fused RRF score; "vector" | "keyword" | "both"
    rec.vector, rec.keyword    # per-retriever rank, best chunk_id and raw score (or None)
    rec.excerpts               # [Excerpt(text, char_start, char_end, chunk_ids, score, truncated)]
result.skipped                 # [SkippedRecord(record_id, rank, score, reason, excerpt_chars, vector_rank, keyword_rank)]
result.budget_substituted      # a lower-ranked record was included after a budget skip
```

- **Pipeline.** The question is embedded with `embed_query` (Gemini `RETRIEVAL_QUERY`) and the `RETRIEVAL_VECTOR_CANDIDATES` (50) nearest chunks of the case are taken from sqlite-vec; the question is converted with `to_fts_query` and the best `RETRIEVAL_KEYWORD_CANDIDATES` (50) BM25 chunks are taken from FTS5. Both searches filter by `case_id` inside the query.
- **Fusion.** Reciprocal rank fusion per record: each list is reduced to records (a record's rank is its best chunk's rank in that list), and `score = sum(weight / (RRF_K + rank))` over the lists the record appears in (`RETRIEVAL_RRF_K` 60, `RETRIEVAL_VECTOR_WEIGHT` / `RETRIEVAL_KEYWORD_WEIGHT` 1.0). Best chunk per list, not a sum over all chunks, so long records are not favoured just for having many chunks; summing across the two lists means a record found by both outranks one found by only one. Ties break by `record_id`.
- **Excerpts.** Each record carries up to `RETRIEVAL_MAX_EXCERPTS_PER_RECORD` (2) excerpts: its best chunks by chunk-level RRF, with overlapping chunks merged. Every excerpt is the exact slice `record.content[char_start:char_end]`.
- **Evidence window.** At most `RETRIEVAL_MAX_RECORDS` (16) records and `RETRIEVAL_MAX_EXCERPT_CHARS` (32000, ~8000 tokens) characters of excerpt text in total. Excerpts that do not fit the remaining budget are skipped (a record with none left is dropped); only the window's first excerpt is truncated, and only if the budget is smaller than it. `dropped_by_limit`, `dropped_by_budget` and `excerpt_chars` report what happened.
- **What the window left out.** `result.skipped` lists every fused candidate that is not in `records`, in rank order: `record_id`, fused `rank` and `score`, `reason` (`"budget"` or `"record_limit"`) and, for budget skips, `excerpt_chars` (the size of the best excerpt that did not fit). It carries IDs and metadata only, never content, plus each search's own rank for the record (`vector_rank`, `keyword_rank`; `None` if that search did not find it). The entries match `dropped_by_budget` / `dropped_by_limit`. `budget_substituted` is true when a lower-ranked record was included after a higher-ranked one was skipped for budget. Analyses save this on the revision as `retrieval_coverage` (BLA-64, below).
- **No relevance threshold.** The vector KNN always returns its `k` nearest chunks, so in a small case every record can land in the window, ranked low. A maximum-distance setting is a TODO if analysis sees noise.
- **Stemming.** The FTS5 index uses the porter stemmer, and quoted query terms are stemmed the same way, so `delivering` matches `delivered` / `delivers` / `deliver`, and `meetings` matches `meeting`. Porter only strips suffixes: irregular forms (`said` / `say`) and some derivations (`deliveries` / `delivering`) still do not match; the vector search covers those.
- **Free text to FTS5.** `keyword_search` takes raw FTS5 syntax (internal callers only). `search_index.to_fts_query(text)` makes user text safe: words are split into letter/digit runs like the `unicode61` tokenizer, every term is quoted (so `AND`/`OR`/`NEAR`/`*`/`:`/`(`/`"` are plain text), words like `it's` or `rec-0042` become phrases (`"it s"`), common English stopwords are dropped unless nothing else is left, and terms are OR'd (at most 32). It returns `None` for empty or punctuation-only text; `retrieve` then returns no records without calling the embedder.
- **Exclusions.** `excluded_record_ids` drops every chunk of those records from both candidate lists before fusion, so they never appear. Pushing the filter into the index queries (so excluded chunks do not use up candidates) and the revision-level exclusion semantics are BLA-14.
- Retrieval is read-only. An unknown case raises `CaseNotFoundError`; an embedder whose dimension does not match the index raises `EmbeddingIndexMismatchError`.
- **Debug endpoint.** `POST /cases/{case_id}/search` with `{"question": "...", "limit": 5}` (`limit` optional) returns the `RetrievalResult` as JSON (including `skipped` and `budget_substituted`), for inspecting and tuning retrieval. A question that is empty or has no letters or digits is a 422 (`question must contain at least one letter or digit`); library callers of `retrieve()` get an empty result instead.

## Analysis with retry (BLA-18)

`app/analysis_retry.py` turns retrieved evidence into a storable `AnalysisRevision`, with at most two LLM calls:

```python
from app.analysis_retry import RevisionMetadata, analyze_with_retry
result = analyze_with_retry(llm, retrieval, case_records, RevisionMetadata(analysis_id, created_at, parent_analysis_id), session=session)
result.revision      # always passes validate_revision; save this
result.needs_review  # True when the revision is a partial result (revision.needs_review)
result.attempts      # per-attempt detail: schema errors, citation warnings, structural errors
```

- **Validation.** Schema problems come from parsing the model's output. Source problems come from `sanitize_citations` (#25): citations outside the permitted evidence, dropped findings, and structural errors such as duplicate IDs.
- **One retry.** If the first answer has any problem, the model is asked once more. The retry prompt is the original prompt, then the previous answer as the model's turn, then a user turn listing every validation error.
- **Partial fallback.** If the retry also fails, the result is the most recent attempt that could be sanitized into a valid revision. If neither could be, it is an empty revision whose only unresolved question says no analysis could be produced. Either way the revision has `needs_review: true` and `validation_errors`.
- **`validation_errors` are server-worded counts** (for example "Attempt 1: 2 citations to records outside the permitted evidence were removed."). They never quote IDs or field names the model chose, because the contract says they are never LLM-authored. The full detail is logged (logger `app.analysis_retry`) and kept in `result.attempts`.
- **Not retried.** A provider failure on the first call is raised (`AnalysisLLMError`). One on the retry falls back to the first attempt's partial result, if it had one. An inconsistent corpus or retrieval set raises `ValueError` before any LLM call.

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

## Saved revisions (BLA-28)

`app/analyses.py` stores every `AnalysisRevision` in `analysis_revisions` and loads it back exactly as saved:

```python
from app.analyses import save_revision, get_revision, list_revisions, revision_lineage
save_revision(session, revision)   # validates, then commits; returns the stored revision
get_revision(session, case_id, analysis_id)
list_revisions(session, case_id)   # summaries, newest first
revision_lineage(session, case_id, analysis_id)  # root ... this revision
```

- **Immutable, append-only.** A saved row is never updated (a SQLite trigger rejects `UPDATE`). A follow-up, exclusion or restore saves a new revision whose `parent_analysis_id` points at the one it came from. Saving the identical revision again is a no-op, so a retried request is safe. Saving different content under a used `analysis_id` raises `RevisionConflictError`, and so does a concurrent save of the same ID.
- **Validated before saving.** `validate_revision` runs against the case's stored records: every citation must be a retrieved, permitted record of this case. The parent must be a saved revision of the same case, enforced by a `(case_id, parent_analysis_id)` foreign key as well as a check. The case must exist. Otherwise it raises `InvalidRevisionError` / `RevisionNotFoundError` and nothing is saved.
- **Case-scoped.** The primary key is `(case_id, analysis_id)`, and every read filters by `case_id`.

| Endpoint | Returns |
|---|---|
| `POST /cases/{case_id}/analyses` | `{"question", "analysis_id"?, "evidence_window"?}`: retrieve, analyze (BLA-18 retry), save and return the new `AnalysisRevision` (201). Optional `analysis_id` is a prior revision of this case; its exclusions and evidence window are inherited. 422 if the question has no letters or digits; 503 if the LLM is unavailable. |
| `GET /cases/{case_id}/analyses` | History, newest first: `analysis_id`, `parent_analysis_id`, `question`, `needs_review`, `created_at`, `excluded_record_ids` (its evidence state) and `restored_from_analysis_id` (set on a restore). |
| `GET /cases/{case_id}/analyses/{analysis_id}` | The revision, as `AnalysisRevision` contract JSON exactly as saved (404 if it is not in this case). |
| `GET /cases/{case_id}/analyses/{analysis_id}/lineage` | The revision and its ancestors, oldest first. |
| `POST /cases/{case_id}/analyses/{analysis_id}/follow-up` | `{"question": "...", "board_id"?: "board_...", "evidence_window"?}`: a follow-up that builds on this revision (BLA-29). Fresh retrieval for the new question with this revision's exclusions and evidence window inherited; returns the new child revision (201). |
| `POST /cases/{case_id}/analyses/{analysis_id}/exclude` | `{"record_ids": [...], "evidence_window"?}`: exclude records and rerun this revision's question on the remaining evidence (BLA-33), in this revision's evidence window unless overridden. Returns the new child revision (201). |
| `POST /cases/{case_id}/analyses/{analysis_id}/rerun` | `{"evidence_window": "expanded" \| "standard"}`: "Want more evidence?" (BLA-64). Reruns this revision's question with its exclusions in another window; returns the new child revision (201). 409 if the revision already used that window. |
| `GET /cases/{case_id}/analyses/{analysis_id}/comparison` | The support diff (BLA-34) between this revision and its parent, as `RevisionComparison` contract JSON. Deterministic: `created_at` is the revision's own. Only for exclusion and restore children (same question as the parent); a follow-up, which asks a different question, gets a 422. An item whose finding was reworded by the rerun may carry `possible_matches` ("possibly the same finding: N of M cited sources remain; please confirm"); it is advisory, omitted when empty, and never changes `support_status` (BLA-63). |
| `POST /cases/{case_id}/analyses/{analysis_id}/restore` | `{"record_ids": [...]}`: bring excluded records back (BLA-35). Returns the new revision (201), with the reused revision's ID in the `X-Restored-From` header. |

**Following up (BLA-29, `app/follow_up.py`).** The parent's question, answer, claims and unresolved questions go into the prompt's "prior analysis — not evidence" context. With `board_id`, so do the board's topic and pinned findings, read from their saved source revisions. Board notes are never sent; the contract keeps investigator notes away from the model. Pinned findings that cite a record excluded in the parent are left out. Like the exclusion context, this filters by citation, so remaining findings may still paraphrase excluded content. Every claim must cite the newly retrieved records. Errors: 404 for an unknown revision or board, 422 for an unsearchable question (no LLM call), 503 if the LLM is unavailable.

**Excluding (BLA-33).** The new revision's exclusions are the base's plus `record_ids`. Retrieval reruns for the same question with every chunk of those records filtered out (#26), and the result is generated by `analyze_with_retry` from permitted evidence only, then saved as a child of the base. The records stay in the corpus and the base stays in the history; restore brings them back.
- **Stable findings.** So the support diff can recognize unchanged findings, the rerun sees the base's findings that rely on no excluded record. The system prompt (`prompts/evidence_analysis.md`) asks it to reuse their IDs and exact wording when the remaining evidence still supports them.
- **Excluded records are never sent verbatim or by citation.** The base's answer, and any claim, edge or event citing an excluded record, are never sent, and neither are nodes whose records are all excluded. Findings that cite only permitted records are sent, and they may still paraphrase excluded content, since they were written by a model that saw it. So the system prompt requires every kept finding to be re-supported by the current records, and validation strips any citation to an excluded record. This needs checking on the real case (BLA-44/BLA-46).
- **Errors:** 422 if a record isn't in this case, none is given, or all are already excluded (no LLM call is made); 404 for an unknown revision; 503 if the LLM is unavailable.

**Evidence window (BLA-64, `app/evidence_window.py`).** Every new revision stores `retrieval_coverage` (see `contracts/README.md`): its window (`standard` = `RETRIEVAL_MAX_RECORDS`, `expanded` = `RETRIEVAL_EXPANDED_MAX_RECORDS`, which must be larger; the character budget is shared), every candidate the window left out (IDs and ranks, never content), and `offer_expanded_window`.
- **When the offer shows.** Retrieval has no relevance threshold, so any case with more records than the window has `record_limit` skips on every question; on Owl all five evaluation questions skip 16. A skip therefore counts as *left out* only when the vector or keyword search on its own ranked the record inside the window, i.e. it was a top match for one search and lost its place in fusion. On the live Owl index this fires on 2 of 5 questions: q2 (both missing "Sarah" texts, vector ranks 10 and 16, fused 17 and 19) and q3 (one record at vector rank 16, not needed). Budget-only skips never offer.
- **Inheritance.** Follow-ups, exclusions and analyses that name a prior revision inherit its window; each request can override it (for example back to `standard`). Revisions saved before coverage existed count as `standard`.
- **Rerun.** Same question, same exclusions, other window; the base's findings go in as "prior analysis — not evidence" (as for exclusions) so `GET .../comparison` can show what the extra records changed. A follow-up's board context is not stored on the revision, so rerunning a follow-up uses its own findings only. One step: an expanded revision never offers again; its `left_out_record_ids` count what was still left out.
- **Restore** copies the reused revision as it was, coverage included, so the window shown is the one that evidence state was analyzed in.

**Restoring (BLA-35).** Restore reuses an earlier evidence state; it deletes and rewrites nothing. The target state is the current exclusions minus `record_ids`. The nearest ancestor with the same question and exactly that exclusion set is found, and its findings are saved again as a new revision whose parent is the current one. A `revision_restores` row records which revision it reused, which is shown in the history. No LLM call is made. Errors: 422 if a record is not excluded in this revision, and 409 if no ancestor ever had that state. For example, if records A then B were excluded, restoring A alone would leave only B excluded, which never existed, so the analysis has to be rerun with that exclusion set.

Creating revisions: `POST /cases/{case_id}/analyses` (BLA-20) runs the pipeline and calls `save_revision`; follow-up and exclude (BLA-29, BLA-33) will do the same. A follow-up that names a prior revision inherits that revision's `excluded_record_ids` so excluded evidence cannot silently return (restoring is BLA-35). Restore is implemented here (`restore_records`). Everything is in SQLite, so revisions and boards (with their notes) reload after a browser refresh or a server restart.

## Boards (BLA-31)

Every response is `InvestigationBoard` contract JSON. A board is a series of append-only snapshots: creating one writes revision 1, and every change writes `revision + 1` with `parent_revision` set. Rows are never updated (a SQLite trigger rejects `UPDATE` on `board_snapshots`).

| Endpoint | Does |
|---|---|
| `POST /cases/{case_id}/boards` | `{"title", "active_analysis_id"?}` creates an empty board (201). |
| `GET /cases/{case_id}/boards` | Latest snapshot of every board, most recently updated first. |
| `GET /cases/{case_id}/boards/{board_id}` | Latest snapshot. |
| `GET /cases/{case_id}/boards/{board_id}/revisions[/{n}]` | Every snapshot, oldest first, or snapshot `n`. |
| `PATCH /cases/{case_id}/boards/{board_id}` | Unpin, pin, review, add notes or corrections, rename, set the active revision; appends one snapshot. |

`PATCH` body (every field optional, applied in this order):

```json
{
  "expected_revision": 3,
  "unpin": ["item_..."],
  "add_reconstruction": {"analysis_id": "analysis_..."},
  "pin": [{"kind": "claim", "source_analysis_id": "analysis_...", "ref_id": "claim-2"}],
  "review": [{"item_id": "item_...", "review_status": "disputed"}],
  "notes": [
    {"text": "Check whether the meeting happened.", "target_item_id": "item_..."},
    {"text": "Casey was not there; the message only says 'may be'.", "kind": "correction", "target_item_id": "item_..."}
  ],
  "title": "New title",
  "active_analysis_id": "analysis_..."
}
```

- **A pin names a finding; the server copies its `evidence_ids` and `status`** from the saved revision. The request cannot set either, so pinning never changes a status or marks anything verified.
- **Investigator review (BLA-48).** `review` sets an item's `review_status` (`unreviewed`, `reviewed` or `disputed`), after pins, so an item pinned in the same request can be reviewed. `unreviewed` is stored as absent. A note with `"kind": "correction"` flags its target as wrong and must have a `target_item_id`. Neither changes the finding's `status`, text or evidence, and neither is sent to the model (follow-ups send pinned findings with the model's status only). 422 for an item that isn't on the board, two different statuses for one item in one request, or a correction with no target. Every review decision is a new snapshot, so `GET …/revisions` shows its history. Board exports (`/export`) show each pinned finding's review status and label corrections separately.
- **"Add this reconstruction to board"** (BLA-30): `"add_reconstruction": {"analysis_id": "analysis_...", "kinds": ["claim", "node", "edge", "event"]}` pins every finding of that revision in one change (`kinds` optional, default all), through the same checks as single pins. Nodes that cite no evidence are skipped. Adding the same revision again changes nothing; adding a second revision appends its findings next to the first.
- **Nodes are never merged.** A node is identified by its revision and ID, so the same or a similar name from another revision (or the same revision) stays a separate item; deciding two are the same person is left to the investigator.
- **No length cap on revision IDs.** `ref_id`, `source_analysis_id`, `analysis_id` and `active_analysis_id` name things stored in a revision, whose contract sets no maximum length, so requests don't cap them either. Item IDs (`unpin`, `target_item_id`) are server-generated and capped at 200 characters.
- **Stable item IDs.** `item_id` is derived from `(source_analysis_id, kind, ref_id)` (`app.boards.item_id_for`), so pinning a finding twice is a no-op, and re-pinning after an unpin gives the same ID. A note can target an item pinned in the same request by that ID.
- **Notes are investigator-only text**, kept in `notes`, never in items. The server sets every note's `author` to `"investigator"`; the request has no author field, so a client cannot label a note as model output. Notes are never removed, so an item with notes cannot be unpinned (422).
- **Validated before saving.** The new snapshot goes through `validate_board`. Any error is a 422 listing every problem, and nothing is saved.
- **Errors:** 404 for an unknown case or board, or a board of another case; 409 when `expected_revision` is stale or another save took that revision number; 422 for an invalid change. A change that alters nothing returns the current snapshot and writes nothing.
- **Revision lookup.** Boards find revisions through the `get_revision_lookup` dependency in `app/routers/boards.py`, which returns `app.analyses.saved_revision_lookup`: only findings of saved revisions of the same case can be pinned, and only a saved revision can be `active_analysis_id` (otherwise a 422: `no saved revision ... in this case` / `active_analysis_id ... is not a revision of case ...`). Tests override the dependency to inject revisions.

## Timeline display adapter (BLA-39)

`POST /timeline/normalize` takes `{"events": [TimelineEvent, ...]}` and returns the
existing `normalize_timeline(...).as_dict()` result: `dated` and `undated` arrays
of `{event_id, instant, precision, timezone_known, display}`. It is a pure input
transformation: it neither looks up case data nor calls a model or saves anything.
This lets synthetic previews and live analyses use the same normalization rules
without changing the AnalysisRevision contract. Duplicate event IDs and malformed
inputs return 422; input is capped at 2,000 events per call.

The frontend displays `display` as text and preserves backend ordering. It never
passes source timestamps through JavaScript Date. Approximate times and exact
wall-clock times with unknown zones are presented in the separate uncertain
section; day/month values remain dated with explicit precision labels. An
unavailable normalizer leaves all events visible as unverified, with a retry,
rather than silently inventing chronology.

## Case report export (BLA-49)

Exports a board or one saved revision as a structured case report. Assembled from stored data only (`app/reports.py`): no model is called, and the same stored data gives byte-identical output apart from `generated_at`. Markdown is rendered by `app/report_markdown.py`; JSON is the `CaseReport` Pydantic model. Routes live in `app/routers/reports.py`.

| Endpoint | Exports |
|---|---|
| `GET /cases/{case_id}/boards/{board_id}/export?format=md\|json[&revision=n]` | A board snapshot (latest unless `revision`): its pinned findings, the revisions they come from (the active one first), excluded evidence, investigator notes. |
| `GET /cases/{case_id}/analyses/{analysis_id}/export?format=md\|json` | One saved revision: every claim, entity, relationship and event in it. |

- `format` defaults to `md`. Markdown is `text/markdown; charset=utf-8`; JSON is `application/json`. Both carry `Content-Disposition: attachment; filename="blanc-report-<case_id>-<board_or_analysis_id>.<md|json>"` (unsafe characters replaced by `_`). An unknown format is a 422.
- 404 for an unknown case, board, board revision or analysis, and for a board or analysis of another case.
- **UI:** an export button can link straight to either URL (a download), or fetch `format=json` to render the report itself.
- Tests fix `generated_at` by overriding the `get_report_clock` dependency.

JSON shape (`report_version: "1.0"`). This is its own API shape, not a `contracts/` schema; see `CaseReport` in `app/reports.py` and `/openapi.json`.

| Field | Holds |
|---|---|
| `report_version`, `report_kind` (`board` / `analysis`), `generated_at` (UTC) | Header. |
| `case` `{case_id, title}`, `board` (`null` for an analysis export) `{board_id, title, revision, parent_revision, active_analysis_id, updated_at}` | What was exported. |
| `analyses[]` | Each revision drawn on: `role` (`exported`, `board_active`, `board_source`), `lineage` (root ... this ID), `question`, `answer`, `unresolved_questions`, `needs_review`, `validation_errors`, `retrieved_record_ids`, `excluded_record_ids`, `created_at`. |
| `claims[]`, `entities[]`, `relationships[]` | AI-generated findings with `analysis_id`, their own ID, text/label/type, `status` (as saved), `evidence_ids`; on a board also `board_item_id`, `review_status` (the investigator's review, BLA-48; `unreviewed` when not set) and `note_ids` (links to notes, never their text). Relationships give `source`/`target` as `{node_id, label, type}`. |
| `timeline` `{dated, uncertain, undated}` | Events with the raw `timestamp`, normalized `display`, `precision`, `timezone_known`. `dated` is in `app.timeline` order; `uncertain` holds approximate times and exact times with no known zone; `undated` holds the rest. Nothing is guessed. |
| `sources[]` | Every cited record once, sorted by ID: `source_file`, `source_locator`, `timestamp`, `timestamp_kind`, `excerpt` (either `{text, char_start, char_end, truncated, content_chars}` where `text == content[char_start:char_end]`, at most the first 2000 characters, **or `null`** when withheld), `cited_by[]` `{kind, analysis_id, ref_id}`, and `excluded_in[]` (every revision in this report that excludes the record; possible on a board that pins items from several revisions). Withholding: for a board with an active revision, or a single-analysis export, `excerpt` is null only when that scope revision excludes the record (a stale pin from an older exclusion does not hide restored evidence). For a board with no active revision, `excerpt` is null when any reported revision excludes the record. |
| `excluded_evidence[]` | `{analysis_id, record_id, inherited, first_excluded_in, source_file, source_locator, timestamp}`. Locators only, no content; never counted as support. |
| `investigator_notes[]` | `{note_id, author, created_at, text, target_item_id, target, kind}`; `kind` is `correction` when the investigator flagged the target as wrong. Board exports only; an analysis export has none. |
| `limitations[]` | Fixed caveats (AI-generated and needs human review; statuses and pins are not verification; exclusions change what can be concluded, not what happened; retrieval may miss records; local storage but Gemini processed the text; formats not ingested) plus case-specific ones (attachments not imported, needs-review revisions, truncated excerpts, uncertain times, records excluded elsewhere on the board). |

**Untrusted text in Markdown.** Evidence, model output, notes and IDs may contain Markdown or HTML, and none of it is emitted as structure. Excerpts and notes go in fenced code blocks whose fence is longer than any backtick run in the text. IDs, files and locators go in code spans built the same way. Prose (answers, claims, labels, descriptions) is escaped line by line: ``\ ` * _ [ ] < > # + ! | ~ & @``, a leading `- = >` or `1.`, and `://` / `www.` so bare URLs are not linked. Control characters are dropped. `tests/test_reports.py` covers `<script>`, headings, links, images, pipes, tables and backtick runs.
