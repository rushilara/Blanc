"""Structured case reports (BLA-49): assemble a board or one analysis revision for export.

A report is built from stored data only: saved revisions, board snapshots and the case's
evidence records. Nothing here calls a model, and the same stored data (and the same
``generated_at``) always gives the same report.

The report is its own documented API shape (``report_version``), not a shared contract.
``app.report_markdown.render_markdown`` renders it as Markdown; the JSON export is
``CaseReport.model_dump(mode="json")``.

Faithfulness rules the report keeps:

- **AI output is labelled as AI output.** The answer, claims, entities, relationships and
  timeline events come from saved revisions and are presented as AI-generated findings
  that need human review. Statuses are copied as saved; nothing is called verified.
- **Every finding cites record IDs**, and every cited record appears once in ``sources``
  with its file, locator and timestamp. Permitted sources also carry an exact slice of
  content (``content[char_start:char_end]``). The excerpt is omitted only when the record
  is excluded in the report's scope revision (the board's active revision, or the single
  exported revision). A board with no active revision has no current evidence state, so
  the excerpt is omitted when *any* reported revision excludes the record. ``excluded_in``
  still lists every reported revision that excludes it.
- **Excluded evidence is listed apart**, by record ID and locator, with no content, and
  is never counted as support. A record excluded in a non-active board source but
  permitted in the active revision keeps its excerpt; a stale pin must not hide restored
  evidence.
- **Investigator notes are kept apart** in ``investigator_notes``, attributed to their
  author, and only linked to findings by ID (``note_ids``); their text is never mixed
  into AI-generated text.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, PrivateAttr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analyses import get_revision, revision_lineage
from app.boards import get_board
from app.contracts import AnalysisRevision, EvidenceRecord, GraphNode, InvestigationBoard
from app.db import utcnow
from app.evidence.importer import SUPPORTED_SUFFIXES
from app.models import Case, EvidenceRecordRow
from app.timeline import normalize_timestamp

REPORT_VERSION = "1.0"
# Longest excerpt quoted per source. Longer records are cut to an exact prefix slice and
# flagged ``truncated``; the full record stays in the local case database.
MAX_EXCERPT_CHARS = 2000

FindingKind = Literal["claim", "node", "edge", "event"]
_KIND_ORDER: dict[str, int] = {"claim": 0, "node": 1, "edge": 2, "event": 3}
_PRECISION_RANK = {"exact": 0, "day": 1, "month": 2, "approximate": 3, "unknown": 4}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FindingRef(_Model):
    """Points at one finding of one saved revision."""

    kind: FindingKind
    analysis_id: str
    ref_id: str


class ReportCase(_Model):
    case_id: str
    title: str


class ReportBoard(_Model):
    board_id: str
    title: str
    revision: int
    parent_revision: int | None
    active_analysis_id: str | None
    updated_at: datetime


class ReportAnalysis(_Model):
    """One saved revision the report draws on. ``answer`` and ``unresolved_questions``
    are AI-generated text."""

    analysis_id: str
    # "exported": the revision being exported; "board_active": the board's active
    # revision; "board_source": a revision that pinned board items come from.
    role: Literal["exported", "board_active", "board_source"]
    parent_analysis_id: str | None
    lineage: list[str]  # root ... this revision
    question: str
    answer: str
    unresolved_questions: list[str]
    needs_review: bool
    validation_errors: list[str]
    retrieved_record_ids: list[str]
    excluded_record_ids: list[str]
    created_at: datetime


class ReportClaim(_Model):
    analysis_id: str
    claim_id: str
    text: str
    status: str
    evidence_ids: list[str]
    board_item_id: str | None = None
    note_ids: list[str] = []


class ReportEntity(_Model):
    analysis_id: str
    node_id: str
    label: str
    type: str
    evidence_ids: list[str]
    board_item_id: str | None = None
    note_ids: list[str] = []


class EntityRef(_Model):
    node_id: str
    label: str | None  # None if the revision has no node with that ID
    type: str | None


class ReportRelationship(_Model):
    analysis_id: str
    edge_id: str
    source: EntityRef
    target: EntityRef
    type: str
    status: str
    description: str | None
    evidence_ids: list[str]
    board_item_id: str | None = None
    note_ids: list[str] = []


class ReportEvent(_Model):
    analysis_id: str
    event_id: str
    description: str
    timestamp: str | None  # as written in the revision
    timestamp_kind: str | None
    display: str | None  # normalized, at the precision actually known (app.timeline)
    precision: str
    timezone_known: bool
    status: str
    evidence_ids: list[str]
    board_item_id: str | None = None
    note_ids: list[str] = []
    # Ordering key only, never exported: with no known zone it orders the time as written.
    _instant: datetime | None = PrivateAttr(default=None)


class ReportTimeline(_Model):
    """``dated``: normalized order. ``uncertain``: approximate times, or exact times with
    no known time zone. ``undated``: no usable timestamp. Nothing is guessed."""

    dated: list[ReportEvent]
    uncertain: list[ReportEvent]
    undated: list[ReportEvent]


class Excerpt(_Model):
    text: str  # == record content[char_start:char_end]
    char_start: int
    char_end: int
    truncated: bool
    content_chars: int  # length of the whole record content


class ReportSource(_Model):
    record_id: str
    source_file: str
    source_locator: str
    timestamp: str | None
    timestamp_kind: str | None
    # Omitted when the record is excluded in any reported revision: exports must not
    # carry excluded evidence content even if an older pin still cites the ID.
    excerpt: Excerpt | None = None
    cited_by: list[FindingRef]
    # Revisions in this report that exclude this record (possible on a board that pins
    # items from several revisions). Empty for a single-revision export.
    excluded_in: list[str]


class ReportExclusion(_Model):
    """A record excluded from a revision. Not support for anything in the report."""

    analysis_id: str
    record_id: str
    # True when the exclusion was carried over from an earlier revision in the lineage.
    inherited: bool
    first_excluded_in: str
    source_file: str | None  # None if the record is not in the case's database
    source_locator: str | None
    timestamp: str | None


class ReportNote(_Model):
    """Investigator-authored text. Never AI output, never evidence."""

    note_id: str
    author: str
    created_at: datetime
    text: str
    target_item_id: str | None
    target: FindingRef | None  # the pinned finding the note is attached to, if any


class CaseReport(_Model):
    report_version: Literal["1.0"]
    report_kind: Literal["board", "analysis"]
    generated_at: datetime
    case: ReportCase
    board: ReportBoard | None
    analyses: list[ReportAnalysis]
    claims: list[ReportClaim]
    entities: list[ReportEntity]
    relationships: list[ReportRelationship]
    timeline: ReportTimeline
    sources: list[ReportSource]
    excluded_evidence: list[ReportExclusion]
    investigator_notes: list[ReportNote]
    limitations: list[str]


# --- Assembly ---------------------------------------------------------------------------


def analysis_report(
    session: Session, case: Case, analysis_id: str, *, now: datetime | None = None
) -> CaseReport:
    """Report for one saved revision: every finding it holds. Raises
    ``RevisionNotFoundError`` if it is not a revision of this case."""
    revision = get_revision(session, case.case_id, analysis_id)
    builder = _Builder(session, case.case_id)
    builder.add_analysis(revision, "exported")
    for claim in revision.claims:
        builder.claims.append(
            ReportClaim(
                analysis_id=revision.analysis_id,
                claim_id=claim.id,
                text=claim.text,
                status=claim.status,
                evidence_ids=list(claim.evidence_ids),
            )
        )
    for node in revision.graph.nodes:
        builder.entities.append(_entity(revision, node))
    for edge in revision.graph.edges:
        builder.relationships.append(_relationship(revision, edge.id))
    for event in revision.timeline_events:
        builder.events.append(_event(revision, event.id))
    return builder.build("analysis", case, None, [], now)


def board_report(
    session: Session,
    case: Case,
    board_id: str,
    *,
    revision: int | None = None,
    now: datetime | None = None,
) -> CaseReport:
    """Report for a board snapshot (latest unless ``revision``): its pinned findings, the
    revisions they come from, and its investigator notes. Raises ``BoardNotFoundError``."""
    board = get_board(session, case.case_id, board_id, revision)
    builder = _Builder(session, case.case_id)
    ordered = list(
        dict.fromkeys(
            ([board.active_analysis_id] if board.active_analysis_id else [])
            + [item.source_analysis_id for item in board.items]
        )
    )
    revisions = {aid: get_revision(session, case.case_id, aid) for aid in ordered}
    for aid in ordered:
        role = "board_active" if aid == board.active_analysis_id else "board_source"
        builder.add_analysis(revisions[aid], role)

    notes_by_item: dict[str, list[str]] = {}
    for note in board.notes:
        if note.target_item_id is not None:
            notes_by_item.setdefault(note.target_item_id, []).append(note.note_id)

    targets: dict[str, FindingRef] = {}
    for item in board.items:
        source = revisions[item.source_analysis_id]
        targets[item.item_id] = FindingRef(
            kind=item.kind, analysis_id=item.source_analysis_id, ref_id=item.ref_id
        )
        pinned = {"board_item_id": item.item_id, "note_ids": notes_by_item.get(item.item_id, [])}
        if item.kind == "claim":
            claim = next(c for c in source.claims if c.id == item.ref_id)
            builder.claims.append(
                ReportClaim(
                    analysis_id=source.analysis_id,
                    claim_id=claim.id,
                    text=claim.text,
                    status=item.status or claim.status,
                    evidence_ids=list(item.evidence_ids),
                    **pinned,
                )
            )
        elif item.kind == "node":
            node = next(n for n in source.graph.nodes if n.id == item.ref_id)
            builder.entities.append(
                _entity(source, node).model_copy(
                    update={"evidence_ids": list(item.evidence_ids), **pinned}
                )
            )
        elif item.kind == "edge":
            builder.relationships.append(
                _relationship(source, item.ref_id).model_copy(
                    update={"evidence_ids": list(item.evidence_ids), **pinned}
                )
            )
        else:
            builder.events.append(
                _event(source, item.ref_id).model_copy(
                    update={"evidence_ids": list(item.evidence_ids), **pinned}
                )
            )

    notes = [
        ReportNote(
            note_id=note.note_id,
            author=note.author,
            created_at=note.created_at,
            text=note.text,
            target_item_id=note.target_item_id,
            target=targets.get(note.target_item_id) if note.target_item_id else None,
        )
        for note in sorted(board.notes, key=lambda n: (n.created_at, n.note_id))
    ]
    return builder.build("board", case, board, notes, now)


def _entity(revision: AnalysisRevision, node: GraphNode) -> ReportEntity:
    return ReportEntity(
        analysis_id=revision.analysis_id,
        node_id=node.id,
        label=node.label,
        type=node.type,
        evidence_ids=list(node.evidence_ids or []),
    )


def _entity_ref(revision: AnalysisRevision, node_id: str) -> EntityRef:
    node = next((n for n in revision.graph.nodes if n.id == node_id), None)
    return EntityRef(
        node_id=node_id,
        label=node.label if node else None,
        type=node.type if node else None,
    )


def _relationship(revision: AnalysisRevision, edge_id: str) -> ReportRelationship:
    edge = next(e for e in revision.graph.edges if e.id == edge_id)
    return ReportRelationship(
        analysis_id=revision.analysis_id,
        edge_id=edge.id,
        source=_entity_ref(revision, edge.source),
        target=_entity_ref(revision, edge.target),
        type=edge.type,
        status=edge.status,
        description=edge.description,
        evidence_ids=list(edge.evidence_ids),
    )


def _event(revision: AnalysisRevision, event_id: str) -> ReportEvent:
    event = next(e for e in revision.timeline_events if e.id == event_id)
    time = normalize_timestamp(event.timestamp, event.timestamp_precision)
    report_event = ReportEvent(
        analysis_id=revision.analysis_id,
        event_id=event.id,
        description=event.description,
        timestamp=event.timestamp,
        timestamp_kind=event.timestamp_kind,
        display=time.display,
        precision=time.precision,
        timezone_known=time.timezone_known,
        status=event.status,
        evidence_ids=list(event.evidence_ids),
    )
    report_event._instant = time.instant
    return report_event


class _Builder:
    def __init__(self, session: Session, case_id: str) -> None:
        self.session = session
        self.case_id = case_id
        self.analyses: list[ReportAnalysis] = []
        self.revisions: list[AnalysisRevision] = []
        self.lineages: dict[str, list[str]] = {}
        self.claims: list[ReportClaim] = []
        self.entities: list[ReportEntity] = []
        self.relationships: list[ReportRelationship] = []
        self.events: list[ReportEvent] = []

    def add_analysis(self, revision: AnalysisRevision, role: str) -> None:
        chain = revision_lineage(self.session, self.case_id, revision.analysis_id)
        lineage = [summary.analysis_id for summary in chain]
        self.lineages[revision.analysis_id] = lineage
        self.revisions.append(revision)
        self.analyses.append(
            ReportAnalysis(
                analysis_id=revision.analysis_id,
                role=role,  # type: ignore[arg-type]
                parent_analysis_id=revision.parent_analysis_id,
                lineage=lineage,
                question=revision.question,
                answer=revision.answer,
                unresolved_questions=list(revision.unresolved_questions),
                needs_review=bool(revision.needs_review),
                validation_errors=list(revision.validation_errors or []),
                retrieved_record_ids=list(revision.retrieved_record_ids),
                excluded_record_ids=list(revision.excluded_record_ids),
                created_at=revision.created_at,
            )
        )

    def build(
        self,
        kind: Literal["board", "analysis"],
        case: Case,
        board: InvestigationBoard | None,
        notes: list[ReportNote],
        now: datetime | None,
    ) -> CaseReport:
        cited = self._citations()
        exclusions = self._exclusions()
        records = self._records({*cited, *(e.record_id for e in exclusions)})
        excluded_in: dict[str, list[str]] = {}
        for exclusion in exclusions:
            excluded_in.setdefault(exclusion.record_id, []).append(exclusion.analysis_id)
        # Withhold excerpts from the scope revision when there is one: the board's active
        # revision, or the single exported analysis. Stale pins from older exclusions must
        # not hide evidence the investigator has restored. A board with no active revision
        # has no current evidence state, so fall back to withholding whenever any reported
        # revision excludes the record (pin order must not decide whether text is shown).
        if board is not None and not board.active_analysis_id:
            scope_excluded = set(excluded_in)
        elif board is not None and board.active_analysis_id:
            scope = next(
                (r for r in self.revisions if r.analysis_id == board.active_analysis_id),
                None,
            )
            scope_excluded = set(scope.excluded_record_ids) if scope else set()
        else:
            scope = self.revisions[0] if self.revisions else None
            scope_excluded = set(scope.excluded_record_ids) if scope else set()
        sources = [
            _source(
                records[rid],
                refs,
                excluded_in.get(rid, []),
                withhold_excerpt=rid in scope_excluded,
            )
            for rid, refs in sorted(cited.items())
            if rid in records
        ]
        exclusions = [
            exclusion.model_copy(
                update={
                    "source_file": records[exclusion.record_id].source_file,
                    "source_locator": records[exclusion.record_id].source_locator,
                    "timestamp": records[exclusion.record_id].timestamp,
                }
            )
            if exclusion.record_id in records
            else exclusion
            for exclusion in exclusions
        ]
        report = CaseReport(
            report_version=REPORT_VERSION,
            report_kind=kind,
            generated_at=(now or utcnow()).astimezone(UTC),
            case=ReportCase(case_id=case.case_id, title=case.title),
            board=(
                ReportBoard(
                    board_id=board.board_id,
                    title=board.title,
                    revision=board.revision,
                    parent_revision=board.parent_revision,
                    active_analysis_id=board.active_analysis_id,
                    updated_at=board.updated_at,
                )
                if board
                else None
            ),
            analyses=self.analyses,
            claims=self.claims,
            entities=self.entities,
            relationships=self.relationships,
            timeline=_timeline(self.events),
            sources=sources,
            excluded_evidence=exclusions,
            investigator_notes=notes,
            limitations=[],
        )
        report.limitations = limitations(report, records.values())
        return report

    def _citations(self) -> dict[str, list[FindingRef]]:
        cited: dict[str, list[FindingRef]] = {}
        findings: list[tuple[FindingKind, str, str, list[str]]] = [
            *(("claim", c.analysis_id, c.claim_id, c.evidence_ids) for c in self.claims),
            *(("node", n.analysis_id, n.node_id, n.evidence_ids) for n in self.entities),
            *(("edge", e.analysis_id, e.edge_id, e.evidence_ids) for e in self.relationships),
            *(("event", e.analysis_id, e.event_id, e.evidence_ids) for e in self.events),
        ]
        for kind, analysis_id, ref_id, evidence_ids in findings:
            ref = FindingRef(kind=kind, analysis_id=analysis_id, ref_id=ref_id)
            for rid in evidence_ids:
                refs = cited.setdefault(rid, [])
                if ref not in refs:
                    refs.append(ref)
        for refs in cited.values():
            refs.sort(key=lambda r: (_KIND_ORDER[r.kind], r.analysis_id, r.ref_id))
        return cited

    def _exclusions(self) -> list[ReportExclusion]:
        """Every record excluded in a reported revision, marking which were inherited.

        ``first_excluded_in`` is the oldest ancestor in the *current continuous* exclusion
        streak ending at this revision (walk back from the parent while the record stays
        excluded). After a restore, a later re-exclusion starts a new streak, so
        ``first_excluded_in == analysis_id`` whenever ``inherited`` is false.
        """
        out: list[ReportExclusion] = []
        cached: dict[str, AnalysisRevision] = {r.analysis_id: r for r in self.revisions}

        def load(aid: str) -> AnalysisRevision:
            if aid not in cached:
                cached[aid] = get_revision(self.session, self.case_id, aid)
            return cached[aid]

        for revision in self.revisions:
            lineage = self.lineages[revision.analysis_id]
            ancestors = {aid: load(aid) for aid in lineage}
            parent = ancestors.get(revision.parent_analysis_id or "")
            inherited = set(parent.excluded_record_ids) if parent else set()
            for rid in sorted(revision.excluded_record_ids):
                first = revision.analysis_id
                for aid in reversed(lineage[:-1]):
                    if rid in ancestors[aid].excluded_record_ids:
                        first = aid
                    else:
                        break
                out.append(
                    ReportExclusion(
                        analysis_id=revision.analysis_id,
                        record_id=rid,
                        inherited=rid in inherited,
                        first_excluded_in=first,
                        source_file=None,
                        source_locator=None,
                        timestamp=None,
                    )
                )
        return out

    def _records(self, ids: set[str]) -> dict[str, EvidenceRecord]:
        if not ids:
            return {}
        rows = self.session.scalars(
            select(EvidenceRecordRow).where(
                EvidenceRecordRow.case_id == self.case_id,
                EvidenceRecordRow.record_id.in_(sorted(ids)),
            )
        )
        return {row.record_id: row.to_contract() for row in rows}


def _source(
    record: EvidenceRecord,
    refs: list[FindingRef],
    excluded_in: list[str],
    *,
    withhold_excerpt: bool,
) -> ReportSource:
    # Never put currently-excluded evidence text in an export that may leave the machine.
    # ``excluded_in`` still flags historical exclusions on the board; withholding follows
    # the scope revision only (active / exported).
    excerpt: Excerpt | None = None
    if not withhold_excerpt:
        end = min(len(record.content), MAX_EXCERPT_CHARS)
        excerpt = Excerpt(
            text=record.content[:end],
            char_start=0,
            char_end=end,
            truncated=end < len(record.content),
            content_chars=len(record.content),
        )
    return ReportSource(
        record_id=record.record_id,
        source_file=record.source_file,
        source_locator=record.source_locator,
        timestamp=record.timestamp,
        timestamp_kind=record.timestamp_kind,
        excerpt=excerpt,
        cited_by=refs,
        excluded_in=excluded_in,
    )


def _timeline(events: list[ReportEvent]) -> ReportTimeline:
    """Same order as ``app.timeline.normalize_timeline``, with the revision ID as a final
    tie-breaker (a board can hold events with the same ID from different revisions)."""

    def key(event: ReportEvent) -> tuple:
        return (
            event._instant,
            _PRECISION_RANK[event.precision],
            event.event_id,
            event.description,
            event.analysis_id,
        )

    dated: list[ReportEvent] = []
    uncertain: list[ReportEvent] = []
    undated: list[ReportEvent] = []
    for event in events:
        if event._instant is None:
            undated.append(event)
        elif event.precision == "approximate" or (
            event.precision == "exact" and not event.timezone_known
        ):
            uncertain.append(event)
        else:
            dated.append(event)
    return ReportTimeline(
        dated=sorted(dated, key=key),
        uncertain=sorted(uncertain, key=key),
        undated=sorted(undated, key=lambda e: (e.event_id, e.description, e.analysis_id)),
    )


# --- Limitations ------------------------------------------------------------------------


def limitations(report: CaseReport, records: Iterable[EvidenceRecord]) -> list[str]:
    """Deterministic caveats for the report, most general first."""
    out = [
        "The answer, claims, entities, relationships and timeline events in this report "
        "are AI-generated (Gemini) from the cited source records. They are not evidence "
        "and require human review against the sources before they are relied on.",
        "Status labels (observed, reported, inferred, unresolved) describe how the cited "
        "records bear on an item, as the analysis saved it. They are not a verification.",
        "Saving a revision or pinning an item to a board does not verify it. Nothing in "
        "this report has been marked as verified.",
        "Investigator notes are the investigator's own text, kept separate from AI output. "
        "They are not evidence and were not given to the model as evidence.",
        "Excluded evidence changes what the analysis can conclude, not what happened. "
        "Excluded records are listed for transparency and support nothing in this report.",
    ]
    for analysis in report.analyses:
        out.append(
            f"Retrieval may have missed relevant records. Analysis {analysis.analysis_id} "
            f"considered only the {len(analysis.retrieved_record_ids)} record(s) it retrieved; "
            "records it did not retrieve were not read by the model."
        )
    out.append(
        "Evidence and the case database are stored locally on the investigator's machine, "
        "but the evidence text was sent to Google's Gemini API for embedding and analysis."
    )
    out.append(
        "Blanc imports only " + ", ".join(SUPPORTED_SUFFIXES) + " files. Other files in the "
        "source export (for example images, audio, video, PDFs or databases) were not "
        "ingested, and non-text email attachments were listed but not read."
    )
    cited = {source.record_id for source in report.sources}
    for record in sorted(records, key=lambda r: r.record_id):
        attachments = (record.metadata or {}).get("attachments")
        if record.record_id in cited and isinstance(attachments, list) and attachments:
            names = ", ".join(
                str(a.get("filename") or a.get("locator") or "unnamed")
                for a in attachments
                if isinstance(a, dict)
            )
            out.append(
                f"Record {record.record_id} lists attachments that were not imported as "
                f"text: {names}."
            )
    for analysis in report.analyses:
        if analysis.needs_review:
            reasons = "; ".join(analysis.validation_errors) or "no reason recorded"
            out.append(
                f"Analysis {analysis.analysis_id} is marked needs review: its output still "
                f"failed validation after one retry and holds a partial result ({reasons})."
            )
    truncated = [
        s.record_id for s in report.sources if s.excerpt is not None and s.excerpt.truncated
    ]
    if truncated:
        out.append(
            f"Excerpts longer than {MAX_EXCERPT_CHARS} characters are cut to their first "
            f"{MAX_EXCERPT_CHARS} characters ({', '.join(truncated)}); the full records "
            "are in the local case database."
        )
    timeline = report.timeline
    if timeline.uncertain or timeline.undated:
        out.append(
            "Some timeline events have approximate times, times with no known time zone, "
            "or no usable timestamp. They are listed apart and their order is not "
            "established."
        )
    flagged = sorted(s.record_id for s in report.sources if s.excluded_in)
    if flagged:
        out.append(
            "Some cited records are excluded in another revision on this board ("
            + ", ".join(flagged)
            + "); items citing them come from a revision that still included them."
        )
    return out
