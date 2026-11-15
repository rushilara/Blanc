"""Hybrid semantic + keyword retrieval over one case's evidence (BLA-13).

``retrieve`` is the entry point for analysis (BLA-15) and the debug search endpoint:

1. Embed the question (``Embedder.embed_query``: Gemini ``RETRIEVAL_QUERY`` task type) and
   pull the ``vector_candidates`` nearest chunks of the case from the vector index.
2. Turn the question into a safe FTS5 query (``to_fts_query``) and pull the best
   ``keyword_candidates`` BM25 chunks of the case from the keyword index.
3. Fuse with reciprocal rank fusion (RRF) **per record**: each retriever's chunk list is
   reduced to a record list (a record's rank is the rank of its best chunk in that list)
   and ``score(record) = sum over retrievers of weight / (rrf_k + rank)``.
4. Cut to the evidence window: at most ``max_records`` records, each with up to
   ``max_excerpts_per_record`` excerpts (its best chunks by chunk-level RRF), and at most
   ``max_excerpt_chars`` characters of excerpt text in total.

Record score = best chunk per retriever, summed across retrievers (not a sum over all
chunks). Summing every matching chunk would rank a record higher just for being long (a
50-page document has many chunks that each match a little), which buries short decisive
messages; taking only the best chunk per retriever is length-neutral. Summing *across*
retrievers keeps the hybrid signal: a record found by both the vector and the keyword index
outranks one found by only one of them at similar ranks, even if different chunks matched.

Excerpts are exact substrings of the stored record content, recovered from the chunk
character offsets (``record.content[char_start:char_end]``); overlapping chunks of the same
record are merged into one excerpt.

Case isolation: both index searches filter by ``case_id`` inside the query (see
``app.search_index``), and record/chunk rows are loaded by ``(case_id, ...)``. Retrieval is
read-only: it never deletes or changes records, chunks or index rows.

Record exclusion (BLA-14) happens at two points, per the architecture doc: every chunk of an
excluded record is filtered before ranking (inside both index queries, then again in
``retrieve``), and ``permitted_records`` checks the result again before the LLM prompt is
built.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.contracts import EvidenceWindow
from app.embeddings import Embedder
from app.evidence_store import CaseNotFoundError, EmbeddingIndexMismatchError, get_chunks
from app.models import Case, EvidenceRecordRow
from app.search_index import (
    KeywordHit,
    VectorHit,
    keyword_search,
    to_fts_query,
    vector_index_dim,
    vector_search,
)

Retriever = Literal["vector", "keyword"]

# Defaults; the app overrides them from Settings (RETRIEVAL_* in app/config.py).
DEFAULT_VECTOR_CANDIDATES = 50
DEFAULT_KEYWORD_CANDIDATES = 50
DEFAULT_RRF_K = 60
DEFAULT_MAX_RECORDS = 16
DEFAULT_EXPANDED_MAX_RECORDS = 24
DEFAULT_MAX_EXCERPT_CHARS = 32000
DEFAULT_MAX_EXCERPTS_PER_RECORD = 2


@dataclass(frozen=True)
class RetrievalConfig:
    vector_candidates: int = DEFAULT_VECTOR_CANDIDATES
    keyword_candidates: int = DEFAULT_KEYWORD_CANDIDATES
    rrf_k: int = DEFAULT_RRF_K
    vector_weight: float = 1.0
    keyword_weight: float = 1.0
    max_records: int = DEFAULT_MAX_RECORDS
    max_excerpt_chars: int = DEFAULT_MAX_EXCERPT_CHARS
    max_excerpts_per_record: int = DEFAULT_MAX_EXCERPTS_PER_RECORD
    # Record count of the "expanded" evidence window (BLA-64); see ``for_window``.
    expanded_max_records: int = DEFAULT_EXPANDED_MAX_RECORDS

    def for_window(self, window: EvidenceWindow) -> RetrievalConfig:
        """This config for an evidence window: ``standard`` is unchanged; ``expanded`` raises
        only ``max_records`` (the character budget stays the same)."""
        if window == "expanded":
            return replace(self, max_records=self.expanded_max_records)
        return self

    @classmethod
    def from_settings(cls, settings: Settings) -> RetrievalConfig:
        return cls(
            vector_candidates=settings.RETRIEVAL_VECTOR_CANDIDATES,
            keyword_candidates=settings.RETRIEVAL_KEYWORD_CANDIDATES,
            rrf_k=settings.RETRIEVAL_RRF_K,
            vector_weight=settings.RETRIEVAL_VECTOR_WEIGHT,
            keyword_weight=settings.RETRIEVAL_KEYWORD_WEIGHT,
            max_records=settings.RETRIEVAL_MAX_RECORDS,
            max_excerpt_chars=settings.RETRIEVAL_MAX_EXCERPT_CHARS,
            max_excerpts_per_record=settings.RETRIEVAL_MAX_EXCERPTS_PER_RECORD,
            expanded_max_records=settings.RETRIEVAL_EXPANDED_MAX_RECORDS,
        )


# --- Result shapes ----------------------------------------------------------------------


class RetrieverMatch(BaseModel):
    """How one retriever ranked a record (for debugging and tuning)."""

    rank: int  # 1-based rank of the record in this retriever's record list
    chunk_id: str  # the record's best chunk in this retriever
    score: float  # vector: cosine distance (lower is closer); keyword: bm25 (lower is better)


class Excerpt(BaseModel):
    """An exact slice ``content[char_start:char_end]`` of the record."""

    text: str
    char_start: int
    char_end: int
    chunk_ids: list[str]  # the chunk(s) this excerpt covers (overlapping chunks are merged)
    score: float  # best chunk-level RRF score among those chunks
    truncated: bool = False  # cut short to fit the character budget


class RetrievedRecord(BaseModel):
    record_id: str
    source_file: str
    source_locator: str
    timestamp: str | None = None
    timestamp_kind: str | None = None
    score: float  # fused RRF score
    matched_by: Literal["vector", "keyword", "both"]
    vector: RetrieverMatch | None = None
    keyword: RetrieverMatch | None = None
    excerpts: list[Excerpt]  # best first; never empty


class SkippedRecord(BaseModel):
    """A fused candidate the evidence window left out. IDs and metadata only, never content,
    so it can be shown to the investigator ("these records were not considered")."""

    record_id: str
    rank: int  # 1-based fused rank among all candidates
    score: float  # fused RRF score
    reason: Literal["budget", "record_limit"]
    # budget skips: size of the record's best excerpt, which did not fit; else None
    excerpt_chars: int | None = None
    # Each retriever's own rank for this record (None if that retriever did not find it).
    vector_rank: int | None = None
    keyword_rank: int | None = None


class RetrievalResult(BaseModel):
    case_id: str
    question: str
    fts_query: str | None  # the FTS5 query actually run; None if the keyword search was skipped
    records: list[RetrievedRecord]  # best first, at most max_records
    vector_candidates: int  # chunk hits from the vector index
    keyword_candidates: int  # chunk hits from the keyword index
    fused_records: int  # distinct records among all candidates, before the window cut
    dropped_by_limit: int  # fused records beyond max_records
    dropped_by_budget: int  # records within max_records left out by the character budget
    excerpt_chars: int  # total excerpt characters returned (<= max_excerpt_chars)
    excluded_record_ids: list[str]  # sorted; see ``retrieve``
    # Every fused candidate not in ``records``, in rank order (budget and record_limit
    # entries match dropped_by_budget / dropped_by_limit).
    skipped: list[SkippedRecord] = []
    # True when a record was included after a higher-ranked one was skipped for budget.
    budget_substituted: bool = False


# --- Fusion -----------------------------------------------------------------------------


@dataclass
class FusedRecord:
    record_id: str
    score: float
    vector: RetrieverMatch | None
    keyword: RetrieverMatch | None
    chunk_scores: dict[str, float]  # chunk_id -> chunk-level RRF score

    @property
    def matched_by(self) -> Literal["vector", "keyword", "both"]:
        if self.vector and self.keyword:
            return "both"
        return "vector" if self.vector else "keyword"


def rrf_term(rank: int, k: int, weight: float = 1.0) -> float:
    """Reciprocal rank fusion contribution of one list: ``weight / (k + rank)``, rank 1-based."""
    return weight / (k + rank)


def _record_ranks(
    hits: Sequence[VectorHit] | Sequence[KeywordHit],
) -> dict[str, RetrieverMatch]:
    """First (best) hit per record, ranked 1.. among records in list order."""
    ranks: dict[str, RetrieverMatch] = {}
    for hit in hits:
        if hit.record_id not in ranks:
            score = hit.distance if isinstance(hit, VectorHit) else hit.score
            ranks[hit.record_id] = RetrieverMatch(
                rank=len(ranks) + 1, chunk_id=hit.chunk_id, score=score
            )
    return ranks


def fuse(
    vector_hits: Sequence[VectorHit],
    keyword_hits: Sequence[KeywordHit],
    *,
    rrf_k: int = DEFAULT_RRF_K,
    vector_weight: float = 1.0,
    keyword_weight: float = 1.0,
) -> list[FusedRecord]:
    """RRF-fuse two ranked chunk lists into records, best first (ties by record_id).

    Record score: ``vector_weight/(rrf_k + vector record rank) + keyword_weight/(rrf_k +
    keyword record rank)``, a term only for the lists the record appears in. Chunk scores
    (used to pick excerpts) use the same formula over the raw chunk ranks.
    """
    vec_ranks = _record_ranks(vector_hits)
    kw_ranks = _record_ranks(keyword_hits)

    chunk_scores: dict[str, dict[str, float]] = {}
    for hits, weight in ((vector_hits, vector_weight), (keyword_hits, keyword_weight)):
        for rank, hit in enumerate(hits, start=1):
            per_record = chunk_scores.setdefault(hit.record_id, {})
            per_record[hit.chunk_id] = per_record.get(hit.chunk_id, 0.0) + rrf_term(
                rank, rrf_k, weight
            )

    fused = []
    for record_id in vec_ranks.keys() | kw_ranks.keys():
        vec, kw = vec_ranks.get(record_id), kw_ranks.get(record_id)
        score = (rrf_term(vec.rank, rrf_k, vector_weight) if vec else 0.0) + (
            rrf_term(kw.rank, rrf_k, keyword_weight) if kw else 0.0
        )
        fused.append(FusedRecord(record_id, score, vec, kw, chunk_scores[record_id]))
    fused.sort(key=lambda f: (-f.score, f.record_id))
    return fused


# --- Excerpts and the evidence window ---------------------------------------------------


@dataclass
class _Span:
    start: int
    end: int
    chunk_ids: list[str]
    score: float


def _excerpt_spans(
    chunk_scores: dict[str, float], spans: dict[str, tuple[int, int]], limit: int
) -> list[_Span]:
    """The record's ``limit`` best chunks, overlapping ones merged, best first."""
    best = sorted(
        (cid for cid in chunk_scores if cid in spans),
        key=lambda cid: (-chunk_scores[cid], spans[cid][0]),
    )[:limit]
    merged: list[_Span] = []
    for cid in sorted(best, key=lambda c: spans[c]):
        start, end = spans[cid]
        if merged and start <= merged[-1].end:
            last = merged[-1]
            last.end = max(last.end, end)
            last.chunk_ids.append(cid)
            last.score = max(last.score, chunk_scores[cid])
        else:
            merged.append(_Span(start, end, [cid], chunk_scores[cid]))
    merged.sort(key=lambda s: (-s.score, s.start))
    return merged


