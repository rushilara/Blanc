"""Cross-reference validation tests, ported from starter/test_validator.py plus the BLA-5
acceptance criteria. Each test mutates the raw fixture JSON, parses it through the contract
models (as the server would) and runs the app.validation checks."""

import copy
import json
from typing import Any

import pytest
from _paths import FIXTURE_FILES, FIXTURES_DIR
from pydantic import ValidationError

from app.comparison import compare_revisions
from app.contracts import (
    AnalysisRevision,
    EvidenceRecord,
    InvestigationBoard,
    RevisionComparison,
)
from app.validation import (
    format_validation_error,
    schema_errors,
    validate_board,
    validate_comparison,
    validate_revision,
)

Raw = dict[str, Any]


def records_of(raw: Raw) -> list[EvidenceRecord]:
    return [EvidenceRecord.model_validate(r) for r in raw["records"]]


def revisions_of(raw: Raw) -> list[AnalysisRevision]:
    return [AnalysisRevision.model_validate(raw[k]) for k in ("revision", "child")]


def check_revision(raw: Raw, key: str = "revision") -> list[str]:
    return validate_revision(records_of(raw), AnalysisRevision.model_validate(raw[key]))


def check_board(raw: Raw) -> list[str]:
    board = InvestigationBoard.model_validate(raw["board"])
    return validate_board(records_of(raw), revisions_of(raw), board)


def check_comparison(raw: Raw) -> list[str]:
    comparison = RevisionComparison.model_validate(raw["comparison"])
    return validate_comparison(revisions_of(raw), comparison)


def item_of_kind(board: Raw, kind: str) -> Raw:
    return next(i for i in board["items"] if i["kind"] == kind)


def assert_error(errors: list[str], *fragments: str) -> None:
    assert any(all(f in e for f in fragments) for e in errors), errors


# --- BLA-5 acceptance criteria ----------------------------------------------------------


class TestAcceptanceCriteria:
    def test_valid_fixtures_pass(self, raw: Raw) -> None:
        assert check_revision(raw, "revision") == []
        assert check_revision(raw, "child") == []
        assert check_board(raw) == []
        assert check_comparison(raw) == []

    def test_citing_excluded_record_fails(self, raw: Raw) -> None:
        # The child excludes rec-02; an LLM claim that still cites it must be rejected.
        raw["child"]["claims"][0]["evidence_ids"] = ["rec-01", "rec-02"]
        assert_error(check_revision(raw, "child"), "claim claim-1", "excluded", "rec-02")

    @pytest.mark.parametrize(
        "path",
        ["claims.0", "graph.edges.0", "timeline_events.0", "graph.nodes.0"],
    )
    def test_citing_unknown_fabricated_id_fails(self, raw: Raw, path: str) -> None:
        target: Any = raw["revision"]
        for part in path.split("."):
            target = target[int(part)] if part.isdigit() else target[part]
        target["evidence_ids"] = ["rec-01", "rec-fabricated"]
        assert_error(check_revision(raw), target["id"], "unknown", "rec-fabricated")


# --- AnalysisRevision (starter TestEvidenceGrounding) -----------------------------------


