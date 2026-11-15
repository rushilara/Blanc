# Blanc schemas

All schemas are JSON Schema draft 2020-12 with `additionalProperties: false`. Every top-level object that carries `schema_version` uses the const `"1.0"`.

| Schema | What it describes |
|---|---|
| `evidence_record.schema.json` | One immutable, traceable source record (`record_id`, `case_id`, file, locator, content, timestamp). Optional media fields (BLA-68): `modality` (`text` \| `image` \| `pdf`), `media_path` (`media/rec_<16 hex>.(png\|jpg\|pdf)`), `content_type` (`image/png` \| `image/jpeg` \| `application/pdf`). When `modality` is `image` or `pdf`, `media_path` and `content_type` are required; `content` stays the empty string (BLA-69 embeds bytes multimodally and indexes a lexical FTS sidecar on the chunk). |
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

### Suggested follow-ups (`suggested_followups`, BLA-54)

Optional array of 1 to 3 unique, non-blank strings of at most 300 characters each; omitted (never empty, never null) when there are none. Added in BLA-54 (additive; `schema_version` unchanged).

- **Model-generated suggestions, not findings and not evidence.** They come from the same single analysis call as the answer: short questions about what the answer left open (an unresolved question, an entity worth checking). They carry no `evidence_ids`, are never cited, never pinned to a board, never included in reports and never sent back to the model as context.
- **Never run automatically.** No server code path executes a suggestion. The UI shows them as clearly labeled AI-generated chips; clicking one only fills the follow-up input, and the investigator decides whether to send it. A sent suggestion is an ordinary follow-up question (a child revision via the follow-up endpoint).
- Stored on the revision so the history records what was offered.
- Server-trimmed, never a failure: non-strings, blank, overlong and duplicate (case-insensitive) items, and items that name an excluded record ID, are dropped, and only the first 3 are kept. Suggestions never cause the validation retry or `needs_review`.
- Rendered as plain text only (no HTML or Markdown): like evidence, they derive from untrusted content.

### Evidence window (`retrieval_coverage`, BLA-64)

Optional, server-assigned, never produced by the LLM. Absent on revisions saved before it existed: treat those as the `standard` window with no offer.

- `evidence_window`: `standard` (`RETRIEVAL_MAX_RECORDS`, default 16 records) or `expanded` (`RETRIEVAL_EXPANDED_MAX_RECORDS`, default 24). Only the record count differs; `max_excerpt_chars` is the same for both.
- `max_records`, `max_excerpt_chars`: the limits this revision ran with.
- `skipped[]`: every ranked candidate the window left out, in rank order: `record_id`, fused `rank`, `reason` (`record_limit`, `budget`, `media_limit`, `media_too_large` or `media`), `vector_rank` / `keyword_rank` when that search found the record, and `modality` (`image` or `pdf`) for media records (each omitted, never null, otherwise). The three media reasons mark an image or PDF in the window that the analysis did **not** send to the model (vision analysis, BLA-71, sends the rest inline): `media_limit` — over a media cap (`RETRIEVAL_MAX_MEDIA`, `ANALYSIS_MAX_IMAGES` / `ANALYSIS_MAX_PDFS`, or the total `ANALYSIS_MAX_MEDIA_BYTES`), so a larger cap could include it; `media_too_large` — the single file is over `ANALYSIS_MAX_MEDIA_FILE_BYTES` and cannot be sent inline at all; `media` — any other reason (the stored file is missing or fails its SHA-256 check), and on revisions saved before vision analysis (BLA-70), every retrieved image/PDF. `media_limit` and `media_too_large` were added in BLA-71 (additive; `schema_version` unchanged). A media `budget` skip is impossible, since media uses no character budget. Media sent to the model is in `retrieved_record_ids` and can be cited by `record_id` alone (there are no character offsets for a file). IDs only, never content. **Skipped records are not citations** and are never shown to the model.
- `budget_substituted`: a lower-ranked record was included after a higher-ranked one was skipped for budget.
- `left_out_record_ids`: the text `record_limit` skips (no `modality`) that the vector or keyword search **on its own** ranked within `max_records`, in rank order. Retrieval has no relevance threshold, so in a small case every record is ranked and something is always past the window; only these records were top matches for one search and lost their place when the two rankings were fused. Budget skips never count: more records would not help when the character budget is full.
- `offer_expanded_window`: true exactly when `evidence_window` is `standard` and `left_out_record_ids` is non-empty. The UI shows "Want more evidence?" only then. There is one step: an `expanded` revision never offers again, and its `left_out_record_ids` give the "N relevant records were still left out" count.

