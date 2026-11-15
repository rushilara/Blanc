"""Evidence windows and retrieval coverage (BLA-64): "want more evidence?".

Retrieval passes the model at most ``max_records`` records. Two named windows:

- ``standard``: ``RETRIEVAL_MAX_RECORDS`` (default 16).
- ``expanded``: ``RETRIEVAL_EXPANDED_MAX_RECORDS`` (default 24). Only the record count is
  raised; the character budget (``RETRIEVAL_MAX_EXCERPT_CHARS``) stays the same.

Every new revision stores a server-assigned ``retrieval_coverage``: the window, its limits,
every ranked candidate it left out (IDs and ranks only, never content), and whether the
investigator should be offered the expanded window.

When to offer. Retrieval has no relevance threshold: the vector search always returns its
nearest chunks, so in a small case every record is ranked and anything past the window is
"skipped for the record limit" on every question. Offering on any such skip would offer on
every question (all five Owl evaluation questions skip 16 records). Instead a skipped record
counts as **left out** only when the vector or the keyword search *on its own* ranked it
within the window, i.e. it was a top match for one retriever but lost its place when the two
rankings were fused. On Owl q2 this flags both missing "Sarah" texts (vector ranks 10 and
16, fused ranks 17 and 19); across the five Owl questions it fires on two (q2, and q3 on a
record at vector rank 16), not five. Budget skips never trigger the offer: more records
would not help when the character budget is what is full.

The offer is shown only for the standard window; there is one step only. An expanded
revision still lists its left-out records so the UI can say "N relevant records were still
left out".
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from app.contracts import AnalysisRevision, EvidenceWindow, RetrievalCoverage, SkippedEvidence

if TYPE_CHECKING:  # retrieval loads the database layer; validation imports this module
    from app.retrieval import RetrievalResult

DEFAULT_WINDOW: EvidenceWindow = "standard"


def window_of(revision: AnalysisRevision) -> EvidenceWindow:
    """The evidence window a revision ran with. Revisions saved before coverage existed ran
    with the standard window."""
    coverage = revision.retrieval_coverage
    return coverage.evidence_window if coverage is not None else DEFAULT_WINDOW


def left_out(skipped: Sequence[SkippedEvidence], max_records: int) -> list[str]:
    """Record-limit skips that the vector or keyword search alone ranked within
    ``max_records``, in fused rank order."""
    return [
        skip.record_id
        for skip in sorted(skipped, key=lambda s: s.rank)
        if skip.reason == "record_limit"
        and any(r is not None and r <= max_records for r in (skip.vector_rank, skip.keyword_rank))
    ]


def offers_expanded(window: EvidenceWindow, left_out_ids: Sequence[str]) -> bool:
    return window == "standard" and bool(left_out_ids)


def coverage_of(
    retrieval: RetrievalResult,
    window: EvidenceWindow,
    *,
    max_records: int,
    max_excerpt_chars: int,
    excluded_record_ids: Sequence[str] = (),
) -> RetrievalCoverage:
    """``retrieval_coverage`` for a revision built from ``retrieval``. Excluded records are
    filtered by retrieval already; they are dropped here too so they can never be listed."""
    excluded = set(excluded_record_ids)
    skipped = [
        SkippedEvidence(
            record_id=skip.record_id,
            rank=skip.rank,
            reason=skip.reason,
            # Omitted, never null, when a retriever did not find the record.
            **{
                name: value
                for name, value in (
                    ("vector_rank", skip.vector_rank),
                    ("keyword_rank", skip.keyword_rank),
                )
                if value is not None
            },
        )
        for skip in retrieval.skipped
        if skip.record_id not in excluded
    ]
    ids = left_out(skipped, max_records)
    return RetrievalCoverage(
        evidence_window=window,
        max_records=max_records,
        max_excerpt_chars=max_excerpt_chars,
        skipped=skipped,
        budget_substituted=retrieval.budget_substituted,
        left_out_record_ids=ids,
        offer_expanded_window=offers_expanded(window, ids),
    )
