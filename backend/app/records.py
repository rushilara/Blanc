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

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

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
    if row.modality in ("image", "pdf") and (chunk_id is not None or has_span):
        # A media record's content is its file: there are no character offsets to quote,
        # and its only chunk is the search sidecar (filename / parent subject), which is not
        # record content (BLA-71). Cite the record_id; the file is served by /media.
        raise InvalidExcerptError(
            f"record {record_id!r} is {row.modality} media and has no text excerpt; "
            "fetch it without chunk_id/char_start/char_end"
        )
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


# --- Source file context (the source panel's "Original file" view) -----------------

# Chrome/WebKit time: microseconds since 1601-01-01 UTC. Used only to *order* rows of a
# browser database whose records carry no ISO timestamp; the stored value is unchanged.
_WEBKIT_EPOCH_OFFSET_S = 11_644_473_600
_RAW_WEBKIT_TIME_KEYS = ("last_visit_time", "visit_time", "start_time")
_DIGITS = re.compile(r"(\d+)")


@dataclass(frozen=True)
class FileContext:
    """Records that share the cited record's ``source_file``, in file order.

    ``items`` is a window of the file: ``items[index - start]`` is the cited record, and
    ``start`` is the window's offset among all ``total`` records of the file.
    """

    source_file: str
    total: int
    index: int
    start: int
    items: list[EvidenceRecord]
    ordered_by: str


def _natural(value: str) -> tuple[object, ...]:
    """``"row 10"`` after ``"row 9"``: digit runs compare as numbers."""
    return tuple(int(part) if part.isdigit() else part.lower() for part in _DIGITS.split(value))


def _raw_webkit_iso(content: str) -> str | None:
    if not content.startswith("{"):
        return None
    try:
        data = json.loads(content)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    for key in _RAW_WEBKIT_TIME_KEYS:
        value = data.get(key)
        # Plausible WebKit range only (after 1990, before 2200); 0 means "not set".
        if isinstance(value, int) and 12_000_000_000_000_000 < value < 19_000_000_000_000_000:
            seconds = value / 1_000_000 - _WEBKIT_EPOCH_OFFSET_S
            return datetime.fromtimestamp(seconds, tz=UTC).isoformat()
    return None


def _order_key(row: EvidenceRecordRow) -> tuple[int, str, tuple[object, ...], str]:
    """Time first (ISO timestamp, else a raw browser time in the content), then the
    position the locator states ("line 12", "JSON array index 6", "urls.id=3"). Records
    with no time follow the timed ones, in locator order."""
    when = _iso_utc(row.timestamp) or _raw_webkit_iso(row.content)
    return (0 if when else 1, when or "", _natural(row.source_locator), row.record_id)


def _iso_utc(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.isoformat()
    return parsed.astimezone(UTC).isoformat()


def file_context(
    session: Session, case_id: str, record_id: str, *, before: int, after: int
) -> FileContext:
    """The cited record among the other records of its source file in this case.

    Only this case's records are read. Raises ``RecordNotFoundError`` (404)."""
    row = _get_row(session, case_id, record_id)
    siblings = sorted(
        session.scalars(
            select(EvidenceRecordRow).where(
                EvidenceRecordRow.case_id == case_id,
                EvidenceRecordRow.source_file == row.source_file,
            )
        ),
        key=_order_key,
    )
    index = next(i for i, item in enumerate(siblings) if item.record_id == record_id)
    start = max(0, index - before)
    window = siblings[start : index + after + 1]
    timed = sum(1 for item in siblings if _order_key(item)[0] == 0)
    if timed == len(siblings):
        ordered_by = "time"
    elif timed == 0:
        ordered_by = "locator"
    else:
        ordered_by = "time_then_locator"
    return FileContext(
        source_file=row.source_file,
        total=len(siblings),
        index=index,
        start=start,
        items=[item.to_contract() for item in window],
        ordered_by=ordered_by,
    )
