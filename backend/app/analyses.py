"""Saved analysis revisions (BLA-28): save once, reload any time, never rewrite.

Every question, follow-up, exclusion or restore produces a new ``AnalysisRevision``; this
module stores it in ``analysis_revisions`` and loads it back exactly as saved. History is
append-only: a row is never updated (a trigger rejects UPDATE), and a later revision points
at the one it came from through ``parent_analysis_id``.

``save_revision`` enforces before writing anything:

- the case exists and the revision belongs to it;
- ``validate_revision`` passes against the case's stored records (every citation is a
  retrieved, permitted record of this case; edges join existing nodes; ...);
- the parent, if any, is a saved revision of the same case;
- the ``analysis_id`` is new. Saving the identical revision again is a no-op, so a retried
  request is safe; saving different content under a used ID is a conflict.

``saved_revision_lookup`` is the board's ``RevisionLookup``: boards pin findings only from
revisions saved here.

Restoring (BLA-35). ``restore_records`` brings excluded records back by reusing an earlier
evidence state, never by deleting or editing anything: it finds the nearest ancestor of
the current revision with the same question and exactly the exclusions that would remain,
and saves a copy of that ancestor's findings as a new child of the current revision. The
restore is recorded in ``revision_restores`` and shown in the history. If no ancestor ever
had that evidence state, there is nothing to reuse, and it raises ``NoPriorStateError``
(the analysis must be rerun instead).

Analyzing (BLA-20). ``run_case_analysis`` retrieves evidence, runs ``analyze_with_retry``,
and saves through ``save_revision``. A follow-up that names a prior revision inherits that
revision's exclusions so excluded evidence cannot silently return.

Excluding (BLA-33). ``exclude_records`` reruns the same question with more records excluded:
retrieval filters every chunk of them, the new revision is generated from permitted evidence
only and saved as a child of the base, and the excluded records stay in the corpus. So that
unchanged findings keep their identity for the support diff (``app.comparison``), the rerun
sees the base revision's findings that cite no excluded record (never its answer, and never
a finding that cites an excluded record), and the system prompt asks it to reuse their IDs
and wording only when the remaining evidence still supports them. This filter is by
citation: a finding that cites only permitted records was still written by a model that saw
the excluded record, so its wording can paraphrase excluded content. The system prompt
requires every kept finding to be re-supported by the current records, and validation
strips any citation to an excluded record, but the prompt is not guaranteed free of excluded
information. ``compare_to_parent`` returns the diff for exclusion and restore children
(same question as the parent).
"""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.analysis_llm import AnalysisLLM
from app.analysis_retry import RevisionMetadata, analyze_with_retry
from app.comparison import compare_revisions
from app.contracts import AnalysisRevision, EvidenceWindow, RevisionComparison
from app.db import utcnow
from app.embeddings import Embedder
from app.evidence_store import list_records
from app.evidence_window import DEFAULT_WINDOW, coverage_of, window_of
from app.ids import new_id
from app.models import AnalysisRevisionRow, Case, EvidenceRecordRow, RevisionRestoreRow
from app.retrieval import RetrievalConfig, permitted_records, retrieve
from app.validation import validate_revision


class RevisionStoreError(Exception):
    pass


class RevisionNotFoundError(RevisionStoreError):
    pass


class RevisionConflictError(RevisionStoreError):
    """The ``analysis_id`` is already saved with different content."""


class InvalidRestoreError(RevisionStoreError):
    """The records to restore are not excluded in the revision (or none were given)."""


class NoPriorStateError(RevisionStoreError):
    """No earlier revision in the lineage had the evidence state the restore asks for."""


class InvalidExclusionError(RevisionStoreError):
    """The records to exclude are missing, unknown in the case or already excluded."""


class ComparisonNotApplicableError(RevisionStoreError):
    """The revision's question differs from its parent's (a follow-up), so a support diff
    would not describe the effect of an exclusion."""


class InvalidRerunError(RevisionStoreError):
    """The rerun would repeat the revision's own evidence window (BLA-64)."""


