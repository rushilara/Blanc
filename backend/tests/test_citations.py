"""Citation sanitizing tests (BLA-17). Each test mutates the raw fixture JSON, parses it
through the contract models (as the server would), runs ``sanitize_citations`` and checks
that the result passes ``validate_revision``."""

from copy import deepcopy
from typing import Any

import pytest

from app.citations import ANSWER_REVIEW_NOTE, SanitizeResult, sanitize_citations
from app.contracts import AnalysisRevision, EvidenceRecord
from app.validation import schema_errors, validate_revision

Raw = dict[str, Any]

EXCLUDED_CONTENT = "Jordan tells Casey there may be a meeting that afternoon."


def records_of(raw: Raw) -> list[EvidenceRecord]:
    return [EvidenceRecord.model_validate(r) for r in raw["records"]]


def sanitize(raw: Raw, key: str = "revision") -> tuple[AnalysisRevision, SanitizeResult]:
    analysis = AnalysisRevision.model_validate(raw[key])
    before = analysis.model_copy(deep=True)
    result = sanitize_citations(records_of(raw), analysis)
    assert analysis == before, "sanitize_citations must not modify its input"
    if not result.errors:
        assert validate_revision(records_of(raw), result.analysis) == []
    return analysis, result


def ids(items: list[Any]) -> list[str]:
    return [i.id for i in items]


def assert_warning(warnings: list[str], *fragments: str) -> None:
    assert any(all(f in w for f in fragments) for w in warnings), warnings


def other_case_record(raw: Raw, record_id: str = "rec-other") -> Raw:
    record = deepcopy(raw["records"][0])
    record.update(record_id=record_id, case_id="other-case", content="Another case's secret.")
    return record


class TestUnchanged:
    @pytest.mark.parametrize("key", ["revision", "child"])
    def test_valid_fixture_round_trips(self, raw: Raw, key: str) -> None:
        analysis, result = sanitize(raw, key)
        assert result.analysis == analysis
        assert result.analysis is not analysis
        assert result.warnings == []
        assert result.errors == []

    def test_output_is_independent_of_input(self, raw: Raw) -> None:
        analysis, result = sanitize(raw)
        result.analysis.claims[0].evidence_ids.append("rec-x")
        assert analysis.claims[0].evidence_ids == ["rec-01"]

    def test_node_without_evidence_is_left_alone(self, raw: Raw) -> None:
        raw["revision"]["graph"]["nodes"].append({"id": "pat", "label": "Pat", "type": "person"})
        analysis, result = sanitize(raw)
        assert result.analysis == analysis
        assert result.warnings == []