class TestRevision:
    def test_cannot_cite_excluded_record(self, raw: Raw) -> None:
        raw["revision"]["excluded_record_ids"] = ["rec-02"]
        raw["revision"]["retrieved_record_ids"] = ["rec-01"]
        errors = check_revision(raw)
        for label in ("claim claim-2", "edge edge-2", "event event-2", "node casey"):
            assert_error(errors, label, "excluded", "rec-02")

    def test_cross_case_blocked(self, raw: Raw) -> None:
        raw["records"][0]["case_id"] = "other-case"
        errors = check_revision(raw)
        assert_error(errors, "cross-case record rec-01")
        assert_error(errors, "retrieved IDs are not in the case corpus", "rec-01")

    def test_duplicate_record_ids(self, raw: Raw) -> None:
        raw["records"].append(dict(raw["records"][0]))
        assert_error(check_revision(raw), "duplicate source record IDs", "rec-01")

    def test_retrieved_must_be_in_corpus(self, raw: Raw) -> None:
        raw["revision"]["retrieved_record_ids"].append("rec-99")
        assert_error(check_revision(raw), "retrieved IDs are not in the case corpus", "rec-99")

    def test_excluded_must_not_be_retrieved(self, raw: Raw) -> None:
        raw["child"]["retrieved_record_ids"].append("rec-02")
        assert_error(check_revision(raw, "child"), "excluded source records", "rec-02")

    def test_cannot_cite_unretrieved_record(self, raw: Raw) -> None:
        raw["child"]["claims"][0]["evidence_ids"] = ["rec-01"]
        raw["child"]["retrieved_record_ids"] = []
        raw["child"]["excluded_record_ids"] = []
        assert_error(check_revision(raw, "child"), "claim claim-1", "not retrieved", "rec-01")

    def test_edge_references_unknown_node(self, raw: Raw) -> None:
        raw["revision"]["graph"]["edges"][0]["target"] = "nobody"
        assert_error(check_revision(raw), "edge edge-1 target", "unknown graph node nobody")

    def test_duplicate_item_ids(self, raw: Raw) -> None:
        raw["revision"]["claims"][1]["id"] = "claim-1"
        assert_error(check_revision(raw), "duplicate claim IDs", "claim-1")

    def test_exact_precision_needs_timestamp(self, raw: Raw) -> None:
        raw["revision"]["timeline_events"][0]["timestamp"] = None
        assert_error(check_revision(raw), "event event-1", "exact")
        raw["revision"]["timeline_events"][0]["timestamp_precision"] = "unknown"
        assert check_revision(raw) == []

    def test_empty_support_rejected(self, raw: Raw) -> None:
        # Invariant 4 is enforced at the schema layer; the model never builds.
        raw["revision"]["claims"][0]["evidence_ids"] = []
        with pytest.raises(ValidationError):
            AnalysisRevision.model_validate(raw["revision"])


# --- InvestigationBoard (starter TestBoard) ---------------------------------------------


class TestBoard:
    def test_valid_board(self, raw: Raw) -> None:
        assert check_board(raw) == []

    def test_board_cites_unknown_evidence(self, raw: Raw) -> None:
        raw["board"]["items"][0]["evidence_ids"] = ["rec-99"]
        errors = check_board(raw)
        assert_error(errors, "board item item-1", "not in case", "rec-99")
        assert_error(errors, "board item item-1", "does not cite", "rec-99")

    def test_board_cross_case_blocked(self, raw: Raw) -> None:
        raw["board"]["case_id"] = "other-case"
        errors = check_board(raw)
        assert_error(errors, "active_analysis_id")
        assert_error(errors, "board item item-1", "not a revision of case other-case")

    def test_board_unknown_ref(self, raw: Raw) -> None:
        raw["board"]["items"][0]["ref_id"] = "edge-99"
        assert_error(check_board(raw), "ref_id edge-99 is not a edge")

    def test_board_ref_wrong_kind(self, raw: Raw) -> None:
        raw["board"]["items"][0]["kind"] = "claim"  # edge-1 is an edge, not a claim
        assert_error(check_board(raw), "ref_id edge-1 is not a claim")

    def test_board_cites_evidence_excluded_in_source(self, raw: Raw) -> None:
        item = raw["board"]["items"][0]
        item["source_analysis_id"] = "analysis-synthetic-2"
        item["evidence_ids"] = ["rec-01", "rec-02"]
        assert_error(check_board(raw), "excluded in its source revision", "rec-02")

    def test_board_status_cannot_be_upgraded(self, raw: Raw) -> None:
        raw["board"]["items"][0]["status"] = "observed"
        assert_error(check_board(raw), "board item item-1", "pinning must not change status")

    def test_note_targets_unknown_item(self, raw: Raw) -> None:
        raw["board"]["notes"][0]["target_item_id"] = "item-99"
        assert_error(check_board(raw), "note note-1 targets unknown board item item-99")

    def test_parent_revision_must_precede(self, raw: Raw) -> None:
        raw["board"]["parent_revision"] = raw["board"]["revision"]
        assert_error(check_board(raw), "parent_revision must be lower")

    def test_duplicate_items_and_notes(self, raw: Raw) -> None:
        raw["board"]["items"].append(dict(raw["board"]["items"][0]))
        raw["board"]["notes"].append(dict(raw["board"]["notes"][0]))
        errors = check_board(raw)
        assert_error(errors, "duplicate board item IDs", "item-1")
        assert_error(errors, "pins the same finding twice")
        assert_error(errors, "duplicate board note IDs", "note-1")

    def test_node_without_status_passes(self, raw: Raw) -> None:
        assert "status" not in item_of_kind(raw["board"], "node")
        assert check_board(raw) == []

    def test_claim_without_status_fails(self, raw: Raw) -> None:
        del item_of_kind(raw["board"], "claim")["status"]
        model, errors = schema_errors(InvestigationBoard, raw["board"])
        assert model is None
        assert_error(errors, "items.2", "status is required for claim items")

    def test_unresolved_status_allowed_by_schema(self, raw: Raw) -> None:
        item_of_kind(raw["board"], "claim")["status"] = "unresolved"
        InvestigationBoard.model_validate(raw["board"])
        # ...but it must still match the source claim, which is 'reported'.
        assert_error(check_board(raw), "status unresolved differs", "reported")


