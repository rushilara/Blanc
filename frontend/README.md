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

**Local-first:** the workspace talks to a local FastAPI backend; case evidence lives in local SQLite, not a hosted store. Keep the landing login page; real cloud auth is tracked in BLA-60 (gates the app — it does not encrypt the DB file by itself).

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


## Timeline view (BLA-39)

The shared workspace uses TimelineView for both the fixture and live revisions.
It calls the backend's pure `/timeline/normalize` adapter to get authoritative
ordering and display text. No browser date parser or local timezone conversion is
used. Known day/month precision is labeled; approximate, unknown-zone exact,
undated and ambiguous values have a separate section. Original timestamps,
source-stated/inferred kind and finding status remain visible.

Click an event description to open its original sources. Multiple cited records
can be selected within the same drawer. Close and Escape restore keyboard focus
to the event. If normalization fails, no event is hidden and no chronological
placement is claimed; Retry requests normalization again. Empty timelines make
no normalization request. The fixture still renders without a running backend,
with unavailable normalization explicitly indicated.

Tests: `npm test` covers response identity/shape, grouping and unavailable states.
The backend route tests compare output to the actual normalizer, including offsets,
coarse precision, unknown zones, ambiguous dates, empty input and duplicate IDs.
`node tests/timeline-browser.mjs` runs synthetic analysis/source mocks against the
real local normalizer; requires the preview on port 3002, its backend on 8002 and
a dedicated headless Chrome test profile exposing CDP on port 9226. It verifies
ordering, multi-source event inspection, Escape/focus, an independent browser time
zone, 390px layout, error preservation/retry and empty timelines.

## Saved versions (BLA-41)

On `/live`, **Saved versions** loads the selected case's history from
`GET /cases/{case_id}/analyses`. Each entry shows its question, saved timestamp
(as stored, with time-zone offset), parent, excluded records, review flag and
restore source when present. With a revision open, the list defaults to its
investigation (the shared root and all sibling branches). The selector also
allows browsing all investigations in the current case.

**Open version** retrieves the immutable saved revision by ID, validates the
shared contract and case/revision identity, and loads original sources for its
claims, nodes, edges, events and excluded-record references. It does not invoke
retrieval, embedding or answer generation. The investigation question updates to
the saved question. Failed opens keep the current view; missing originals remain
visibly unavailable in the source drawer. Case changes abort pending loads and
ignore late revision/source responses. History is refreshed each time the drawer
opens, so newly generated revisions appear without a page reload.

`npm test` covers history shape/lineage validation, root grouping, saved response
identity and partial source loading. Browser regression checks use synthetic
responses only, including errors/retry, branch filtering, excluded originals,
failed opens preserving the view, case switches during revision and source
loads, empty history, Escape/focus and mobile wrapping. Run against an isolated
frontend preview and dedicated Chrome CDP session:

```bash
PREVIEW_URL=http://127.0.0.1:3006/live CDP_URL=http://127.0.0.1:9233 node tests/versions-browser.mjs
```

The browser script makes GET requests only. It does not use live model APIs or
modify a case. Reopening saved versions after refresh uses the drawer; automatic
restoration of the selected version on refresh belongs to BLA-32.
