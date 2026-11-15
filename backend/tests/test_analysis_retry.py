"""BLA-18: one retry with the validation errors, then a partial result flagged for review."""

import copy
import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest
from _paths import CONTRACTS_DIR, RAW, load_json
from jsonschema import Draft202012Validator

from app.analysis_llm import AnalysisLLMError, FakeAnalysisLLM
from app.analysis_retry import (
    MAX_ECHOED_OUTPUT_CHARS,
    NO_ANALYSIS_NOTE,
    RevisionMetadata,
    analyze_with_retry,
)
from app.citations import ANSWER_REVIEW_NOTE
from app.contracts import EvidenceRecord
from app.retrieval import Excerpt, RetrievalResult, RetrievedRecord
from app.validation import validate_revision

REVISION_SCHEMA = Draft202012Validator(
    load_json(CONTRACTS_DIR / "analysis_revision.schema.json")
)
CASE = "synthetic-demo"
META = RevisionMetadata(
    analysis_id="analysis-test-1", created_at=datetime(2026, 11, 15, 2, 0, tzinfo=UTC)
)
OUTPUT_KEYS = ("answer", "claims", "graph", "timeline_events", "unresolved_questions")


def records() -> list[EvidenceRecord]:
    return [EvidenceRecord.model_validate(r) for r in RAW["records"]]


def retrieval(excluded: Sequence[str] = (), extra: Sequence[str] = ()) -> RetrievalResult:
    retrieved = []
    for record in records():
        if record.record_id not in excluded or record.record_id in extra:
            retrieved.append(
                RetrievedRecord(
                    record_id=record.record_id,
                    source_file=record.source_file,
                    source_locator=record.source_locator,
                    score=0.03,
                    matched_by="both",
                    excerpts=[
                        Excerpt(
                            text=record.content,
                            char_start=0,
                            char_end=len(record.content),
                            chunk_ids=["chk-1"],
                            score=0.03,
                        )
                    ],
                )
            )
    return RetrievalResult(
        case_id=CASE,
        question=RAW["revision"]["question"],
        fts_query='"alex" OR "casey"',
        records=retrieved,
        vector_candidates=2,
        keyword_candidates=2,
        fused_records=2,
        dropped_by_limit=0,
        dropped_by_budget=0,
        excerpt_chars=100,
        excluded_record_ids=sorted(excluded),
    )


def good() -> dict[str, Any]:
    """The fixture revision's LLM-authored fields: valid against the fixture corpus."""
    return {key: copy.deepcopy(RAW["revision"][key]) for key in OUTPUT_KEYS}


def fabricated(evidence_id: str = "rec-99") -> dict[str, Any]:
    """Valid JSON, but claim-1 cites a record that does not exist."""
    output = good()
    output["claims"][0]["evidence_ids"] = [evidence_id]
    return output


def duplicate_ids() -> dict[str, Any]:
    """Valid JSON and citations, but two claims share an ID (not repairable by stripping)."""
    output = good()
    output["claims"][1]["id"] = output["claims"][0]["id"]
    return output


BAD_JSON = '{"answer": "unterminated'
WRONG_SHAPE = json.dumps({"answer": "x", "claims": "not a list"})


def run(llm: Any, **kwargs: Any):
    result = analyze_with_retry(llm, retrieval(), records(), META, **kwargs)
    payload = result.revision.to_contract_json()
    errors = sorted(REVISION_SCHEMA.iter_errors(payload), key=str)
    assert not errors, [e.message for e in errors]
    assert validate_revision(records(), result.revision) == []  # always storable
    return result, payload


# --- First attempt passes ----------------------------------------------------------------


def test_a_valid_first_answer_is_used_without_a_retry(caplog) -> None:
    llm = FakeAnalysisLLM(responses=[good()])
    with caplog.at_level(logging.WARNING, logger="app.analysis_retry"):
        result, payload = run(llm)
    assert len(llm.calls) == 1
    assert not result.needs_review
    assert "needs_review" not in payload and "validation_errors" not in payload
    # Unchanged by sanitizing: exactly the model's findings.
    for key in OUTPUT_KEYS:
        assert payload[key] == good()[key]
    assert result.revision.analysis_id == "analysis-test-1"
    assert result.revision.retrieved_record_ids == ["rec-01", "rec-02"]
    assert [a.passed for a in result.attempts] == [True]
    assert caplog.records == []


# --- Retry -------------------------------------------------------------------------------


@pytest.mark.parametrize("first", [BAD_JSON, WRONG_SHAPE])
def test_a_schema_failure_is_retried_with_the_errors_and_can_recover(caplog, first) -> None:
    llm = FakeAnalysisLLM(responses=[first, good()])
    with caplog.at_level(logging.WARNING, logger="app.analysis_retry"):
        result, payload = run(llm)
    assert len(llm.calls) == 2
    assert not result.needs_review and "needs_review" not in payload
    first_call, retry_call = llm.calls
    # The retry is the original prompt plus the previous answer and the errors.
    assert retry_call[: len(first_call)] == first_call
    assert retry_call[len(first_call)] == {"role": "assistant", "content": first}
    feedback = retry_call[-1]
    assert feedback["role"] == "user"
    assert "failed server validation" in feedback["content"]
    for problem in result.attempts[0].schema_errors:
        assert problem in feedback["content"]
    assert [a.passed for a in result.attempts] == [False, True]
    assert "attempt 1 failed validation" in caplog.text


