# Blanc schemas

All schemas are JSON Schema draft 2020-12 with `additionalProperties: false`. Every top-level object that carries `schema_version` uses the const `"1.0"`.

| Schema | What it describes |
|---|---|
| `evidence_record.schema.json` | One immutable, traceable source record (`record_id`, `case_id`, file, locator, content, timestamp). Optional media fields (BLA-68): `modality` (`text` \| `image` \| `pdf`), `media_path` (`media/rec_<16 hex>.(png\|jpg\|pdf)`), `content_type` (`image/png` \| `image/jpeg` \| `application/pdf`). When `modality` is `image` or `pdf`, `media_path` and `content_type` are required; `content` is the empty string until multimodal embedding. |
| `analysis_revision.schema.json` | One immutable, question-specific reconstruction: answer, claims, graph, timeline, unresolved questions, plus the retrieved and excluded record IDs it was built from. |
| `investigation_board.schema.json` | One append-only snapshot of an investigator's board: pinned findings and investigator notes. |
| `revision_comparison.schema.json` | How support for each claim, edge and event changed between a parent revision and the child revision created by excluding evidence. |

The `../fixtures/` folder contains **synthetic example content only**. It is safe for frontend development, but **must not** be presented as findings extracted from Narcos or any actual forensic dataset. `synthetic_analysis_revision_excluded.json` is the child of `synthetic_analysis_revision.json` with `rec-02` excluded; the board and comparison fixtures reference IDs from those two revisions.

## Date-time fields

Every `*_at` field (`created_at`, `updated_at`, `added_at`) is an ISO-8601 date-time **with a UTC offset**, either `Z` or `+HH:MM`/`-HH:MM` (e.g. `2026-11-14T20:00:00Z` or `2026-11-14T15:00:00-05:00`). There is no UTC-only rule. Naive timestamps (no offset) and unparseable values are rejected. Consumers must parse these values before comparing them, never compare them as strings: `2026-11-14T15:00:00-05:00` and `2026-11-14T20:00:00Z` are the same instant. JSON Schema `format` is not enforced by default, so `starter/validate_fixture.py` parses them.

This rule does not cover evidence `timestamp` fields, which may be null, partial or approximate (see `timestamp_precision` and `timestamp_kind`).

## AnalysisRevision

Revisions are immutable. A follow-up or an exclusion creates a child revision with `parent_analysis_id` set; the parent stays inspectable.

Optional review fields:

- `needs_review` (boolean): set by the server when LLM output still failed schema/source validation after one retry and the revision holds a partial result. Absent or `false` means validation passed.
- `validation_errors` (string array): server-generated reasons for `needs_review`. Never LLM-authored.

The frontend must show a review banner when `needs_review` is `true`.

### Evidence window (`retrieval_coverage`, BLA-64)

Optional, server-assigned, never produced by the LLM. Absent on revisions saved before it existed: treat those as the `standard` window with no offer.

- `evidence_window`: `standard` (`RETRIEVAL_MAX_RECORDS`, default 16 records) or `expanded` (`RETRIEVAL_EXPANDED_MAX_RECORDS`, default 24). Only the record count differs; `max_excerpt_chars` is the same for both.
- `max_records`, `max_excerpt_chars`: the limits this revision ran with.
- `skipped[]`: every ranked candidate the window left out, in rank order: `record_id`, fused `rank`, `reason` (`record_limit` or `budget`), and `vector_rank` / `keyword_rank` when that search found the record (omitted, never null, otherwise). IDs only, never content. **Skipped records are not citations** and are never shown to the model.
- `budget_substituted`: a lower-ranked record was included after a higher-ranked one was skipped for budget.
- `left_out_record_ids`: the `record_limit` skips that the vector or keyword search **on its own** ranked within `max_records`, in rank order. Retrieval has no relevance threshold, so in a small case every record is ranked and something is always past the window; only these records were top matches for one search and lost their place when the two rankings were fused. Budget skips never count: more records would not help when the character budget is full.
- `offer_expanded_window`: true exactly when `evidence_window` is `standard` and `left_out_record_ids` is non-empty. The UI shows "Want more evidence?" only then. There is one step: an `expanded` revision never offers again, and its `left_out_record_ids` give the "N relevant records were still left out" count.