## InvestigationBoard

- Each save writes a new snapshot: same `board_id`, `revision` incremented, `parent_revision` set to the previous snapshot. Older snapshots are never rewritten.
- Each item pins one finding: `kind` (`claim`, `edge`, `node` or `event`), the originating `source_analysis_id`, the `ref_id` of the item inside that revision, its `evidence_ids` and its `status`.
- **Saving to the board does not mean verified.** There is no verified flag. `status` (`observed`, `reported`, `inferred` or `unresolved`) is copied from the originating item and pinning never changes it (e.g. `reported` stays `reported`).
- `status` is required for `claim`, `edge` and `event` items. It is optional for `node` items, because graph nodes carry no status. If the source item has a status, the pinned item's status must match it.
- `notes[]` are investigator-authored, kept separate from LLM output, and never passed to the model as evidence. `target_item_id` attaches a note to a board item; null or absent means a board-level note.
- **Investigator review (BLA-48).** An item may carry `review_status`: `unreviewed`, `reviewed` or `disputed`; absent means `unreviewed`. It records the investigator's judgement and never changes `status`, which stays the model's label. A note with `kind: "correction"` flags the item it targets as wrong and says why; a correction must have a `target_item_id` (also enforced in the schema), and `kind` absent means `"note"`. Review status and corrections, like notes, are never sent to the model. Each change is a new snapshot, so the history of review decisions is kept.

## RevisionComparison

Produced after an exclusion. `excluded_record_ids` lists the records excluded in `compared_analysis_id` but not in `base_analysis_id`. Each item maps a base finding to its counterpart (either side may be null) and gives a `support_status`:

| `support_status` | Meaning |
|---|---|
| `still_supported` | Present in both; remaining evidence still supports it. |
| `weakened` | Present in both, with fewer or weaker supporting records. |
| `not_supported` | Present in base; no remaining permitted record supports it. Never has a possible match (no base record remains). |
| `newly_supported` | Only present in the compared revision. May carry `possible_matches` pointing at base findings it possibly restates. |
| `unresolved` | The remaining evidence is insufficient to decide, or no finding in the compared revision could be identified as this one. In the second case, if it lost at least one cited source to this exclusion, it may carry `possible_matches` pointing at newly supported findings. |

"Present in both" means identified by structure, not by ID: an edge joining the same two entities with the same type, a claim with the same wording, or an event with the same wording and timestamp (case, spacing and a closing full stop ignored).

### Possibly the same finding (`possible_matches`)

When a rerun rewords a finding, strict identity shows it twice: the base finding `unresolved` and the new one `newly_supported`. To help the investigator see that the conclusion may have survived with less support, the server adds an optional, **advisory** `possible_matches` list to both items:

- Links are made **only when the base finding lost at least one cited source to this exclusion**: its cited records must include at least one of `excluded_record_ids`, so `remaining_sources` is always less than `original_sources`. A finding on records the exclusion did not touch is never linked, even if a reworded child cites a subset of them, because the exclusion cannot explain the change (it may as well be a reversal).
- A base item that has no counterpart (`compared_ref_id: null`, status `unresolved` or `not_supported`) and that lost a source, and a `newly_supported` item of the same `kind`, are linked when the new item cites at least one record and **every record it cites was cited by the base item and is not among `excluded_record_ids`**. Edges must also join the same two entities (node ID, label and type at each end, in the same direction); the edge type may differ. Events need no extra condition, since their wording differs by definition.
- The link is listed **on both items**, so the UI can badge either card without a lookup. On a base-only item each `ref_id` is a `compared_ref_id`; on a `newly_supported` item each `ref_id` is a `base_ref_id`. Which side a `ref_id` names is therefore fixed by which ref ID is null on the item carrying it.
- Each link holds `shared_evidence_ids` (the records both cite, each once), and `remaining_sources` of `original_sources`: how many of the base item's unique cited records are still permitted, out of how many it cited. Both counts describe the base item.
- Links can be one-to-many and many-to-one: one new finding can link to every vanished base finding whose remaining citations cover it. Each list is ordered by number of shared records (most first), then `ref_id`, and is omitted when empty; the list is not a ranking and has no cap, so the UI must not treat order as a confidence ranking. Items with both ref IDs never carry it (also enforced in the schema).
- It **never changes `support_status`**. It is citation-based only, so even after a lost source it cannot tell a rewording from a changed conclusion that rests on the remaining records ("Alice paid Bob" and "Alice never paid Bob"): the wording is always "Possibly the same finding: N of M cited sources remain; please confirm", never "same", "confirmed" or "weakened". The explanation gets that sentence appended.
- **Why not reuse model IDs:** IDs are regenerated on every LLM run, and a reused ID can name a different finding. Trusting it would label a changed claim `weakened`, presenting a new conclusion as the old one with less support. The citation subset test plus "please confirm" keeps the decision with the investigator.
- Server-computed, never LLM-generated. Confirming a match (badge and interaction) is the UI's job, tracked in BLA-36.

