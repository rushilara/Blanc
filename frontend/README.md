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

- `src/app/page.tsx`: redirects to the branded landing at `/blanc/index.html`.
- `public/blanc/`: animated landing / sign-in UI (placeholder auth until BLA-60).
- `src/app/workspace/page.tsx`: loads the shared synthetic revision fixture (with records for citation chips), links to `/live`, and retains the backend health indicator.
- `src/app/live/page.tsx`: live case search / analysis UI (BLA-42).
- `src/lib/api.ts`: backend base URL and API helpers.
- `src/components/`: UI components.

## Fixture workspace (BLA-4)

`/workspace` renders `../fixtures/synthetic_analysis_revision.json` without
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

## Live workspace (BLA-42)

Open `/live` (also linked from the synthetic preview). The page loads cases from
the backend, searches only the selected case, shows exact retrieved excerpts and
loads original records through the case-scoped record endpoint. Source content
is rendered as text. No downloaded evidence is bundled into the frontend.

Set `NEXT_PUBLIC_API_BASE_URL` to the reachable backend URL before building or
starting dev mode; it is a public, build-time value. Optionally set
`NEXT_PUBLIC_DEFAULT_CASE_ID` to the server-created case to select initially.
The repo-root `.env` is supported as well as frontend/environment configuration.
Never put a Gemini key in a `NEXT_PUBLIC_` variable. Gemini stays on the backend.

`Generate analysis` posts `{question}` to `/cases/{case_id}/analyses`. It expects
the direct AnalysisRevision contract response. Schema-valid responses for the
same case/question use the existing Answer/Relationships/Timeline renderer.
Original cited records are fetched separately; missing sources are visibly
unavailable. Until the backend implements this endpoint, the page explains that
search is available but generated answers are not. There is no fixture fallback
in the live workspace. Case changes clear results and abort pending requests;
late responses are ignored.

Validation: `npm test`, `npm run lint`, `npm run build` (which generates Next's
route types), then `npx tsc --noEmit`. The API tests cover contract failures,
case/question mismatches, excluded/dangling citations, exact source identity,
abort signals and unavailable endpoints. `tests/browser-smoke.mjs` uses only
synthetic API mocks to test a valid analysis rendering and a delayed response
after a case switch. Run it with the frontend preview on port 3001 and a dedicated
headless Chrome test profile exposing CDP on port 9225:

```bash
node tests/browser-smoke.mjs
```

Live-provider QA additionally covered an indexed local case, both supporting
sources in search, the original-record modal and Escape, and a 390px viewport.
These checks validate retrieval/source inspection, not generated live answers.
