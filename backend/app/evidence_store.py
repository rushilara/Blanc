"""Persist evidence records and their embedded chunks (BLA-12 / BLA-69).

The importer (BLA-9) parses files into ``EvidenceRecord`` objects and calls
``store_records``. Retrieval (BLA-13) reads chunks back through ``app.search_index``.

Rules:

- Records are keyed by ``(case_id, record_id)``. Storing a record identical to one already
  stored (same SHA-256 of its canonical contract JSON) is a no-op: no new rows, no
  embedding calls. So re-importing a case does not duplicate anything.
- A record that differs from the stored one **only** in file-provenance fields
  (``PROVENANCE_FIELDS``: ``original_sha256``, the hash of the whole source file) is
  treated as unchanged. This happens when a source file is edited, e.g. a chat export with
  new messages appended: the record's own content, locator and timestamp are the same. The
  stored version is kept as-is (its ``original_sha256`` is not overwritten), nothing is
  embedded, and the ``record_id`` is listed in ``StoreResult.source_changed`` so the
  importer can surface it. These records are counted in ``StoreResult.unchanged`` too.
- Records are immutable (docs/02 §2). A record whose ``record_id`` is already stored in
  the case with any other difference is rejected, the stored version is kept, and the
  reason names the differing fields. The same applies to a second, different copy of a
  ``record_id`` within one call.
- A record whose ``case_id`` is not the target case is rejected.
- Rejections do not abort the call: the other records are stored and every rejection is
  listed in ``StoreResult.rejected``. An unknown case raises ``CaseNotFoundError``.
- Once the case holds at least one record, its ``import_status`` is set to ``ready`` (the
  workspace only lets investigators ask about ready cases). A call that stores nothing
  into an empty case leaves it ``empty``.
- All new chunks are embedded before anything is written, so an embedding failure leaves
  the database unchanged. Like other services, this flushes but does not commit; the
  caller owns the transaction.
- Media (image/PDF) records keep empty ``content`` but get one chunk whose ``text`` is a
  lexical FTS sidecar (filename; for email images, parent subject/headers). The vector
  comes from ``Embedder.embed_media`` (bytes + MIME), not from that sidecar text.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.case_media import prepare_media_for_embedding
from app.chunking import chunk_spans
from app.contracts import EvidenceRecord
from app.embeddings import Embedder, EmbeddingError, MediaInput
from app.models import Case, EvidenceChunk, EvidenceRecordRow, ImportStatus
from app.search_index import (
    IndexedChunk,
    delete_vectors,
    insert_keywords,
    insert_vectors,
    vector_chunk_ids,
    vector_index_dim,
)

logger = logging.getLogger(__name__)

# Keep IN (...) lists well under SQLite's bound-parameter limit.
_IN_BATCH = 500

_MEDIA_MODALITIES = frozenset({"image", "pdf"})


class CaseNotFoundError(LookupError):
    """The target case does not exist."""


class EmbeddingIndexMismatchError(RuntimeError):
    """The embedder's dimension does not match the vector index."""


@dataclass(frozen=True)
class RejectedRecord:
    record_id: str
    reason: str


@dataclass
class StoreResult:
    inserted: int = 0
    #: Records already stored, including the ``source_changed`` ones.
    unchanged: int = 0
    chunks_created: int = 0
    rejected: list[RejectedRecord] = field(default_factory=list)
    #: IDs of unchanged records whose source file hash differs from the stored one (a
    #: subset of ``unchanged``). The stored version, with its old hash, was kept.
    source_changed: list[str] = field(default_factory=list)


#: Contract fields that describe the source *file* rather than the record itself. A
#: record differing from the stored one only in these is not a content change. Kept to
#: ``original_sha256``: ``source_file`` and ``metadata`` can carry record meaning, so a
#: change there is still a conflict.
PROVENANCE_FIELDS: frozenset[str] = frozenset({"original_sha256"})