# --- Date-times (starter TestTimestamps) ------------------------------------------------


class TestTimestamps:
    def test_same_instant_different_offsets(self, raw: Raw) -> None:
        raw["board"]["created_at"] = "2026-11-14T20:03:30Z"
        raw["board"]["updated_at"] = "2026-11-14T21:03:30+01:00"
        assert check_board(raw) == []

    def test_offset_ordering_not_lexical(self, raw: Raw) -> None:
        # Lexically '...T19...' < '...T20...', but 19:00-05:00 is 00:00Z the next day.
        raw["board"]["created_at"] = "2026-11-14T19:00:00-05:00"
        assert_error(check_board(raw), "updated_at is before created_at")

    def test_item_and_note_not_after_updated_at(self, raw: Raw) -> None:
        raw["board"]["items"][0]["added_at"] = "2026-11-14T16:00:00-05:00"
        raw["board"]["notes"][1]["created_at"] = "2026-11-14T20:03:31Z"
        errors = check_board(raw)
        assert_error(errors, "board item item-1 added_at is after")
        assert_error(errors, "note note-2 created_at is after")

    def test_naive_timestamp_fails(self, raw: Raw) -> None:
        raw["board"]["updated_at"] = "2026-11-14T20:03:30"
        model, errors = schema_errors(InvestigationBoard, raw["board"])
        assert model is None
        assert_error(errors, "updated_at", "UTC offset")

    def test_unparseable_timestamp_fails(self, raw: Raw) -> None:
        raw["board"]["notes"][0]["created_at"] = "last tuesday"
        model, errors = schema_errors(InvestigationBoard, raw["board"])
        assert model is None
        assert_error(errors, "notes.0.created_at", "last tuesday")
        raw["comparison"]["created_at"] = "not-a-date"
        model, errors = schema_errors(RevisionComparison, raw["comparison"])
        assert model is None
        assert_error(errors, "created_at")


# --- RevisionComparison (starter TestComparison) ----------------------------------------


