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

`app/config.py` defines `Settings` (pydantic-settings), loaded from the repo-root `.env`; real environment variables override it. Fields: `GEMINI_API_KEY` (LLM and embeddings), `GEMINI_MODEL`, `EMBEDDING_MODEL` (default `gemini-embedding-001`), `EMBEDDING_DIM` (default 768; the Gemini `output_dimensionality` and the vector index size), `DATABASE_URL`, `CORS_ORIGINS` (comma-separated), and the `RETRIEVAL_*` tuning settings (see "Retrieval" below). Keys are optional, so the app starts without them. Use `get_settings()` to access config.

## Layout

- `app/main.py`: app factory (`create_app`), CORS, lifespan (opens the DB and creates tables), `GET /health` (status plus a non-secret config summary).
- `app/config.py`: settings.
- `app/db.py`: SQLAlchemy 2.0 engine/session factory, `Base`, `UTCDateTime`, `get_session` dependency. SQLite foreign keys are enabled on every connection, and the DB file's parent directory is created on startup.
- `app/models.py`: ORM tables: `Case`, `EvidenceRecordRow` (`evidence_records`), `EvidenceChunk` (`evidence_chunks`).
- `app/evidence_store.py`: `store_records` (persist records + chunks + vectors + keyword index), `reembed_stale_chunks`, case-scoped reads. See "Evidence storage" below.
- `app/search_index.py`: sqlite-vec and FTS5 virtual tables, their cleanup trigger, and case-scoped `vector_search` / `keyword_search` primitives, and `to_fts_query` (free text to a safe FTS5 query).
- `app/retrieval.py`: `retrieve`, hybrid vector + keyword retrieval with RRF fusion and the capped evidence window. See "Retrieval" below.
- `app/chunking.py`: `chunk_spans`, the ~400-token / ~50-token-overlap splitter.
- `app/embeddings.py`: `Embedder` protocol, `GeminiEmbedder`, `FakeEmbedder` (tests), `make_embedder(settings)`.
- `app/schemas/`: Pydantic request/response models. Kept separate from ORM tables.
- `app/deps.py`: shared dependencies: `SessionDep`, `require_case` / `CaseDep`, `get_case_or_404`, `EmbedderDep` (the app's embedder, `app.state.embedder`).
- `app/ids.py`: `new_id(prefix)` returns prefixed ULIDs (`case_01J...`). Use it for every server-generated ID (`analysis_`, `board_`, `rec_`, ...).
- `app/contracts.py`: Pydantic models for the shared contracts in `../contracts/` (`EvidenceRecord`, `AnalysisRevision`, `InvestigationBoard`, `RevisionComparison`). Kept in sync with the JSON Schemas by `tests/test_contracts.py`.
- `app/validation.py`: cross-reference checks (`validate_revision`, `validate_board`, `validate_comparison`) plus `schema_errors` for parsing untrusted JSON. Each returns readable error strings; an empty list means valid.
- `app/routers/`: HTTP routers (`cases.py`: `POST /cases`, `GET /cases`, `GET /cases/{case_id}`; `search.py`: `POST /cases/{case_id}/search`).
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
```

- **Pipeline.** The question is embedded with `embed_query` (Gemini `RETRIEVAL_QUERY`) and the `RETRIEVAL_VECTOR_CANDIDATES` (50) nearest chunks of the case are taken from sqlite-vec; the question is converted with `to_fts_query` and the best `RETRIEVAL_KEYWORD_CANDIDATES` (50) BM25 chunks are taken from FTS5. Both searches filter by `case_id` inside the query.
- **Fusion.** Reciprocal rank fusion per record: each list is reduced to records (a record's rank is its best chunk's rank in that list), and `score = sum(weight / (RRF_K + rank))` over the lists the record appears in (`RETRIEVAL_RRF_K` 60, `RETRIEVAL_VECTOR_WEIGHT` / `RETRIEVAL_KEYWORD_WEIGHT` 1.0). Best chunk per list, not a sum over all chunks, so long records are not favoured just for having many chunks; summing across the two lists means a record found by both outranks one found by only one. Ties break by `record_id`.
- **Excerpts.** Each record carries up to `RETRIEVAL_MAX_EXCERPTS_PER_RECORD` (2) excerpts: its best chunks by chunk-level RRF, with overlapping chunks merged. Every excerpt is the exact slice `record.content[char_start:char_end]`.
- **Evidence window.** At most `RETRIEVAL_MAX_RECORDS` (16) records and `RETRIEVAL_MAX_EXCERPT_CHARS` (32000, ~8000 tokens) characters of excerpt text in total. Excerpts that do not fit the remaining budget are skipped (a record with none left is dropped); only the window's first excerpt is truncated, and only if the budget is smaller than it. `dropped_by_limit`, `dropped_by_budget` and `excerpt_chars` report what happened.
- **Free text to FTS5.** `keyword_search` takes raw FTS5 syntax (internal callers only). `search_index.to_fts_query(text)` makes user text safe: words are split into letter/digit runs like the `unicode61` tokenizer, every term is quoted (so `AND`/`OR`/`NEAR`/`*`/`:`/`(`/`"` are plain text), words like `it's` or `rec-0042` become phrases (`"it s"`), common English stopwords are dropped unless nothing else is left, and terms are OR'd (at most 32). It returns `None` for empty or punctuation-only text; `retrieve` then returns no records without calling the embedder.
- **Exclusions.** `excluded_record_ids` drops every chunk of those records from both candidate lists before fusion, so they never appear. Pushing the filter into the index queries (so excluded chunks do not use up candidates) and the revision-level exclusion semantics are BLA-14.
- Retrieval is read-only. An unknown case raises `CaseNotFoundError`; an embedder whose dimension does not match the index raises `EmbeddingIndexMismatchError`.
- **Debug endpoint.** `POST /cases/{case_id}/search` with `{"question": "...", "limit": 5}` (`limit` optional) returns the `RetrievalResult` as JSON, for inspecting and tuning retrieval.
