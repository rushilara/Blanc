# 15-Hour Development Plan — Three Parallel Workstreams

**Interpretation:** ~15 elapsed hours with three teammates, not 15 total person-hours. The plan assumes extracted evidence, existing LLM/embedding APIs, and fast coding assistance. These are planning checkpoints, not guaranteed timings.

## Owner tracks

| Owner | Primary scope | Owns |
|---|---|---|
| A — Evidence / evaluation | Obtain actual case exports; normalize evidence; verify answer keys; prepare test case | fixtures/, ingest adapters, test questions, evaluation |
| B — RAG / backend | Search, LLM structured response, grounding checks, save/branch/exclusion orchestration | backend/, contracts validation, DB |
| C — UI / integration | Case workspace, three views, evidence drawer, saved revisions, comparison | frontend/, demo flow |

**Unblock in parallel:** Everyone uses `contracts/` and a synthetic reconstruction fixture; frontend does not wait for a working LLM. Assign a single integrator for shared API and schema changes.

## Stage gates

| Elapsed hour | Goal | Concrete acceptance test | Fallback |
|---|---|---|---|
| 0–1.5 | Lock contracts + dataset | Agree on record/analysis IDs. Find one readable conversation and an independent supporting artifact in Narcos; record exact locators. | Switch to Owl or M57-Jean if evidence cannot be recovered promptly. |
| 1.5–4 | Import, index, skeleton UI | New case created, real record searchable, synthetic answer/graph/timeline renders. | Support TXT/JSON/CSV first; treat PDF/EML as stretch if parsing blocks. |
| 4–7 | End-to-end first question | Question → hybrid retrieval → structured JSON → cited answer/graph/timeline for one verified case. | Use one LLM pass; skip extra retrieval hops. |
| 7–10 | Persistent workspace + follow-up | Save/reload a revision, ask follow-up, add new sourced elements to board; original saved version remains intact. | Save snapshots rather than complex automated graph merging. |
| 10–12.5 | Exclusion and comparison | Exclude record; retrieval and LLM both respect exclusion; show changed/unresolved finding; restore or branch. | Use deterministic changed/unchanged/unsupported diff if LLM comparison is unreliable. |
| 12.5–15 | QA, second-case check, presentation | Fresh-start demo, verify citations/locators, demonstrate saving and evidence sensitivity. If time permits, load second case unchanged. | Prefer one truly real, reliable case over two shallow or fabricated ones. |

## Hard priorities under time pressure

1. Never compromise on source IDs, citation validation, case isolation or save/reload.
2. Do not replace real retrieved artifacts with a scripted, hardcoded graph while describing it as live inference.
3. Keep graph and timeline visually simple before implementing sophisticated graph editing.
4. Evidence exclusion must rerun retrieval, not only filter an existing answer.
5. If the case dataset is inaccessible, **switch data source early**, not late.

## Demo acceptance checklist

- [ ] A case is opened and at least several unrelated records are also indexed.
- [ ] Investigator asks a question whose answer requires at least two real records.
- [ ] Retrieved source IDs and locators match actual artifacts.
- [ ] Every answer claim, graph edge and timeline event opens valid evidence.
- [ ] Investigator saves the reconstruction; it persists after refresh.
- [ ] Follow-up question adds at least one sourced item to the saved investigation.
- [ ] Excluding one pivotal record triggers a fresh retrieval/analysis and changes support appropriately.
- [ ] The original reconstruction can be restored or compared without losing the work.
- [ ] Demo explains limits: simulated case, unsupported formats, imperfect retrieval and no trained forensic model.

## Two-minute demo narrative

1. **0:00–0:20**: Open preloaded case and show number of indexed artifacts (without indexing the answer key).
2. **0:20–0:45**: Ask verified investigative question; show answer, relationship graph and timeline.
3. **0:45–1:05**: Click graph edge and a timeline event; reveal original locators and quoted evidence.
4. **1:05–1:25**: Save board and ask follow-up; add new cited event/connection to the existing investigation.
5. **1:25–1:50**: Exclude a pivotal record; show before/after support and timeline changes.
6. **1:50–2:00**: Restore; state case-agnostic architecture and later multi-hop/model-training roadmap.

## After the hackathon

**Milestone 2 (MLP):** second/third case benchmarks, analyst annotations, robust merge/diff, export, better retrieval and entity resolution.  
**Milestone 3 (research):** collect relevance-pair labels, test fine-tuned vs generic embeddings, add capped agentic retrieval and run held-out case evaluations.  
**Milestone 4 (real deployments):** audited access, secure deployment, supported forensic tool export adapters, rigorous provenance/case isolation, human review workflows.
