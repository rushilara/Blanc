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
        "Still supported by rec-01, but it no longer rests on rec-02, which is excluded."
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


def test_findings_keep_their_identity_when_ids_are_regenerated(base_raw, child_raw) -> None:
    # A second LLM run numbers its findings differently but words them the same.
    child_raw["claims"][0]["id"] = "claim-a"
    child_raw["timeline_events"][0]["id"] = "event-a"
    child_raw["graph"]["edges"][0]["id"] = "edge-a"
    result = statuses(compare(base_raw, child_raw))
    assert result[("claim", "claim-1", "claim-a")] == "still_supported"
    assert result[("event", "event-1", "event-a")] == "still_supported"
    assert result[("edge", "edge-1", "edge-a")] == "still_supported"
    assert "newly_supported" not in result.values()


def test_a_light_rewording_is_still_the_same_finding(base_raw, child_raw) -> None:
    child_raw["claims"][0]["id"] = "claim-a"
    child_raw["claims"][0]["text"] = base_raw["claims"][0]["text"].rstrip(".") + " ."
    result = statuses(compare(base_raw, child_raw))
    assert result[("claim", "claim-1", "claim-a")] == "still_supported"


TRANSFER = "Alice transferred the money to Bob on Tuesday."


def restated(base_raw: dict, child_raw: dict, text: str, child_text: str) -> dict:
    """Base claim-1 says `text`; the child replaces it with `child_text` on the same record."""
    base_raw["claims"][0]["text"] = text
    child_raw["claims"][0].update(id="claim-a", text=child_text)
    return statuses(compare(base_raw, child_raw))


@pytest.mark.parametrize(
    ("text", "child_text"),
    [
        # Negation: one word reverses the finding.
        (TRANSFER, TRANSFER.replace("transferred", "never transferred")),
        ("Alex sent the document to Jordan.", "Alex did not send the document to Jordan."),
        # Changed amount, date and person.
        (TRANSFER.replace("the money", "$5,000"), TRANSFER.replace("the money", "$9,000")),
        (TRANSFER, TRANSFER.replace("Tuesday", "Thursday")),
        (TRANSFER, TRANSFER.replace("Bob", "Rob")),
        # Swapped roles: the same words in a different order.
        ("Alice paid Bob.", "Bob paid Alice."),
        # Even a harmless-looking rewording is not taken as proof of identity.
        (
            "Alex emails Jordan about a document on Friday.",
            "Alex emails Jordan about the document on Friday.",
        ),
    ],
)
def test_similar_wording_is_not_the_same_finding(base_raw, child_raw, text, child_text) -> None:
    result = restated(base_raw, child_raw, text, child_text)
    # Both are shown: the base claim is not reported as surviving.
    assert result[("claim", "claim-1", None)] == "unresolved"
    assert result[("claim", None, "claim-a")] == "newly_supported"
    assert "still_supported" not in [v for (kind, _, _), v in result.items() if kind == "claim"]


def test_an_event_at_a_different_time_is_not_the_same_event(base_raw, child_raw) -> None:
    event = child_raw["timeline_events"][0]
    assert event["description"] == base_raw["timeline_events"][0]["description"]
    event.update(id="event-a", timestamp="2026-04-11T09:15:00Z")
    result = statuses(compare(base_raw, child_raw))
    assert result[("event", "event-1", None)] == "unresolved"
    assert result[("event", None, "event-a")] == "newly_supported"

    # The same description and timestamp, with spacing differences, is the same event.
    timestamp = base_raw["timeline_events"][0]["timestamp"]
    child_raw["timeline_events"][0]["timestamp"] = f" {timestamp} "
    result = statuses(compare(base_raw, child_raw))
    assert result[("event", "event-1", "event-a")] == "still_supported"


def test_a_finding_whose_records_were_replaced_is_weakened(base_raw, child_raw) -> None:
    # Base cites [rec-01, rec-02]; rec-02 is excluded and the child cites [rec-01, rec-03].
    base_raw["claims"][0]["evidence_ids"] = ["rec-01", "rec-02"]
    child_raw["claims"][0]["evidence_ids"] = ["rec-01", "rec-03"]
    comparison = compare(base_raw, child_raw)
    item = next(i for i in comparison.items if i.base_ref_id == "claim-1")
    assert item.support_status == "weakened"
    assert item.explanation == (
        "Still supported by rec-01, rec-03, but it no longer rests on rec-02, which is excluded."
    )

    # A record the child stops citing without it being excluded is named too.
    base_raw["claims"][0]["evidence_ids"] = ["rec-01", "rec-02", "rec-03"]
    child_raw["claims"][0]["evidence_ids"] = ["rec-01"]
    comparison = compare(base_raw, child_raw)
    item = next(i for i in comparison.items if i.base_ref_id == "claim-1")
    assert item.support_status == "weakened"
    assert item.explanation == (
        "Still supported by rec-01, but it no longer rests on rec-02, which is excluded; "
        "rec-03, which the active analysis no longer cites."
    )


