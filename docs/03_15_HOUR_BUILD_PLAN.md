# 15-Hour Development Plan — Three Parallel Workstreams

**Interpretation:** ~15 elapsed hours with three teammates, not 15 total person-hours. The plan assumes extracted evidence, existing LLM/embedding APIs, and fast coding assistance. These are planning checkpoints, not guaranteed timings.

## Owner tracks

| Owner | Primary scope | Owns |
|---|---|---|
| A — Evidence / evaluation | Obtain actual case exports; normalize evidence; verify answer keys; prepare test case | fixtures/, ingest adapters, test questions, evaluation |
| B — RAG / backend | Search, LLM structured response, grounding checks, save/branch/exclusion orchestration | backend/, contracts validation, DB |
| C — UI / integration | Case workspace, three views, evidence drawer, saved revisions, comparison | frontend/, demo flow |

**Unblock in parallel:** Everyone uses `contracts/` and a synthetic reconstruction fixture; frontend does not wait for a working LLM. Assign a single integrator for shared API and schema changes.

## Stage gates

| Elapsed hour | Goal | Concrete acceptance test | Fallback |
|---|---|---|---|
| 0–1.5 | Lock contracts + dataset | Agree on record/analysis IDs. Find one readable conversation and an independent supporting artifact in Narcos; record exact locators. | Switch to Owl or M57-Jean if evidence cannot be recovered promptly. |
| 1.5–4 | Import, index, skeleton UI | New case created, real record searchable, synthetic answer/graph/timeline renders. | Support TXT/JSON/CSV first; treat PDF/EML as stretch if parsing blocks. |
| 4–7 | End-to-end first question | Question → hybrid retrieval → structured JSON → cited answer/graph/timeline for one verified case. | Use one LLM pass; skip extra retrieval hops. |
| 7–10 | Persistent workspace + follow-up | Save/reload a revision, ask follow-up, add new sourced elements to board; original saved version remains intact. | Save snapshots rather than complex automated graph merging. |
| 10–12.5 | Exclusion and comparison | Exclude record; retrieval and LLM both respect exclusion; show changed/unresolved finding; restore or branch. | Use deterministic changed/unchanged/unsupported diff if LLM comparison is unreliable. |
| 12.5–15 | QA, second-case check, presentation | Fresh-start **localhost** demo (`cp .env.example .env`, set `GEMINI_API_KEY`, `make backend` + `make import` + `make frontend`, open `http://localhost:3000`), verify citations/locators, demonstrate saving and evidence sensitivity. If time permits, load second case unchanged. Do not spend MVP time on cloud-hosting the case DB. | Prefer one truly real, reliable case over two shallow or fabricated ones; optional static landing host only. |

## Hard priorities under time pressure

1. Never compromise on source IDs, citation validation, case isolation or save/reload.
2. Do not replace real retrieved artifacts with a scripted, hardcoded graph while describing it as live inference.
3. Keep graph and timeline visually simple before implementing sophisticated graph editing.
4. Evidence exclusion must rerun retrieval, not only filter an existing answer.
5. If the case dataset is inaccessible, **switch data source early**, not late.

## Fresh local machine setup (BLA-58)

Runbook detail lives in the root `README.md`. **Share/demo install** is laptop Docker ([`docs/07_LAPTOP_INSTALL.md`](07_LAPTOP_INSTALL.md), BLA-74). **Dev** path on a clean checkout:

1. `cp .env.example .env` and set `GEMINI_API_KEY` (leave `DATABASE_URL=sqlite:///./data/blanc.db`).
2. `make backend` and `make frontend` in separate terminals (Python 3.12 + Node 20.12+) — not the share/deploy path.
3. Open `http://localhost:3000` → sign-in landing → `/workspace`. Health: `http://localhost:8000/health`.
4. `make import EVIDENCE=path/to/exports TITLE="Case name"` (embeds via Gemini into the local DB). Point `EVIDENCE` at the extracted evidence folder only—keep answer keys, briefs, and question sets out of that path.

