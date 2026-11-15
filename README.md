# Blanc — Product and Engineering Kickoff

**Status:** Proposed / ready for team review  
**Date:** November 14, 2026  
**Target:** A general-purpose evidence-grounded investigative reconstruction prototype, developed by three teammates in ~15 elapsed hours with AI coding assistance.

## Product in one sentence

Blanc is a persistent investigative workspace in which an investigator asks case questions and receives a source-cited answer, a relationship graph, and a timeline; they can save the reconstruction, expand it with follow-up questions, and exclude evidence to see how supported conclusions change.

**The core innovation is not three separate AI outputs; it is one editable, evidence-backed investigation state with auditable versions.**

## Start here

1. [`docs/07_LAPTOP_INSTALL.md`](docs/07_LAPTOP_INSTALL.md): **primary demo / share install** — Docker Desktop + compose on a laptop (BLA-74).
2. [`docs/01_PRODUCT_PRD.md`](docs/01_PRODUCT_PRD.md): final product vision, MVP/MLP, requirements, epics, acceptance criteria.
3. [`docs/02_ARCHITECTURE_AND_DATA_CONTRACTS.md`](docs/02_ARCHITECTURE_AND_DATA_CONTRACTS.md): lean RAG architecture; save, expand and evidence-exclusion mechanics; APIs and shared JSON contracts.
4. [`docs/03_15_HOUR_BUILD_PLAN.md`](docs/03_15_HOUR_BUILD_PLAN.md): three parallel workstreams, stage gates, fallback decisions and 2-minute demo sequence.
5. [`docs/04_DATASETS_AND_EVALUATION.md`](docs/04_DATASETS_AND_EVALUATION.md): prioritized sources, ground-truth realities and test protocol.
6. [`docs/05_CLAUDE_CODE_KICKOFF.md`](docs/05_CLAUDE_CODE_KICKOFF.md): initial issue board and handoff prompts for developers/coding agents.
7. [`contracts/`](contracts/): minimal machine-readable evidence and reconstruction schemas plus a synthetic example.
8. [`docs/06_DEPLOYMENT.md`](docs/06_DEPLOYMENT.md): optional hosted landing / container notes only — **not** the demo deploy.

## Architecture posture (MVP)

**Local-first data, cloud AI compute.** Forensic files, parsed records, embeddings, SQLite (including sqlite-vec + FTS5), and saved investigations live on the investigator’s machine. FastAPI and the Next.js UI run on localhost. **Google Gemini** is used for embeddings and structured LLM analysis, so evidence *content* still leaves the machine for those API calls. This is **not** a fully offline or zero-third-party design. Use public/synthetic datasets for the hackathon.

Cloud hosting of the case database or original evidence is **out of MVP**. The landing **login page stays**; cloud auth can gate the local app against casual local misuse and support later hosted surfaces. Optional later: host a marketing/landing site, optional full cloud deploy for teams that want it, and local/offline models.

## Scope decision

- **In MVP:** local import of *supported extracted* evidence, hybrid retrieval, one structured LLM reconstruction via Gemini, clickable provenance, persistent local case workspace, follow-up queries, save/append findings, reversible source exclusion, reconstruction comparison, at least one real verified forensic case; demo on localhost.
- **Target if ready:** second unrelated case using the same code path, basic exports, annotations, keyword baseline; optional static landing host (no evidence in the cloud).
- **After MVP:** bounded multi-hop agentic search, forensic-domain embedder fine-tuning, native raw disk/memory acquisition, desktop packaging, optional cloud deploy, fully offline/local models, production-grade investigation governance.

## Hard rules

- No case-specific hardcoded facts, answer strings, graph edges or timeline events in the inference pipeline.
- The case brief, solutions and test answer keys must stay outside the searchable evidence corpus.
- LLM output is **not** evidence. Graph edges, claims and timeline events must cite valid source evidence IDs.
- Exclusion applies to whole source records (and all their chunks), not merely to already generated answer text.
- If support disappears, show **unresolved** rather than manufacturing alternative certainty.
- Saving a finding does not make it verified fact; preserve its source references, inferred/observed status and revision history.
- Treat uploaded evidence as untrusted input; do not obey instructions embedded inside documents.

## Repository layout

```text
frontend/   # Next.js investigator workspace (Owner C)
backend/    # FastAPI, retrieval and LLM pipeline (Owner B)
contracts/  # JSON Schemas: evidence + reconstructed analysis
fixtures/   # Synthetic example for frontend integration only
starter/    # Minimal contract validator + tests
docs/       # PRD, architecture, build plan, datasets, kickoff (full PRD in docs/prd/)
```

Copy `.env.example` to `.env` for local config; `.env` is gitignored.

## Demo runbook (local-first)

**Talk track:** case storage is local-first (SQLite + evidence files on this machine). Gemini is cloud compute for embeddings and structured analysis, so evidence *text* still leaves the machine for those API calls. This is not fully offline. MVP demos do **not** use Railway/Render volumes or cloud object storage for case evidence.

**How do we deploy?** Put Blanc on a laptop with Docker Desktop + compose. That is the primary install and the intended share/demo path (BLA-74). Full steps: [`docs/07_LAPTOP_INSTALL.md`](docs/07_LAPTOP_INSTALL.md).

