"""Tests for the support diff between revisions (BLA-34), using the synthetic fixtures."""

import copy
from datetime import UTC, datetime

import pytest
from _paths import RAW

from app.comparison import compare_revisions
from app.contracts import AnalysisRevision, RevisionComparison
from app.validation import validate_comparison

FORBIDDEN_WORDING = ("disproved", "disproven", "is false", "was false", "never happened")


def revision(raw: dict) -> AnalysisRevision:
    return AnalysisRevision.model_validate(raw)


@pytest.fixture
def base_raw() -> dict:
    return copy.deepcopy(RAW["revision"])


@pytest.fixture
def child_raw() -> dict:
    return copy.deepcopy(RAW["child"])


def statuses(comparison: RevisionComparison) -> dict[tuple[str, str | None, str | None], str]:
    return {
        (item.kind, item.base_ref_id, item.compared_ref_id): item.support_status
        for item in comparison.items
    }


def compare(base_raw: dict, child_raw: dict) -> RevisionComparison:
    base, child = revision(base_raw), revision(child_raw)
    comparison = compare_revisions(base, child, created_at=datetime(2026, 11, 14, tzinfo=UTC))
    # Every comparison this module produces must satisfy the server-side invariants.
    assert validate_comparison([base, child], comparison) == []
    return comparison


def with_second_source(base_raw: dict) -> None:
    """Give claim-2, edge-2 and event-2 an independent second source, rec-01."""
    base_raw["claims"][1]["evidence_ids"] = ["rec-02", "rec-01"]
    base_raw["graph"]["edges"][1]["evidence_ids"] = ["rec-02", "rec-01"]
    base_raw["timeline_events"][1]["evidence_ids"] = ["rec-02", "rec-01"]


def test_matches_the_hand_written_fixture_comparison(base_raw, child_raw) -> None:
    comparison = compare(base_raw, child_raw)
    expected = RevisionComparison.model_validate(copy.deepcopy(RAW["comparison"]))
    assert statuses(comparison) == statuses(expected)
    assert comparison.excluded_record_ids == expected.excluded_record_ids == ["rec-02"]
    assert comparison.base_analysis_id == "analysis-synthetic-1"
    assert comparison.compared_analysis_id == "analysis-synthetic-2"
    by_ref = {(i.kind, i.base_ref_id): i for i in comparison.items}
    assert by_ref[("claim", "claim-1")].explanation == (
        "Still supported by rec-01, which was not excluded."
    )
    assert by_ref[("claim", "claim-2")].explanation == (
        "Its only supporting record, rec-02, is excluded, so the active analysis can no "
        "longer conclude this. The exclusion does not show it did not happen."
    )


def test_every_claim_edge_and_event_is_classified(base_raw, child_raw) -> None:
    comparison = compare(base_raw, child_raw)
    counts = {kind: 0 for kind in ("claim", "edge", "event")}
    for item in comparison.items:
        counts[item.kind] += 1
    assert counts == {"claim": 2, "edge": 2, "event": 2}


def test_a_finding_with_a_second_source_is_weakened_not_erased(base_raw, child_raw) -> None:
    with_second_source(base_raw)
    # The child keeps the finding, now resting on rec-01 alone.
    child_raw["claims"].append({**base_raw["claims"][1], "evidence_ids": ["rec-01"]})
    child_raw["graph"]["nodes"] = copy.deepcopy(base_raw["graph"]["nodes"])
    edge_2 = {**base_raw["graph"]["edges"][1], "evidence_ids": ["rec-01"]}
    child_raw["graph"]["edges"].append(edge_2)
    comparison = compare(base_raw, child_raw)
    result = statuses(comparison)
    assert result[("claim", "claim-2", "claim-2")] == "weakened"
    assert result[("edge", "edge-2", "edge-2")] == "weakened"
    edge = next(i for i in comparison.items if i.kind == "edge" and i.base_ref_id == "edge-2")
    assert edge.compared_evidence_ids == ["rec-01"]
    assert edge.explanation == (
        "Still supported by rec-01, but it no longer rests on rec-02, so it has fewer "
        "supporting records than before."
    )


