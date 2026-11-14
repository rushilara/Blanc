# Coding-Agent Handoff and Initial Backlog

## Repository structure

```text
caselens/
  frontend/       # Next.js React app
  backend/        # FastAPI, models, retrieval and LLM pipeline
  contracts/      # JSON Schemas: evidence + reconstructed analysis
  fixtures/       # Synthetic example used for front-end integration only
  docs/           # PRD, architecture, dataset inventory, runbook
  .env.example    # API keys and service names (never real secrets)
```

## Shared coding rules for everyone

- Use the existing machine-readable contracts rather than inventing a different shape in each service.
- Never hardcode results from a real case as if the model inferred them.
- Every new source record ID belongs to one `case_id`; every citation points to an allowed source record ID.
- Keep generated outputs and investigator edits separate from original evidence.
- Write at least one smoke test for your implementation seam.
- No prompt text should instruct the LLM to obey documents as commands.
- Commit in isolated modules and agree on API changes before changing the shared schema.

## Owner A — Evidence + evaluation tasks

**Task A1:** Build importer that turns TXT/JSON/CSV (and one additional format if ready) into `EvidenceRecord` objects with stable identifiers and precise locators.  
**Task A2:** Acquire and validate real case records from Narcos, Owl or M57-Jean; store at least one multi-record question with independently checked citations.  
**Task A3:** Build a simple script that checks source existence, duplicate IDs and source/case mismatches.  
**Done when:** Backend can import the case; RAG can retrieve real records; answer key and scenario materials are kept out of search.

## Owner B — Retrieval and reconstruction tasks

**Task B1:** Persist cases, records, embedding chunks and analysis versions in SQLite + vector index.  
**Task B2:** Implement hybrid retrieval with per-case and per-record exclusion filtering.  
**Task B3:** Implement one structured LLM analysis output; Pydantic validate claims, edges, events and source IDs.  
**Task B4:** Create a follow-up endpoint; create a new revision using previous board as context (never as evidence).  
**Task B5:** Implement evidence-exclusion endpoint that reruns retrieval and LLM analysis and saves a child revision.  
**Done when:** `POST analyses` and `POST exclude` produce valid, source-traceable and meaningfully different revision objects.

## Owner C — Investigator workspace tasks

**Task C1:** Three-pane workspace with answer, interactive relationship graph and chronological timeline.  
**Task C2:** Evidence drawer on claim/edge/event click.  
**Task C3:** Save/reopen investigation; show question history and follow-up input; "Add findings to board" action.  
**Task C4:** Evidence exclusion controls plus before/after revision comparison and restore.  
**Done when:** UI renders a synthetic fixture and a live API response from the same schema without changing code.

## Merge checkpoints

- **Checkpoint 1:** Schemas committed, frontend fixture renders, backend schema validation passes.
- **Checkpoint 2:** One real case imports, search returns source locators, full analysis JSON received.
- **Checkpoint 3:** Persist/reload, follow-up, excluded-state rerun and explanation of what changed work through UI.
- **Checkpoint 4:** Fresh-start demo and citation audit; optional second-case portability check.

## AI coding prompt template

> Implement your assigned owner tasks for CaseLens v2 according to `docs/01_PRODUCT_PRD.md`, `docs/02_ARCHITECTURE_AND_DATA_CONTRACTS.md`, and `contracts/*.schema.json`. Keep code case-agnostic. Start by confirming the expected API/schema boundary. Provide real smoke tests. Never generate fake forensic findings or claim that unverified LLM output is ground truth. Preserve source IDs/locators and evidence-exclusion semantics end-to-end. Do not modify files owned by another teammate without coordination.