**Accurate today vs still open:** setup, local DB defaults, import, ask-on-`/workspace`, citation open, and auto-persisted revisions (reopen via `/live` → Saved versions) are the demo path. Follow-up and exclude/restore UI on the demo pages are **not** wired yet (backend endpoints exist; exclude control shows “available in a later step”). Do not check those boxes until the UI path works. End-to-end rehearsal and citation audit of the full checklist is **BLA-46**.

## Demo acceptance checklist

Setup / talk track (local-first + Gemini compute):

- [ ] Demo runs locally (backend + frontend on localhost); case DB and evidence files are on the demo machine, not a hosted volume. Prefer laptop Docker for share/demo; `make backend` / `make frontend` for development.
- [ ] Fresh machine followed either [`docs/07_LAPTOP_INSTALL.md`](07_LAPTOP_INSTALL.md) (`docker compose up --build -d`) or the dev path: `cp .env.example .env` → set `GEMINI_API_KEY` → `make backend` → `make frontend` → open `http://localhost:3000`.
- [ ] The case is loaded on the demo machine (Docker import from `07_LAPTOP_INSTALL.md`, or `make import EVIDENCE=... TITLE=...`) and shows as ready in the workspace.
- [ ] Team can state clearly: Gemini is used for embeddings/LLM (evidence text leaves the machine for those calls); storage is local-first, not fully offline; no Vercel/Railway required for the demo.

Investigative demo (rehearse under BLA-46; leave unchecked until verified on the real case):

- [ ] A case is opened and at least several unrelated records are also indexed.
- [ ] Investigator asks a question whose answer requires at least two real records.
- [ ] Retrieved source IDs and locators match actual artifacts.
- [ ] Every answer claim, graph edge and timeline event opens valid evidence.
- [ ] Asking on `/workspace` persists a revision in the local DB; after refresh it can be reopened from `/live` → Saved versions (there is no separate Save button).
- [ ] Follow-up question adds at least one sourced item to the saved investigation. *(Backend: `POST .../follow-up`. Demo UI on `/workspace` still shows a disabled follow-up field.)*
- [ ] Excluding one pivotal record triggers a fresh retrieval/analysis and changes support appropriately. *(Backend: `POST .../exclude`. Demo UI exclude control is not wired yet.)*
- [ ] The original reconstruction can be restored or compared without losing the work. *(Backend restore/comparison endpoints exist; demo UI wiring still open.)*
- [ ] Demo explains limits: simulated case, unsupported formats, imperfect retrieval and no trained forensic model.

## Two-minute demo narrative

1. **0:00–0:20**: Open preloaded case and show number of indexed artifacts (without indexing the answer key).
2. **0:20–0:45**: Ask verified investigative question; show answer, relationship graph and timeline.
3. **0:45–1:05**: Click graph edge and a timeline event; reveal original locators and quoted evidence.
4. **1:05–1:25**: Save board and ask follow-up; add new cited event/connection to the existing investigation.
5. **1:25–1:50**: Exclude a pivotal record; show before/after support and timeline changes.
6. **1:50–2:00**: Restore; state case-agnostic architecture and later multi-hop/model-training roadmap.

## After the hackathon

**Milestone 2 (MLP):** second/third case benchmarks, analyst annotations, robust merge/diff, export, better retrieval and entity resolution; desktop packaging polish.  
**Milestone 3 (research):** collect relevance-pair labels, test fine-tuned vs generic embeddings, add capped agentic retrieval and run held-out case evaluations; explore fully offline/local models.  
**Milestone 4 (optional cloud + real deployments):** optional hosted app deploy for teams that want it (still preferring local evidence where possible), audited access/auth, secure deployment, supported forensic tool export adapters, rigorous provenance/case isolation, human review workflows.
