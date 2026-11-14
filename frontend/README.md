# Frontend (Owner C)

Next.js investigator workspace: answer, relationship graph and timeline panes; evidence drawer; save/reopen; follow-ups; evidence exclusion and revision comparison.

Develop against `../fixtures/synthetic_analysis_revision.json` until the backend is live. See `../docs/05_CLAUDE_CODE_KICKOFF.md` (tasks C1–C4).

## Stack

Next.js (App Router) + TypeScript + Tailwind CSS + ESLint, npm. Node 20+.

## Run

From the repo root: `make frontend` (runs `npm install` and `npm run dev`), then open http://localhost:3000.

Or from `frontend/`:

```bash
npm ci          # install from the committed package-lock.json
npm run dev     # dev server on http://localhost:3000
npm run lint
npm run build
```

## Config

`next.config.ts` loads the shared repo-root `.env`. The only variable the frontend reads is `NEXT_PUBLIC_API_BASE_URL` (default `http://localhost:8000`), via `src/lib/api.ts`, which also exposes `getHealth()`.

## Layout

- `src/app/page.tsx`: workspace shell with empty Answer, Graph and Timeline panes and a backend health indicator.
- `src/lib/api.ts`: backend base URL and API helpers.
- `src/components/`: UI components.
