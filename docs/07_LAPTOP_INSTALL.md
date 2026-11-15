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

Local sign-in gates the browser and API against casual misuse on a shared machine (someone opening `localhost` without your password). It does **not** encrypt SQLite or imported media on disk — someone with your OS account or the drive can still read that data. Pair Blanc with an OS login and full-disk encryption. The steps below turn on the existing BLA-60 session gate; design details stay in [`docs/06_DEPLOYMENT.md`](06_DEPLOYMENT.md#sign-in-bla-60).

**Demo bypass:** leave `BLANC_AUTH_SECRET` empty in `.env` (the default). Sign-in stays off, and the landing form opens the workspace without a password.

### Turn sign-in on

1. Generate a secret (32+ characters) and put it in `.env`:

   ```bash
   mkdir -p .blanc
   docker compose run --rm --no-deps frontend npm run -s auth:secret
   ```

   Copy the printed `BLANC_AUTH_SECRET=...` line into `.env`. Never commit a real value.

2. Add the first user (it asks for a password, 8+ characters):

   ```bash
   docker compose run --rm --no-deps frontend npm run -s auth:add-user -- yourname
   ```

   Users are stored as scrypt hashes in `.blanc/users.json` on the laptop (compose mounts `./.blanc` into the frontend container at `/app/.blanc`). Override the path with `BLANC_USERS_FILE` in `.env` if needed. Passwords are never written in plain text.

3. Restart and sign in on the landing page:

   ```bash
   docker compose up -d
   ```

   Open <http://localhost:3000>, enter the username and password, and continue into the workspace.

With the secret set, `/workspace`, `/live`, `/sample`, and every API route except `/health` require a valid session cookie. Five wrong passwords in a row lock that username for a minute. Sign out at `/api/auth/logout`.

## What runs where

| Container | Port (this laptop only) | Holds |
|---|---|---|
| `backend` | `127.0.0.1:8000` | The case database and imported media, on the `blanc-data` volume |
| `frontend` | `127.0.0.1:3000` | Nothing durable: it reads cases from the backend. Sign-in users live on the host in `.blanc/` (mounted read/write) |

Both ports are bound to `127.0.0.1`, so other machines on the network cannot reach them. Neither image contains `.env`, databases or evidence; CI checks that on every change.

| Setting | Where it lives |
|---|---|
| `GEMINI_API_KEY`, `BLANC_AUTH_SECRET` | Repo-root `.env` (compose `env_file` for both services) |
| Users file | Host `.blanc/users.json` → container `/app/.blanc/users.json` |

## Troubleshooting

- **"The analysis service is not ready"**: `GEMINI_API_KEY` is missing from `.env`. Add it and run `docker compose up -d`.
- **Sign-in says it is not set up correctly**: `BLANC_AUTH_SECRET` is set but shorter than 32 characters, or `.blanc/users.json` is missing/unreadable. Regenerate the secret, add a user, and restart.
- **Port already in use**: something else is on 3000 or 8000 (often a local `make backend` / `make frontend`). Stop it first.
- **Logs**: `docker compose logs -f`.
