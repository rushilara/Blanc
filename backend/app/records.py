"""Case-scoped source record lookup with an exact cited excerpt (BLA-59).

Backs ``GET /cases/{case_id}/records/{record_id}`` (the evidence drawer) and is meant to be
reused wherever a citation must be resolved to its source text (BLA-17 / BLA-20).

Every lookup filters by ``(case_id, record_id)`` and, for chunks, by
``(case_id, record_id, chunk_id)``: an ID that exists only in another case, or a chunk of
another record, behaves exactly like an unknown ID.

Offsets are Python ``str`` indices (Unicode code points), the same as
``EvidenceChunk.char_start`` / ``char_end``: ``content[char_start:char_end]``.

Stored content is untrusted evidence text. It is returned verbatim and never interpreted.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts import EvidenceRecord
from app.models import EvidenceChunk, EvidenceRecordRow


class RecordNotFoundError(LookupError):
    """The record is not in this case (it may exist in another case)."""


class ChunkNotFoundError(LookupError):
    """The chunk is not a chunk of this record in this case."""


class InvalidExcerptError(ValueError):
    """The requested excerpt is malformed: both modes, a lone offset, or offsets out of range."""


@dataclass(frozen=True)
class Excerpt:
    text: str
    char_start: int
    char_end: int
    chunk_id: str | None = None


@dataclass(frozen=True)
class RecordWithExcerpt:
    record: EvidenceRecord
    excerpt: Excerpt | None


def _get_row(session: Session, case_id: str, record_id: str) -> EvidenceRecordRow:
    row = session.get(EvidenceRecordRow, (case_id, record_id))
    if row is None:
        raise RecordNotFoundError(f"record {record_id!r} not found in case {case_id!r}")
    return row


def chunk_excerpt(session: Session, case_id: str, record_id: str, chunk_id: str) -> Excerpt:
    """The text and offsets of ``chunk_id``, which must belong to this case and record."""
    chunk = session.scalars(
        select(EvidenceChunk).where(
            EvidenceChunk.case_id == case_id,
            EvidenceChunk.record_id == record_id,
            EvidenceChunk.chunk_id == chunk_id,
        )
    ).one_or_none()
    if chunk is None:
        raise ChunkNotFoundError(
            f"chunk {chunk_id!r} not found for record {record_id!r} in case {case_id!r}"
        )
    return Excerpt(chunk.text, chunk.char_start, chunk.char_end, chunk.chunk_id)


def span_excerpt(content: str, char_start: int, char_end: int) -> Excerpt:
    """``content[char_start:char_end]``; requires ``0 <= char_start < char_end <= len``."""
    if char_start < 0 or char_end < 0:
        raise InvalidExcerptError("char_start and char_end must be >= 0")
    if char_start >= char_end:
        raise InvalidExcerptError("char_start must be less than char_end")
    if char_end > len(content):
        raise InvalidExcerptError(
            f"char_end {char_end} is past the end of the record content ({len(content)} chars)"
        )
    return Excerpt(content[char_start:char_end], char_start, char_end)


def get_record_with_excerpt(
    session: Session,
    case_id: str,
    record_id: str,
    *,
    chunk_id: str | None = None,
    char_start: int | None = None,
    char_end: int | None = None,
) -> RecordWithExcerpt:
    """Fetch a record of ``case_id`` and, optionally, one exact excerpt of it.

    Excerpt modes (at most one): ``chunk_id``, or both ``char_start`` and ``char_end``.
    With neither, ``excerpt`` is None.

    Raises ``RecordNotFoundError`` / ``ChunkNotFoundError`` (map to 404) and
    ``InvalidExcerptError`` (map to 422). The request shape is checked before any query.
    """
    has_span = char_start is not None or char_end is not None
    if chunk_id is not None and has_span:
        raise InvalidExcerptError("use either chunk_id or char_start/char_end, not both")
    if has_span and (char_start is None or char_end is None):
        raise InvalidExcerptError("char_start and char_end must be given together")

    row = _get_row(session, case_id, record_id)
    excerpt: Excerpt | None = None
    if chunk_id is not None:
        excerpt = chunk_excerpt(session, case_id, record_id, chunk_id)
    elif char_start is not None and char_end is not None:
        excerpt = span_excerpt(row.content, char_start, char_end)
    return RecordWithExcerpt(row.to_contract(), excerpt)


def page_records(
    session: Session, case_id: str, *, limit: int, offset: int
) -> tuple[list[EvidenceRecord], int]:
    """One page of ``case_id``'s records ordered by ``(source_file, record_id)``, plus the
    case's total record count."""
    total = session.scalar(
        select(func.count()).select_from(EvidenceRecordRow).where(
            EvidenceRecordRow.case_id == case_id
        )
    )
    rows = session.scalars(
        select(EvidenceRecordRow)
        .where(EvidenceRecordRow.case_id == case_id)
        .order_by(EvidenceRecordRow.source_file, EvidenceRecordRow.record_id)
        .limit(limit)
        .offset(offset)
    )
    return [row.to_contract() for row in rows], int(total or 0)