def _load_rows(
    session: Session, case_id: str, record_ids: Iterable[str]
) -> dict[str, EvidenceRecordRow]:
    ids = list(record_ids)
    if not ids:
        return {}
    rows = session.scalars(
        select(EvidenceRecordRow).where(
            EvidenceRecordRow.case_id == case_id, EvidenceRecordRow.record_id.in_(ids)
        )
    )
    return {row.record_id: row for row in rows}


def retrieve(
    session: Session,
    case_id: str,
    question: str,
    embedder: Embedder,
    *,
    limit: int | None = None,
    max_excerpt_chars: int | None = None,
    excluded_record_ids: Iterable[str] | None = None,
    config: RetrievalConfig | None = None,
) -> RetrievalResult:
    """Hybrid retrieval for ``question`` within ``case_id``; see the module docstring.

    ``limit`` / ``max_excerpt_chars`` override ``config.max_records`` /
    ``config.max_excerpt_chars`` for this call. ``config`` defaults to the module defaults;
    the app passes ``RetrievalConfig.from_settings(settings)``.

    ``excluded_record_ids`` (the revision's excluded set): every chunk of these records is
    left out inside both index queries, so the ``vector_candidates`` / ``keyword_candidates``
    slots go to permitted chunks; the hit lists are filtered once more here, so an excluded
    chunk can never reach fusion or ranking. The records stay in the corpus and the indexes
    and are found again once no longer excluded. Before building the LLM prompt, re-check
    the result with ``permitted_records``.

    There is no relevance threshold: the vector KNN always returns its ``k`` nearest chunks,
    so in a small case every record can land in the window, ranked low.
    TODO: add a maximum cosine distance setting if analysis sees noise from weak matches.

    An empty question, or one with no letters or digits, returns no records without calling
    the embedder. Raises ``CaseNotFoundError`` for an unknown case and
    ``EmbeddingIndexMismatchError`` if the embedder's dimension does not match the index.
    Read-only: flushes nothing and changes nothing.
    """
    cfg = config or RetrievalConfig()
    max_records = cfg.max_records if limit is None else limit
    budget = cfg.max_excerpt_chars if max_excerpt_chars is None else max_excerpt_chars
    if max_records < 1 or budget < 1:
        raise ValueError("limit and max_excerpt_chars must be at least 1")
    if session.get(Case, case_id) is None:
        raise CaseNotFoundError(f"case {case_id!r} does not exist")
    excluded = set(excluded_record_ids or ())

    fts_query = to_fts_query(question)
    empty = RetrievalResult(
        case_id=case_id,
        question=question,
        fts_query=fts_query,
        records=[],
        vector_candidates=0,
        keyword_candidates=0,
        fused_records=0,
        dropped_by_limit=0,
        dropped_by_budget=0,
        excerpt_chars=0,
        excluded_record_ids=sorted(excluded),
    )
    if fts_query is None:  # nothing searchable: empty, whitespace or punctuation only
        return empty

    index_dim = vector_index_dim(session)
    if index_dim != embedder.dim:
        raise EmbeddingIndexMismatchError(
            f"embedder {embedder.model!r} produces {embedder.dim}-dim vectors but the vector "
            f"index is {index_dim}-dim"
        )
    vector_hits = [
        h
        for h in vector_search(
            session,
            case_id,
            embedder.embed_query(question),
            k=cfg.vector_candidates,
            excluded_record_ids=excluded,
        )
        if h.record_id not in excluded
    ]
    keyword_hits = [
        h
        for h in keyword_search(
            session, case_id, fts_query, k=cfg.keyword_candidates, excluded_record_ids=excluded
        )
        if h.record_id not in excluded
    ]

    fused = fuse(
        vector_hits,
        keyword_hits,
        rrf_k=cfg.rrf_k,
        vector_weight=cfg.vector_weight,
        keyword_weight=cfg.keyword_weight,
    )
    window = fused[:max_records]
    rows = _load_rows(session, case_id, (f.record_id for f in window))
    chunk_ids = [cid for f in window for cid in f.chunk_scores]
    chunk_rows = get_chunks(session, case_id, chunk_ids)
    spans = {c.chunk_id: (c.char_start, c.char_end) for c in chunk_rows}
    # Media chunks store an FTS sidecar in ``text`` (content is empty); use it for excerpts.
    chunk_text = {c.chunk_id: c.text for c in chunk_rows}

    records: list[RetrievedRecord] = []
    skipped: list[SkippedRecord] = []
    remaining = budget
    dropped_by_budget = 0
    budget_substituted = False
    for rank, f in enumerate(window, start=1):
        row = rows.get(f.record_id)
        if row is None:  # pragma: no cover - index and tables are kept consistent
            continue
        excerpts: list[Excerpt] = []
        candidate_spans = _excerpt_spans(f.chunk_scores, spans, cfg.max_excerpts_per_record)
        for span in candidate_spans:
            length = span.end - span.start
            truncated = False
            if length > remaining:
                # Only the very first excerpt of the window is cut to fit, so a tiny budget
                # still returns something; otherwise skip what does not fit.
                if records or excerpts:
                    continue
                length, truncated = remaining, True
            end = span.start + length
            if row.modality in ("image", "pdf") and not row.content:
                # Sidecar text lives on the chunk, not in empty media content.
                primary = span.chunk_ids[0] if span.chunk_ids else None
                text = (chunk_text.get(primary) or "")[span.start : end] if primary else ""
            else:
                text = row.content[span.start : end]
            excerpts.append(
                Excerpt(
                    text=text,
                    char_start=span.start,
                    char_end=end,
                    chunk_ids=span.chunk_ids,
                    score=span.score,
                    truncated=truncated,
                )
            )
            remaining -= length
        if not excerpts:
            dropped_by_budget += 1
            best = candidate_spans[0] if candidate_spans else None
            skipped.append(
                SkippedRecord(
                    record_id=f.record_id,
                    rank=rank,
                    score=f.score,
                    reason="budget",
                    excerpt_chars=best.end - best.start if best else None,
                    vector_rank=f.vector.rank if f.vector else None,
                    keyword_rank=f.keyword.rank if f.keyword else None,
                )
            )
            continue
        if dropped_by_budget:
            budget_substituted = True
        records.append(
            RetrievedRecord(
                record_id=row.record_id,
                source_file=row.source_file,
                source_locator=row.source_locator,
                timestamp=row.timestamp,
                timestamp_kind=row.timestamp_kind,
                score=f.score,
                matched_by=f.matched_by,
                vector=f.vector,
                keyword=f.keyword,
                excerpts=excerpts,
            )
        )

    skipped.extend(
        SkippedRecord(
            record_id=f.record_id,
            rank=rank,
            score=f.score,
            reason="record_limit",
            vector_rank=f.vector.rank if f.vector else None,
            keyword_rank=f.keyword.rank if f.keyword else None,
        )
        for rank, f in enumerate(fused[max_records:], start=max_records + 1)
    )
    return empty.model_copy(
        update={
            "records": records,
            "skipped": skipped,
            "budget_substituted": budget_substituted,
            "vector_candidates": len(vector_hits),
            "keyword_candidates": len(keyword_hits),
            "fused_records": len(fused),
            "dropped_by_limit": len(fused) - len(window),
            "dropped_by_budget": dropped_by_budget,
            "excerpt_chars": budget - remaining,
        }
    )