def test_a_citation_failure_is_retried_and_the_retry_can_recover(caplog) -> None:
    llm = FakeAnalysisLLM(responses=[fabricated(), good()])
    with caplog.at_level(logging.WARNING, logger="app.analysis_retry"):
        result, _ = run(llm)
    assert len(llm.calls) == 2
    assert not result.needs_review
    first = result.attempts[0]
    assert first.invalid_citations == 1 and first.dropped_findings == 1
    assert "claim claim-1: removed unknown evidence IDs: rec-99" in llm.calls[1][-1]["content"]
    # The previous answer is echoed as the model's own turn.
    assert json.loads(llm.calls[1][-2]["content"])["claims"][0]["evidence_ids"] == ["rec-99"]
    assert "rec-99" in caplog.text  # the log keeps the full detail


def test_there_is_exactly_one_retry() -> None:
    llm = FakeAnalysisLLM(responses=[BAD_JSON, BAD_JSON, good()])
    result, _ = run(llm)
    assert len(llm.calls) == 2
    assert result.needs_review


# --- Partial fallback ----------------------------------------------------------------------


def test_two_schema_failures_give_an_empty_result_flagged_for_review(caplog) -> None:
    llm = FakeAnalysisLLM(responses=[BAD_JSON, WRONG_SHAPE])
    with caplog.at_level(logging.WARNING, logger="app.analysis_retry"):
        result, payload = run(llm)
    assert payload["needs_review"] is True
    assert result.revision.answer == ""
    assert result.revision.claims == [] and result.revision.timeline_events == []
    assert result.revision.unresolved_questions == [NO_ANALYSIS_NOTE]
    assert payload["validation_errors"] == [
        "Attempt 1: the model's output did not match the analysis format (1 problem).",
        f"Attempt 2: the model's output did not match the analysis format "
        f"({len(result.attempts[1].schema_errors)} problem"
        f"{'' if len(result.attempts[1].schema_errors) == 1 else 's'}).",
    ]
    assert "attempt 2 failed validation" in caplog.text
    assert "returning a partial result flagged for review (no usable output)" in caplog.text


def test_when_both_answers_cite_badly_the_sanitized_retry_is_kept() -> None:
    second = fabricated("rec-77")
    second["answer"] = "Second answer."
    llm = FakeAnalysisLLM(responses=[fabricated(), second])
    result, payload = run(llm)
    assert result.needs_review
    assert result.revision.answer == "Second answer."  # the most recent usable attempt
    assert [c.id for c in result.revision.claims] == ["claim-2"]  # claim-1 was stripped
    assert ANSWER_REVIEW_NOTE in result.revision.unresolved_questions
    assert payload["validation_errors"] == [
        "Attempt 1: 1 citation to records outside the permitted evidence was removed.",
        "Attempt 1: 1 finding was dropped because no permitted evidence supports them, so "
        "the answer may overstate the evidence.",
        "Attempt 2: 1 citation to records outside the permitted evidence was removed.",
        "Attempt 2: 1 finding was dropped because no permitted evidence supports them, so "
        "the answer may overstate the evidence.",
    ]


def test_a_usable_first_answer_is_kept_when_the_retry_is_not_valid_json() -> None:
    llm = FakeAnalysisLLM(responses=[fabricated(), BAD_JSON])
    result, payload = run(llm)
    assert result.needs_review
    assert [c.id for c in result.revision.claims] == ["claim-2"]
    assert payload["validation_errors"][-1] == (
        "Attempt 2: the model's output did not match the analysis format (1 problem)."
    )


def test_structural_errors_that_stripping_cannot_fix_give_the_empty_fallback() -> None:
    llm = FakeAnalysisLLM(responses=[duplicate_ids(), duplicate_ids()])
    result, payload = run(llm)
    assert result.needs_review
    assert result.revision.unresolved_questions == [NO_ANALYSIS_NOTE]
    assert payload["validation_errors"] == [
        "Attempt 1: 1 structural problem remained (such as duplicate IDs or an exact time "
        "without a timestamp).",
        "Attempt 2: 1 structural problem remained (such as duplicate IDs or an exact time "
        "without a timestamp).",
    ]
    assert "duplicate claim IDs: claim-1" in result.attempts[0].revision_errors


