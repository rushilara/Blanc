"""Server-side cross-reference validation for Blanc contracts.

Blanc's core rule: LLM output is not evidence. Every claim, graph edge, graph node and
timeline event must cite source record IDs that exist in the same case, were retrieved
for this revision and were not excluded. This module enforces the server-side invariants
in ``contracts/README.md`` that apply to a single revision, board snapshot or comparison.

Schema-level rules (types, enums, required fields, ``additionalProperties: false``,
offset-aware date-times) are enforced earlier by the models in ``app.contracts``; use
``schema_errors`` to turn untrusted JSON into a model plus readable errors.

Every ``validate_*`` function returns a list of human-readable error strings; an empty list
means valid. The messages name the offending IDs so they can be fed back to the LLM on a
retry or stored as ``validation_errors`` on a ``needs_review`` revision.

``starter/validate_fixture.py`` is the language-neutral reference these checks were ported
from; this module is authoritative at runtime.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ValidationError

from app.contracts import (
    AnalysisRevision,
    Claim,
    EvidenceRecord,
    GraphEdge,
    GraphNode,
    InvestigationBoard,
    RevisionComparison,
    TimelineEvent,
    parse_datetime,
)

__all__ = [
    "parse_datetime",
    "schema_errors",
    "format_validation_error",
    "case_record_ids",
    "permitted_evidence_ids",
    "revision_integrity_errors",
    "validate_revision",
    "validate_board",
    "validate_comparison",
]

RevisionItem = Claim | GraphNode | GraphEdge | TimelineEvent


# --- Schema-level helpers -------------------------------------------------------------


def format_validation_error(exc: ValidationError) -> list[str]:
    """Flatten a Pydantic ``ValidationError`` into ``"path: message"`` strings."""
    errors = []
    for err in exc.errors(include_url=False):
        path = ".".join(str(part) for part in err["loc"]) or "<root>"
        errors.append(f"{path}: {err['msg']}")
    return errors


def schema_errors[M: BaseModel](model: type[M], data: Any) -> tuple[M | None, list[str]]:
    """Validate untrusted data (a dict or a JSON string/bytes) against a contract model.

    Returns ``(instance, [])`` on success or ``(None, errors)`` on failure.
    """
    try:
        if isinstance(data, str | bytes | bytearray):
            return model.model_validate_json(data), []
        return model.model_validate(data), []
    except ValidationError as exc:
        return None, format_validation_error(exc)


# --- Shared helpers -------------------------------------------------------------------


def _duplicates(values: Iterable[str]) -> list[str]:
    return sorted(v for v, n in Counter(values).items() if n > 1)


def _fmt(ids: Iterable[str]) -> str:
    return ", ".join(sorted(ids))


def _revision_items(analysis: AnalysisRevision) -> dict[tuple[str, str], RevisionItem]:
    """Index a revision's citable items by ``(kind, id)``."""
    index: dict[tuple[str, str], RevisionItem] = {}
    for claim in analysis.claims:
        index[("claim", claim.id)] = claim
    for node in analysis.graph.nodes:
        index[("node", node.id)] = node
    for edge in analysis.graph.edges:
        index[("edge", edge.id)] = edge
    for event in analysis.timeline_events:
        index[("event", event.id)] = event
    return index


def _citation_errors(
    label: str,
    cited: Iterable[str],
    *,
    known: set[str],
    retrieved: set[str],
    excluded: set[str],
) -> list[str]:
    """Explain why any cited ID is not in the permitted support set (retrieved - excluded)."""
    cited = set(cited)
    errors = []
    unknown = cited - known
    if unknown:
        errors.append(f"{label} cites unknown evidence IDs not in the case corpus: {_fmt(unknown)}")
    excluded_hits = cited & excluded
    if excluded_hits:
        errors.append(f"{label} cites excluded evidence: {_fmt(excluded_hits)}")
    unretrieved = (cited & known) - retrieved - excluded
    if unretrieved:
        errors.append(f"{label} cites evidence that was not retrieved: {_fmt(unretrieved)}")
    return errors


# --- AnalysisRevision -----------------------------------------------------------------