class TestFabricatedIds:
    @pytest.mark.parametrize(
        ("path", "kind"),
        [("claims", "claim"), ("graph.edges", "edge"), ("timeline_events", "event")],
    )
    def test_partial_support_keeps_item_with_valid_ids(
        self, raw: Raw, path: str, kind: str
    ) -> None:
        items: Any = raw["revision"]
        for part in path.split("."):
            items = items[part]
        items[0]["evidence_ids"] = ["rec-fabricated", "rec-01"]
        _, result = sanitize(raw)
        out: Any = result.analysis
        for part in path.split("."):
            out = getattr(out, part)
        assert out[0].evidence_ids == ["rec-01"]
        assert_warning(result.warnings, f"{kind} {items[0]['id']}", "unknown", "rec-fabricated")
        assert result.errors == []

    def test_zero_support_drops_claim_and_adds_note(self, raw: Raw) -> None:
        raw["revision"]["claims"][0]["evidence_ids"] = ["rec-fabricated"]
        _, result = sanitize(raw)
        assert ids(result.analysis.claims) == ["claim-2"]
        assert_warning(result.warnings, "claim claim-1 dropped")
        note = result.analysis.unresolved_questions[-1]
        assert "Alex communicated with Jordan" in note
        assert "rec-fabricated" not in note
        assert result.analysis.unresolved_questions[1:] == [ANSWER_REVIEW_NOTE, note]

    def test_zero_support_drops_event_and_adds_note(self, raw: Raw) -> None:
        raw["revision"]["timeline_events"][1]["evidence_ids"] = ["rec-made-up"]
        _, result = sanitize(raw)
        assert ids(result.analysis.timeline_events) == ["event-1"]
        assert "Jordan messaged Casey" in result.analysis.unresolved_questions[-1]

    def test_zero_support_drops_edge_and_orphaned_node(self, raw: Raw) -> None:
        graph = raw["revision"]["graph"]
        graph["edges"][1]["evidence_ids"] = ["rec-made-up"]
        graph["nodes"][2]["evidence_ids"] = ["rec-made-up"]  # casey
        _, result = sanitize(raw)
        assert ids(result.analysis.graph.edges) == ["edge-1"]
        assert ids(result.analysis.graph.nodes) == ["alex", "jordan"]
        assert_warning(result.warnings, "edge edge-2 dropped")
        assert_warning(result.warnings, "node casey dropped")
        assert "Jordan communicated with Casey" in result.analysis.unresolved_questions[-1]

    def test_node_partial_support_is_stripped(self, raw: Raw) -> None:
        raw["revision"]["graph"]["nodes"][1]["evidence_ids"] = ["rec-01", "rec-zz", "rec-02"]
        _, result = sanitize(raw)
        assert result.analysis.graph.nodes[1].evidence_ids == ["rec-01", "rec-02"]
        assert_warning(result.warnings, "node jordan", "unknown", "rec-zz")

    def test_unsupported_node_kept_while_an_edge_references_it(self, raw: Raw) -> None:
        raw["revision"]["graph"]["nodes"][0]["evidence_ids"] = ["rec-zz"]  # alex, on edge-1
        _, result = sanitize(raw)
        alex = result.analysis.graph.nodes[0]
        assert alex.id == "alex" and alex.evidence_ids is None
        assert ids(result.analysis.graph.edges) == ["edge-1", "edge-2"]
        assert "evidence_ids" not in result.analysis.graph.to_contract_json()["nodes"][0]

    def test_note_is_truncated_and_not_duplicated(self, raw: Raw) -> None:
        long_text = "Alex " + "word " * 40
        for claim in raw["revision"]["claims"]:
            claim.update(text=long_text, evidence_ids=["rec-fabricated"])
        _, result = sanitize(raw)
        notes = result.analysis.unresolved_questions[2:]
        assert len(notes) == 1
        assert "…" in notes[0] and len(notes[0]) < 160


class TestCrossCase:
    def test_record_from_other_case_is_stripped(self, raw: Raw) -> None:
        # The caller passed a mixed corpus: the cross-case record is reported as an error
        # (stripping cannot fix the corpus) and is never accepted as support.
        raw["records"].append(other_case_record(raw))
        raw["revision"]["claims"][0]["evidence_ids"] = ["rec-01", "rec-other"]
        raw["revision"]["claims"][1]["evidence_ids"] = ["rec-other"]
        _, result = sanitize(raw)
        assert result.analysis.claims[0].evidence_ids == ["rec-01"]
        assert ids(result.analysis.claims) == ["claim-1"]
        assert_warning(result.warnings, "claim claim-1", "another case", "rec-other")
        assert_warning(result.errors, "cross-case record rec-other")
        assert all("secret" not in q for q in result.analysis.unresolved_questions)
        records = [r for r in records_of(raw) if r.case_id == "synthetic-demo"]
        assert validate_revision(records, result.analysis) == []

    def test_other_case_id_unknown_to_case_corpus(self, raw: Raw) -> None:
        # The usual path: only this case's records are passed, so a foreign ID is unknown.
        raw["revision"]["graph"]["edges"][0]["evidence_ids"] = ["rec-other"]
        _, result = sanitize(raw)
        assert ids(result.analysis.graph.edges) == ["edge-2"]
        assert_warning(result.warnings, "edge edge-1", "unknown", "rec-other")
        assert result.errors == []

    def test_retrieving_other_case_record_is_an_error(self, raw: Raw) -> None:
        raw["records"].append(other_case_record(raw))
        raw["revision"]["retrieved_record_ids"].append("rec-other")
        raw["revision"]["claims"][1]["evidence_ids"] = ["rec-02", "rec-other"]
        _, result = sanitize(raw)
        assert result.analysis.claims[1].evidence_ids == ["rec-02"]
        assert_warning(result.errors, "retrieved IDs are not in the case corpus", "rec-other")


