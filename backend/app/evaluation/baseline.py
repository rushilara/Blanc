"""Keyword-only retrieval baseline (BLA-51).

Answers one question: does the pipeline's retrieval find evidence that a plain
keyword search would miss? For each question in a question set it ranks the
case's records by keyword match alone and reports the top-k hit rate (how many
of the expected records are in the top k) next to the pipeline's.

The keyword search is BM25 over each record's content, with lowercase word
tokens and a short stop-word list. It is deliberately simple and self-contained:
no embeddings, no index, no LLM. It stands in for "an investigator typing the
question into a search box".

Run it through the evaluation runner (from `backend/`):

    python -m app.evaluation --questions q.json --records r.json --results out/ --keyword-baseline
    python -m app.evaluation --questions q.json --records r.json --keyword-only

Only the question set's case is searched: records and saved revisions from any
other case are ignored, as in the main runner, because record IDs can repeat
across cases.

The keyword side is plain BM25 with word tokens and no stemming, so it is not
the pipeline's own FTS5 retriever (which stems); a gap between the columns is
hybrid retrieval against a search box, not against the pipeline's keyword half.

How to read the numbers: on a handful of questions this is a demonstration on
one case, not a benchmark. A record that both methods find tells you nothing; a
record only the pipeline finds is the interesting one, so the table lists what
each method missed.
"""

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from app.contracts import AnalysisRevision, EvidenceRecord
from app.evaluation.questions import QuestionSet
from app.evaluation.runner import CheckResult, ResultSource
from app.validation import schema_errors

DEFAULT_K = 10

_TOKEN = re.compile(r"\w+")
STOPWORDS = frozenset(
    """a about an and are as at be been between by did do does for from had has have how in
    is it of on or that the this to was were what when where which who whom why with""".split()
)

# Standard BM25 parameters.
_K1 = 1.5
_B = 0.75


def tokenize(text: str) -> list[str]:
    return [token for token in _TOKEN.findall(text.casefold()) if token not in STOPWORDS]


def keyword_rank(query: str, records: Sequence[EvidenceRecord], k: int = DEFAULT_K) -> list[str]:
    """IDs of the `k` records that best match `query` by keywords alone.

    Records that share no keyword with the query are never returned, so the list
    can be shorter than `k`. Ties are broken by record ID, so the order is stable.
    """
    terms = set(tokenize(query))
    if not terms or not records:
        return []
    documents = [tokenize(record.content) for record in records]
    average_length = sum(len(tokens) for tokens in documents) / len(documents) or 1.0
    containing = Counter(term for tokens in documents for term in terms.intersection(tokens))

    scored: list[tuple[float, str]] = []
    for record, tokens in zip(records, documents, strict=True):
        counts = Counter(tokens)
        score = 0.0
        for term in terms:
            frequency = counts.get(term, 0)
            if not frequency:
                continue
            idf = math.log(1 + (len(documents) - containing[term] + 0.5) / (containing[term] + 0.5))
            length_norm = 1 - _B + _B * len(tokens) / average_length
            score += idf * frequency * (_K1 + 1) / (frequency + _K1 * length_norm)
        if score > 0:
            scored.append((score, record.record_id))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [record_id for _, record_id in scored[:k]]


@dataclass(frozen=True)
class RetrievalComparison:
    """Top-k hits for one question: keyword search next to the pipeline."""

    question_id: str
    expected: int
    keyword_missing: list[str]
    # None when there is no usable saved pipeline result for the question.
    pipeline_missing: list[str] | None
    # Why there is no usable result: "no result", "invalid result" or "wrong case".
    pipeline_problem: str | None = None

    @property
    def keyword_hits(self) -> int:
        return self.expected - len(self.keyword_missing)

    @property
    def pipeline_hits(self) -> int | None:
        return None if self.pipeline_missing is None else self.expected - len(self.pipeline_missing)