def case_record_ids(records: Sequence[EvidenceRecord], case_id: str) -> set[str]:
    """IDs of the records in ``records`` that belong to ``case_id``."""
    return {r.record_id for r in records if r.case_id == case_id}


def permitted_evidence_ids(
    records: Sequence[EvidenceRecord], analysis: AnalysisRevision
) -> set[str]:
    """The record IDs a revision may cite: same-case, retrieved and not excluded."""
    known = case_record_ids(records, analysis.case_id)
    return (known & set(analysis.retrieved_record_ids)) - set(analysis.excluded_record_ids)


def _corpus_errors(records: Sequence[EvidenceRecord], analysis: AnalysisRevision) -> list[str]:
    """Records are unique and same-case; retrieved IDs are in the corpus and not excluded."""
    errors: list[str] = []
    dupes = _duplicates(r.record_id for r in records)
    if dupes:
        errors.append(f"duplicate source record IDs: {_fmt(dupes)}")
    case_id = analysis.case_id
    for record in records:
        if record.case_id != case_id:
            errors.append(
                f"cross-case record {record.record_id}: case {record.case_id}, expected {case_id}"
            )
    known = case_record_ids(records, case_id)
    excluded = set(analysis.excluded_record_ids)
    retrieved = set(analysis.retrieved_record_ids)
    missing = retrieved - known
    if missing:
        errors.append(f"retrieved IDs are not in the case corpus: {_fmt(missing)}")
    leaked = retrieved & excluded
    if leaked:
        errors.append(f"excluded source records appear in the retrieval set: {_fmt(leaked)}")
    return errors


def _duplicate_item_errors(analysis: AnalysisRevision) -> list[str]:
    errors = []
    for kind, ids in (
        ("claim", [c.id for c in analysis.claims]),
        ("node", [n.id for n in analysis.graph.nodes]),
        ("edge", [e.id for e in analysis.graph.edges]),
        ("event", [e.id for e in analysis.timeline_events]),
    ):
        dupes = _duplicates(ids)
        if dupes:
            errors.append(f"duplicate {kind} IDs: {_fmt(dupes)}")
    return errors


def _timestamp_errors(analysis: AnalysisRevision) -> list[str]:
    return [
        f"event {event.id} has exact timestamp_precision but no timestamp"
        for event in analysis.timeline_events
        if event.timestamp_precision == "exact" and event.timestamp is None
    ]


def revision_integrity_errors(
    records: Sequence[EvidenceRecord], analysis: AnalysisRevision
) -> list[str]:
    """The ``validate_revision`` checks that do not depend on what items cite.

    Covers the corpus and retrieval set, duplicate item IDs and invented exact timestamps.
    Stripping citations cannot fix these, so ``app.citations.sanitize_citations`` reports
    them as errors instead of repairing them.
    """
    return [
        *_corpus_errors(records, analysis),
        *_duplicate_item_errors(analysis),
        *_timestamp_errors(analysis),
    ]


def validate_revision(records: Sequence[EvidenceRecord], analysis: AnalysisRevision) -> list[str]:
    """Cross-reference checks for one revision against its case corpus.

    ``records`` is the case corpus the revision was built from. Enforces README invariants
    1 to 5: records are unique and same-case, retrieved IDs are in the corpus and disjoint
    from excluded IDs, every claim/edge/event cites at least one permitted record (and node
    evidence, when present, is permitted too), edges reference existing nodes, and no event
    claims an exact timestamp without one.
    """
    errors = _corpus_errors(records, analysis)
    errors.extend(_duplicate_item_errors(analysis))

    known = case_record_ids(records, analysis.case_id)
    excluded = set(analysis.excluded_record_ids)
    retrieved = set(analysis.retrieved_record_ids)
    cite = {"known": known, "retrieved": retrieved, "excluded": excluded}
    supported: list[tuple[str, Claim | GraphEdge | TimelineEvent]] = [
        *(("claim", c) for c in analysis.claims),
        *(("edge", e) for e in analysis.graph.edges),
        *(("event", e) for e in analysis.timeline_events),
    ]
    for kind, item in supported:
        label = f"{kind} {item.id}"
        if not item.evidence_ids:
            errors.append(f"{label} has no evidence IDs")
        errors.extend(_citation_errors(label, item.evidence_ids, **cite))
    for node in analysis.graph.nodes:
        errors.extend(_citation_errors(f"node {node.id}", node.evidence_ids or [], **cite))

    node_ids = {n.id for n in analysis.graph.nodes}
    for edge in analysis.graph.edges:
        for end in ("source", "target"):
            ref = getattr(edge, end)
            if ref not in node_ids:
                errors.append(f"edge {edge.id} {end} references unknown graph node {ref}")

    errors.extend(_timestamp_errors(analysis))
    return errors


