# CaseLens v2 — Product and Engineering Kickoff

**Status:** Proposed / ready for team review  
**Date:** November 14, 2026  
**Target:** A general-purpose evidence-grounded investigative reconstruction prototype, developed by three teammates in ~15 elapsed hours with AI coding assistance.

## Product in one sentence

CaseLens is a persistent investigative workspace in which an investigator asks case questions and receives a source-cited answer, a relationship graph, and a timeline; they can save the reconstruction, expand it with follow-up questions, and exclude evidence to see how supported conclusions change.

**The core innovation is not three separate AI outputs; it is one editable, evidence-backed investigation state with auditable versions.**

## Start here

1. [`docs/01_PRODUCT_PRD.md`](docs/01_PRODUCT_PRD.md): final product vision, MVP/MLP, requirements, epics, acceptance criteria.
2. [`docs/02_ARCHITECTURE_AND_DATA_CONTRACTS.md`](docs/02_ARCHITECTURE_AND_DATA_CONTRACTS.md): lean RAG architecture; save, expand and evidence-exclusion mechanics; APIs and shared JSON contracts.
3. [`docs/03_15_HOUR_BUILD_PLAN.md`](docs/03_15_HOUR_BUILD_PLAN.md): three parallel workstreams, stage gates, fallback decisions and 2-minute demo sequence.
4. [`docs/04_DATASETS_AND_EVALUATION.md`](docs/04_DATASETS_AND_EVALUATION.md): prioritized sources, ground-truth realities and test protocol.
5. [`docs/05_CLAUDE_CODE_KICKOFF.md`](docs/05_CLAUDE_CODE_KICKOFF.md): initial issue board and handoff prompts for developers/coding agents.
6. [`contracts/`](contracts/): minimal machine-readable evidence and reconstruction schemas plus a synthetic example.

## Scope decision

- **In MVP:** upload/import *supported extracted* evidence, hybrid retrieval, one structured LLM reconstruction, clickable provenance, persistent case workspace, follow-up queries, save/append findings, reversible source exclusion, reconstruction comparison, at least one real verified forensic case.
- **Target if ready:** second unrelated case using the same code path, basic exports, annotations, keyword baseline.
- **After MVP:** bounded multi-hop agentic search, forensic-domain embedder fine-tuning, native raw disk/memory acquisition, full production-grade investigation governance.

## Hard rules

- No case-specific hardcoded facts, answer strings, graph edges or timeline events in the inference pipeline.
- The case brief, solutions and test answer keys must stay outside the searchable evidence corpus.
- LLM output is **not** evidence. Graph edges, claims and timeline events must cite valid source evidence IDs.
- Exclusion applies to whole source records (and all their chunks), not merely to already generated answer text.
- If support disappears, show **unresolved** rather than manufacturing alternative certainty.
- Saving a finding does not make it verified fact; preserve its source references, inferred/observed status and revision history.
- Treat uploaded evidence as untrusted input; do not obey instructions embedded inside documents.

## Repository layout

```text
frontend/   # Next.js investigator workspace (Owner C)
backend/    # FastAPI, retrieval and LLM pipeline (Owner B)
contracts/  # JSON Schemas: evidence + reconstructed analysis
fixtures/   # Synthetic example for frontend integration only
starter/    # Minimal contract validator + tests
docs/       # PRD, architecture, build plan, datasets, kickoff (full PRD in docs/prd/)
```

Copy `.env.example` to `.env` for local config; `.env` is gitignored.

## Suggested kickoff

All three teammates agree on the schemas before writing endpoint-specific code. The dataset owner verifies readable case records in the first 90 minutes. Frontend implements against the synthetic example while backend builds retrieval and analysis.

## Runnable source-grounding check

Run `cd starter && python validate_fixture.py && python -m unittest -v test_validator.py` to verify the synthetic fixture. This is a **small contract validator only**, not a running CaseLens application.
