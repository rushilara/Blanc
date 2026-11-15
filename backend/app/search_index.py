"""Vector (sqlite-vec) and keyword (FTS5) indexes over evidence chunks.

Both indexes are virtual tables in the same SQLite database as the ORM tables. The
``evidence_chunks`` table stays the source of truth (it has the foreign keys); the virtual
tables are derived from it and keyed by ``chunk_id``.

Case isolation: virtual tables cannot carry foreign keys, so each row stores its own
``case_id`` and every search filters on it inside the query:

- ``evidence_chunk_vectors`` (vec0): ``case_id`` is a **partition key**. A KNN query with
  ``case_id = ?`` only scans that case's partition, so the ``k`` nearest neighbours are all
  from the case, however close another case's vectors are. ``record_id`` is a metadata
  column, so a KNN query can also filter by record (for exclusions, BLA-14).
- ``evidence_chunks_fts`` (FTS5): ``case_id`` / ``record_id`` / ``chunk_id`` are
  ``UNINDEXED`` columns; searches filter ``case_id = ?`` in the same statement as ``MATCH``,
  before ``ORDER BY`` / ``LIMIT``.

Record exclusion (BLA-14): both searches take ``excluded_record_ids`` and drop every chunk
of those records inside the query, so the ``k`` results are all permitted chunks whenever
the case has that many. FTS5 takes ``record_id NOT IN (...)`` before ``LIMIT``. vec0 is
pickier (sqlite-vec 0.1.9, checked in tests): ANDed ``record_id != ?`` constraints are
applied inside the KNN, but ``NOT IN (...)`` is applied *after* the ``k`` nearest are picked
(excluded chunks use up slots), and a KNN with more than ``VEC_MAX_EXCLUSION_CONSTRAINTS``
``!=`` constraints fails to plan. Larger exclusion sets over-fetch instead: ask for ``k``
plus the number of excluded vectors in the case, then filter. Exclusion only filters reads;
index rows are never deleted or changed.

Consistency: ``AFTER DELETE`` triggers on ``evidence_chunks`` delete the matching vector and
FTS rows, so deleting a chunk, a record (FK cascade) or a case (FK cascade) never leaves
orphans. Because of those triggers every connection must have sqlite-vec loaded
(``app.db.make_engine`` does this).

Ranking and fusion of the two result lists live in ``app.retrieval`` (BLA-13);
``vector_search`` and ``keyword_search`` are the case-scoped primitives it builds on.
``keyword_search`` takes raw FTS5 syntax (for internal callers); turn user text into a safe
query with ``to_fts_query`` first.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import sqlite_vec
from sqlalchemy import Connection, bindparam, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

VEC_TABLE = "evidence_chunk_vectors"
FTS_TABLE = "evidence_chunks_fts"

# sqlite-vec 0.1.9 limits: a KNN with MATCH, k, case_id and 14+ ``record_id != ?``
# constraints fails with "unable to use function MATCH"; k may be at most 4096.
VEC_MAX_EXCLUSION_CONSTRAINTS = 13
VEC_MAX_K = 4096

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


# Porter stemming over unicode61 (diacritics removed): "delivering", "delivered" and
# "delivers" all index as "deliv". Query terms are stemmed the same way, quoted or not.
# Irregular forms are not related by a stemmer ("said" is not "say").
FTS_TOKENIZE = "porter unicode61 remove_diacritics 2"

_FTS_DDL = (
    f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5("
    "text, chunk_id UNINDEXED, case_id UNINDEXED, record_id UNINDEXED, "
    f"tokenize = '{FTS_TOKENIZE}')"
)

# Refill the keyword index from its source of truth (after a tokenizer change).
_FTS_REBUILD = (
    f"INSERT INTO {FTS_TABLE} (text, chunk_id, case_id, record_id) "
    "SELECT text, chunk_id, case_id, record_id FROM evidence_chunks"
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


_TOKENIZE_RE = re.compile(r"tokenize\s*=\s*'([^']*)'", re.IGNORECASE)


def _table_sql(conn: Connection | Session, name: str) -> str | None:
    return conn.execute(
        text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :name"),
        {"name": name},
    ).scalar_one_or_none()


def keyword_index_tokenizer(conn: Connection | Session) -> str | None:
    """The ``tokenize`` option of the existing FTS5 table ("" if it has none), or None if
    the table does not exist."""
    sql = _table_sql(conn, FTS_TABLE)
    if sql is None:
        return None
    match = _TOKENIZE_RE.search(sql)
    return " ".join(match.group(1).split()) if match else ""


def vector_index_dim(conn: Connection | Session) -> int | None:
    """Dimension of the existing vec0 table, or None if it does not exist."""
    sql = _table_sql(conn, VEC_TABLE)
    if sql is None:
        return None
    match = _DIM_RE.search(sql)
    return int(match.group(1)) if match else None


def ensure_search_index(conn: Connection, dim: int) -> None:
    """Create the vec0 and FTS5 tables and the cleanup trigger if they are missing.

    If the vec0 table exists with a different dimension (``EMBEDDING_DIM`` changed), it is
    dropped and recreated empty. Chunk rows are kept, so every chunk then counts as stale
    and ``app.evidence_store.reembed_stale_chunks`` rebuilds the vectors.

    If the FTS5 table exists with a different tokenizer (e.g. a database created before
    porter stemming), it is dropped, recreated and refilled from ``evidence_chunks`` right
    away (no API calls needed), with a warning.
    """
    tokenizer = keyword_index_tokenizer(conn)
    rebuild_keywords = tokenizer is not None and tokenizer != FTS_TOKENIZE
    if rebuild_keywords:
        logger.warning(
            "Keyword index tokenizer changed (%r -> %r): rebuilding %s from evidence_chunks.",
            tokenizer,
            FTS_TOKENIZE,
            FTS_TABLE,
        )
        conn.execute(text(f"DROP TABLE {FTS_TABLE}"))
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
    if rebuild_keywords:
        conn.execute(text(_FTS_REBUILD))
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


def _excluded_vector_count(session: Session, case_id: str, excluded: Sequence[str]) -> int:
    return session.execute(
        text(
            f"SELECT count(*) FROM {VEC_TABLE} "
            "WHERE case_id = :case_id AND record_id IN :excluded"
        ).bindparams(bindparam("excluded", expanding=True)),
        {"case_id": case_id, "excluded": list(excluded)},
    ).scalar_one()


def vector_search(
    session: Session,
    case_id: str,
    query_vector: Sequence[float],
    k: int = 10,
    excluded_record_ids: Iterable[str] = (),
) -> list[VectorHit]:
    """The ``k`` nearest chunks of ``case_id`` (filtered inside the KNN by partition key),
    leaving out every chunk of ``excluded_record_ids`` (see the module docstring).

    With more than ``VEC_MAX_EXCLUSION_CONSTRAINTS`` exclusions the KNN over-fetches, capped
    at ``VEC_MAX_K``; only if the excluded records have nearly that many vectors can it
    return fewer than ``k`` permitted chunks.
    """
    excluded = sorted(set(excluded_record_ids))
    params: dict[str, Any] = {
        "query": sqlite_vec.serialize_float32(list(query_vector)),
        "k": k,
        "case_id": case_id,
    }
    filters = ""
    if len(excluded) <= VEC_MAX_EXCLUSION_CONSTRAINTS:
        filters = "".join(f" AND record_id != :ex{i}" for i in range(len(excluded)))
        params.update({f"ex{i}": rid for i, rid in enumerate(excluded)})
    else:
        params["k"] = min(k + _excluded_vector_count(session, case_id, excluded), VEC_MAX_K)
    rows = session.execute(
        text(
            f"SELECT chunk_id, record_id, distance FROM {VEC_TABLE} "
            f"WHERE embedding MATCH :query AND k = :k AND case_id = :case_id{filters} "
            "ORDER BY distance"
        ),
        params,
    )
    skip = set(excluded)
    hits = [
        VectorHit(chunk_id=r[0], record_id=r[1], distance=float(r[2]))
        for r in rows
        if r[1] not in skip
    ]
    return hits[:k]


def keyword_search(
    session: Session,
    case_id: str,
    match: str,
    k: int = 10,
    excluded_record_ids: Iterable[str] = (),
) -> list[KeywordHit]:
    """Best ``k`` FTS5 matches within ``case_id``, leaving out every chunk of
    ``excluded_record_ids`` (filtered before ``LIMIT``).

    ``match`` is raw FTS5 query syntax (e.g. ``'"delivering" OR "meeting"'``); quote user
    input before passing it, since bare punctuation is an FTS5 syntax error.
    """
    excluded = sorted(set(excluded_record_ids))
    query = text(
        f"SELECT chunk_id, record_id, bm25({FTS_TABLE}) AS score FROM {FTS_TABLE} "
        f"WHERE {FTS_TABLE} MATCH :match AND case_id = :case_id"
        + (" AND record_id NOT IN :excluded" if excluded else "")
        + " ORDER BY score LIMIT :k"
    )
    params: dict[str, Any] = {"match": match, "case_id": case_id, "k": k}
    if excluded:
        query = query.bindparams(bindparam("excluded", expanding=True))
        params["excluded"] = excluded
    rows = session.execute(query, params)
    return [KeywordHit(chunk_id=r[0], record_id=r[1], score=float(r[2])) for r in rows]


# --- Free text -> safe FTS5 query ------------------------------------------------------

# Words as the unicode61 tokenizer sees them: runs of letters/digits. This is ``\w`` minus
# ``_``, because unicode61 treats underscore (like all other punctuation) as a separator.
# Terms are left unstemmed: FTS5 runs quoted query strings through the same porter
# tokenizer as the indexed text, so "delivering" in a question matches "delivered".
_WORD_RE = re.compile(r"[^\W_]+")

# Very common English words that only add noise to an OR query. They are dropped unless the
# question has nothing else (so "who is it?" still searches).
FTS_STOPWORDS = frozenset(
    """a an and are as at be been but by can could did do does for from had has have he her
    him his how i if in into is it its me my no not of on or our she so than that the their
    them then there these they this to us was we were what when where which who whom why
    will with would you your""".split()
)

# Upper bound on OR'd terms, so a pasted essay does not become a huge FTS query.
MAX_FTS_TERMS = 32


def _fts_string(term: str) -> str:
    """An FTS5 string literal: double quotes, with embedded quotes doubled."""
    return '"' + term.replace('"', '""') + '"'


def to_fts_query(question: str) -> str | None:
    """Turn free text (an investigator's question) into a safe FTS5 ``MATCH`` expression.

    ``keyword_search`` takes raw FTS5 syntax, where punctuation (``?``, ``'``, ``-``, ``:``,
    ``*``, ``(``, ``"``) and bare ``AND`` / ``OR`` / ``NOT`` / ``NEAR`` are operators, so
    passing a question straight through raises ``fts5: syntax error``. This never emits an
    operator taken from user text:

    - The text is NFKC-normalized, lower-cased and split on whitespace into words; each word
      is split into letter/digit runs the same way ``unicode61`` splits indexed text.
    - A word with several runs (``it's``, ``a-b``, ``rec-0042``, ``10:30``) becomes a quoted
      phrase (``"it s"``) that matches those tokens adjacent, as they appear in the source;
      its non-stopword runs of 2+ characters are also added as single terms for recall.
    - Every term is a quoted string, so ``AND``/``OR``/``NEAR`` in the question are words.
    - Terms are de-duplicated and joined with ``OR`` (AND would drop any chunk missing one
      word of the question); BM25 still ranks chunks matching more and rarer terms first.
      Stopwords are dropped unless nothing else is left. At most ``MAX_FTS_TERMS`` terms.

    Returns None when the text has no searchable words (empty, whitespace or punctuation
    only); callers should then skip the keyword search.
    """
    normalized = unicodedata.normalize("NFKC", question or "").lower()
    terms: list[str] = []
    fallback: list[str] = []  # stopwords, used only if nothing else is left
    for word in normalized.split():
        parts = _WORD_RE.findall(word)
        if not parts:
            continue
        if len(parts) > 1:
            terms.append(" ".join(parts))
            terms.extend(p for p in parts if len(p) > 1 and p not in FTS_STOPWORDS)
        elif parts[0] in FTS_STOPWORDS:
            fallback.append(parts[0])
        else:
            terms.append(parts[0])
    chosen = list(dict.fromkeys(terms or fallback))[:MAX_FTS_TERMS]
    if not chosen:
        return None
    return " OR ".join(_fts_string(t) for t in chosen)