def content_hash(record: EvidenceRecord) -> str:
    """SHA-256 of the record's canonical (sorted-key) contract JSON."""
    canonical = json.dumps(
        record.to_contract_json(), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_MISSING = object()


def differing_fields(a: EvidenceRecord, b: EvidenceRecord) -> list[str]:
    """Sorted contract fields whose values differ (a field absent on one side counts)."""
    ja, jb = a.to_contract_json(), b.to_contract_json()
    keys = ja.keys() | jb.keys()
    return sorted(k for k in keys if ja.get(k, _MISSING) != jb.get(k, _MISSING))


def is_media_record(record: EvidenceRecord) -> bool:
    """True when the record is an image/PDF with on-disk bytes to embed."""
    return (
        record.modality in _MEDIA_MODALITIES
        and record.media_path is not None
        and record.content_type is not None
    )


def lexical_sidecar(record: EvidenceRecord) -> str:
    """FTS text for a media record whose ``content`` is empty.

    Includes the filename and, for email-promoted images, parent subject/headers so hybrid
    RRF is not vector-only. Never empty (DB requires ``char_end > char_start``).
    """
    meta = record.metadata or {}
    parts: list[str] = []
    filename = meta.get("filename")
    if isinstance(filename, str) and filename.strip():
        parts.append(filename.strip())
    else:
        name = Path(record.source_file).name
        if name:
            parts.append(name)
    subject = meta.get("Subject") if isinstance(meta.get("Subject"), str) else None
    if not subject and isinstance(meta.get("subject"), str):
        subject = meta["subject"]
    if subject and subject.strip():
        parts.append(subject.strip())
    for key in ("From", "To", "Date", "Message-ID", "Sender", "Cc"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(f"{key}: {value.strip()}")
    text = "\n".join(parts).strip()
    return text or "media"


def resolve_media_path(
    case_id: str,
    record: EvidenceRecord,
    *,
    cases_dir: Path | None = None,
    media_dir: Path | None = None,
) -> Path:
    """Absolute path to a media record's bytes on disk."""
    if record.media_path is None:
        raise EmbeddingError(f"record {record.record_id!r} has no media_path")
    name = Path(record.media_path).name
    if media_dir is not None:
        return Path(media_dir) / name
    root = Path(cases_dir) if cases_dir is not None else _default_cases_dir()
    return root / case_id / record.media_path


def _default_cases_dir() -> Path:
    from app.config import get_settings

    return Path(get_settings().CASES_DIR)


def _require_case(session: Session, case_id: str) -> None:
    if session.get(Case, case_id) is None:
        raise CaseNotFoundError(f"case {case_id!r} does not exist")


def _check_embedder(session: Session, embedder: Embedder) -> None:
    index_dim = vector_index_dim(session)
    if index_dim != embedder.dim:
        raise EmbeddingIndexMismatchError(
            f"embedder {embedder.model!r} produces {embedder.dim}-dim vectors but the vector "
            f"index is {index_dim}-dim; set EMBEDDING_DIM to match and restart"
        )


def _embed_texts(embedder: Embedder, texts: list[str]) -> list[list[float]]:
    vectors = embedder.embed_documents(texts) if texts else []
    if len(vectors) != len(texts) or any(len(v) != embedder.dim for v in vectors):
        raise EmbeddingError("embedder returned the wrong number or size of vectors")
    return vectors


def _embed_media(embedder: Embedder, items: list[MediaInput]) -> list[list[float]]:
    vectors = embedder.embed_media(items) if items else []
    if len(vectors) != len(items) or any(len(v) != embedder.dim for v in vectors):
        raise EmbeddingError("embedder returned the wrong number or size of media vectors")
    return vectors


def _embed_pending(
    embedder: Embedder, pending: Sequence[tuple[EvidenceChunk, str | MediaInput]]
) -> list[list[float]]:
    """Embed a mixed list of text and media payloads, preserving order."""
    vectors: list[list[float] | None] = [None] * len(pending)
    text_idxs = [i for i, (_, payload) in enumerate(pending) if isinstance(payload, str)]
    media_idxs = [i for i, (_, payload) in enumerate(pending) if isinstance(payload, MediaInput)]
    if text_idxs:
        texts = [pending[i][1] for i in text_idxs]
        assert all(isinstance(t, str) for t in texts)
        for index, vector in zip(text_idxs, _embed_texts(embedder, texts), strict=True):  # type: ignore[arg-type]
            vectors[index] = vector
    if media_idxs:
        items = [pending[i][1] for i in media_idxs]
        assert all(isinstance(m, MediaInput) for m in items)
        for index, vector in zip(media_idxs, _embed_media(embedder, items), strict=True):  # type: ignore[arg-type]
            vectors[index] = vector
    if any(v is None for v in vectors):
        raise EmbeddingError("internal error: unfinished embedding batch")
    return vectors  # type: ignore[return-value]


def _batched(items: Sequence[str]) -> Iterable[Sequence[str]]:
    for i in range(0, len(items), _IN_BATCH):
        yield items[i : i + _IN_BATCH]


def _stored_hashes(session: Session, case_id: str, record_ids: Sequence[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for batch in _batched(list(dict.fromkeys(record_ids))):
        rows = session.execute(
            select(EvidenceRecordRow.record_id, EvidenceRecordRow.content_hash).where(
                EvidenceRecordRow.case_id == case_id, EvidenceRecordRow.record_id.in_(batch)
            )
        )
        hashes.update({rid: h for rid, h in rows})
    return hashes


def _stored_records(
    session: Session, case_id: str, record_ids: Sequence[str]
) -> dict[str, EvidenceRecord]:
    found: dict[str, EvidenceRecord] = {}
    for batch in _batched(list(dict.fromkeys(record_ids))):
        rows = session.scalars(
            select(EvidenceRecordRow).where(
                EvidenceRecordRow.case_id == case_id, EvidenceRecordRow.record_id.in_(batch)
            )
        )
        found.update({row.record_id: row.to_contract() for row in rows})
    return found


def _write_index(session: Session, chunks: list[EvidenceChunk], vectors: list[list[float]]):
    indexed = [IndexedChunk(c.chunk_id, c.case_id, c.record_id, c.text) for c in chunks]
    insert_vectors(session, indexed, vectors)
    insert_keywords(session, indexed)


def _media_input_for(
    case_id: str,
    record: EvidenceRecord,
    *,
    cases_dir: Path | None,
    media_dir: Path | None,
) -> tuple[MediaInput, dict[str, Any] | None]:
    path = resolve_media_path(case_id, record, cases_dir=cases_dir, media_dir=media_dir)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise EmbeddingError(
            f"cannot read media for {record.record_id!r} at {path}: {exc}"
        ) from exc
    assert record.content_type is not None
    prepared, mime_type, coverage = prepare_media_for_embedding(data, record.content_type)
    return MediaInput(data=prepared, mime_type=mime_type), coverage


def _chunks_for_record(
    case_id: str,
    record: EvidenceRecord,
    embedder: Embedder,
    *,
    cases_dir: Path | None,
    media_dir: Path | None,
) -> list[tuple[EvidenceChunk, str | MediaInput]]:
    """Build chunk rows + embed payloads for one newly stored record.

    PDF truncation coverage is stored on the media chunk's ``embedding_coverage``, never on
    the evidence record (records stay immutable after insert).
    """
    if is_media_record(record):
        sidecar = lexical_sidecar(record)
        media_input, coverage = _media_input_for(
            case_id, record, cases_dir=cases_dir, media_dir=media_dir
        )
        chunk = EvidenceChunk(
            case_id=case_id,
            record_id=record.record_id,
            chunk_index=0,
            char_start=0,
            char_end=len(sidecar),
            text=sidecar,
            embedding_model=embedder.model,
            embedding_dim=embedder.dim,
            embedding_coverage=coverage,
        )
        return [(chunk, media_input)]
    pending: list[tuple[EvidenceChunk, str | MediaInput]] = []
    for index, (start, end) in enumerate(chunk_spans(record.content)):
        text = record.content[start:end]
        chunk = EvidenceChunk(
            case_id=case_id,
            record_id=record.record_id,
            chunk_index=index,
            char_start=start,
            char_end=end,
            text=text,
            embedding_model=embedder.model,
            embedding_dim=embedder.dim,
        )
        pending.append((chunk, text))
    return pending


def store_records(
    session: Session,
    case_id: str,
    records: Sequence[EvidenceRecord],
    embedder: Embedder,
    *,
    cases_dir: Path | None = None,
    media_dir: Path | None = None,
) -> StoreResult:
    """Store ``records`` in ``case_id``: rows, chunks, vectors and keyword index entries.

    ``cases_dir`` / ``media_dir`` locate on-disk image and PDF bytes for multimodal
    embedding. Prefer ``media_dir`` when the importer wrote to a custom directory; otherwise
    bytes are read from ``{cases_dir or CASES_DIR}/{case_id}/{media_path}``.

    See the module docstring for the dedupe and rejection rules.
    """
    _require_case(session, case_id)
    _check_embedder(session, embedder)
    result = StoreResult()

    stored = _stored_hashes(session, case_id, [r.record_id for r in records])
    accepted: dict[str, str] = {}  # record_id -> hash, for records new in this call
    new_records: list[tuple[EvidenceRecord, str]] = []
    first_seen: dict[str, EvidenceRecord] = {}  # record_id -> the version that wins
    conflicts: list[EvidenceRecord] = []  # same record_id, different hash; resolved below
    for record in records:
        if record.case_id != case_id:
            result.rejected.append(
                RejectedRecord(
                    record.record_id,
                    f"record case_id {record.case_id!r} does not match target case {case_id!r}",
                )
            )
            continue
        digest = content_hash(record)
        known = accepted.get(record.record_id) or stored.get(record.record_id)
        if known is None:
            accepted[record.record_id] = digest
            new_records.append((record, digest))
            first_seen[record.record_id] = record
        elif known == digest:
            result.unchanged += 1
        else:
            conflicts.append(record)

    # Hashes differ: load the stored versions (only for these) to see which fields differ.
    missing = [r.record_id for r in conflicts if r.record_id not in first_seen]
    first_seen.update(_stored_records(session, case_id, missing))
    for record in conflicts:
        fields = differing_fields(first_seen[record.record_id], record)
        if set(fields) <= PROVENANCE_FIELDS:
            result.unchanged += 1
            result.source_changed.append(record.record_id)
        else:
            result.rejected.append(
                RejectedRecord(
                    record.record_id,
                    f"record_id already stored in this case with different content "
                    f"(fields: {', '.join(fields)}); records are immutable, so the stored "
                    f"version was kept",
                )
            )

    rows: list[EvidenceRecordRow] = []
    pending: list[tuple[EvidenceChunk, str | MediaInput]] = []
    for record, digest in new_records:
        record_pending = _chunks_for_record(
            case_id, record, embedder, cases_dir=cases_dir, media_dir=media_dir
        )
        rows.append(
            EvidenceRecordRow(
                case_id=case_id,
                record_id=record.record_id,
                source_file=record.source_file,
                source_locator=record.source_locator,
                content=record.content,
                timestamp=record.timestamp,
                timestamp_kind=record.timestamp_kind,
                original_sha256=record.original_sha256,
                modality=record.modality,
                media_path=record.media_path,
                content_type=record.content_type,
                record_metadata=record.metadata,
                content_hash=digest,
            )
        )
        pending.extend(record_pending)

    # Embed first: if this fails nothing has been written.
    vectors = _embed_pending(embedder, pending)
    chunks = [chunk for chunk, _ in pending]

    session.add_all(rows)
    session.flush()
    session.add_all(chunks)
    session.flush()  # assigns chunk_ids
    _write_index(session, chunks, vectors)
    mark_ready_if_stored(session, case_id)

    result.inserted = len(rows)
    result.chunks_created = len(chunks)
    return result


def mark_ready_if_stored(session: Session, case_id: str) -> None:
    """Set ``case_id`` to ``ready`` if it holds at least one record. Flushes, no commit."""
    case = session.get(Case, case_id)
    if case is None or case.import_status == ImportStatus.READY:
        return
    has_record = session.scalar(
        select(EvidenceRecordRow.record_id).where(EvidenceRecordRow.case_id == case_id).limit(1)
    )
    if has_record is not None:
        case.import_status = ImportStatus.READY
        session.flush()


def _stale_chunks(session: Session, case_id: str, embedder: Embedder) -> list[EvidenceChunk]:
    have_vector = vector_chunk_ids(session, case_id)
    chunks = session.scalars(
        select(EvidenceChunk)
        .where(EvidenceChunk.case_id == case_id)
        .order_by(EvidenceChunk.record_id, EvidenceChunk.chunk_index)
    )
    return [
        c
        for c in chunks
        if c.chunk_id not in have_vector
        or c.embedding_model != embedder.model
        or c.embedding_dim != embedder.dim
    ]


def _ensure_media_chunks(
    session: Session,
    case_id: str,
    embedder: Embedder,
    *,
    cases_dir: Path | None,
    media_dir: Path | None,
) -> list[EvidenceChunk]:
    """Create missing sidecar chunks for stored media records (pre-BLA-69 imports).

    Returns the new chunk rows (still without vectors / FTS until the caller indexes them).
    """
    rows = session.scalars(
        select(EvidenceRecordRow)
        .where(EvidenceRecordRow.case_id == case_id)
        .order_by(EvidenceRecordRow.record_id)
    )
    created: list[EvidenceChunk] = []
    for row in rows:
        record = row.to_contract()
        if not is_media_record(record):
            continue
        existing = session.scalars(
            select(EvidenceChunk)
            .where(
                EvidenceChunk.case_id == case_id,
                EvidenceChunk.record_id == record.record_id,
            )
            .limit(1)
        ).first()
        if existing is not None:
            continue
        sidecar = lexical_sidecar(record)
        # Validate bytes are readable before inserting a chunk that cannot be embedded.
        try:
            _payload, coverage = _media_input_for(
                case_id, record, cases_dir=cases_dir, media_dir=media_dir
            )
        except EmbeddingError as exc:
            logger.warning(
                "skipping media backfill for %s in case %s: %s",
                record.record_id,
                case_id,
                exc,
            )
            continue
        chunk = EvidenceChunk(
            case_id=case_id,
            record_id=record.record_id,
            chunk_index=0,
            char_start=0,
            char_end=len(sidecar),
            text=sidecar,
            embedding_model=embedder.model,
            embedding_dim=embedder.dim,
            embedding_coverage=coverage,
        )
        session.add(chunk)
        created.append(chunk)
    if created:
        session.flush()
    return created


def stale_chunk_count(
    session: Session,
    case_id: str,
    embedder: Embedder,
    *,
    cases_dir: Path | None = None,
    media_dir: Path | None = None,
) -> int:
    """How many chunks of ``case_id`` ``reembed_stale_chunks`` would re-embed.

    Counts media records that still need a sidecar chunk (pre-BLA-69 imports) as stale.
    """
    _require_case(session, case_id)
    pending_media = 0
    rows = session.scalars(select(EvidenceRecordRow).where(EvidenceRecordRow.case_id == case_id))
    for row in rows:
        record = row.to_contract()
        if not is_media_record(record):
            continue
        has_chunk = session.scalars(
            select(EvidenceChunk.chunk_id)
            .where(
                EvidenceChunk.case_id == case_id,
                EvidenceChunk.record_id == record.record_id,
            )
            .limit(1)
        ).first()
        if has_chunk is None:
            pending_media += 1
    return pending_media + len(_stale_chunks(session, case_id, embedder))


def reembed_stale_chunks(
    session: Session,
    case_id: str,
    embedder: Embedder,
    *,
    cases_dir: Path | None = None,
    media_dir: Path | None = None,
) -> int:
    """Re-embed the chunks of ``case_id`` whose vector is missing or was made by a different
    model/dimension than ``embedder``. Also creates sidecar chunks for media records that
    were imported before multimodal embedding. Returns how many chunks were re-embedded.

    Use after changing ``EMBEDDING_MODEL`` or ``EMBEDDING_DIM``. Re-sends every stale
    record to Gemini — re-run Owl / keyword baseline / "want more evidence?" measurements
    after a corpus-wide model switch. Flushes, does not commit.
    """
    _require_case(session, case_id)
    _check_embedder(session, embedder)
    new_media_chunks = _ensure_media_chunks(
        session, case_id, embedder, cases_dir=cases_dir, media_dir=media_dir
    )
    stale = _stale_chunks(session, case_id, embedder)
    if not stale:
        return 0
    records = _stored_records(session, case_id, [c.record_id for c in stale])
    pending: list[tuple[EvidenceChunk, str | MediaInput]] = []
    skipped_chunk_ids: set[str] = set()
    for chunk in stale:
        record = records.get(chunk.record_id)
        if record is not None and is_media_record(record):
            try:
                payload, coverage = _media_input_for(
                    case_id, record, cases_dir=cases_dir, media_dir=media_dir
                )
            except EmbeddingError as exc:
                logger.warning(
                    "skipping re-embed of media chunk %s (%s) in case %s: %s",
                    chunk.chunk_id,
                    chunk.record_id,
                    case_id,
                    exc,
                )
                skipped_chunk_ids.add(chunk.chunk_id)
                continue
            # Index metadata only — never mutate the stored evidence record.
            chunk.embedding_coverage = coverage
            pending.append((chunk, payload))
        else:
            pending.append((chunk, chunk.text))
    if not pending:
        return 0
    vectors = _embed_pending(embedder, pending)
    updated = [chunk for chunk, _ in pending]
    # Replace any old vector (delete is a no-op for chunks that had none).
    delete_vectors(session, [c.chunk_id for c in updated])
    indexed = [IndexedChunk(c.chunk_id, c.case_id, c.record_id, c.text) for c in updated]
    insert_vectors(session, indexed, vectors)
    # Only newly created media chunks that were successfully embedded need FTS rows.
    new_ok = [c for c in new_media_chunks if c.chunk_id not in skipped_chunk_ids]
    if new_ok:
        insert_keywords(
            session,
            [IndexedChunk(c.chunk_id, c.case_id, c.record_id, c.text) for c in new_ok],
        )
    for chunk in updated:
        chunk.embedding_model = embedder.model
        chunk.embedding_dim = embedder.dim
    session.flush()
    return len(updated)


# --- Case-scoped reads ------------------------------------------------------------------


def get_record(session: Session, case_id: str, record_id: str) -> EvidenceRecord | None:
    """The stored record, or None if it is not in this case (even if another case has it)."""
    row = session.get(EvidenceRecordRow, (case_id, record_id))
    return row.to_contract() if row is not None else None


def list_records(session: Session, case_id: str) -> list[EvidenceRecord]:
    rows = session.scalars(
        select(EvidenceRecordRow)
        .where(EvidenceRecordRow.case_id == case_id)
        .order_by(EvidenceRecordRow.record_id)
    )
    return [row.to_contract() for row in rows]


def list_chunks(
    session: Session, case_id: str, record_id: str | None = None
) -> list[EvidenceChunk]:
    query = select(EvidenceChunk).where(EvidenceChunk.case_id == case_id)
    if record_id is not None:
        query = query.where(EvidenceChunk.record_id == record_id)
    return list(session.scalars(query.order_by(EvidenceChunk.record_id, EvidenceChunk.chunk_index)))


def get_chunks(session: Session, case_id: str, chunk_ids: Sequence[str]) -> list[EvidenceChunk]:
    """Chunks by ID, limited to ``case_id`` (IDs from other cases are silently absent)."""
    found: list[EvidenceChunk] = []
    for batch in _batched(list(chunk_ids)):
        found.extend(
            session.scalars(
                select(EvidenceChunk).where(
                    EvidenceChunk.case_id == case_id, EvidenceChunk.chunk_id.in_(batch)
                )
            )
        )
    return found
