# Backend (Owner B)

FastAPI service: case/record persistence (SQLite + vector index), hybrid retrieval with exclusion filtering, structured LLM reconstruction, follow-up and exclusion endpoints.

See `../docs/02_ARCHITECTURE_AND_DATA_CONTRACTS.md` and `../docs/05_CLAUDE_CODE_KICKOFF.md` (tasks B1–B5). Validate all output against `../contracts/*.schema.json`.

## Stack

Python 3.12, FastAPI, Pydantic v2, pydantic-settings, uvicorn. LLM: Google Gemini API (`google-genai`). Embeddings: OpenAI embeddings API (`openai`). The SDKs are installed; no call logic exists yet.

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

## Config

`app/config.py` defines `Settings` (pydantic-settings), loaded from the repo-root `.env`; real environment variables override it. Fields: `GEMINI_API_KEY`, `GEMINI_MODEL`, `OPENAI_API_KEY`, `EMBEDDING_MODEL`, `DATABASE_URL`, `VECTOR_INDEX_PATH`, `CORS_ORIGINS` (comma-separated). Keys are optional, so the app starts without them. Use `get_settings()` to access config.

## Layout

- `app/main.py`: app factory (`create_app`), CORS, lifespan (opens the DB and creates tables), `GET /health` (status plus a non-secret config summary).
- `app/config.py`: settings.
- `app/db.py`: SQLAlchemy 2.0 engine/session factory, `Base`, `UTCDateTime`, `get_session` dependency. SQLite foreign keys are enabled on every connection, and the DB file's parent directory is created on startup.
- `app/models.py`: ORM tables (currently `Case`).
- `app/schemas/`: Pydantic request/response models. Kept separate from ORM tables.
- `app/deps.py`: shared dependencies: `SessionDep`, `require_case` / `CaseDep`, `get_case_or_404`.
- `app/ids.py`: `new_id(prefix)` returns prefixed ULIDs (`case_01J...`). Use it for every server-generated ID (`analysis_`, `board_`, `rec_`, ...).
- `app/routers/`: HTTP routers (`cases.py`: `POST /cases`, `GET /cases`, `GET /cases/{case_id}`).
- `tests/`: pytest suite (`TestClient`). `conftest.py` gives each test its own temp SQLite file.

## Persistence

SQLite via SQLAlchemy 2.0 (typed `Mapped[...]` ORM) at `DATABASE_URL` (default `sqlite:///./data/blanc.db`, relative to `backend/`; `data/` is gitignored). Tables are created with `Base.metadata.create_all` at startup. There are no migrations yet (Alembic can come later), so during development delete `backend/data/blanc.db` after changing a table.

The DB is opened in the lifespan, not at import. In tests, enter the client (`with TestClient(app) as client:`) so the lifespan runs.

## Case isolation (required for every new table and query)

Every piece of evidence belongs to exactly one case. Search for one case must never return another case's records. To guarantee this:

1. **Every table that holds evidence or anything derived from it** (records, chunks, embeddings, analyses, boards, ...) has a `case_id` column declared `ForeignKey("cases.case_id"), nullable=False`, plus an index on it. Child rows (for example chunks of a record) carry their own `case_id` too, so they can be filtered without a join.
2. **Every route under `/cases/{case_id}/...` resolves the case with `CaseDep`** (or `require_case`). An unknown case returns 404 before any other query runs.
3. **Every query is scoped by the resolved `case.case_id`.** Never look up a record, chunk or analysis by its own ID alone. Filter by `(case_id, id)`, so an ID from another case behaves as not found (404). The same applies to vector-index searches: filter by `case_id` inside the search, not after it.
4. Tests for each new table or endpoint must create two cases and assert that nothing from one case is visible through the other.
