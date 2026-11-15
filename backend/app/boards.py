"""Investigation boards: create, pin, unpin and annotate (BLA-31).

A board is a list of append-only ``InvestigationBoard`` snapshots (contracts/README.md).
Creating a board writes revision 1; every change writes revision + 1 with
``parent_revision`` set, and older snapshots are never rewritten.

Rules this module enforces:

- **Pinned content comes from the source revision, never from the request.** A pin
  names a finding (``kind``, ``source_analysis_id``, ``ref_id``); the server copies its
  evidence IDs and status from that revision. Pinning never changes a status and never
  means "verified".
- **Investigator notes stay separate.** Notes are the only investigator-authored text on
  a board, and they live in ``notes``, not in items.
- **Items are deduplicated by a stable ID.** ``item_id`` is derived from
  ``(source_analysis_id, kind, ref_id)``, so pinning the same finding twice is a no-op and
  re-pinning it after an unpin gives the same ID.
- **Every snapshot is validated before it is saved** with ``app.validation.validate_board``
  (citations exist in the case, are cited by the source item and are not excluded there;
  statuses match; notes target items on the board). An invalid change saves nothing.
- A change that leaves the board as it was writes no snapshot.
- **"Add this reconstruction to board"** (``add_reconstruction``) pins every claim, node,
  edge and event of one revision in one change, through the same checks as single pins.
  Nodes are never merged across revisions or by similar names: each stays its own item.

Revisions are looked up through a ``RevisionLookup``, because saved analysis revisions
are BLA-28. Until that lands, the app's lookup knows no revisions, so a board can be
created and annotated but nothing can be pinned yet.
"""

import hashlib
import json
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.contracts import AnalysisRevision, BoardItem, BoardNote, InvestigationBoard
from app.db import utcnow
from app.ids import new_id
from app.models import BoardSnapshotRow, EvidenceRecordRow
from app.schemas.boards import ALL_KINDS, BoardChange, NoteCreate, PinRequest
from app.validation import validate_board

# (session, case_id, analysis_ids) -> the saved revisions of that case among those IDs.
RevisionLookup = Callable[[Session, str, Collection[str]], list[AnalysisRevision]]


def no_saved_revisions(
    session: Session, case_id: str, analysis_ids: Collection[str]
) -> list[AnalysisRevision]:
    """The lookup until revisions are persisted (BLA-28): no revision is known."""
    return []


class BoardError(Exception):
    pass


class BoardNotFoundError(BoardError):
    pass


class BoardConflictError(BoardError):
    """The board moved on since ``expected_revision``, or another save won the race."""


