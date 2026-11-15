"""Validated analysis with one retry and a partial fallback (BLA-18).

``analyze_with_retry`` turns retrieved evidence into a saved-ready ``AnalysisRevision``:

1. Ask the LLM once (``app.analysis_llm``) and build the revision from its output plus the
   server-assigned fields.
2. Validate it. Schema problems come from parsing the output; source problems from
   ``app.citations.sanitize_citations`` (citations outside the permitted evidence, dropped
   findings, structural errors such as duplicate IDs).
3. If anything failed, ask once more, with the previous output and the validation errors
   included, so the model can correct itself.
4. If the second answer still fails, return a partial result flagged for review
   (``needs_review: true``) instead of an error: the most recent attempt that could be
   sanitized into a valid revision, or, if neither could, an empty revision that says no
   analysis could be produced.

Every failed attempt is logged (logger ``app.analysis_retry``) with its full validation
errors.

What is stored on the revision. ``validation_errors`` must be server-generated, never
LLM-authored (contracts/README.md), but detailed errors quote IDs and field names the
model chose. So the revision gets fixed, server-worded summaries with counts; the detailed
errors go to the log, to the retry prompt and to ``AnalysisResult.attempts``.

Not retried: provider failures (``AnalysisLLMError`` other than ``AnalysisOutputError``;
the SDK already retries those) and an inconsistent corpus or retrieval set (a server bug,
raised as ``ValueError`` before any LLM call). A provider failure on the retry falls back
to the first attempt's partial result, flagged for review.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.analysis_llm import (
    EMPTY_OUTPUT,
    AnalysisLLM,
    AnalysisLLMError,
    AnalysisOutput,
    AnalysisOutputError,
    build_analysis_messages,
    record_metadata,
)
from app.citations import sanitize_citations
from app.contracts import AnalysisRevision, EvidenceRecord
from app.retrieval import RetrievalResult
from app.validation import permitted_evidence_ids, validate_revision

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2  # the first answer and one retry
# The previous output is echoed back on the retry; cap it so a runaway answer cannot blow
# up the second prompt.
MAX_ECHOED_OUTPUT_CHARS = 50_000

NO_ANALYSIS_NOTE = (
    "No analysis could be produced: the model's output failed validation twice. "
    "Ask the question again or review the evidence directly."
)


@dataclass(frozen=True)
class RevisionMetadata:
    """Server-assigned revision fields; the rest come from the retrieval and the LLM."""

    analysis_id: str
    created_at: datetime
    parent_analysis_id: str | None = None


@dataclass(frozen=True)
class AttemptReport:
    """What went wrong in one LLM attempt (detailed; may quote model-chosen text)."""

    number: int
    schema_errors: list[str] = field(default_factory=list)
    citation_warnings: list[str] = field(default_factory=list)
    revision_errors: list[str] = field(default_factory=list)
    provider_error: str | None = None
    invalid_citations: int = 0
    dropped_findings: int = 0

    @property
    def passed(self) -> bool:
        return not (
            self.schema_errors
            or self.citation_warnings
            or self.revision_errors
            or self.provider_error
        )

    @property
    def problems(self) -> list[str]:
        """Every detailed problem, for the log and the retry prompt."""
        provider = [f"provider error: {self.provider_error}"] if self.provider_error else []
        return [*self.schema_errors, *self.citation_warnings, *self.revision_errors, *provider]


@dataclass(frozen=True)
class AnalysisResult:
    revision: AnalysisRevision
    attempts: list[AttemptReport]

    @property
    def needs_review(self) -> bool:
        return bool(self.revision.needs_review)


@dataclass
class _Attempt:
    report: AttemptReport
    revision: AnalysisRevision | None  # sanitized and valid, or None if unusable
    raw: str | None  # what the model returned, to echo back on the retry


def analyze_with_retry(
    llm: AnalysisLLM,
    retrieval: RetrievalResult,
    records: Sequence[EvidenceRecord],
    metadata: RevisionMetadata,
    *,
    session: Session | None = None,
    excluded_record_ids: Iterable[str] = (),
    prior_analysis: str | None = None,
) -> AnalysisResult:
    """Analyze ``retrieval.question`` with at most two LLM calls; see the module docstring.

    ``records`` is the case corpus, as for ``validate_revision`` (it must contain every
    retrieved record). Excluded IDs are ``retrieval.excluded_record_ids`` plus
    ``excluded_record_ids``. Raises ``ValueError`` if the corpus or retrieval set is
    inconsistent, and ``AnalysisLLMError`` if the provider fails before any usable answer.
    """
    excluded = sorted({*retrieval.excluded_record_ids, *excluded_record_ids})
    retrieved = [r.record_id for r in retrieval.records if r.record_id not in set(excluded)]
    base_fields = {
        "schema_version": "1.0",
        "analysis_id": metadata.analysis_id,
        "parent_analysis_id": metadata.parent_analysis_id,
        "case_id": retrieval.case_id,
        "question": retrieval.question,
        "excluded_record_ids": excluded,
        "retrieved_record_ids": retrieved,
        "created_at": metadata.created_at.isoformat(),
    }
    setup_errors = validate_revision(records, _revision(base_fields, EMPTY_OUTPUT))
    if setup_errors:
        raise ValueError(
            "cannot analyze: the corpus or retrieval set is inconsistent: "
            + "; ".join(setup_errors)
        )

    metadata_by_id = (
        record_metadata(session, retrieval.case_id, retrieved) if session is not None else None
    )
    messages = build_analysis_messages(
        retrieval,
        excluded_record_ids=excluded,
        prior_analysis=prior_analysis,
        metadata_by_id=metadata_by_id,
    )

    attempts: list[_Attempt] = []
    for number in range(1, MAX_ATTEMPTS + 1):
        if attempts:
            messages = [*messages, *_retry_messages(attempts[-1])]
        try:
            attempt = _attempt(number, llm, messages, records, base_fields)
        except AnalysisLLMError as error:  # provider failure, not invalid output
            usable = [a for a in attempts if a.revision is not None]
            if not usable:
                raise
            report = AttemptReport(number=number, provider_error=str(error))
            logger.warning(
                "analysis %s attempt %d failed: provider error: %s",
                metadata.analysis_id,
                number,
                error,
            )
            attempts.append(_Attempt(report=report, revision=None, raw=None))
            break
        attempts.append(attempt)
        if attempt.report.passed:
            assert attempt.revision is not None
            return AnalysisResult(attempt.revision, [a.report for a in attempts])
        logger.warning(
            "analysis %s attempt %d failed validation: %s",
            metadata.analysis_id,
            number,
            "; ".join(attempt.report.problems),
        )

    reports = [a.report for a in attempts]
    usable = [a for a in attempts if a.revision is not None]
    if usable:
        partial = usable[-1].revision
    else:
        partial = _revision(base_fields, EMPTY_OUTPUT).model_copy(
            update={"unresolved_questions": [NO_ANALYSIS_NOTE]}
        )
    assert partial is not None
    flagged = partial.model_copy(
        update={"needs_review": True, "validation_errors": _review_summaries(reports)}
    )
    logger.warning(
        "analysis %s still failed validation after %d attempts; returning a partial "
        "result flagged for review (%s)",
        metadata.analysis_id,
        len(attempts),
        "from attempt " + str(attempts.index(usable[-1]) + 1) if usable else "no usable output",
    )
    return AnalysisResult(_reparsed(flagged), reports)


def _attempt(
    number: int,
    llm: AnalysisLLM,
    messages: Sequence[dict[str, str]],
    records: Sequence[EvidenceRecord],
    base_fields: dict[str, Any],
) -> _Attempt:
    try:
        output = llm.generate(messages)
    except AnalysisOutputError as error:
        report = AttemptReport(number=number, schema_errors=list(error.errors) or [str(error)])
        return _Attempt(report=report, revision=None, raw=error.raw)

    revision = _revision(base_fields, output)
    permitted = permitted_evidence_ids(records, revision)
    invalid = sum(
        1
        for item in (
            *revision.claims,
            *revision.graph.edges,
            *revision.graph.nodes,
            *revision.timeline_events,
        )
        for rid in (item.evidence_ids or [])
        if rid not in permitted
    )
    sanitized = sanitize_citations(records, revision)
    remaining = validate_revision(records, sanitized.analysis)
    report = AttemptReport(
        number=number,
        citation_warnings=list(sanitized.warnings),
        revision_errors=list(dict.fromkeys([*sanitized.errors, *remaining])),
        invalid_citations=invalid,
        dropped_findings=sanitized.dropped_findings,
    )
    usable = None if remaining else _reparsed(sanitized.analysis)
    return _Attempt(report=report, revision=usable, raw=json.dumps(output.to_contract_json()))


def _revision(base_fields: dict[str, Any], output: AnalysisOutput) -> AnalysisRevision:
    return AnalysisRevision.model_validate_json(
        json.dumps({**output.to_contract_json(), **base_fields})
    )


def _reparsed(revision: AnalysisRevision) -> AnalysisRevision:
    """Round-trip through contract JSON, so the result is exactly what would be stored."""
    return AnalysisRevision.model_validate_json(json.dumps(revision.to_contract_json()))


def _retry_messages(previous: _Attempt) -> list[dict[str, str]]:
    """The previous answer (as the model's turn) and the errors to fix (as the user's)."""
    turns: list[dict[str, str]] = []
    if previous.raw and previous.raw.strip():
        turns.append({"role": "assistant", "content": previous.raw[:MAX_ECHOED_OUTPUT_CHARS]})
    problems = "\n".join(f"- {problem}" for problem in previous.report.problems)
    turns.append(
        {
            "role": "user",
            "content": (
                "Your previous response failed server validation:\n"
                f"{problems}\n\n"
                "Return one corrected JSON object for the same question and the same "
                "source_records. Cite only record_id values that appear in source_records; "
                "every claim, edge and event needs at least one. Every edge source and "
                "target must be a node id. Use unique ids. Do not invent timestamps or "
                "precision. Do not include Markdown fences."
            ),
        }
    )
    return turns


def _review_summaries(reports: Sequence[AttemptReport]) -> list[str]:
    """Server-worded reasons for ``needs_review``: counts only, never model-chosen text."""
    lines: list[str] = []
    for report in reports:
        prefix = f"Attempt {report.number}:"
        if report.provider_error:
            lines.append(f"{prefix} the model could not be reached for the retry.")
            continue
        if report.schema_errors:
            lines.append(
                f"{prefix} the model's output did not match the analysis format "
                f"({_count(len(report.schema_errors), 'problem')})."
            )
            continue
        if report.invalid_citations:
            lines.append(
                f"{prefix} {_count(report.invalid_citations, 'citation')} to records outside "
                f"the permitted evidence {_were(report.invalid_citations)} removed."
            )
        if report.dropped_findings:
            lines.append(
                f"{prefix} {_count(report.dropped_findings, 'finding')} "
                f"{_were(report.dropped_findings)} dropped because no permitted evidence "
                "supports them, so the answer may overstate the evidence."
            )
        if report.revision_errors:
            lines.append(
                f"{prefix} {_count(len(report.revision_errors), 'structural problem')} remained "
                "(such as duplicate IDs or an exact time without a timestamp)."
            )
        if not (report.invalid_citations or report.dropped_findings or report.revision_errors):
            lines.append(f"{prefix} the output needed corrections before it could be used.")
    return lines


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _were(n: int) -> str:
    return "was" if n == 1 else "were"