def compare_retrieval(
    question_set: QuestionSet,
    records: Sequence[EvidenceRecord],
    results: ResultSource | None = None,
    *,
    k: int = DEFAULT_K,
) -> list[RetrievalComparison]:
    """One row per question that has expected records. `results=None` is keyword-only.

    Only records and revisions of `question_set.case_id` are used.
    """
    case_id = question_set.case_id
    case_records = [record for record in records if record.case_id == case_id]
    rows: list[RetrievalComparison] = []
    for question in question_set.questions:
        expected = question.expected_record_ids
        if not expected:
            continue
        keyword_top = keyword_rank(question.question, case_records, k)
        pipeline_missing: list[str] | None = None
        problem: str | None = None
        if results is not None:
            pipeline_missing, problem = _pipeline_missing(
                results.base(question.question_id), case_id, expected, k
            )
        rows.append(
            RetrievalComparison(
                question_id=question.question_id,
                expected=len(expected),
                keyword_missing=[rid for rid in expected if rid not in keyword_top],
                pipeline_missing=pipeline_missing,
                pipeline_problem=problem,
            )
        )
    return rows


def _pipeline_missing(
    raw: object, case_id: str, expected: Sequence[str], k: int
) -> tuple[list[str] | None, str | None]:
    """Expected records missing from the saved revision's top k, or why it cannot be used."""
    if raw is None:
        return None, "no result"
    revision, _ = schema_errors(AnalysisRevision, raw)
    if revision is None:
        return None, "invalid result"
    if revision.case_id != case_id:
        return None, "wrong case"
    top = revision.retrieved_record_ids[:k]
    return [rid for rid in expected if rid not in top], None


def keyword_only_checks(
    question_set: QuestionSet, records: Sequence[EvidenceRecord], *, k: int = DEFAULT_K
) -> list[CheckResult]:
    """The runner's retrieval check, with keyword search in place of the pipeline."""
    checks = []
    for row in compare_retrieval(question_set, records, None, k=k):
        detail = f"{row.keyword_hits}/{row.expected} expected records in the keyword top {k}"
        if row.keyword_missing:
            detail += f"; missing {', '.join(row.keyword_missing)}"
        checks.append(
            CheckResult(row.question_id, "retrieval.keyword", not row.keyword_missing, detail)
        )
    return checks


def format_comparison(rows: Sequence[RetrievalComparison], k: int = DEFAULT_K) -> str:
    """A plain-text table: per question, then totals as a top-k hit rate."""
    if not rows:
        return "Retrieval comparison: no question lists expected records."
    width = max(len("question"), len("TOTAL"), *(len(row.question_id) for row in rows))
    lines = [
        f"Retrieval, top {k}: keyword-only search next to the pipeline",
        "(keyword: plain BM25, no stemming; not the pipeline's own keyword retriever)",
        f"{'question':<{width}}  expected  keyword  pipeline",
    ]
    for row in rows:
        pipeline = row.pipeline_problem or "no result"
        if row.pipeline_hits is not None:
            pipeline = f"{row.pipeline_hits}/{row.expected}"
        lines.append(
            f"{row.question_id:<{width}}  {row.expected:>8}  "
            f"{f'{row.keyword_hits}/{row.expected}':>7}  {pipeline:>8}"
        )
    total = sum(row.expected for row in rows)
    keyword_total = sum(row.keyword_hits for row in rows)
    scored = [row for row in rows if row.pipeline_hits is not None]
    pipeline_rate = "n/a"
    if scored:
        pipeline_rate = _rate(sum(row.pipeline_hits or 0 for row in scored), _expected(scored))
    lines.append(
        f"{'TOTAL':<{width}}  {total:>8}  {_rate(keyword_total, total):>7}  {pipeline_rate:>8}"
    )
    for row in rows:
        only_pipeline = [
            rid for rid in row.keyword_missing if rid not in (row.pipeline_missing or [])
        ]
        if row.pipeline_missing is not None and only_pipeline:
            lines.append(
                f"{row.question_id}: found by the pipeline but not by keyword search: "
                f"{', '.join(only_pipeline)}"
            )
        only_keyword = [
            rid for rid in (row.pipeline_missing or []) if rid not in row.keyword_missing
        ]
        if only_keyword:
            lines.append(
                f"{row.question_id}: found by keyword search but not by the pipeline: "
                f"{', '.join(only_keyword)}"
            )
    return "\n".join(lines)


def _expected(rows: Sequence[RetrievalComparison]) -> int:
    return sum(row.expected for row in rows)


def _rate(hits: int, total: int) -> str:
    return f"{round(100 * hits / total)}%" if total else "n/a"