def test_stored_validation_errors_never_quote_model_chosen_text(caplog) -> None:
    injected = "IGNORE-PREVIOUS-INSTRUCTIONS"
    first = fabricated(injected)
    second = good()
    second["claims"][0][injected.lower()] = "extra field named by the model"
    llm = FakeAnalysisLLM(responses=[first, json.dumps(second)])
    with caplog.at_level(logging.WARNING, logger="app.analysis_retry"):
        result, payload = run(llm)
    assert result.needs_review
    stored = " ".join(payload["validation_errors"])
    assert injected not in stored and injected.lower() not in stored
    # The detail still reaches the model on the retry and the log.
    assert injected in llm.calls[1][-1]["content"]
    assert injected.lower() in caplog.text


# --- Provider failures ---------------------------------------------------------------------


class FlakyLLM(FakeAnalysisLLM):
    """Fails with a provider error on the listed call numbers (1-based)."""

    fail_on: tuple[int, ...] = ()

    def generate(self, messages):
        if len(self.calls) + 1 in self.fail_on:
            self.calls.append([dict(m) for m in messages])
            raise AnalysisLLMError("Gemini analysis request failed: 503")
        return super().generate(messages)


def test_a_provider_failure_on_the_first_call_is_raised() -> None:
    llm = FlakyLLM(responses=[good()])
    llm.fail_on = (1,)
    with pytest.raises(AnalysisLLMError, match="503"):
        analyze_with_retry(llm, retrieval(), records(), META)
    assert len(llm.calls) == 1


def test_a_provider_failure_on_the_retry_keeps_the_usable_first_answer(caplog) -> None:
    llm = FlakyLLM(responses=[fabricated()])
    llm.fail_on = (2,)
    with caplog.at_level(logging.WARNING, logger="app.analysis_retry"):
        result, payload = run(llm)
    assert result.needs_review
    assert [c.id for c in result.revision.claims] == ["claim-2"]
    assert payload["validation_errors"][-1] == (
        "Attempt 2: the model could not be reached for the retry."
    )
    assert "attempt 2 failed: provider error" in caplog.text


def test_a_provider_failure_on_the_retry_after_unusable_output_is_raised() -> None:
    llm = FlakyLLM(responses=[BAD_JSON])
    llm.fail_on = (2,)
    with pytest.raises(AnalysisLLMError, match="503"):
        analyze_with_retry(llm, retrieval(), records(), META)


# --- Setup and inputs ----------------------------------------------------------------------


def test_an_inconsistent_retrieval_set_fails_before_any_llm_call() -> None:
    llm = FakeAnalysisLLM(responses=[good()])
    corpus = [r for r in records() if r.record_id != "rec-02"]  # rec-02 retrieved, not stored
    with pytest.raises(ValueError, match="corpus or retrieval set is inconsistent"):
        analyze_with_retry(llm, retrieval(), corpus, META)
    assert llm.calls == []


def test_excluded_records_are_never_retrieved_prompted_or_citable() -> None:
    llm = FakeAnalysisLLM(responses=[good(), good()])
    # rec-02 is excluded but (by an upstream bug) still in the retrieval list.
    result = analyze_with_retry(
        llm, retrieval(excluded=["rec-02"], extra=["rec-02"]), records(), META
    )
    assert result.revision.excluded_record_ids == ["rec-02"]
    assert result.revision.retrieved_record_ids == ["rec-01"]
    prompt = json.loads(llm.calls[0][1]["content"])
    assert [r["record_id"] for r in prompt["source_records"]] == ["rec-01"]
    # The fixture answer cites rec-02, so it is stripped and the result is flagged.
    assert result.needs_review
    assert all("rec-02" not in c.evidence_ids for c in result.revision.claims)
    assert validate_revision(records(), result.revision) == []

    # An exclusion passed by the caller counts too.
    llm = FakeAnalysisLLM(responses=[good(), good()])
    result = analyze_with_retry(
        llm, retrieval(), records(), META, excluded_record_ids=["rec-02"]
    )
    assert result.revision.excluded_record_ids == ["rec-02"]
    assert result.revision.retrieved_record_ids == ["rec-01"]


def test_parent_and_timestamps_come_from_the_server() -> None:
    meta = RevisionMetadata(
        analysis_id="analysis-child",
        created_at=datetime(2026, 11, 15, 3, 0, tzinfo=UTC),
        parent_analysis_id="analysis-test-1",
    )
    llm = FakeAnalysisLLM(responses=[good()])
    result = analyze_with_retry(llm, retrieval(), records(), meta)
    assert result.revision.analysis_id == "analysis-child"
    assert result.revision.parent_analysis_id == "analysis-test-1"
    assert result.revision.created_at == datetime(2026, 11, 15, 3, 0, tzinfo=UTC)
    assert result.revision.question == RAW["revision"]["question"]


def test_a_huge_previous_answer_is_cut_before_it_is_echoed() -> None:
    huge = '{"answer": "' + "x" * (MAX_ECHOED_OUTPUT_CHARS * 2)
    llm = FakeAnalysisLLM(responses=[huge, good()])
    run(llm)
    assert len(llm.calls[1][-2]["content"]) == MAX_ECHOED_OUTPUT_CHARS