def test_sharing_a_record_does_not_make_two_findings_the_same(base_raw, child_raw) -> None:
    # The child drops claim-1 and adds an unrelated claim that happens to cite rec-01.
    child_raw["claims"] = [
        {
            "id": "completely-new-claim",
            "text": "The delivery was scheduled before the quarterly audit.",
            "evidence_ids": ["rec-01"],
            "status": "inferred",
        }
    ]
    comparison = compare(base_raw, child_raw)
    result = statuses(comparison)
    # The vanished conclusion is not shown as surviving, and the new one is not hidden.
    assert result[("claim", "claim-1", None)] == "unresolved"
    assert result[("claim", None, "completely-new-claim")] == "newly_supported"
    assert result[("claim", "claim-2", None)] == "not_supported"
    vanished = next(i for i in comparison.items if i.base_ref_id == "claim-1")
    assert "no finding in the active analysis could be identified as this one" in (
        vanished.explanation
    )


def test_a_reused_id_does_not_make_two_findings_the_same(base_raw, child_raw) -> None:
    # The child reuses the ID "claim-1" for a different claim resting on the same record.
    child_raw["claims"] = [
        {"id": "claim-1", "text": "Unrelated.", "evidence_ids": ["rec-01"], "status": "inferred"}
    ]
    result = statuses(compare(base_raw, child_raw))
    assert result[("claim", "claim-1", None)] == "unresolved"
    assert result[("claim", None, "claim-1")] == "newly_supported"


def test_two_findings_on_one_record_are_paired_by_wording_not_order(
    base_raw, child_raw
) -> None:
    first = {"id": "claim-1", "text": "Alex wrote to Jordan.", "evidence_ids": ["rec-01"]}
    second = {"id": "claim-2", "text": "A document was due on Friday.", "evidence_ids": ["rec-01"]}
    base_raw["claims"] = [{**first, "status": "reported"}, {**second, "status": "reported"}]
    base_events = base_raw["timeline_events"]
    base_events[1]["evidence_ids"] = ["rec-01"]
    # The child regenerates IDs and lists the same findings in the opposite order.
    child_raw["claims"] = [
        {**second, "id": "claim-x", "status": "reported"},
        {**first, "id": "claim-y", "status": "reported"},
    ]
    child_raw["timeline_events"] = [
        {**base_events[1], "id": "event-x"},
        {**base_events[0], "id": "event-y"},
    ]
    result = statuses(compare(base_raw, child_raw))
    assert result[("claim", "claim-1", "claim-y")] == "still_supported"
    assert result[("claim", "claim-2", "claim-x")] == "still_supported"
    assert result[("event", "event-1", "event-y")] == "still_supported"
    assert result[("event", "event-2", "event-x")] == "still_supported"


def test_support_is_counted_in_unique_records(base_raw, child_raw) -> None:
    # The contract allows a finding to cite the same record twice.
    base_raw["claims"][0]["evidence_ids"] = ["rec-01", "rec-01"]
    comparison = compare(base_raw, child_raw)
    item = next(i for i in comparison.items if i.base_ref_id == "claim-1")
    assert item.support_status == "still_supported"  # one record before, one after
    assert item.base_evidence_ids == ["rec-01"]

    child_raw["claims"][0]["evidence_ids"] = ["rec-01", "rec-01"]
    base_raw["claims"][0]["evidence_ids"] = ["rec-01", "rec-02", "rec-02"]
    comparison = compare(base_raw, child_raw)
    item = next(i for i in comparison.items if i.base_ref_id == "claim-1")
    assert item.support_status == "weakened"  # two records before, one after
    assert item.compared_evidence_ids == ["rec-01"]


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
    stamp = datetime(2026, 11, 14, tzinfo=UTC)
    first = compare_revisions(revision(base_raw), revision(child_raw), created_at=stamp)
    assert first == compare_revisions(revision(base_raw), revision(child_raw), created_at=stamp)
    assert comparison.to_contract_json()["schema_version"] == "1.0"
