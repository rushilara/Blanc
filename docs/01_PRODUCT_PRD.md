# CaseLens — Unified Product Requirements Document (v2)

**Status:** Product direction agreed in discussion; engineering details proposed for team review.  
**Primary persona:** Investigator or digital-forensic analyst using exported records from a new case.  
**Prototype window:** ~15 elapsed hours / three teammates / coding agents.  
**Background:** Integrates the teammate's *Forensic Reconstruction — Arlequin Track 1* (short hackathon demonstration), the earlier general-purpose CaseLens vision, and the later decision to prioritize persistent investigation workspaces.

## 1. Vision

Help investigators move from unorganized digital evidence to an inspectable, revisable case reconstruction. Investigators ask natural-language questions and receive (1) a short answer with citations, (2) a relationship graph of entities and supported links, and (3) a timeline of recorded and inferred events. They can **save** useful work, **ask follow-up questions that build on it**, **exclude disputed evidence**, and **compare reconstructions**. Every user-visible claim, relationship and event is traceable to a source record.

CaseLens is a **general-purpose software pipeline**, not a Narcos-specific chatbot. Published forensic datasets are development/evaluation cases; no specialized model training is necessary for the first release.

## 2. Product principles

1. **Workspace first:** The output of a query is a revision of an investigation, not a disposable chat response.
2. **Grounded by default:** Every substantive finding, graph edge and event links to original evidence. Evidence, allegations and model inferences are visually distinguished.
3. **Reversible reasoning:** An investigator can challenge evidence without deleting it or destroying earlier versions.
4. **Progressive reconstruction:** Follow-up questions retrieve new evidence and contribute verified, sourced elements to the saved workspace.
5. **Case-agnostic ingestion:** Supported extracted file formats follow the same schema and inference pipeline, whatever the case theme.
6. **Prototype honesty:** Missing evidence, weak support and unknown timestamps are explicit. This is investigative decision support, not autonomous fact finding or a guilt classifier.

## 3. Core user journey

**Create a case → Upload supported evidence exports → Ask a question → Inspect cited answer, graph and timeline → Open original excerpts → Save reconstruction → Ask a follow-up and add new findings → Exclude a disputed record → Compare before/after → Restore or save a new branch.**

Example illustrative questions (not claims about any particular dataset):
- "Which records connect these two individuals, and what do those records actually show?"
- "What happened between the initial contact and the reported transfer?"
- "What parts of the explanation remain if we exclude this chat record?"
- "What is still unknown about the actor using this device?"

### Workspace layout

- **Left rail:** case picker, saved investigations, question history, evidence inclusion/exclusion status.
- **Top / main:** investigator question input; concise answer with claim-level citations and uncertainty notes.
- **Center:** interactive entity/relationship graph with clickable edge-to-evidence drawer.
- **Below / adjacent:** timestamped timeline + separate undated events section; click to open evidence.
- **Right drawer:** original excerpt, document path, locator, timestamp kind, evidence ID, exclusion and annotation controls.
- **Versions drawer:** saved analysis revisions, parent revision, excluded evidence, before/after differences, investigator notes.

## 4. MVP scope — required for first usable prototype

| ID | Requirement | Acceptance criteria |
|---|---|---|
| FR-01 | Create/select case | Case has distinct ID and separate searchable evidence. |
| FR-02 | Ingest extracted evidence | Import supported TXT/CSV/JSON and at least one of PDF/EML; preserve source filename, source locator, stable record ID, and timestamp if available; display errors for unsupported formats. |
| FR-03 | Search and retrieve | Hybrid semantic + keyword retrieval returns relevant source records with IDs, excerpts and locators; unrelated same-case records remain in index. |
| FR-04 | Answer with citations | Answer cites source record IDs; unsupported statements are suppressed or marked unresolved. |
| FR-05 | Build graph dynamically | LLM generates question-relevant entities and typed relationships at runtime; each edge has one or more source IDs. |
| FR-06 | Build timeline dynamically | LLM generates cited events; date parsing/sorting is deterministic; undated and uncertain events are segregated. |
| FR-07 | Evidence drill-down | Clicking a claim, edge or event opens the corresponding source excerpt and locator. |
| FR-08 | Persist investigation | Save case, question, answer, graph, timeline, source IDs, annotations and revision metadata; reload after browser refresh. |
| FR-09 | Expand with follow-ups | Asking a follow-up searches the original case corpus and produces a new revision; investigator can add the sourced results to the existing board without losing previous work. |
| FR-10 | Exclude evidence | Exclude one source record and all its chunks; rerun retrieval + reconstruction under the new inclusion set; do not merely hide a graph edge. |
| FR-11 | Compare and restore | Show previous/current finding status and changed edges/events; restore the record to recover the previous evidence state. |
| FR-12 | Provenance integrity | Reject fabricated citation IDs; prevent cross-case citation leakage; mark observed vs claimed vs inferred statements. |

### Definitions that avoid ambiguity