Example base item after "Owner is Sarah" loses `rec-03` of three records and the rerun rewords it as `claim-x`:

```json
{
  "kind": "claim",
  "base_ref_id": "claim-owner",
  "compared_ref_id": null,
  "support_status": "unresolved",
  "base_evidence_ids": ["rec-01", "rec-02", "rec-03"],
  "compared_evidence_ids": [],
  "explanation": "rec-01, rec-02 are still permitted, but no finding in the active analysis could be identified as this one, so it is not established by the remaining evidence. That does not show it did not happen. Possibly the same finding as claim-x in the active analysis: 2 of 3 cited sources remain; please confirm.",
  "possible_matches": [
    {"ref_id": "claim-x", "shared_evidence_ids": ["rec-01", "rec-02"], "remaining_sources": 2, "original_sources": 3}
  ]
}
```

The `newly_supported` item for `claim-x` carries the mirror link, `{"ref_id": "claim-owner", ...}` with the same shared records and counts.

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
9. A comparison's `compared_analysis_id` is a child of `base_analysis_id`, both in the same case, and its `excluded_record_ids` equal the exclusions added between them. The compared side never cites an excluded record. When a side's ref ID is present, its evidence IDs exactly match that revision's item; when it is null, its evidence IDs are empty (also enforced in the schema). Every `possible_matches` link joins an unmatched base item that lost at least one cited source to this exclusion (`remaining_sources` < `original_sources`) and a `newly_supported` item of the same kind, is listed the same way on both, shares only records both cite and none that is excluded, and has `original_sources`/`remaining_sources` equal to the base item's unique cited records and those not excluded.
10. User-authored notes remain distinct from LLM-generated conclusions.
11. Date-time fields are parsed as offset-aware instants before comparison; a board snapshot's `updated_at` is not before its `created_at`, and no item `added_at` or note `created_at` is after its `updated_at`.
12. `retrieval_coverage`, when present, agrees with its revision: skipped records exist in the case and are never retrieved, excluded or cited; `record_limit` skips rank beyond `max_records`, and `budget` skips before the first `record_limit` skip (images and PDFs never take a text slot, so media skips — `media`, `media_limit`, `media_too_large` — have no rank bound); media skips name a `modality` and `budget` skips never do; no more than `max_records` text records are retrieved (images/PDFs sent to the model are on top); `budget_substituted` needs a budget skip; `left_out_record_ids` and `offer_expanded_window` follow from the skips as defined above.
13. `suggested_followups` never names an excluded record ID; suggestions are model text, never findings, and never cite or carry evidence. Like findings, a suggestion can still paraphrase what an excluded record said — the ID guard does not stop that.

`starter/validate_fixture.py` implements cheap versions of checks 1 to 4, 7 to 9, 11 to 13 against the fixtures; CI runs them on every PR.

For AI generation, prefer a separate subset of the schema containing only answer/claims/graph/events; assign analysis IDs, excluded IDs, retrieved IDs, `needs_review`, `validation_errors` and creation timestamps on the trusted server. `suggested_followups` is part of the generated subset but is sanitized server-side before it is stored.