class TestComparison:
    def test_valid_comparison(self, raw: Raw) -> None:
        assert check_comparison(raw) == []

    def test_unknown_revisions(self, raw: Raw) -> None:
        raw["comparison"]["base_analysis_id"] = "analysis-missing"
        assert check_comparison(raw) == [
            "base_analysis_id and compared_analysis_id must both be known revisions"
        ]

    def test_cross_case(self, raw: Raw) -> None:
        raw["comparison"]["case_id"] = "other-case"
        assert_error(check_comparison(raw), "share one case_id")

    def test_excluded_ids_must_match_delta(self, raw: Raw) -> None:
        raw["comparison"]["excluded_record_ids"] = ["rec-01"]
        assert_error(check_comparison(raw), "excluded_record_ids must equal", "rec-02")

    def test_compared_side_cannot_cite_excluded(self, raw: Raw) -> None:
        raw["comparison"]["items"][0]["compared_evidence_ids"] = ["rec-01", "rec-02"]
        errors = check_comparison(raw)
        assert_error(errors, "comparison item 0 compared side cites excluded", "rec-02")
        assert_error(errors, "comparison item 0 compared_evidence_ids do not match")

    def test_base_evidence_must_match(self, raw: Raw) -> None:
        raw["comparison"]["items"][0]["base_evidence_ids"] = ["rec-02"]
        assert_error(check_comparison(raw), "comparison item 0 base_evidence_ids do not match")

    def test_unknown_ref(self, raw: Raw) -> None:
        raw["comparison"]["items"][0]["compared_ref_id"] = "claim-9"
        assert_error(check_comparison(raw), "compared_ref_id claim-9 is not a claim")

    def test_not_supported_with_surviving_evidence_rejected(self, raw: Raw) -> None:
        raw["comparison"]["items"][0]["support_status"] = "not_supported"
        assert_error(check_comparison(raw), "comparison item 0 not_supported")

    def test_still_supported_needs_both_sides(self, raw: Raw) -> None:
        raw["comparison"]["items"][1]["support_status"] = "still_supported"
        assert_error(check_comparison(raw), "comparison item 1 still_supported needs both")

    def test_both_refs_null_rejected(self, raw: Raw) -> None:
        item = raw["comparison"]["items"][1]
        item.update(base_ref_id=None, base_evidence_ids=[], support_status="unresolved")
        assert_error(check_comparison(raw), "needs base_ref_id or compared_ref_id")

    def test_null_compared_ref_rejects_fabricated_evidence(self, raw: Raw) -> None:
        item = raw["comparison"]["items"][1]
        assert item["compared_ref_id"] is None
        item["compared_evidence_ids"] = ["fabricated-record"]
        item["support_status"] = "unresolved"
        model, errors = schema_errors(RevisionComparison, raw["comparison"])
        assert model is None
        assert_error(errors, "items.1", "compared_evidence_ids must be empty")

    def test_null_base_ref_rejects_evidence(self, raw: Raw) -> None:
        new_item = {
            "kind": "claim",
            "base_ref_id": None,
            "compared_ref_id": "claim-1",
            "support_status": "newly_supported",
            "base_evidence_ids": ["rec-01"],
            "compared_evidence_ids": ["rec-01"],
        }
        raw["comparison"]["items"].append(new_item)
        model, errors = schema_errors(RevisionComparison, raw["comparison"])
        assert model is None
        assert_error(errors, "base_evidence_ids must be empty")
        new_item["base_evidence_ids"] = []
        assert check_comparison(raw) == []

    def test_newly_supported_needs_only_compared(self, raw: Raw) -> None:
        raw["comparison"]["items"][0]["support_status"] = "newly_supported"
        assert_error(check_comparison(raw), "newly_supported needs only compared_ref_id")

    def test_must_be_parent_child(self, raw: Raw) -> None:
        cmp = raw["comparison"]
        cmp["base_analysis_id"], cmp["compared_analysis_id"] = (
            cmp["compared_analysis_id"],
            cmp["base_analysis_id"],
        )
        assert_error(check_comparison(raw), "must be a child of the base revision")


# --- possible_matches (BLA-63) ----------------------------------------------------------