- **Evidence record:** a source-traceable unit from an imported file, for example a message, log event or bounded document excerpt. A document may contain multiple records; each record may have multiple embedding chunks.
- **Analysis revision:** saved output for a question, retrieval set and explicit list of excluded source records.
- **Investigation board:** user-curated set of cited findings/edges/events saved across questions; it is not itself primary evidence.
- **Exclude:** remove a record from active evidence used for retrieval, expansion and generation while keeping it retained in the source corpus and audit history.
- **Expand:** attach new source-validated findings to the board. Prior LLM summaries provide context, **never** proof.

## 5. MLP — make it lovable after the prototype

- Clear, persistent "Save to board" / "Add to timeline" controls and useful visual difference highlighting.
- Investigator notes, manual correction/flagging and status (unreviewed, reviewed, disputed).
- Split-screen compare of two analysis versions with the evidence state shown on each side.
- Export a structured case report containing source references and limitations.
- Cleaner large-graph navigation, search/filter by node or event type, manual pinning of important entities.
- Multi-case reliability testing, responsive UI, loading/empty/error states and recovery of interrupted tasks.

## 6. Nice-to-have / future epics (explicitly not needed for first prototype)

- **Bounded agentic multi-hop RAG:** LLM can request up to two further retrieval iterations, with a fixed token/search budget, for nonobvious connections.
- Forensic-domain embedding fine-tuning using carefully annotated query-positive-hard-negative examples.
- Ingestion-time entity/event preprocessing and persistent graph database for very large cases.
- Native E01/raw disk, RAM, cloud export and mobile extraction ingestion.
- Advanced entity disambiguation, geospatial views and richer temporal reasoning.
- Role-based permissions, organization-wide case management and forensic-grade chain-of-custody integrations.

## 7. Epics and prioritized user stories

| Epic | Priority | Stories / done condition |
|---|---|---|
| E1. Shared contracts + case foundation | P0 | Agree on record/reconstruction schemas; create case-specific IDs; fixture renders in UI and validates in backend. |
| E2. Evidence import + search | P0 | Import extracted records; embed/index; hybrid search and cited retrieval; support exclusions at record level. |
| E3. Structured reconstruction | P0 | One LLM call outputs answer, cited claims, graph and events; validate source IDs; reject invalid output. |
| E4. Persistent investigator workspace | P0 | Save and reload revisions; ask follow-up using prior board for context; add verified sourced elements to board. |
| E5. Evidence sensitivity analysis | P0 | Exclude, re-retrieve, regenerate, compare and restore; show what loses support. |
| E6. Evidence-first UI | P0 | Synchronized answer/graph/timeline; source drawer; save/expand/version controls. |
| E7. Case evaluation + demo | P0 | At least one real case and verified multi-record question; no hardcoded answer; fresh-start test. |
| E8. Cross-case portability + polish | P1 | Second case using same code; investigator annotations, exports, visual diff, search baseline. |
| E9. Bounded agentic retrieval | P2 | Up to two retrieval expansions with guardrails and cost limits. |
| E10. Model specialization | P2 | Fine-tuning and held-out benchmark after sufficient high-quality labels. |

## 8. Success criteria and non-goals

**Minimum demo pass:** A teammate who did not write the prompt can open a case, ask a manually validated multi-record question, inspect at least one cited graph edge and a cited event, save the workspace, ask a follow-up that adds evidence-backed material, exclude a pivotal record, see a meaningful support change, and reload the saved before/after states.

**Secondary validation:** Load a different case without changing parser-specific business logic, answer one independently verified question, and note which formats or extraction tasks still fail. This shows portability but does not prove universal investigator readiness.

**Non-goals for the 15-hour prototype:** native image/RAM analysis, legal conclusions, guilt scores, automatically finding every relevant artifact, production-grade evidentiary admissibility, autonomous investigations, universal format compatibility, or claims of statistical generalization from two scenarios.

## 9. Open engineering decisions (defaults recommended)

- **Embedding baseline:** choose one pretrained model/service and lock it at kickoff. Avoid fine-tuning now.
- **Storage:** SQLite for cases, source records and revision history; local vector index for embeddings. No Neo4j required.
- **UI:** React graph library, simple event list/timeline; functionality before elaborate animation.
- **Source granularity:** exclude at evidence-record level; optionally add document-level exclusion later.
- **Merging follow-up findings:** match exact canonical IDs where available; otherwise ask user to confirm a merge and never silently equate similar names.
- **Investigation branches:** save every exclusion analysis as a revision linked to a parent; do not mutate history.

## 10. Safety, reliability and governance

Uploaded documents are untrusted data and may contain instructions directed at the model. Treat them as quoted evidence, not as higher-priority prompts. Maintain case isolation and prevent retrieval across case IDs. Preserve file provenance, file hashes where practical, and a record of excluded evidence. Do not equate communication, shared identifiers or proximity with criminal culpability. Report uncertainty; allow a human investigator to verify each claim. Synthetic datasets support prototype demonstrations but do not establish operational reliability.
