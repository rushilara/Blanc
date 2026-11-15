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
"""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.contracts import AnalysisRevision
from app.models import AnalysisRevisionRow, Case, EvidenceRecordRow
from app.validation import validate_revision


class RevisionStoreError(Exception):
    pass


class RevisionNotFoundError(RevisionStoreError):
    pass


class RevisionConflictError(RevisionStoreError):
    """The ``analysis_id`` is already saved with different content."""


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

    session.add(
        AnalysisRevisionRow(
            case_id=case_id,
            analysis_id=revision.analysis_id,
            parent_analysis_id=parent,
            question=revision.question,
            needs_review=bool(revision.needs_review),
            revision=stored,
            created_at=revision.created_at,
        )
    )
    try:
        session.commit()
    except IntegrityError as error:  # a concurrent save of the same ID won the race
        session.rollback()
        raise RevisionConflictError(
            f"analysis {revision.analysis_id} was saved by another request"
        ) from error
    return _reparsed(stored)


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
    return [_summary(row) for row in rows]


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
        chain.append(_summary(row))
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
    """The case's stored records that the revision retrieves, excludes or cites."""
    ids = {*revision.retrieved_record_ids, *revision.excluded_record_ids}
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


def _summary(row: AnalysisRevisionRow) -> RevisionSummary:
    return RevisionSummary(
        analysis_id=row.analysis_id,
        parent_analysis_id=row.parent_analysis_id,
        question=row.question,
        needs_review=row.needs_review,
        created_at=row.created_at,
    )