class InvalidBoardChangeError(BoardError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class BoardUpdate:
    board: InvestigationBoard
    created_snapshot: bool  # False when the change left the board as it was


def item_id_for(source_analysis_id: str, kind: str, ref_id: str) -> str:
    """Stable board item ID for one finding of one revision."""
    digest = hashlib.sha256(f"{source_analysis_id}\x1f{kind}\x1f{ref_id}".encode()).hexdigest()
    return f"item_{digest[:20]}"


# --- Reading ----------------------------------------------------------------------------


def get_board(
    session: Session, case_id: str, board_id: str, revision: int | None = None
) -> InvestigationBoard:
    """The latest snapshot of a board, or a given ``revision``; ``BoardNotFoundError`` if
    the board (or that revision) is not in this case."""
    query = select(BoardSnapshotRow).where(
        BoardSnapshotRow.case_id == case_id, BoardSnapshotRow.board_id == board_id
    )
    if revision is None:
        query = query.order_by(BoardSnapshotRow.revision.desc()).limit(1)
    else:
        query = query.where(BoardSnapshotRow.revision == revision)
    row = session.scalars(query).first()
    if row is None:
        raise BoardNotFoundError(board_id)
    return _to_board(row)


def list_boards(session: Session, case_id: str) -> list[InvestigationBoard]:
    """The latest snapshot of every board in the case, most recently updated first."""
    latest = (
        select(BoardSnapshotRow.board_id, func.max(BoardSnapshotRow.revision).label("revision"))
        .where(BoardSnapshotRow.case_id == case_id)
        .group_by(BoardSnapshotRow.board_id)
        .subquery()
    )
    rows = session.scalars(
        select(BoardSnapshotRow)
        .join(
            latest,
            (BoardSnapshotRow.board_id == latest.c.board_id)
            & (BoardSnapshotRow.revision == latest.c.revision),
        )
        .where(BoardSnapshotRow.case_id == case_id)
        .order_by(BoardSnapshotRow.created_at.desc(), BoardSnapshotRow.board_id.desc())
    )
    return [_to_board(row) for row in rows]


def list_revisions(session: Session, case_id: str, board_id: str) -> list[InvestigationBoard]:
    """Every snapshot of a board, oldest first."""
    rows = list(
        session.scalars(
            select(BoardSnapshotRow)
            .where(BoardSnapshotRow.case_id == case_id, BoardSnapshotRow.board_id == board_id)
            .order_by(BoardSnapshotRow.revision)
        )
    )
    if not rows:
        raise BoardNotFoundError(board_id)
    return [_to_board(row) for row in rows]


# --- Writing ----------------------------------------------------------------------------


def create_board(
    session: Session,
    case_id: str,
    title: str,
    *,
    active_analysis_id: str | None = None,
    lookup: RevisionLookup = no_saved_revisions,
    now: datetime | None = None,
) -> InvestigationBoard:
    """Create a board with no items or notes (revision 1) and commit it."""
    moment = now or utcnow()
    board = InvestigationBoard(
        schema_version="1.0",
        board_id=new_id("board"),
        case_id=case_id,
        title=title,
        revision=1,
        parent_revision=None,
        active_analysis_id=active_analysis_id,
        items=[],
        notes=[],
        created_at=moment,
        updated_at=moment,
    )
    _validate(session, case_id, board, lookup)
    _save(session, board)
    return board


def update_board(
    session: Session,
    case_id: str,
    board_id: str,
    change: BoardChange,
    *,
    lookup: RevisionLookup = no_saved_revisions,
    now: datetime | None = None,
) -> BoardUpdate:
    """Apply ``change`` (unpin, then pin, then notes) and append the new snapshot.

    Raises ``BoardNotFoundError``, ``BoardConflictError`` (stale ``expected_revision`` or
    a concurrent save) or ``InvalidBoardChangeError`` (nothing is saved).
    """
    current = get_board(session, case_id, board_id)
    if change.expected_revision is not None and change.expected_revision != current.revision:
        raise BoardConflictError(
            f"board {board_id} is at revision {current.revision}, "
            f"not {change.expected_revision}; reload it and apply the change again"
        )
    moment = now or utcnow()
    errors: list[str] = []

    items = _unpin(current, change, errors)
    pins = _reconstruction_pins(session, case_id, change, lookup, errors) + change.pin
    items = items + _pins(session, case_id, items, pins, lookup, moment, errors)
    notes = current.notes + _new_notes(change.notes, moment)
    if errors:
        raise InvalidBoardChangeError(errors)

    title = change.title if change.title is not None else current.title
    active = current.active_analysis_id
    if "active_analysis_id" in change.model_fields_set:
        active = change.active_analysis_id
    if (items, notes, title, active) == (
        current.items,
        current.notes,
        current.title,
        current.active_analysis_id,
    ):
        return BoardUpdate(board=current, created_snapshot=False)

    board = current.model_copy(
        update={
            "revision": current.revision + 1,
            "parent_revision": current.revision,
            "title": title,
            "active_analysis_id": active,
            "items": items,
            "notes": notes,
            "updated_at": max(moment, current.updated_at),
        }
    )
    board = InvestigationBoard.model_validate_json(json.dumps(board.to_contract_json()))
    _validate(session, case_id, board, lookup)
    _save(session, board)
    return BoardUpdate(board=board, created_snapshot=True)


def _unpin(current: InvestigationBoard, change: BoardChange, errors: list[str]) -> list[BoardItem]:
    on_board = {item.item_id for item in current.items}
    unpin = set(change.unpin)
    for item_id in sorted(unpin - on_board):
        errors.append(f"cannot unpin {item_id}: it is not on this board")
    noted = {note.target_item_id for note in current.notes} | {
        note.target_item_id for note in change.notes
    }
    for item_id in sorted(unpin & noted & on_board):
        errors.append(
            f"cannot unpin {item_id}: notes are attached to it, and investigator notes are "
            "never removed"
        )
    return [item for item in current.items if item.item_id not in unpin]


def _reconstruction_pins(
    session: Session,
    case_id: str,
    change: BoardChange,
    lookup: RevisionLookup,
    errors: list[str],
) -> list[PinRequest]:
    """One pin per finding of the requested revision, in kind order (claims, nodes, edges,
    events), skipping nodes that cite no evidence.

    Nodes are never merged: a node is identified by its revision and ID, so "Alex" from
    one revision and "Alex" (or "A. Smith") from another stay separate items, and it is
    left to the investigator to decide they are the same person.
    """
    request = change.add_reconstruction
    if request is None:
        return []
    found = [
        revision
        for revision in lookup(session, case_id, {request.analysis_id})
        if revision.case_id == case_id and revision.analysis_id == request.analysis_id
    ]
    if not found:
        errors.append(
            f"cannot add reconstruction {request.analysis_id}: no saved revision with that ID "
            "in this case"
        )
        return []
    revision = found[0]
    findings: dict[str, Iterable[Any]] = {
        "claim": revision.claims,
        "node": [node for node in revision.graph.nodes if node.evidence_ids],
        "edge": revision.graph.edges,
        "event": revision.timeline_events,
    }
    return [
        PinRequest(kind=kind, source_analysis_id=revision.analysis_id, ref_id=finding.id)
        for kind in ALL_KINDS
        if kind in request.kinds
        for finding in findings[kind]
    ]


def _pins(
    session: Session,
    case_id: str,
    items: list[BoardItem],
    pins: list[PinRequest],
    lookup: RevisionLookup,
    moment: datetime,
    errors: list[str],
) -> list[BoardItem]:
    if not pins:
        return []
    revisions = {
        revision.analysis_id: revision
        for revision in lookup(session, case_id, {pin.source_analysis_id for pin in pins})
        if revision.case_id == case_id
    }
    present = {item.item_id for item in items}
    added: list[BoardItem] = []
    for pin in pins:
        item_id = item_id_for(pin.source_analysis_id, pin.kind, pin.ref_id)
        if item_id in present:
            continue  # already pinned: deduplicated by stable ID
        label = f"cannot pin {pin.kind} {pin.ref_id} from {pin.source_analysis_id}"
        revision = revisions.get(pin.source_analysis_id)
        if revision is None:
            errors.append(f"{label}: no saved revision {pin.source_analysis_id} in this case")
            continue
        finding = _finding(revision, pin.kind, pin.ref_id)
        if finding is None:
            errors.append(f"{label}: the revision has no {pin.kind} with that ID")
            continue
        evidence = list(dict.fromkeys(finding.evidence_ids or []))
        if not evidence:
            errors.append(f"{label}: it cites no evidence")
            continue
        copied: dict[str, Any] = {"evidence_ids": evidence}
        status = getattr(finding, "status", None)
        if status is not None:  # graph nodes carry no status: leave it out, not null
            copied["status"] = status
        added.append(
            BoardItem(
                item_id=item_id,
                kind=pin.kind,
                source_analysis_id=pin.source_analysis_id,
                ref_id=pin.ref_id,
                added_at=moment,
                **copied,
            )
        )
        present.add(item_id)
    return added


def _finding(revision: AnalysisRevision, kind: str, ref_id: str) -> Any | None:
    candidates: Iterable[Any] = {
        "claim": revision.claims,
        "node": revision.graph.nodes,
        "edge": revision.graph.edges,
        "event": revision.timeline_events,
    }[kind]
    return next((item for item in candidates if item.id == ref_id), None)


def _new_notes(notes: list[NoteCreate], moment: datetime) -> list[BoardNote]:
    """Investigator notes; their targets are checked against the new snapshot."""
    return [
        BoardNote(
            note_id=new_id("note"),
            text=note.text,
            target_item_id=note.target_item_id,
            author=note.author,
            created_at=moment,
        )
        for note in notes
    ]


def _validate(
    session: Session, case_id: str, board: InvestigationBoard, lookup: RevisionLookup
) -> None:
    analysis_ids = {item.source_analysis_id for item in board.items}
    if board.active_analysis_id is not None:
        analysis_ids.add(board.active_analysis_id)
    revisions = lookup(session, case_id, analysis_ids) if analysis_ids else []
    cited = {rid for item in board.items for rid in item.evidence_ids}
    records = (
        [
            row.to_contract()
            for row in session.scalars(
                select(EvidenceRecordRow).where(
                    EvidenceRecordRow.case_id == case_id, EvidenceRecordRow.record_id.in_(cited)
                )
            )
        ]
        if cited
        else []
    )
    errors = validate_board(records, revisions, board)
    if errors:
        raise InvalidBoardChangeError(errors)


def _save(session: Session, board: InvestigationBoard) -> None:
    session.add(
        BoardSnapshotRow(
            case_id=board.case_id,
            board_id=board.board_id,
            revision=board.revision,
            snapshot=board.to_contract_json(),
            created_at=board.updated_at,
        )
    )
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise BoardConflictError(
            f"board {board.board_id} revision {board.revision} was saved by another request; "
            "reload it and apply the change again"
        ) from error


def _to_board(row: BoardSnapshotRow) -> InvestigationBoard:
    return InvestigationBoard.model_validate_json(json.dumps(row.snapshot))
