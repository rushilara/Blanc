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

- `src/app/page.tsx`: loads the shared synthetic revision fixture, labels it as fictional, and retains the backend health indicator.
- `src/lib/api.ts`: backend base URL and API helpers.
- `src/components/`: UI components.

## Fixture workspace (BLA-4)

The home page renders `../fixtures/synthetic_analysis_revision.json` without
requiring the backend. `AnalysisWorkspace` accepts an `AnalysisRevision` prop
and has no fixture import or case-specific findings. A validated live response
can use the same component in BLA-42. The view shows cited claims, entities and
relationships, raw source timestamps and uncertainty, and unresolved questions.
Source references open a read-only evidence panel for the supplied case records.
The panel displays exact excerpts, source paths, locators and source timestamps;
missing or ambiguous records show an unavailable message. The backend record
endpoint, exclusion controls and annotations remain BLA-40 and later work.
Interactive graph layout and timeline normalization remain separate tickets.

Turbopack's root is the repository root so the shared fixture can be imported
directly; keep the repository's `fixtures/` directory in frontend build contexts.

## Answer pane (BLA-37)

The question input sits above the saved analysis. New question submission is
disabled and labeled as preview mode until the API is connected. `QuestionInput`
accepts an async `onAsk` callback for that integration and handles pending/error
states without pretending the saved answer has changed.

Citation buttons are deduplicated and open a native modal dialog (Close or Esc).
Only records matching the revision's case are passed to client controls, and
ambiguous source IDs are not resolved arbitrarily. Finding badges distinguish
Observed, Reported, Inferred and Unresolved using both text and color.

Manual QA: click a citation, compare the excerpt and locator with the fixture,
close the panel by button and Escape, check keyboard focus returns to the chip,
and check the question input/source panel at phone width and in dark mode.