class TestExcluded:
    def test_excluded_ids_stripped_without_leaking(self, raw: Raw) -> None:
        # The child excludes rec-02; the LLM still cites it.
        child = raw["child"]
        child["claims"][0]["evidence_ids"] = ["rec-01", "rec-02"]
        child["claims"].append(
            {
                "id": "claim-2",
                "text": "Jordan discussed a possible meeting with Casey.",
                "evidence_ids": ["rec-02"],
                "status": "reported",
            }
        )
        child["graph"]["nodes"].append(
            {"id": "casey", "label": "Casey", "type": "person", "evidence_ids": ["rec-02"]}
        )
        child["graph"]["edges"].append(
            {
                "id": "edge-2",
                "source": "jordan",
                "target": "casey",
                "type": "communicated_with",
                "evidence_ids": ["rec-02"],
                "status": "reported",
            }
        )
        _, result = sanitize(raw, "child")
        out = result.analysis
        assert out.claims[0].evidence_ids == ["rec-01"]
        assert ids(out.claims) == ["claim-1"]
        assert ids(out.graph.edges) == ["edge-1"]
        assert ids(out.graph.nodes) == ["alex", "jordan"]
        assert_warning(result.warnings, "claim claim-2", "excluded", "rec-02")
        new_notes = out.unresolved_questions[1:]
        assert new_notes == [
            ANSWER_REVIEW_NOTE,
            "A claim was removed because no permitted evidence supports it.",
            "A relationship was removed because no permitted evidence supports it.",
        ]
        for note in new_notes:
            assert "rec-02" not in note
            assert "Casey" not in note and "meeting" not in note
            assert EXCLUDED_CONTENT not in note
        assert result.errors == []


class TestNotRetrieved:
    def test_cited_but_not_retrieved_is_stripped(self, raw: Raw) -> None:
        raw["revision"]["retrieved_record_ids"] = ["rec-01"]
        _, result = sanitize(raw)
        out = result.analysis
        assert ids(out.claims) == ["claim-1"]
        assert ids(out.timeline_events) == ["event-1"]
        assert ids(out.graph.edges) == ["edge-1"]
        assert ids(out.graph.nodes) == ["alex", "jordan"]
        assert out.graph.nodes[1].evidence_ids == ["rec-01"]
        assert_warning(result.warnings, "claim claim-2", "not retrieved", "rec-02")
        assert out.unresolved_questions[1] == ANSWER_REVIEW_NOTE
        assert "Jordan discussed a possible meeting" in out.unresolved_questions[2]
        assert result.errors == []


class TestGraph:
    def test_dangling_edge_dropped(self, raw: Raw) -> None:
        raw["revision"]["graph"]["edges"][1]["target"] = "nobody"
        _, result = sanitize(raw)
        assert ids(result.analysis.graph.edges) == ["edge-1"]
        assert_warning(result.warnings, "edge edge-2 dropped", "unknown graph nodes nobody")
        # casey still has valid evidence of its own, so it stays.
        assert "casey" in ids(result.analysis.graph.nodes)

    def test_dangling_edge_orphans_unsupported_node(self, raw: Raw) -> None:
        graph = raw["revision"]["graph"]
        graph["edges"][1]["source"] = "nobody"
        graph["nodes"][2]["evidence_ids"] = ["rec-zz"]  # casey
        _, result = sanitize(raw)
        assert ids(result.analysis.graph.nodes) == ["alex", "jordan"]


class TestStatus:
    @pytest.mark.parametrize(
        "path",
        ["claims.0", "graph.edges.0", "timeline_events.0"],
    )
    def test_missing_or_invalid_status_rejected(self, raw: Raw, path: str) -> None:
        item: Any = raw["revision"]
        for part in path.split("."):
            item = item[int(part)] if part.isdigit() else item[part]
        item["status"] = "claimed"
        model, errors = schema_errors(AnalysisRevision, raw["revision"])
        assert model is None
        assert any(e.startswith(f"{path}.status") for e in errors), errors
        del item["status"]
        model, errors = schema_errors(AnalysisRevision, raw["revision"])
        assert model is None
        assert any(e.startswith(f"{path}.status") for e in errors), errors

    def test_sanitizing_never_changes_status(self, raw: Raw) -> None:
        rev = raw["revision"]
        statuses = ["observed", "inferred"]
        for n, claim in enumerate(rev["claims"]):
            claim["status"] = statuses[n]
            claim["evidence_ids"] = ["rec-fabricated", *claim["evidence_ids"]]
        rev["claims"].append(
            {
                "id": "claim-3",
                "text": "Open point.",
                "evidence_ids": ["rec-01"],
                "status": "unresolved",
            }
        )
        rev["graph"]["edges"][0].update(status="inferred", evidence_ids=["rec-01", "rec-zz"])
        rev["timeline_events"][0].update(status="observed", evidence_ids=["rec-zz", "rec-01"])
        _, result = sanitize(raw)
        out = result.analysis
        assert [c.status for c in out.claims] == ["observed", "inferred", "unresolved"]
        assert out.graph.edges[0].status == "inferred"
        assert out.timeline_events[0].status == "observed"
        assert result.warnings