def test_a_dropped_finding_with_a_surviving_source_is_unresolved(base_raw, child_raw) -> None:
    with_second_source(base_raw)
    # The child drops edge-2 entirely even though rec-01 is still permitted.
    comparison = compare(base_raw, child_raw)
    result = statuses(comparison)
    assert result[("edge", "edge-2", None)] == "unresolved"
    assert result[("claim", "claim-2", None)] == "unresolved"
    assert "not_supported" not in result.values()
    edge = next(i for i in comparison.items if i.kind == "edge" and i.base_ref_id == "edge-2")
    assert "rec-01 is still permitted" in edge.explanation


def test_a_claim_the_child_marks_unresolved_is_unresolved(base_raw, child_raw) -> None:
    child_raw["claims"][0]["status"] = "unresolved"
    comparison = compare(base_raw, child_raw)
    assert statuses(comparison)[("claim", "claim-1", "claim-1")] == "unresolved"


def test_a_finding_only_in_the_child_is_newly_supported(base_raw, child_raw) -> None:
    child_raw["claims"].append(
        {"id": "claim-9", "text": "New.", "evidence_ids": ["rec-01"], "status": "inferred"}
    )
    base_raw["claims"] = [base_raw["claims"][1]]  # the base no longer has claim-1
    comparison = compare(base_raw, child_raw)
    new = [i for i in comparison.items if i.support_status == "newly_supported"]
    assert [(i.kind, i.base_ref_id) for i in new] == [("claim", None), ("claim", None)]
    assert sorted(i.compared_ref_id for i in new) == ["claim-1", "claim-9"]
    assert all(i.base_evidence_ids == [] for i in new)


def test_findings_are_paired_by_evidence_when_ids_change(base_raw, child_raw) -> None:
    # A second LLM run numbers its findings differently.
    child_raw["claims"][0]["id"] = "claim-a"
    child_raw["timeline_events"][0]["id"] = "event-a"
    child_raw["graph"]["edges"][0]["id"] = "edge-a"
    result = statuses(compare(base_raw, child_raw))
    assert result[("claim", "claim-1", "claim-a")] == "still_supported"
    assert result[("event", "event-1", "event-a")] == "still_supported"
    assert result[("edge", "edge-1", "edge-a")] == "still_supported"
    assert "newly_supported" not in result.values()


def test_a_reused_id_with_different_evidence_is_not_treated_as_the_same_finding(
    base_raw, child_raw
) -> None:
    # The child reuses "claim-2" for an unrelated claim resting on rec-01.
    child_raw["claims"] = [
        {"id": "claim-2", "text": "Unrelated.", "evidence_ids": ["rec-01"], "status": "inferred"}
    ]
    result = statuses(compare(base_raw, child_raw))
    assert result[("claim", "claim-1", "claim-2")] == "still_supported"
    assert result[("claim", "claim-2", None)] == "not_supported"


def test_explanations_never_claim_something_did_not_happen(base_raw, child_raw) -> None:
    with_second_source(base_raw)
    variants = [compare(base_raw, child_raw), compare(copy.deepcopy(RAW["revision"]), child_raw)]
    for comparison in variants:
        for item in comparison.items:
            assert item.explanation
            lowered = item.explanation.lower()
            assert not any(phrase in lowered for phrase in FORBIDDEN_WORDING)
            if "did not happen" in lowered:
                assert "does not show it did not happen" in lowered


def test_the_comparison_is_deterministic(base_raw, child_raw) -> None:
    first = compare(base_raw, child_raw)
    second = compare(base_raw, child_raw)
    assert first == second


def test_only_a_parent_and_its_child_in_one_case_can_be_compared(base_raw, child_raw) -> None:
    child_raw["parent_analysis_id"] = "some-other-analysis"
    with pytest.raises(ValueError, match="is not a child of"):
        compare_revisions(revision(base_raw), revision(child_raw))

    child_raw = copy.deepcopy(RAW["child"])
    child_raw["case_id"] = "another-case"
    with pytest.raises(ValueError, match="different cases"):
        compare_revisions(revision(base_raw), revision(child_raw))


def test_created_at_defaults_to_an_offset_aware_time(base_raw, child_raw) -> None:
    comparison = compare_revisions(revision(base_raw), revision(child_raw))
    assert comparison.created_at.tzinfo is not None
    assert comparison.to_contract_json()["schema_version"] == "1.0"