# --- Re-check before prompt building ----------------------------------------------------


class ExcludedRecordLeakError(RuntimeError):
    """An excluded record reached prompt building. ``retrieve`` filters exclusions before
    ranking, so this is a bug upstream, never a user error."""


def permitted_records(
    retrieved: RetrievalResult | Iterable[RetrievedRecord],
    excluded_record_ids: Iterable[str],
) -> list[RetrievedRecord]:
    """Second exclusion check, between retrieval and building the LLM prompt (BLA-20).

    Returns the records (in order) once it has checked that none of them is in
    ``excluded_record_ids`` (the revision's excluded set). Given a ``RetrievalResult``, also
    checks that retrieval ran with at least that excluded set, since otherwise the excluded
    records took part in ranking. Raises ``ExcludedRecordLeakError`` instead of quietly
    dropping anything: a leak means the analysis would rest on the wrong evidence.
    """
    excluded = set(excluded_record_ids)
    if isinstance(retrieved, RetrievalResult):
        not_applied = excluded - set(retrieved.excluded_record_ids)
        if not_applied:
            raise ExcludedRecordLeakError(
                f"retrieval did not exclude {sorted(not_applied)}; rerun it with the "
                "revision's excluded set"
            )
        records = list(retrieved.records)
    else:
        records = list(retrieved)
    leaked = sorted({r.record_id for r in records} & excluded)
    if leaked:
        raise ExcludedRecordLeakError(f"excluded records reached prompt building: {leaked}")
    return records