```bash
cp .env.example .env
# Edit .env and set GEMINI_API_KEY=...  (https://aistudio.google.com/apikey)

docker compose up --build -d
# then open http://localhost:3000
```

**No Vercel or Railway required**; Gemini is the only cloud dependency.

### Dev path (`make backend` / `make frontend`)

Use this when you are changing code. It is **not** the share/deploy path.

Prerequisites: Python 3.12 (must load SQLite extensions — see `backend/README.md`), Node **20.12+**, npm, make. If your shell still has Node 18, switch first (for example `nvm use 20`).

```bash
cp .env.example .env
# Edit .env and set GEMINI_API_KEY=...  (https://aistudio.google.com/apikey)
# Both backend and frontend read this file. Leave DATABASE_URL at the default.

make backend    # terminal 1: FastAPI with reload on http://localhost:8000 (BACKEND_PORT)
make frontend   # terminal 2: Next.js on http://localhost:3000
```

Open [http://localhost:3000](http://localhost:3000): the branded landing/sign-in page loads. With `BLANC_AUTH_SECRET` empty, **Sign in** opens `/workspace`; set it and add a user to require a real sign-in ([`docs/06_DEPLOYMENT.md`](docs/06_DEPLOYMENT.md#sign-in-bla-60)). Confirm the backend at [http://localhost:8000/health](http://localhost:8000/health) (`gemini_api_key_set` should be true once the key is set).

If `python3.12` is not on your PATH: `make backend PYTHON=/path/to/python3.12`. If you change the frontend origin/port, set `CORS_ORIGINS` in `.env` to that exact origin (default is `http://localhost:3000`). Relative `DATABASE_URL` paths resolve from `backend/`, where uvicorn runs.

`make test` runs backend ruff + pytest and frontend lint. CI also runs frontend tests and typecheck.

### Load a case (dev path)

A fresh database has no cases. Import **only the extracted evidence folder** (TXT, JSON, CSV, EML, plus PNG/JPEG/PDF)—keep the case brief, teacher guides, solutions, and evaluation question sets elsewhere so they never enter the searchable corpus (needs `GEMINI_API_KEY` to embed). For the Docker/laptop path, use the import command in [`docs/07_LAPTOP_INSTALL.md`](docs/07_LAPTOP_INSTALL.md) instead.

```bash
make import EVIDENCE=path/to/exports TITLE="Owl case"      # new case; prints its case_id
make import EVIDENCE=path/to/more CASE=case_01...          # add to an existing case
```

This parses the files, embeds text and media with Gemini (`gemini-embedding-2`, dim 768) and stores everything in the local SQLite file at `DATABASE_URL`; the case shows up as `ready` in the workspace. Text originals are read where they are and not copied: each record keeps the file's path (relative to the export folder) and its SHA-256. Image and PDF bytes are copied under `CASES_DIR/{case_id}/media/` (default `./data/cases`); media keeps empty `content` but is embedded multimodally and gets a lexical FTS sidecar (filename / parent email subject). `gemini-embedding-2` ignores `task_type`, so query and document vectors are currently symmetric—re-run Owl / keyword baseline / "want more evidence?" measurements after `reembed_stale_chunks` before quoting prior numbers. Re-running import is safe; already-stored records are skipped and not re-embedded. If parsing or embedding fails, the DB write is rolled back and any media files that run newly wrote are deleted. Options such as `--txt-mode line` or `--media-dir` go in `IMPORT_ARGS="..."`; see `python -m app.evidence --help` in `backend/`.

Then in `/workspace`, pick the case and ask a question. Each successful ask persists an analysis revision in the local DB (reopen saved versions from `/live` → **Saved versions**). Follow-up and evidence-exclusion controls in the demo UI are not wired yet; the backend endpoints exist (see `docs/03_15_HOUR_BUILD_PLAN.md` and BLA-46).

- LLM: Google Gemini API (`GEMINI_API_KEY`, `GEMINI_MODEL`) — cloud compute; key stays in local `.env`.
- Embeddings: Gemini embeddings API (`GEMINI_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_DIM`); vectors are stored locally in SQLite with sqlite-vec.
- Persistence: local `DATABASE_URL` (default `sqlite:///./data/blanc.db` under `backend/`). Do not point MVP demos at a hosted volume for case evidence.

### Optional hosted landing / container notes

**Not the demo deploy.** Blanc is local-first; laptop Docker is how you share and demo it. [`docs/06_DEPLOYMENT.md`](docs/06_DEPLOYMENT.md) covers an optional public landing page (workspace closed) and operator-controlled container notes only. It is not a cloud evidence store.

## Suggested kickoff

All three teammates agree on the schemas before writing endpoint-specific code. The dataset owner verifies readable case records in the first 90 minutes. Frontend implements against the synthetic example while backend builds retrieval and analysis.

## Runnable source-grounding check

Run `cd starter && python validate_fixture.py && python -m unittest -v test_validator.py` to verify the synthetic fixture. This is a **small contract validator only**, not a running Blanc application.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the branch → PR → review → merge workflow and what CI checks.