# --- InvestigationBoard ---------------------------------------------------------------


def validate_board(
    records: Sequence[EvidenceRecord],
    revisions: Sequence[AnalysisRevision],
    board: InvestigationBoard,
) -> list[str]:
    """Cross-reference checks for one board snapshot (README invariants 6 to 8 and 11).

    ``revisions`` are the known revisions; only those in the board's case are considered.
    """
    errors: list[str] = []
    case_id = board.case_id
    case_records = {r.record_id for r in records if r.case_id == case_id}
    by_id: Mapping[str, AnalysisRevision] = {
        a.analysis_id: a for a in revisions if a.case_id == case_id
    }

    if board.parent_revision is not None and board.parent_revision >= board.revision:
        errors.append("parent_revision must be lower than revision (snapshots are append-only)")
    if board.updated_at < board.created_at:
        errors.append("updated_at is before created_at")
    for item in board.items:
        if item.added_at > board.updated_at:
            errors.append(f"board item {item.item_id} added_at is after the snapshot's updated_at")
    for note in board.notes:
        if note.created_at > board.updated_at:
            errors.append(f"note {note.note_id} created_at is after the snapshot's updated_at")
    active = board.active_analysis_id
    if active is not None and active not in by_id:
        errors.append(f"active_analysis_id {active} is not a revision of case {case_id}")

    item_ids = [i.item_id for i in board.items]
    dupes = _duplicates(item_ids)
    if dupes:
        errors.append(f"duplicate board item IDs: {_fmt(dupes)}")
    seen_refs: set[tuple[str, str, str]] = set()
    for item in board.items:
        label = f"board item {item.item_id}"
        key = (item.source_analysis_id, item.kind, item.ref_id)
        if key in seen_refs:
            errors.append(f"{label} pins the same finding twice")
        seen_refs.add(key)
        cited = set(item.evidence_ids)
        foreign = cited - case_records
        if foreign:
            errors.append(f"{label} cites evidence not in case {case_id}: {_fmt(foreign)}")
        source = by_id.get(item.source_analysis_id)
        if source is None:
            errors.append(
                f"{label} source_analysis_id {item.source_analysis_id} "
                f"is not a revision of case {case_id}"
            )
            continue
        excluded_hits = cited & set(source.excluded_record_ids)
        if excluded_hits:
            errors.append(
                f"{label} cites evidence excluded in its source revision: {_fmt(excluded_hits)}"
            )
        ref = _revision_items(source).get((item.kind, item.ref_id))
        if ref is None:
            errors.append(
                f"{label} ref_id {item.ref_id} is not a {item.kind} in {source.analysis_id}"
            )
            continue
        extra = cited - set(ref.evidence_ids or [])
        if extra:
            errors.append(
                f"{label} cites evidence its source {item.kind} does not cite: {_fmt(extra)}"
            )
        source_status = getattr(ref, "status", None)
        if source_status is not None and item.status != source_status:
            errors.append(
                f"{label} status {item.status} differs from its source {item.kind} status "
                f"{source_status} (pinning must not change status)"
            )

    dupes = _duplicates(n.note_id for n in board.notes)
    if dupes:
        errors.append(f"duplicate board note IDs: {_fmt(dupes)}")
    known_items = set(item_ids)
    for note in board.notes:
        target = note.target_item_id
        if target is not None and target not in known_items:
            errors.append(f"note {note.note_id} targets unknown board item {target}")
    return errors


