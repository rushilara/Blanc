# Hosted app path (BLA-61)

> **Not the demo deploy.** The intended share/demo install is Docker Desktop + compose on a laptop — see [`docs/07_LAPTOP_INSTALL.md`](07_LAPTOP_INSTALL.md) ([BLA-74](https://linear.app/blushandbryann/issue/BLA-74): laptop Docker = deploy; no Vercel/Railway required). This document is optional hosted landing / container notes only.

Blanc is **local-first**: the default and recommended way to run the product is on the investigator's machine (`README.md`, "Demo runbook", and the laptop Docker path above). This document describes the *optional* hosted path for the app tier. It is **not** a cloud evidence store: case evidence, the SQLite database and imported media stay on storage the operator controls.

## What is hosted, and where

| Part | Where it runs | What it holds |
|---|---|---|
| Landing page (`frontend/public/blanc/`) | Vercel (or any static/Next.js host), public | Marketing copy and the sign-in screen. No evidence. |
| Workspace routes (`/workspace`, `/live`, `/sample`) | Same Next.js deploy, **closed** on a public deploy (`BLANC_HOSTED=1`); behind sign-in (BLA-60) locally | Nothing until a signed-in user reaches a backend that holds their case. |
| Backend API (`backend/Dockerfile`) | A container the operator runs: their own machine, a lab server, or a private VM | Reads and writes the case database and media on a mounted volume (`/data`). |
| Case evidence, SQLite DB, media | The `/data` volume, on operator-controlled storage | Records, chunks, vectors, revisions, boards, imported images and PDFs. |
| Gemini (embeddings, analysis) | Google's API | Receives the evidence text (and, for media, the bytes) needed for each request. Nothing is stored there by Blanc. |

**Optional marketing landing only:** if you want a public marketing page with the workspace closed, you can put the landing on Vercel with `BLANC_HOSTED=1`. That is not how teammates or demo recipients run Blanc — they use the laptop Docker path. Anyone who opens a case still needs backend + workspace on a machine they control.

## Frontend on Vercel

- **Project root:** `frontend/`. Keep "Include files outside the root directory" on: the frontend imports the shared JSON Schemas from `contracts/`.
- **Environment variables:**
  - `BLANC_HOSTED=1` closes the workspace routes. `src/proxy.ts` redirects `/workspace`, `/live` and `/sample` to `/blanc/hosted.html`, which explains how to run Blanc locally. The landing page stays public. Local runs leave it unset and are unaffected.
  - `NEXT_PUBLIC_API_BASE_URL` is only needed once a backend is reachable from that deploy (same site as the frontend; see Sign-in). It is public and build-time; never put a key in a `NEXT_PUBLIC_` variable.
- **Keep `BLANC_HOSTED=1` on a public deploy.** Sign-in (below) works when the frontend and backend share a site, such as both on `localhost`. A public frontend talking to a backend on another site would not carry the session cookie, so the workspace stays closed there.

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
- **Network:** bind to `127.0.0.1` or a private network. Set `CORS_ORIGINS` to the exact frontend origin. Set `BLANC_AUTH_SECRET` so the API requires a signed-in session (see Sign-in). Even then, do not expose it publicly: sign-in is a gate for shared machines, not internet-facing account security.
- **CI:** the "Backend image" job builds the image, checks nothing from the ignore list got in, starts it with a named volume, calls `/health`, creates a case and checks the database landed on the volume.

## Sign-in (BLA-60)

The landing page's **Sign in** is a real check when `BLANC_AUTH_SECRET` is set in the repo-root `.env`. Both servers read that one value:

- The Next server checks the username and password against `.blanc/users.json` (scrypt hashes, gitignored; override with `BLANC_USERS_FILE`). On success it sets an HttpOnly, SameSite=Lax `blanc_session` cookie, signed with the secret, for 12 hours.
- The Proxy sends anyone without a valid cookie from `/workspace`, `/live` and `/sample` to the sign-in page. It brings them back to where they were going afterwards, and only ever to one of those routes.
- The backend returns 401 on every route except `/health` without the same valid cookie. The frontend sends it with `credentials: "include"`, so open the app and the API on the same host (`localhost` and `localhost`, not `localhost` and `127.0.0.1`).

Set it up (from `frontend/`):

```bash
npm run auth:secret                 # prints BLANC_AUTH_SECRET=...; put that line in the repo-root .env
npm run auth:add-user -- <username> # prompts for the password (8+ characters)
npm run auth:remove-user -- <username>
```

Restart both servers after changing the secret. To sign out, open `/api/auth/logout`. Five wrong passwords in a row lock that username for a minute.

**Demo bypass:** leave `BLANC_AUTH_SECRET` empty. Sign-in is then off on both servers, and the form opens the workspace as before. A secret shorter than 32 characters is refused: the backend will not start, and the app routes stay closed.

**Limits:** this gates the app and API on the investigator's machine. It does not encrypt the SQLite file or media, so pair it with OS accounts and disk encryption. It is one shared user list, not per-case access. Evidence never leaves the machine for sign-in.

## Threat model

| Risk | Mitigation |
|---|---|
| Evidence uploaded to a vendor by default | No hosted evidence store. The database and media live on an operator-mounted volume; the public deploy serves the landing page only. |
| Public workspace exposes cases | `BLANC_HOSTED=1` closes the workspace routes on a public deploy; locally, `BLANC_AUTH_SECRET` puts them and the API behind sign-in. The backend binds to localhost or a private network. |
| Key leakage | The key lives only in the host secret store and backend environment, never in the image, the repo, `NEXT_PUBLIC_` variables or responses. |
| Evidence text sent to the AI provider | Inherent to cloud compute. Gemini receives the text, and for media the bytes, needed for each request. Documented in the talk track; fully offline models are post-MVP (BLA-62). Use public or synthetic data for demos. |
| Cross-case access on a shared backend | Every route is case-scoped (404 for another case's IDs), and media is served only from that case's folder. Sign-in gates the whole backend, not single cases: every signed-in user sees every case on it. |
| Lost or tampered evidence on the volume | Records are immutable once stored, and each keeps the SHA-256 of its source file (imported media also records its own `media_sha256`). Images served from `EVIDENCE_ROOT` are checked against the evidence manifest (409 on mismatch). Back up the volume like any case file. |

## Not covered here

- **Per-user access to cases**, and a hosted identity provider (SSO/OAuth): sign-in is one local user list for the whole app.
- **Remote evidence storage** (an object store, a vendor volume): deliberately not offered. Revisit only with customer-controlled storage and encryption at rest.