class TestPossibleMatches:
    """A computed comparison where base claim-2 (rec-02 + rec-01) loses rec-02 and the child
    rewords it as claim-x on rec-01, so the two are linked on both items."""

    @pytest.fixture
    def linked(self, raw: Raw) -> Raw:
        raw["revision"]["claims"][1]["evidence_ids"] = ["rec-02", "rec-01"]
        raw["child"]["claims"].append(
            {
                "id": "claim-x",
                "text": "Jordan may meet Casey.",
                "evidence_ids": ["rec-01"],
                "status": "reported",
            }
        )
        base, child = revisions_of(raw)
        raw["comparison"] = compare_revisions(base, child).to_contract_json()
        return raw

    @staticmethod
    def items(raw: Raw) -> tuple[Raw, Raw]:
        items = raw["comparison"]["items"]
        before = next(i for i in items if i["base_ref_id"] == "claim-2")
        after = next(i for i in items if i["compared_ref_id"] == "claim-x")
        return before, after

    def test_computed_links_are_valid(self, linked: Raw) -> None:
        before, after = self.items(linked)
        assert before["possible_matches"] == [
            {
                "ref_id": "claim-x",
                "shared_evidence_ids": ["rec-01"],
                "remaining_sources": 1,
                "original_sources": 2,
            }
        ]
        assert after["possible_matches"][0]["ref_id"] == "claim-2"
        assert check_comparison(linked) == []

    def test_link_to_an_unknown_item(self, linked: Raw) -> None:
        before, _ = self.items(linked)
        before["possible_matches"][0]["ref_id"] = "claim-9"
        assert_error(check_comparison(linked), "possible match claim-9 is not a newly_supported")

    def test_link_to_an_item_of_another_kind(self, linked: Raw) -> None:
        _, after = self.items(linked)
        after["possible_matches"][0]["ref_id"] = "edge-2"
        assert_error(check_comparison(linked), "possible match edge-2 is not an unmatched base")

    def test_link_on_an_item_that_cannot_have_one(self, linked: Raw) -> None:
        _, after = self.items(linked)
        after["support_status"] = "unresolved"
        assert_error(check_comparison(linked), "possible_matches is only for")

    def test_link_on_an_item_with_both_refs_is_a_schema_error(self, linked: Raw) -> None:
        before, _ = self.items(linked)
        linked["comparison"]["items"][0]["possible_matches"] = before["possible_matches"]
        model, errors = schema_errors(RevisionComparison, linked["comparison"])
        assert model is None
        assert_error(errors, "possible_matches is only for items with one ref ID null")

    def test_shared_must_equal_new_item_citations(self, linked: Raw) -> None:
        for item in self.items(linked):
            item["possible_matches"][0]["shared_evidence_ids"] = ["rec-02"]
        errors = check_comparison(linked)
        assert_error(errors, "shared_evidence_ids must equal the new item's unique cited records")
        assert_error(errors, "possible match claim-x shares excluded records")

    def test_new_item_citations_must_be_permitted_base_subset(self, linked: Raw) -> None:
        # Hand-written link: new claim also cites rec-04, which the base never cited.
        before, after = self.items(linked)
        after["compared_evidence_ids"] = ["rec-01", "rec-04"]
        linked["child"]["claims"][-1]["evidence_ids"] = ["rec-01", "rec-04"]
        for item in (before, after):
            item["possible_matches"][0]["shared_evidence_ids"] = ["rec-01"]
        assert_error(
            check_comparison(linked),
            "cites records that are not among the base item's still-permitted citations",
        )

    def test_edge_link_requires_same_direction_endpoints(self, raw: Raw) -> None:
        # Base edge-1 cites rec-01+rec-02; exclude rec-02; child adds a reversed edge on rec-01.
        raw["revision"]["graph"]["edges"][0]["evidence_ids"] = ["rec-01", "rec-02"]
        edge = copy.deepcopy(raw["revision"]["graph"]["edges"][0])
        edge["id"] = "edge-rev"
        edge["source"], edge["target"] = edge["target"], edge["source"]
        edge["evidence_ids"] = ["rec-01"]
        raw["child"]["graph"]["edges"] = [
            e for e in raw["child"]["graph"]["edges"] if e["id"] != "edge-1"
        ] + [edge]
        base, child = revisions_of(raw)
        raw["comparison"] = compare_revisions(base, child).to_contract_json()
        # compare_revisions must not link them; a hand-written link must be rejected.
        before = next(
            i
            for i in raw["comparison"]["items"]
            if i["kind"] == "edge" and i["base_ref_id"] == "edge-1" and i["compared_ref_id"] is None
        )
        after = next(
            i
            for i in raw["comparison"]["items"]
            if i["kind"] == "edge" and i["compared_ref_id"] == "edge-rev"
        )
        assert "possible_matches" not in before and "possible_matches" not in after
        link = {
            "shared_evidence_ids": ["rec-01"],
            "remaining_sources": 1,
            "original_sources": 2,
        }
        before["possible_matches"] = [{"ref_id": "edge-rev", **link}]
        after["possible_matches"] = [{"ref_id": "edge-1", **link}]
        assert_error(
            check_comparison(raw),
            "edge endpoints must match in the same direction",
        )

    def test_counts_must_match_the_base_item(self, linked: Raw) -> None:
        for item in self.items(linked):
            item["possible_matches"][0]["remaining_sources"] = 2
        assert_error(
            check_comparison(linked), "claim-x source counts do not match", "expected 1 of 2"
        )

    def test_link_on_a_base_item_that_lost_no_source(self, raw: Raw) -> None:
        # claim-2 now cites only rec-01, which is not excluded: no link may be computed...
        raw["revision"]["claims"][1]["evidence_ids"] = ["rec-01"]
        raw["child"]["claims"].append(
            {
                "id": "claim-x",
                "text": "Jordan may meet Casey.",
                "evidence_ids": ["rec-01"],
                "status": "reported",
            }
        )
        base, child = revisions_of(raw)
        raw["comparison"] = compare_revisions(base, child).to_contract_json()
        before, after = self.items(raw)
        assert "possible_matches" not in before and "possible_matches" not in after
        assert check_comparison(raw) == []
        # ...and a hand-written one is rejected.
        link = {"shared_evidence_ids": ["rec-01"], "remaining_sources": 1, "original_sources": 1}
        before["possible_matches"] = [{"ref_id": "claim-x", **link}]
        after["possible_matches"] = [{"ref_id": "claim-2", **link}]
        errors = check_comparison(raw)
        assert_error(errors, "claim-x links a base item that lost no cited source")
        assert_error(errors, "claim-x must have fewer remaining than original sources")

    def test_link_must_be_listed_on_both_items(self, linked: Raw) -> None:
        _, after = self.items(linked)
        del after["possible_matches"]
        assert_error(check_comparison(linked), "claim-x is not listed the same way")

    def test_link_must_not_repeat(self, linked: Raw) -> None:
        before, _ = self.items(linked)
        before["possible_matches"].append(before["possible_matches"][0])
        assert_error(check_comparison(linked), "possible_matches repeats claim-x")


