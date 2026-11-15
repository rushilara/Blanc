"""Vector (sqlite-vec) and keyword (FTS5) indexes over evidence chunks.

Both indexes are virtual tables in the same SQLite database as the ORM tables. The
``evidence_chunks`` table stays the source of truth (it has the foreign keys); the virtual
tables are derived from it and keyed by ``chunk_id``.

Case isolation: virtual tables cannot carry foreign keys, so each row stores its own
``case_id`` and every search filters on it inside the query:

- ``evidence_chunk_vectors`` (vec0): ``case_id`` is a **partition key**. A KNN query with
  ``case_id = ?`` only scans that case's partition, so the ``k`` nearest neighbours are all
  from the case, however close another case's vectors are. ``record_id`` is a metadata
  column, so a KNN query can also filter by record (for exclusions, BLA-13).
- ``evidence_chunks_fts`` (FTS5): ``case_id`` / ``record_id`` / ``chunk_id`` are
  ``UNINDEXED`` columns; searches filter ``case_id = ?`` in the same statement as ``MATCH``,
  before ``ORDER BY`` / ``LIMIT``.

Consistency: ``AFTER DELETE`` triggers on ``evidence_chunks`` delete the matching vector and
FTS rows, so deleting a chunk, a record (FK cascade) or a case (FK cascade) never leaves
orphans. Because of those triggers every connection must have sqlite-vec loaded
(``app.db.make_engine`` does this).

Ranking and fusion of the two result lists is BLA-13's job; ``vector_search`` and
``keyword_search`` are the case-scoped primitives it builds on.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import sqlite_vec
from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

VEC_TABLE = "evidence_chunk_vectors"
FTS_TABLE = "evidence_chunks_fts"

_EXTENSION_HELP = (
    "This Python's sqlite3 module cannot load SQLite extensions, which Blanc needs for "
    "sqlite-vec. Use a Python built with loadable extensions (python.org installer, "
    "Homebrew, pyenv with PYTHON_CONFIGURE_OPTS=--enable-loadable-sqlite-extensions, or "
    "GitHub's setup-python). See backend/README.md."
)


def load_sqlite_vec(dbapi_connection: Any) -> None:
    """Load sqlite-vec into a raw ``sqlite3`` connection, then disable extension loading."""
    try:
        dbapi_connection.enable_load_extension(True)
    except AttributeError as exc:  # pragma: no cover - depends on how Python was built
        raise RuntimeError(_EXTENSION_HELP) from exc
    try:
        sqlite_vec.load(dbapi_connection)
    finally:
        dbapi_connection.enable_load_extension(False)


def _vec_table_ddl(dim: int) -> str:
    return (
        f"CREATE VIRTUAL TABLE IF NOT EXISTS {VEC_TABLE} USING vec0("
        "chunk_id text primary key, "
        "case_id text partition key, "
        "record_id text, "
        f"embedding float[{dim}] distance_metric=cosine)"
    )


_FTS_DDL = (
    f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5("
    "text, chunk_id UNINDEXED, case_id UNINDEXED, record_id UNINDEXED, "
    "tokenize = 'unicode61 remove_diacritics 2')"
)

_TRIGGER_DDL = f"""
CREATE TRIGGER IF NOT EXISTS evidence_chunks_delete_index
AFTER DELETE ON evidence_chunks
BEGIN
    DELETE FROM {VEC_TABLE} WHERE chunk_id = OLD.chunk_id;
    DELETE FROM {FTS_TABLE} WHERE chunk_id = OLD.chunk_id;