## InvestigationBoard

- Each save writes a new snapshot: same `board_id`, `revision` incremented, `parent_revision` set to the previous snapshot. Older snapshots are never rewritten.
- Each item pins one finding: `kind` (`claim`, `edge`, `node` or `event`), the originating `source_analysis_id`, the `ref_id` of the item inside that revision, its `evidence_ids` and its `status`.
- **Saving to the board does not mean verified.** There is no verified flag. `status` (`observed`, `reported`, `inferred` or `unresolved`) is copied from the originating item and pinning never changes it (e.g. `reported` stays `reported`).
- `status` is required for `claim`, `edge` and `event` items. It is optional for `node` items, because graph nodes carry no status. If the source item has a status, the pinned item's status must match it.
- `notes[]` are investigator-authored, kept separate from LLM output, and never passed to the model as evidence. `target_item_id` attaches a note to a board item; null or absent means a board-level note.

## RevisionComparison

Produced after an exclusion. `excluded_record_ids` lists the records excluded in `compared_analysis_id` but not in `base_analysis_id`. Each item maps a base finding to its counterpart (either side may be null) and gives a `support_status`:

| `support_status` | Meaning |
|---|---|
| `still_supported` | Present in both; remaining evidence still supports it. |
| `weakened` | Present in both, with fewer or weaker supporting records. |
| `not_supported` | Present in base; no remaining permitted record supports it. |
| `newly_supported` | Only present in the compared revision. |
| `unresolved` | The remaining evidence is insufficient to decide. |

Wording guidance for `explanation` and the UI:

- An exclusion **changes what the active analysis can conclude, not what happened.** Say "no longer supported by the remaining evidence", never "did not happen" or "disproved".
- An edge (or claim or event) with two independent supporting records is **not erased** when only one is excluded. It is `still_supported` or `weakened`, not `not_supported`.
- Restoring an excluded record reuses the earlier revision's inclusion state; it does not delete the child revision.

## Server-side invariants

These schemas are minimal cross-team contracts. The server must additionally enforce:

1. Every cited ID exists in the retrieved, permitted corpus of the same `case_id`.
2. No excluded record is cited, retrieved or passed to the LLM.
3. Every graph edge references existing graph nodes.
4. Claims, edges and events have at least one evidence ID; empty support is rejected or marked unresolved.
5. Timestamps are treated according to `timestamp_precision` and `timestamp_kind`; no precise timestamp is invented.
6. Revisions and board snapshots are append-only.
7. Board items reference an existing item of the stated `kind` in their `source_analysis_id`, cite only evidence that item cites, and keep its `status`.
8. Board note `target_item_id` values reference items on the same board snapshot.
9. A comparison's `compared_analysis_id` is a child of `base_analysis_id`, both in the same case, and its `excluded_record_ids` equal the exclusions added between them. The compared side never cites an excluded record. When a side's ref ID is present, its evidence IDs exactly match that revision's item; when it is null, its evidence IDs are empty (also enforced in the schema).
10. User-authored notes remain distinct from LLM-generated conclusions.
11. Date-time fields are parsed as offset-aware instants before comparison; a board snapshot's `updated_at` is not before its `created_at`, and no item `added_at` or note `created_at` is after its `updated_at`.
12. `retrieval_coverage`, when present, agrees with its revision: skipped records exist in the case and are never retrieved, excluded or cited; `record_limit` skips rank beyond `max_records` and `budget` skips within it; no more than `max_records` records are retrieved; `budget_substituted` needs a budget skip; `left_out_record_ids` and `offer_expanded_window` follow from the skips as defined above.

`starter/validate_fixture.py` implements cheap versions of checks 1 to 4, 7 to 9, 11 and 12 against the fixtures; CI runs them on every PR.

For AI generation, prefer a separate subset of the schema containing only answer/claims/graph/events; assign analysis IDs, excluded IDs, retrieved IDs, `needs_review`, `validation_errors` and creation timestamps on the trusted server.