class InvalidRevisionError(RevisionStoreError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class RevisionSummary:
    """One line of a case's revision history (no findings)."""

    analysis_id: str
    parent_analysis_id: str | None
    question: str
    needs_review: bool
    created_at: datetime
    # The evidence state: which records this revision excluded.
    excluded_record_ids: list[str]
    # Set when this revision is a restore: the earlier revision whose state it reuses.
    restored_from_analysis_id: str | None = None


@dataclass(frozen=True)
class RestoreResult:
    revision: AnalysisRevision  # the new, saved revision
    restored_from: AnalysisRevision  # the earlier revision whose state it reuses


def save_revision(session: Session, revision: AnalysisRevision) -> AnalysisRevision:
    """Validate and save ``revision``, commit, and return it as stored.

    Raises ``RevisionNotFoundError`` if the case or the parent revision is not saved,
    ``InvalidRevisionError`` if it fails ``validate_revision`` against the case's records,
    and ``RevisionConflictError`` if its ID is already used by a different revision.
    """
    case_id = revision.case_id
    if session.get(Case, case_id) is None:
        raise RevisionNotFoundError(f"case {case_id!r} does not exist")
    stored = _contract_json(revision)

    existing = session.get(AnalysisRevisionRow, (case_id, revision.analysis_id))
    if existing is not None:
        if existing.revision == stored:
            return _to_revision(existing)
        raise RevisionConflictError(
            f"analysis {revision.analysis_id} is already saved with different content; "
            "revisions are immutable, so save the change as a new revision"
        )
    parent = revision.parent_analysis_id
    if parent is not None and session.get(AnalysisRevisionRow, (case_id, parent)) is None:
        raise RevisionNotFoundError(
            f"parent analysis {parent} is not a saved revision of case {case_id}"
        )

    errors = validate_revision(_records_for(session, revision), revision)
    if errors:
        raise InvalidRevisionError(errors)

    session.add(_row(revision, stored))
    _commit(session, revision.analysis_id)
    return _reparsed(stored)


def restore_records(
    session: Session,
    case_id: str,
    analysis_id: str,
    record_ids: Collection[str],
    *,
    new_analysis_id: str | None = None,
    now: datetime | None = None,
) -> RestoreResult:
    """Bring ``record_ids`` back into the evidence of revision ``analysis_id``.

    Finds the nearest ancestor with the same question whose excluded set is exactly the
    current one minus ``record_ids``, and saves its findings again as a new revision whose
    parent is ``analysis_id``. Nothing is deleted or rewritten; the current revision stays
    in the history. Raises ``RevisionNotFoundError``, ``InvalidRestoreError`` (a record is
    not excluded here) or ``NoPriorStateError`` (no ancestor had that evidence state).
    """
    current = get_revision(session, case_id, analysis_id)
    restore = set(record_ids)
    if not restore:
        raise InvalidRestoreError("name at least one excluded record to restore")
    excluded_now = set(current.excluded_record_ids)
    not_excluded = sorted(restore - excluded_now)
    if not_excluded:
        raise InvalidRestoreError(
            f"not excluded in analysis {analysis_id}, so nothing to restore: "
            + ", ".join(not_excluded)
        )
    target = excluded_now - restore

    source = None
    for summary in reversed(revision_lineage(session, case_id, analysis_id)[:-1]):
        candidate = get_revision(session, case_id, summary.analysis_id)
        if candidate.question == current.question and set(candidate.excluded_record_ids) == target:
            source = candidate
            break
    if source is None:
        remaining = ", ".join(sorted(target)) or "none"
        raise NoPriorStateError(
            f"no earlier revision of this question had exactly these exclusions "
            f"(remaining: {remaining}), so there is no prior state to reuse; rerun the "
            "analysis with that exclusion set instead"
        )

    restored = source.model_copy(
        update={
            "analysis_id": new_analysis_id or new_id("analysis"),
            "parent_analysis_id": current.analysis_id,
            "created_at": now or utcnow(),
        }
    )
    stored = _contract_json(restored)
    restored = _reparsed(stored)
    errors = validate_revision(_records_for(session, restored), restored)
    if errors:  # pragma: no cover - the source revision was validated when it was saved
        raise InvalidRevisionError(errors)
    session.add(_row(restored, stored))
    session.add(
        RevisionRestoreRow(
            case_id=case_id,
            analysis_id=restored.analysis_id,
            restored_from_analysis_id=source.analysis_id,
            restored_record_ids=sorted(restore),
        )
    )
    _commit(session, restored.analysis_id)
    return RestoreResult(revision=restored, restored_from=source)


def _row(revision: AnalysisRevision, stored: dict) -> AnalysisRevisionRow:
    return AnalysisRevisionRow(
        case_id=revision.case_id,
        analysis_id=revision.analysis_id,
        parent_analysis_id=revision.parent_analysis_id,
        question=revision.question,
        needs_review=bool(revision.needs_review),
        revision=stored,
        created_at=revision.created_at,
    )


def _commit(session: Session, analysis_id: str) -> None:
    try:
        session.commit()
    except IntegrityError as error:  # a concurrent save of the same ID won the race
        session.rollback()
        raise RevisionConflictError(
            f"analysis {analysis_id} was saved by another request"
        ) from error


def get_revision(session: Session, case_id: str, analysis_id: str) -> AnalysisRevision:
    """The saved revision, exactly as stored; ``RevisionNotFoundError`` if it is not a
    revision of this case."""
    row = session.get(AnalysisRevisionRow, (case_id, analysis_id))
    if row is None:
        raise RevisionNotFoundError(f"analysis {analysis_id} is not a revision of {case_id}")
    return _to_revision(row)


def list_revisions(session: Session, case_id: str) -> list[RevisionSummary]:
    """The case's revision history, newest first."""
    rows = session.scalars(
        select(AnalysisRevisionRow)
        .where(AnalysisRevisionRow.case_id == case_id)
        .order_by(AnalysisRevisionRow.created_at.desc(), AnalysisRevisionRow.analysis_id.desc())
    )
    rows = list(rows)
    restored = _restored_from(session, case_id, [row.analysis_id for row in rows])
    return [_summary(row, restored.get(row.analysis_id)) for row in rows]


def revision_lineage(session: Session, case_id: str, analysis_id: str) -> list[RevisionSummary]:
    """The revision and its ancestors, oldest first (root ... this one)."""
    chain: list[RevisionSummary] = []
    seen: set[str] = set()
    current: str | None = analysis_id
    while current is not None:
        if current in seen:  # pragma: no cover - the parent FK and insert order prevent it
            raise RevisionStoreError(f"revision lineage of {analysis_id} has a cycle")
        seen.add(current)
        row = session.get(AnalysisRevisionRow, (case_id, current))
        if row is None:
            raise RevisionNotFoundError(f"analysis {current} is not a revision of {case_id}")
        chain.append(_summary(row, _restored_from(session, case_id, [current]).get(current)))
        current = row.parent_analysis_id
    return list(reversed(chain))


def saved_revision_lookup(
    session: Session, case_id: str, analysis_ids: Collection[str]
) -> list[AnalysisRevision]:
    """``app.boards.RevisionLookup`` backed by saved revisions."""
    ids = list(analysis_ids)
    if not ids:
        return []
    rows = session.scalars(
        select(AnalysisRevisionRow).where(
            AnalysisRevisionRow.case_id == case_id, AnalysisRevisionRow.analysis_id.in_(ids)
        )
    )
    return [_to_revision(row) for row in rows]


def _records_for(session: Session, revision: AnalysisRevision):
    """The case's stored records that the revision retrieves, excludes, cites or lists as
    skipped by the evidence window."""
    ids = {*revision.retrieved_record_ids, *revision.excluded_record_ids}
    if revision.retrieval_coverage is not None:
        ids.update(skip.record_id for skip in revision.retrieval_coverage.skipped)
    for item in (*revision.claims, *revision.graph.edges, *revision.timeline_events):
        ids.update(item.evidence_ids)
    for node in revision.graph.nodes:
        ids.update(node.evidence_ids or [])
    if not ids:
        return []
    rows = session.scalars(
        select(EvidenceRecordRow).where(
            EvidenceRecordRow.case_id == revision.case_id,
            EvidenceRecordRow.record_id.in_(sorted(ids)),
        )
    )
    return [row.to_contract() for row in rows]


def _contract_json(revision: AnalysisRevision) -> dict:
    return json.loads(json.dumps(revision.to_contract_json()))


def _reparsed(data: dict) -> AnalysisRevision:
    return AnalysisRevision.model_validate_json(json.dumps(data))


def _to_revision(row: AnalysisRevisionRow) -> AnalysisRevision:
    return _reparsed(row.revision)


def _restored_from(session: Session, case_id: str, analysis_ids: list[str]) -> dict[str, str]:
    if not analysis_ids:
        return {}
    rows = session.scalars(
        select(RevisionRestoreRow).where(
            RevisionRestoreRow.case_id == case_id,
            RevisionRestoreRow.analysis_id.in_(analysis_ids),
        )
    )
    return {row.analysis_id: row.restored_from_analysis_id for row in rows}


def _summary(row: AnalysisRevisionRow, restored_from: str | None = None) -> RevisionSummary:
    return RevisionSummary(
        analysis_id=row.analysis_id,
        parent_analysis_id=row.parent_analysis_id,
        question=row.question,
        needs_review=row.needs_review,
        created_at=row.created_at,
        excluded_record_ids=list(row.revision.get("excluded_record_ids", [])),
        restored_from_analysis_id=restored_from,
    )


# --- Analyze pipeline (BLA-20) -----------------------------------------------------------


MAX_PRIOR_SUMMARY_CHARS = 4_000


def prior_analysis_summary(revision: AnalysisRevision) -> str:
    """Short investigator context from a prior revision (answer + a few claims)."""
    lines: list[str] = []
    answer = revision.answer.strip()
    if answer:
        lines.append(answer)
    for claim in revision.claims[:5]:
        lines.append(f"- {claim.text}")
    unresolved = [q for q in revision.unresolved_questions if q.strip()][:3]
    if unresolved:
        lines.append("Unresolved: " + "; ".join(unresolved))
    text = "\n".join(lines).strip() or revision.question
    return text[:MAX_PRIOR_SUMMARY_CHARS]


def run_case_analysis(
    session: Session,
    case_id: str,
    question: str,
    llm: AnalysisLLM,
    embedder: Embedder,
    *,
    retrieval_config: RetrievalConfig | None = None,
    prior_analysis_id: str | None = None,
    evidence_window: EvidenceWindow | None = None,
    now: datetime | None = None,
) -> AnalysisRevision:
    """Retrieve, analyze with retry, save and return a new immutable revision (BLA-20).

    ``evidence_window`` (BLA-64) defaults to the prior revision's window, or ``standard``.

    Optional ``prior_analysis_id`` loads a saved revision of this case: it becomes
    ``parent_analysis_id``, supplies prompt context tagged as prior analysis (not evidence),
    and its ``excluded_record_ids`` are inherited so a follow-up cannot silently reintroduce
    excluded evidence (restoring exclusions is an explicit action, BLA-35).

    Raises ``RevisionNotFoundError`` if the prior is missing or belongs to another case,
    ``ExcludedRecordLeakError`` if retrieval leaked an exclusion (a server bug), and
    ``AnalysisLLMError`` on provider failure. Persistence goes through ``save_revision``.
    """
    parent_id: str | None = None
    prior_text: str | None = None
    excluded: list[str] = []
    window = evidence_window or DEFAULT_WINDOW
    if prior_analysis_id is not None:
        prior = get_revision(session, case_id, prior_analysis_id)
        parent_id = prior.analysis_id
        prior_text = prior_analysis_summary(prior)
        excluded = list(prior.excluded_record_ids)
        window = evidence_window or window_of(prior)

    return _analyze_and_save(
        session,
        case_id,
        question,
        llm,
        embedder,
        excluded=excluded,
        parent_id=parent_id,
        prior_text=prior_text,
        retrieval_config=retrieval_config,
        window=window,
        now=now,
    )


def exclude_records(
    session: Session,
    case_id: str,
    analysis_id: str,
    record_ids: Collection[str],
    llm: AnalysisLLM,
    embedder: Embedder,
    *,
    retrieval_config: RetrievalConfig | None = None,
    evidence_window: EvidenceWindow | None = None,
    now: datetime | None = None,
) -> AnalysisRevision:
    """Exclude ``record_ids`` from revision ``analysis_id`` and rerun its question (BLA-33).

    The rerun uses the base's evidence window unless ``evidence_window`` overrides it.

    The new revision's excluded set is the base's plus ``record_ids``; it is generated from
    the remaining evidence only, saved as a child of the base, and returned. Nothing is
    deleted: the excluded records stay in the corpus and the base stays in the history.

    Raises ``RevisionNotFoundError`` for an unknown base, ``InvalidExclusionError`` if no
    record is given, a record is not in this case, or every record is already excluded,
    ``ExcludedRecordLeakError`` if retrieval leaked an exclusion (a server bug), and
    ``AnalysisLLMError`` on provider failure.
    """
    base = get_revision(session, case_id, analysis_id)
    requested = set(record_ids)
    if not requested:
        raise InvalidExclusionError("name at least one record to exclude")
    known = set(
        session.scalars(
            select(EvidenceRecordRow.record_id).where(
                EvidenceRecordRow.case_id == case_id,
                EvidenceRecordRow.record_id.in_(sorted(requested)),
            )
        )
    )
    unknown = sorted(requested - known)
    if unknown:
        raise InvalidExclusionError(
            "not records of this case, so they cannot be excluded: " + ", ".join(unknown)
        )
    already = set(base.excluded_record_ids)
    if requested <= already:
        raise InvalidExclusionError(
            f"already excluded in analysis {analysis_id}: " + ", ".join(sorted(requested))
        )
    excluded = sorted(already | requested)
    return _analyze_and_save(
        session,
        case_id,
        base.question,
        llm,
        embedder,
        excluded=excluded,
        parent_id=base.analysis_id,
        prior_text=prior_findings_context(base, excluded),
        retrieval_config=retrieval_config,
        window=evidence_window or window_of(base),
        now=now,
    )


def rerun_with_window(
    session: Session,
    case_id: str,
    analysis_id: str,
    evidence_window: EvidenceWindow,
    llm: AnalysisLLM,
    embedder: Embedder,
    *,
    retrieval_config: RetrievalConfig | None = None,
    now: datetime | None = None,
) -> AnalysisRevision:
    """Rerun revision ``analysis_id``'s question with another evidence window (BLA-64).

    "Want more evidence?": the same question, the same exclusions, a different window; the
    result is saved as a new child and the base is never changed. As for exclusion reruns,
    the base's findings are passed as "prior analysis — not evidence" so findings that are
    still supported keep their identity for ``compare_to_parent``. A follow-up's board
    context is not stored on the revision, so rerunning a follow-up uses its findings only.

    Raises ``RevisionNotFoundError`` for an unknown revision, ``InvalidRerunError`` if the
    revision already used ``evidence_window``, ``ExcludedRecordLeakError`` (a server bug)
    and ``AnalysisLLMError`` on provider failure.
    """
    base = get_revision(session, case_id, analysis_id)
    if window_of(base) == evidence_window:
        raise InvalidRerunError(
            f"analysis {analysis_id} already used the {evidence_window} evidence window"
        )
    excluded = list(base.excluded_record_ids)
    return _analyze_and_save(
        session,
        case_id,
        base.question,
        llm,
        embedder,
        excluded=excluded,
        parent_id=base.analysis_id,
        prior_text=prior_findings_context(base, excluded),
        retrieval_config=retrieval_config,
        window=evidence_window,
        now=now,
    )


def prior_findings_context(base: AnalysisRevision, excluded: Collection[str]) -> str:
    """The base revision's findings that do not rely on any excluded record, as JSON, for
    the rerun's "prior analysis — not evidence" context.

    Left out: the answer and unresolved questions (written with the excluded evidence in
    view), every claim, edge and event citing an excluded record, every node whose cited
    records are all excluded, and edges whose endpoints were left out. What remains cites
    only permitted records, so excluded content is never sent verbatim or by citation. It
    can still be paraphrased: the remaining findings were written by a model that saw the
    excluded record. The system prompt therefore requires every kept finding to be
    re-supported by the current source records.
    """
    out = set(excluded)

    def clean(ids: Collection[str] | None) -> bool:
        return not (set(ids or ()) & out)

    nodes = [
        node
        for node in base.graph.nodes
        if not node.evidence_ids or not set(node.evidence_ids) <= out
    ]
    node_ids = {node.id for node in nodes}
    edges = [
        edge
        for edge in base.graph.edges
        if clean(edge.evidence_ids) and edge.source in node_ids and edge.target in node_ids
    ]
    referenced = {end for edge in edges for end in (edge.source, edge.target)}
    nodes = [node for node in nodes if node.evidence_ids or node.id in referenced]
    for_node = [
        {
            **node.to_contract_json(),
            "evidence_ids": [r for r in (node.evidence_ids or []) if r not in out],
        }
        for node in nodes
    ]
    context = {
        "label": "Earlier findings for this question that do not rely on any excluded record",
        "claims": [c.to_contract_json() for c in base.claims if clean(c.evidence_ids)],
        "nodes": [{k: v for k, v in n.items() if v != []} for n in for_node],
        "edges": [e.to_contract_json() for e in edges],
        "timeline_events": [
            e.to_contract_json() for e in base.timeline_events if clean(e.evidence_ids)
        ],
    }
    return json.dumps(context, ensure_ascii=False)


def compare_to_parent(session: Session, case_id: str, analysis_id: str) -> RevisionComparison:
    """The support diff between a saved revision and its parent (``app.comparison``).

    Deterministic: ``created_at`` is the child's own creation time, so the same two
    revisions always give the identical comparison. Only for children that ask the same
    question as their parent (exclusions and restores): a follow-up asks something else, so
    its "not supported" findings would just be ones the new question did not cover. Raises
    ``RevisionNotFoundError`` if the revision is unknown or has no parent, and
    ``ComparisonNotApplicableError`` if the questions differ.
    """
    child = get_revision(session, case_id, analysis_id)
    if child.parent_analysis_id is None:
        raise RevisionNotFoundError(f"analysis {analysis_id} has no parent to compare with")
    parent = get_revision(session, case_id, child.parent_analysis_id)
    if child.question != parent.question:
        raise ComparisonNotApplicableError(
            f"analysis {analysis_id} asks a different question than its parent (a follow-up), "
            "so a support comparison would not show the effect of an exclusion"
        )
    return compare_revisions(parent, child, created_at=child.created_at)


def _analyze_and_save(
    session: Session,
    case_id: str,
    question: str,
    llm: AnalysisLLM,
    embedder: Embedder,
    *,
    excluded: list[str],
    parent_id: str | None,
    prior_text: str | None,
    retrieval_config: RetrievalConfig | None,
    window: EvidenceWindow,
    now: datetime | None,
) -> AnalysisRevision:
    """Retrieve without ``excluded`` in evidence window ``window``, analyze with one retry,
    record what the window left out, save and return the revision."""
    config = (retrieval_config or RetrievalConfig()).for_window(window)
    retrieval = retrieve(
        session,
        case_id,
        question,
        embedder,
        excluded_record_ids=excluded,
        config=config,
    )
    # Defense in depth before the LLM prompt; a leak is a bug upstream.
    permitted_records(retrieval, excluded)

    moment = now or utcnow()
    metadata = RevisionMetadata(
        analysis_id=new_id("analysis"),
        created_at=moment,
        parent_analysis_id=parent_id,
    )
    records = list_records(session, case_id)
    result = analyze_with_retry(
        llm,
        retrieval,
        records,
        metadata,
        session=session,
        excluded_record_ids=excluded,
        prior_analysis=prior_text,
    )
    coverage = coverage_of(
        retrieval,
        window,
        max_records=config.max_records,
        max_excerpt_chars=config.max_excerpt_chars,
        excluded_record_ids=excluded,
    )
    revision = _reparsed(
        _contract_json(result.revision.model_copy(update={"retrieval_coverage": coverage}))
    )
    return save_revision(session, revision)
