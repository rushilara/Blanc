# Hosted app path (BLA-61)

Blanc is **local-first**: the default and recommended way to run it is on the investigator's machine (`README.md`, "Demo runbook"). This document describes the *optional* hosted path for the app tier. It is **not** a cloud evidence store: case evidence, the SQLite database and imported media stay on storage the operator controls.

## What is hosted, and where

| Part | Where it runs | What it holds |
|---|---|---|
| Landing page (`frontend/public/blanc/`) | Vercel (or any static/Next.js host), public | Marketing copy and the sign-in screen. No evidence. |
| Workspace routes (`/workspace`, `/live`, `/sample`) | Same Next.js deploy, **closed** on a public deploy (`BLANC_HOSTED=1`) until auth (BLA-60) | Nothing until a signed-in user reaches a backend that holds their case. |
| Backend API (`backend/Dockerfile`) | A container the operator runs: their own machine, a lab server, or a private VM | Reads and writes the case database and media on a mounted volume (`/data`). |
| Case evidence, SQLite DB, media | The `/data` volume, on operator-controlled storage | Records, chunks, vectors, revisions, boards, imported images and PDFs. |
| Gemini (embeddings, analysis) | Google's API | Receives the evidence text (and, for media, the bytes) needed for each request. Nothing is stored there by Blanc. |

**Recommended split for a shared demo:** the landing page on Vercel with `BLANC_HOSTED=1`, and the backend plus frontend run locally (or on a private machine) for anyone who opens a case.

## Frontend on Vercel

- **Project root:** `frontend/`. Keep "Include files outside the root directory" on: the frontend imports the shared JSON Schemas from `contracts/`.
- **Environment variables:**
  - `BLANC_HOSTED=1` closes the workspace routes. `src/proxy.ts` redirects `/workspace`, `/live` and `/sample` to `/blanc/hosted.html`, which explains how to run Blanc locally. The landing page stays public. Local runs leave it unset and are unaffected.
  - `NEXT_PUBLIC_API_BASE_URL` is only needed once a backend is reachable from that deploy (after BLA-60). It is public and build-time; never put a key in a `NEXT_PUBLIC_` variable.
- **Remove `BLANC_HOSTED` only when auth (BLA-60) gates the workspace.** A publicly reachable workspace without auth would let anyone who finds a backend URL browse its cases.

## Backend container

```bash
docker build -t blanc-backend backend
docker run -d -p 127.0.0.1:8000:8000 \
  -v /path/on/your/machine/blanc-data:/data \
  -e GEMINI_API_KEY \
  -e CORS_ORIGINS=http://localhost:3000 \
  blanc-backend
```

- **Image contents:** `app/`, `evidence_prompt.py`, `prompts/` and the pinned `requirements.txt`. `.dockerignore` keeps out `data/`, databases, `.env`, tests and local tooling, and CI checks that.
- **Data:** `DATABASE_URL=sqlite:////data/blanc.db` and `CASES_DIR=/data/cases` by default. Mount `/data` from storage you control. To import evidence into it, run `python -m app.evidence … --store` in the container, or import locally and copy the database file onto the volume.
- **Secrets:** pass `GEMINI_API_KEY` from the host's secret store, for example `-e GEMINI_API_KEY` from the environment, Docker or Kubernetes secrets, or a platform secret manager. The image never contains a `.env` file. `/health` reports only whether a key is set, never the key.
- **User:** the container runs as an unprivileged user that owns only `/data`.
- **Network:** bind to `127.0.0.1` or a private network. Set `CORS_ORIGINS` to the exact frontend origin. The API has no auth of its own yet (BLA-60), so it must not be exposed publicly.
- **CI:** the "Backend image" job builds the image, checks nothing from the ignore list got in, starts it with a named volume, calls `/health`, creates a case and checks the database landed on the volume.

## Threat model

| Risk | Mitigation |
|---|---|
| Evidence uploaded to a vendor by default | No hosted evidence store. The database and media live on an operator-mounted volume; the public deploy serves the landing page only. |
| Public workspace exposes cases | `BLANC_HOSTED=1` closes the workspace routes until auth (BLA-60) exists. The backend binds to localhost or a private network. |
| Key leakage | The key lives only in the host secret store and backend environment, never in the image, the repo, `NEXT_PUBLIC_` variables or responses. |
| Evidence text sent to the AI provider | Inherent to cloud compute. Gemini receives the text, and for media the bytes, needed for each request. Documented in the talk track; fully offline models are post-MVP (BLA-62). Use public or synthetic data for demos. |
| Cross-case access on a shared backend | Every route is case-scoped (404 for another case's IDs), and media is served only from that case's folder. A shared backend still needs auth (BLA-60) before more than one person uses it. |
| Lost or tampered evidence on the volume | Records are immutable once stored, and each keeps the SHA-256 of its source file (imported media also records its own `media_sha256`). Images served from `EVIDENCE_ROOT` are checked against the evidence manifest (409 on mismatch). Back up the volume like any case file. |

## Not covered here

- **Real authentication and per-user access** to cases: that's BLA-60.
- **Remote evidence storage** (an object store, a vendor volume): deliberately not offered. Revisit only with customer-controlled storage and encryption at rest.