# --- RevisionComparison ---------------------------------------------------------------


def validate_comparison(
    revisions: Sequence[AnalysisRevision], comparison: RevisionComparison
) -> list[str]:
    """Cross-reference checks for a comparison between a parent and child revision
    (README invariant 9)."""
    errors: list[str] = []
    by_id = {a.analysis_id: a for a in revisions}
    base = by_id.get(comparison.base_analysis_id)
    compared = by_id.get(comparison.compared_analysis_id)
    if base is None or compared is None:
        return ["base_analysis_id and compared_analysis_id must both be known revisions"]
    case_id = comparison.case_id
    if base.case_id != case_id or compared.case_id != case_id:
        errors.append("comparison and both revisions must share one case_id")
    if compared.parent_analysis_id != base.analysis_id:
        errors.append("compared revision must be a child of the base revision")
    delta = set(compared.excluded_record_ids) - set(base.excluded_record_ids)
    if set(comparison.excluded_record_ids) != delta:
        errors.append(
            "excluded_record_ids must equal the exclusions added between base and compared "
            f"revisions: expected [{_fmt(delta)}], got [{_fmt(comparison.excluded_record_ids)}]"
        )

    base_items = _revision_items(base)
    compared_items = _revision_items(compared)
    compared_excluded = set(compared.excluded_record_ids)
    for n, item in enumerate(comparison.items):
        label = f"comparison item {n}"
        b, c, status = item.base_ref_id, item.compared_ref_id, item.support_status
        if b is None and c is None:
            errors.append(f"{label} needs base_ref_id or compared_ref_id")
        if b is not None:
            ref = base_items.get((item.kind, b))
            if ref is None:
                errors.append(f"{label} base_ref_id {b} is not a {item.kind} in the base revision")
            elif set(item.base_evidence_ids) != set(ref.evidence_ids or []):
                errors.append(f"{label} base_evidence_ids do not match the base revision")
        if c is not None:
            ref = compared_items.get((item.kind, c))
            if ref is None:
                errors.append(
                    f"{label} compared_ref_id {c} is not a {item.kind} in the compared revision"
                )
            elif set(item.compared_evidence_ids) != set(ref.evidence_ids or []):
                errors.append(f"{label} compared_evidence_ids do not match the compared revision")
        if b is None and item.base_evidence_ids:
            errors.append(f"{label} has base_evidence_ids but no base_ref_id")
        if c is None and item.compared_evidence_ids:
            errors.append(f"{label} has compared_evidence_ids but no compared_ref_id")
        excluded_hits = set(item.compared_evidence_ids) & compared_excluded
        if excluded_hits:
            errors.append(f"{label} compared side cites excluded records: {_fmt(excluded_hits)}")
        if status in ("still_supported", "weakened") and (b is None or c is None):
            errors.append(f"{label} {status} needs both base_ref_id and compared_ref_id")
        if status == "newly_supported" and (b is not None or c is None):
            errors.append(f"{label} newly_supported needs only compared_ref_id")
        if status == "not_supported" and (b is None or item.compared_evidence_ids):
            errors.append(f"{label} not_supported needs base_ref_id and no compared evidence")
    errors += _possible_match_errors(comparison, base, compared)
    return errors


_LINKABLE_BASE_STATUSES = ("unresolved", "not_supported")