# --- Schema-error helpers ---------------------------------------------------------------


class TestSchemaErrors:
    def test_accepts_dict_and_json(self, raw: Raw) -> None:
        model, errors = schema_errors(AnalysisRevision, raw["revision"])
        assert errors == [] and model is not None
        text = (FIXTURES_DIR / FIXTURE_FILES["revision"]).read_text()
        model_from_json, errors = schema_errors(AnalysisRevision, text)
        assert errors == [] and model_from_json == model
        assert json.loads(text) == raw["revision"]

    def test_errors_are_readable_paths(self, raw: Raw) -> None:
        raw["revision"]["claims"][0]["evidence_ids"] = []
        raw["revision"]["graph"]["edges"][0]["status"] = "unresolved"
        raw["revision"]["confidence"] = 1
        model, errors = schema_errors(AnalysisRevision, raw["revision"])
        assert model is None
        assert_error(errors, "claims.0.evidence_ids")
        assert_error(errors, "graph.edges.0.status")
        assert_error(errors, "confidence", "Extra inputs are not permitted")

    def test_format_validation_error(self) -> None:
        with pytest.raises(ValidationError) as exc:
            EvidenceRecord.model_validate({"record_id": ""})
        errors = format_validation_error(exc.value)
        assert_error(errors, "record_id")
        assert_error(errors, "case_id", "required")