END
"""

_DIM_RE = re.compile(r"embedding\s+float\[(\d+)\]", re.IGNORECASE)


def vector_index_dim(conn: Connection | Session) -> int | None:
    """Dimension of the existing vec0 table, or None if it does not exist."""
    sql = conn.execute(
        text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :name"),
        {"name": VEC_TABLE},
    ).scalar_one_or_none()
    if sql is None:
        return None
    match = _DIM_RE.search(sql)
    return int(match.group(1)) if match else None


def ensure_search_index(conn: Connection, dim: int) -> None:
    """Create the vec0 and FTS5 tables and the cleanup trigger if they are missing.

    If the vec0 table exists with a different dimension (``EMBEDDING_DIM`` changed), it is
    dropped and recreated empty. Chunk rows are kept, so every chunk then counts as stale
    and ``app.evidence_store.reembed_stale_chunks`` rebuilds the vectors.
    """
    existing = vector_index_dim(conn)
    if existing is not None and existing != dim:
        logger.warning(
            "Embedding dimension changed (%s -> %s): recreating %s; run "
            "reembed_stale_chunks to rebuild vectors.",
            existing,
            dim,
            VEC_TABLE,
        )
        conn.execute(text(f"DROP TABLE {VEC_TABLE}"))
    conn.execute(text(_vec_table_ddl(dim)))
    conn.execute(text(_FTS_DDL))
    conn.execute(text(_TRIGGER_DDL))


@dataclass(frozen=True)
class IndexedChunk:
    """What the indexes store for one chunk."""

    chunk_id: str
    case_id: str
    record_id: str
    text: str


def insert_vectors(
    session: Session, chunks: Sequence[IndexedChunk], vectors: Sequence[Sequence[float]]
) -> None:
    if not chunks:
        return
    session.execute(
        text(
            f"INSERT INTO {VEC_TABLE} (chunk_id, case_id, record_id, embedding) "
            "VALUES (:chunk_id, :case_id, :record_id, :embedding)"
        ),
        [
            {
                "chunk_id": c.chunk_id,
                "case_id": c.case_id,
                "record_id": c.record_id,
                "embedding": sqlite_vec.serialize_float32(list(v)),
            }
            for c, v in zip(chunks, vectors, strict=True)
        ],
    )


def delete_vectors(session: Session, chunk_ids: Sequence[str]) -> None:
    if chunk_ids:
        session.execute(
            text(f"DELETE FROM {VEC_TABLE} WHERE chunk_id = :chunk_id"),
            [{"chunk_id": cid} for cid in chunk_ids],
        )


def insert_keywords(session: Session, chunks: Sequence[IndexedChunk]) -> None:
    if not chunks:
        return
    session.execute(
        text(
            f"INSERT INTO {FTS_TABLE} (text, chunk_id, case_id, record_id) "
            "VALUES (:text, :chunk_id, :case_id, :record_id)"
        ),
        [
            {"text": c.text, "chunk_id": c.chunk_id, "case_id": c.case_id, "record_id": c.record_id}
            for c in chunks
        ],
    )


def vector_chunk_ids(session: Session, case_id: str) -> set[str]:
    """IDs of the chunks of one case that have a vector."""
    rows = session.execute(
        text(f"SELECT chunk_id FROM {VEC_TABLE} WHERE case_id = :case_id"), {"case_id": case_id}
    )
    return {row[0] for row in rows}


@dataclass(frozen=True)
class VectorHit:
    chunk_id: str
    record_id: str
    distance: float  # cosine distance: 0 = identical direction, lower is closer


@dataclass(frozen=True)
class KeywordHit:
    chunk_id: str
    record_id: str
    score: float  # FTS5 bm25(): lower (more negative) is a better match


def vector_search(
    session: Session, case_id: str, query_vector: Sequence[float], k: int = 10
) -> list[VectorHit]:
    """The ``k`` nearest chunks of ``case_id`` (filtered inside the KNN by partition key)."""
    rows = session.execute(
        text(
            f"SELECT chunk_id, record_id, distance FROM {VEC_TABLE} "
            "WHERE embedding MATCH :query AND k = :k AND case_id = :case_id "
            "ORDER BY distance"
        ),
        {"query": sqlite_vec.serialize_float32(list(query_vector)), "k": k, "case_id": case_id},
    )
    return [VectorHit(chunk_id=r[0], record_id=r[1], distance=float(r[2])) for r in rows]


def keyword_search(session: Session, case_id: str, match: str, k: int = 10) -> list[KeywordHit]:
    """Best ``k`` FTS5 matches within ``case_id``.

    ``match`` is raw FTS5 query syntax (e.g. ``'"delivering" OR "meeting"'``); quote user
    input before passing it, since bare punctuation is an FTS5 syntax error.
    """
    rows = session.execute(
        text(
            f"SELECT chunk_id, record_id, bm25({FTS_TABLE}) AS score FROM {FTS_TABLE} "
            f"WHERE {FTS_TABLE} MATCH :match AND case_id = :case_id "
            "ORDER BY score LIMIT :k"
        ),
        {"match": match, "case_id": case_id, "k": k},
    )
    return [KeywordHit(chunk_id=r[0], record_id=r[1], score=float(r[2])) for r in rows]