def _wording(text: str) -> str:
    """Same normalisation as ``compare_revisions``: case, spacing and a closing full stop."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split()).rstrip(" .")


def _edge_ends(revision: AnalysisRevision, edge_id: str) -> tuple[Any, ...] | None:
    """Directed endpoints matching ``compare_revisions._edge_endpoints``: each end is the
    node ID plus its normalised label and type. None if the edge or an endpoint is missing.
    """
    edge = next((e for e in revision.graph.edges if e.id == edge_id), None)
    if edge is None:
        return None
    entities = {node.id: (_wording(node.label), node.type) for node in revision.graph.nodes}
    source, target = entities.get(edge.source), entities.get(edge.target)
    if source is None or target is None:
        return None
    return (edge.source, source, edge.target, target)


def _possible_match_errors(
    comparison: RevisionComparison,
    base: AnalysisRevision,
    compared: AnalysisRevision,
) -> list[str]:
    """``possible_matches`` (BLA-63) links an unmatched base item that lost at least one cited
    source to this exclusion and a newly supported item of the same kind, listed on both,
    with consistent shared records and counts (remaining < original).

    The new item's unique cited records must be a subset of the base item's citations that
    are still permitted, and ``shared_evidence_ids`` must equal that cited set. Edge links
    must join the same endpoints in the same direction, comparing each end's node ID plus
    normalised label and type (same as ``compare_revisions._edge_endpoints``).
    """
    errors: list[str] = []
    excluded = set(comparison.excluded_record_ids)
    vanished = {
        (i.kind, i.base_ref_id): i
        for i in comparison.items
        if i.compared_ref_id is None and i.support_status in _LINKABLE_BASE_STATUSES
    }
    new = {
        (i.kind, i.compared_ref_id): i
        for i in comparison.items
        if i.base_ref_id is None and i.support_status == "newly_supported"
    }
    for n, item in enumerate(comparison.items):
        if item.possible_matches is None:
            continue
        label = f"comparison item {n}"
        on_base = item.compared_ref_id is None
        own = (item.kind, item.base_ref_id if on_base else item.compared_ref_id)
        if own not in (vanished if on_base else new):
            errors.append(
                f"{label} possible_matches is only for an unmatched unresolved or "
                "not_supported base item, or a newly_supported item"
            )
            continue
        others = new if on_base else vanished
        dupes = _duplicates(m.ref_id for m in item.possible_matches)
        if dupes:
            errors.append(f"{label} possible_matches repeats {_fmt(dupes)}")
        for match in item.possible_matches:
            other = others.get((item.kind, match.ref_id))
            side = "a newly_supported" if on_base else "an unmatched base"
            if other is None:
                errors.append(f"{label} possible match {match.ref_id} is not {side} {item.kind}")
                continue
            base_item, new_item = (item, other) if on_base else (other, item)
            shared = set(match.shared_evidence_ids)
            child_cited = set(new_item.compared_evidence_ids)
            remaining = set(base_item.base_evidence_ids) - excluded
            if not child_cited or not child_cited <= remaining:
                errors.append(
                    f"{label} possible match {match.ref_id} cites records that are not among "
                    "the base item's still-permitted citations"
                )
            if shared != child_cited:
                errors.append(
                    f"{label} possible match {match.ref_id} shared_evidence_ids must equal "
                    "the new item's unique cited records"
                )
            if shared & excluded:
                errors.append(f"{label} possible match {match.ref_id} shares excluded records")
            if len(remaining) == len(base_item.base_evidence_ids):
                errors.append(
                    f"{label} possible match {match.ref_id} links a base item that lost no "
                    "cited source to this exclusion"
                )
            if match.remaining_sources >= match.original_sources:
                errors.append(
                    f"{label} possible match {match.ref_id} must have fewer remaining than "
                    "original sources"
                )
            if match.original_sources != len(base_item.base_evidence_ids) or (
                match.remaining_sources != len(remaining)
            ):
                errors.append(
                    f"{label} possible match {match.ref_id} source counts do not match the "
                    f"base item: expected {len(remaining)} of {len(base_item.base_evidence_ids)}"
                )
            if item.kind == "edge":
                base_ends = _edge_ends(base, base_item.base_ref_id or "")
                new_ends = _edge_ends(compared, new_item.compared_ref_id or "")
                if base_ends is None or new_ends is None or base_ends != new_ends:
                    errors.append(
                        f"{label} possible match {match.ref_id} edge endpoints must match "
                        "in the same direction"
                    )
            mirrored = [m for m in other.possible_matches or [] if m.ref_id == own[1]]
            if [m.model_copy(update={"ref_id": match.ref_id}) for m in mirrored] != [match]:
                errors.append(
                    f"{label} possible match {match.ref_id} is not listed the same way on "
                    "the other item"
                )
    return errors
