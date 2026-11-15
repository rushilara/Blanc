# Run Blanc on a laptop (Docker)

**Primary demo / share install** (BLA-74): one command starts the whole app — the backend API and the investigator workspace. Case evidence stays on the laptop, in a Docker volume. Only calls to Gemini (embeddings and analysis) leave it. No Vercel or Railway required. For day-to-day coding, use `make backend` / `make frontend` in the root `README.md` instead.

## Install

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and start it.
2. Get Blanc: `git clone https://github.com/rushilara/Blanc.git`, or download the repository ZIP and unzip it. Open a terminal in that folder.
3. Create your settings file and add your Gemini key:

   ```bash
   cp .env.example .env
   ```

   Open `.env` and set `GEMINI_API_KEY=` (from [Google AI Studio](https://aistudio.google.com/apikey)). Never share or commit this file.
4. Start Blanc:

   ```bash
   docker compose up --build -d
   ```

   The first build takes a few minutes. Then open <http://localhost:3000>.

Stop it with `docker compose down`. Your cases stay in the `blanc-data` volume, and `docker compose up -d` brings them back. `docker compose down -v` **deletes all cases**.

## Import a case

Put the extracted evidence in a folder, then run (replace the path and title):

```bash
docker compose run --rm -v "/path/to/export:/import:ro" backend python -m app.evidence /import --store --title "Case title"
```

The case appears in the workspace when the import finishes. Text, CSV, JSON, email, images and PDFs are supported. The export folder is mounted read-only.

## Sign-in (recommended on a shared laptop)

Sign-in is off until you set a secret. To turn it on:

```bash
mkdir -p .blanc
docker compose run --rm --no-deps frontend npm run -s auth:secret
```

Copy the printed `BLANC_AUTH_SECRET=...` line into `.env`, then add a user (it asks for a password, 8+ characters):

```bash
docker compose run --rm --no-deps frontend npm run -s auth:add-user -- yourname
```

Restart with `docker compose up -d`. The workspace and the API now need that username and password. Users are stored as scrypt hashes in `.blanc/` on the laptop, never in the image. Details and limits: `docs/06_DEPLOYMENT.md`, "Sign-in".

## What runs where

| Container | Port (this laptop only) | Holds |
|---|---|---|
| `backend` | `127.0.0.1:8000` | The case database and imported media, on the `blanc-data` volume |
| `frontend` | `127.0.0.1:3000` | Nothing: it reads cases from the backend |

Both ports are bound to `127.0.0.1`, so other machines on the network cannot reach them. Neither image contains `.env`, databases or evidence; CI checks that on every change.

## Troubleshooting

- **"The analysis service is not ready"**: `GEMINI_API_KEY` is missing from `.env`. Add it and run `docker compose up -d`.
- **Port already in use**: something else is on 3000 or 8000 (often a local `make backend` / `make frontend`). Stop it first.
- **Logs**: `docker compose logs -f`.
