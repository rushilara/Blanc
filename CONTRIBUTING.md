# Contributing to Blanc

Work is tracked in Linear (team **BLA**, project **Blanc**). Every change maps to one Linear issue.

## The loop

1. **Pick an issue.** Take your highest-priority issue in Linear that has no open blockers. If an issue blocks someone else, finish it (or review it) before starting something new.
2. **Branch from `main`.** In Linear, press `Cmd+Shift+.` to copy the issue's branch name, then:

   ```bash
   git checkout main && git pull
   git checkout -b <branch-name-from-linear>
   ```

   One branch per issue.
3. **Work in small commits.** Include the issue ID (e.g. `BLA-13`) in commit messages or the PR title.
4. **Open a PR to `main`.** Use the PR template. Put `Fixes BLA-13` in the description so Linear links and closes the issue. Request one teammate as reviewer.
5. **Review.** The reviewer checks the issue's acceptance criteria, not just the code, and that CI is green. Aim to review within ~30 minutes during the build sprint.
6. **Merge.** After one approval and green CI, the author merges and deletes the branch. Never push directly to `main`.

With the Linear GitHub integration enabled, issues move automatically: **In Progress** when a branch is pushed, **In Review** when the PR opens, **Done** when it merges.

## Team rules

- **Contracts are owned by the integrator (Rushil).** Any change to `contracts/`, `fixtures/` or endpoint request/response shapes needs his review.
- **Keep the pipeline case-agnostic.** No hardcoded case facts, answers, graph edges or timeline events.
- **LLM output is not evidence.** Claims, edges and events must cite valid source record IDs.
- **Keep answer keys and case briefs out of the searchable corpus**, and never commit real case evidence or secrets. `.env` is gitignored; use `.env.example` for new settings.

## CI

GitHub Actions runs on every PR and on pushes to `main` (`.github/workflows/ci.yml`):

| Job | What it checks | When it runs |
|---|---|---|
| Contracts + fixtures | Schemas are valid JSON Schema; fixtures conform; source-grounding checks in `starter/` pass | Always |
| Backend | `ruff check`, then `pytest` with line coverage in `backend/` (fails below 80%) | Once `backend/requirements.txt` exists |
| Frontend | `npm ci`, then `lint`, `build` and `test` scripts if defined | Once `frontend/package.json` exists |

Backend conventions CI relies on: runtime deps in `backend/requirements.txt`, optional dev deps in `backend/requirements-dev.txt`, tests in `backend/tests/`. Frontend: commit `frontend/package-lock.json`.

### Coverage

The Backend job measures which lines of `backend/` (the `app` package and top-level modules such as `evidence_prompt.py`; `tests/` excluded) the unit tests run. The result shows up in three places:

- **Job summary** on the Actions run page: total % and a per-file table with missing lines.
- **PR comment**: one sticky comment, updated on every push (not posted for PRs from forks).
- **`backend-coverage` artifact** on the run: `coverage.xml` and the browsable `htmlcov/` report.

Run it locally from the repo root:

```bash
make coverage   # prints the report; open backend/htmlcov/index.html for line-by-line detail
```

A minimum of **80% line coverage is enforced**: the Backend job (and `make coverage`) fails if total coverage drops below it. To change the floor, edit `fail_under` under `[tool.coverage.report]` in `backend/pyproject.toml`; commenting it out goes back to report-only.

Frontend coverage will be added once the frontend has tests.

Run the contract checks locally:

```bash
pip install jsonschema
python starter/validate_schemas.py
cd starter && python validate_fixture.py && python -m unittest -v test_validator.py
```
