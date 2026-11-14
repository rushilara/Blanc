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

- `app/main.py`: app factory (`create_app`), CORS, `GET /health` (status plus a non-secret config summary).
- `app/config.py`: settings.
- `tests/`: pytest suite (`TestClient`).