class TestUnrepairable:
    def test_duplicate_records(self, raw: Raw) -> None:
        raw["records"].append(dict(raw["records"][0]))
        _, result = sanitize(raw)
        assert_warning(result.errors, "duplicate source record IDs", "rec-01")

    def test_retrieved_not_in_corpus(self, raw: Raw) -> None:
        raw["revision"]["retrieved_record_ids"].append("rec-99")
        raw["revision"]["claims"][0]["evidence_ids"] = ["rec-01", "rec-99"]
        _, result = sanitize(raw)
        assert_warning(result.errors, "retrieved IDs are not in the case corpus", "rec-99")
        assert result.analysis.claims[0].evidence_ids == ["rec-01"]
        assert_warning(result.warnings, "claim claim-1", "unknown", "rec-99")

    def test_excluded_record_retrieved(self, raw: Raw) -> None:
        raw["child"]["retrieved_record_ids"].append("rec-02")
        raw["child"]["claims"][0]["evidence_ids"] = ["rec-01", "rec-02"]
        _, result = sanitize(raw, "child")
        assert_warning(result.errors, "excluded source records appear in the retrieval set")
        assert result.analysis.claims[0].evidence_ids == ["rec-01"]

    def test_duplicate_item_ids_and_exact_timestamp(self, raw: Raw) -> None:
        raw["revision"]["claims"][1]["id"] = "claim-1"
        raw["revision"]["timeline_events"][0]["timestamp"] = None
        _, result = sanitize(raw)
        assert_warning(result.errors, "duplicate claim IDs", "claim-1")
        assert_warning(result.errors, "event event-1", "exact")
        assert result.warnings == []


class TestAnswerReview:
    def test_all_findings_dropped_flags_answer(self, raw: Raw) -> None:
        rev = raw["revision"]
        for item in [*rev["claims"], *rev["graph"]["edges"], *rev["timeline_events"]]:
            item["evidence_ids"] = ["zzz"]
        analysis, result = sanitize(raw)
        out = result.analysis
        assert out.claims == [] and out.graph.edges == [] and out.timeline_events == []
        assert out.answer == analysis.answer  # flagged, never rewritten
        assert result.dropped_findings == 6
        assert result.answer_needs_review
        assert out.unresolved_questions.count(ANSWER_REVIEW_NOTE) == 1
        assert_warning(result.warnings, "answer needs review", "6")
        assert result.errors == []

    def test_kept_findings_do_not_flag_answer(self, raw: Raw) -> None:
        raw["revision"]["claims"][0]["evidence_ids"] = ["rec-fabricated", "rec-01"]
        _, result = sanitize(raw)
        assert result.dropped_findings == 0
        assert not result.answer_needs_review
        assert ANSWER_REVIEW_NOTE not in result.analysis.unresolved_questions

    @pytest.mark.parametrize("key", ["revision", "child"])
    def test_valid_revision_is_not_flagged(self, raw: Raw, key: str) -> None:
        _, result = sanitize(raw, key)
        assert result.dropped_findings == 0
        assert not result.answer_needs_review

    def test_dangling_edge_counts_as_dropped_finding(self, raw: Raw) -> None:
        raw["revision"]["graph"]["edges"][1]["target"] = "nobody"
        _, result = sanitize(raw)
        assert result.dropped_findings == 1
        assert result.answer_needs_review
        assert ANSWER_REVIEW_NOTE in result.analysis.unresolved_questions

    def test_note_does_not_leak_exclusions(self, raw: Raw) -> None:
        raw["child"]["claims"][0]["evidence_ids"] = ["rec-02"]
        _, result = sanitize(raw, "child")
        assert result.answer_needs_review
        for word in ("rec-", "Casey", "meeting", EXCLUDED_CONTENT):
            assert word not in ANSWER_REVIEW_NOTE

    def test_note_not_repeated_on_resanitize(self, raw: Raw) -> None:
        raw["revision"]["claims"][0]["evidence_ids"] = ["zzz"]
        _, first = sanitize(raw)
        second = sanitize_citations(records_of(raw), first.analysis)
        assert second.dropped_findings == 0
        assert second.analysis.unresolved_questions.count(ANSWER_REVIEW_NOTE) == 1
