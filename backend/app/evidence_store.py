"""Persist evidence records and their embedded chunks (BLA-12).

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
- All new chunks are embedded before anything is written, so an embedding failure leaves
  the database unchanged. Like other services, this flushes but does not commit; the
  caller owns the transaction.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chunking import chunk_spans
from app.contracts import EvidenceRecord
from app.embeddings import Embedder, EmbeddingError
from app.models import Case, EvidenceChunk, EvidenceRecordRow
from app.search_index import (
    IndexedChunk,
    delete_vectors,
    insert_keywords,
    insert_vectors,
    vector_chunk_ids,
    vector_index_dim,
)

# Keep IN (...) lists well under SQLite's bound-parameter limit.
_IN_BATCH = 500


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


def _embed(embedder: Embedder, texts: list[str]) -> list[list[float]]:
    vectors = embedder.embed_documents(texts) if texts else []
    if len(vectors) != len(texts) or any(len(v) != embedder.dim for v in vectors):
        raise EmbeddingError("embedder returned the wrong number or size of vectors")
    return vectors


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


def store_records(
    session: Session, case_id: str, records: Sequence[EvidenceRecord], embedder: Embedder
) -> StoreResult:
    """Store ``records`` in ``case_id``: rows, chunks, vectors and keyword index entries.

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
    chunks: list[EvidenceChunk] = []
    for record, digest in new_records:
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
                record_metadata=record.metadata,
                content_hash=digest,
            )
        )
        for index, (start, end) in enumerate(chunk_spans(record.content)):
            chunks.append(
                EvidenceChunk(
                    case_id=case_id,
                    record_id=record.record_id,
                    chunk_index=index,
                    char_start=start,
                    char_end=end,
                    text=record.content[start:end],
                    embedding_model=embedder.model,
                    embedding_dim=embedder.dim,
                )
            )

    # Embed first: if this fails nothing has been written.
    vectors = _embed(embedder, [c.text for c in chunks])

    session.add_all(rows)
    session.flush()
    session.add_all(chunks)
    session.flush()  # assigns chunk_ids
    _write_index(session, chunks, vectors)

    result.inserted = len(rows)
    result.chunks_created = len(chunks)
    return result


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


def stale_chunk_count(session: Session, case_id: str, embedder: Embedder) -> int:
    """How many chunks of ``case_id`` ``reembed_stale_chunks`` would re-embed."""
    return len(_stale_chunks(session, case_id, embedder))


def reembed_stale_chunks(session: Session, case_id: str, embedder: Embedder) -> int:
    """Re-embed the chunks of ``case_id`` whose vector is missing or was made by a different
    model/dimension than ``embedder``. Returns how many chunks were re-embedded.

    Use after changing ``EMBEDDING_MODEL`` or ``EMBEDDING_DIM``. Flushes, does not commit.
    """
    _require_case(session, case_id)
    _check_embedder(session, embedder)
    stale = _stale_chunks(session, case_id, embedder)
    if not stale:
        return 0
    vectors = _embed(embedder, [c.text for c in stale])
    # Replace any old vector (delete is a no-op for chunks that had none).
    delete_vectors(session, [c.chunk_id for c in stale])
    indexed = [IndexedChunk(c.chunk_id, c.case_id, c.record_id, c.text) for c in stale]
    insert_vectors(session, indexed, vectors)
    for chunk in stale:
        chunk.embedding_model = embedder.model
        chunk.embedding_dim = embedder.dim
    session.flush()
    return len(stale)


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
    return list(
        session.scalars(query.order_by(EvidenceChunk.record_id, EvidenceChunk.chunk_index))
    )


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
