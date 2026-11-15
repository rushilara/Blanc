"""BLA-64: "want more evidence?" — evidence windows, retrieval coverage and the rerun route.

The corpus is built so the rankings are known exactly (window: standard 1 record,
expanded 2):

- rec-a: the only keyword match, vector rank 3 → fused rank 1 (both retrievers).
- rec-b: vector rank 1, no keyword match → fused rank 2: one retriever put it in the
  standard window but fusion pushed it out, so it is "left out" and the offer is shown.
- rec-c: vector rank 2, no keyword match → fused rank 3: outside the standard window for
  both retrievers (not left out there); inside the expanded window for the vector search,
  so an expanded run reports it as "still left out".
"""

from __future__ import annotations

import copy
import json
import math
from typing import Any

import pytest
from _paths import CONTRACTS_DIR, load_json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from app.analyses import get_revision, save_revision
from app.analysis_llm import FakeAnalysisLLM
from app.config import Settings
from app.contracts import AnalysisRevision, EvidenceRecord, RetrievalCoverage
from app.embeddings import FakeEmbedder
from app.evidence_store import store_records
from app.evidence_window import left_out, window_of
from app.retrieval import RetrievalConfig
from app.validation import validate_revision

REVISION_SCHEMA = Draft202012Validator(load_json(CONTRACTS_DIR / "analysis_revision.schema.json"))
CONTENT = {
    "rec-a": "Alex emails Jordan about the warehouse key.",
    "rec-b": "Sarah, the delivery is at seven tonight.",
    "rec-c": "Jordan confirms the van is parked outside.",
}
QUESTION = "Who mentioned the warehouse key?"
FOLLOW_UP = "Who else mentioned the warehouse key?"
CONFIG = RetrievalConfig(max_records=1, expanded_max_records=2)


class SteeredEmbedder(FakeEmbedder):
    """Queries embed close to rec-b, a little less close to rec-c, and not to rec-a."""

    def embed_query(self, text: str) -> list[float]:
        b, c = self.vector(CONTENT["rec-b"]), self.vector(CONTENT["rec-c"])
        raw = [x + 0.8 * y for x, y in zip(b, c, strict=True)]
        norm = math.sqrt(sum(v * v for v in raw))
        return [v / norm for v in raw]


def output(*cited: str) -> dict[str, Any]:
    ids = list(cited) or ["rec-a"]
    return {
        "answer": "Alex mentioned the warehouse key.",
        "claims": [
            {"id": "claim-1", "text": "Alex emailed Jordan about the key.",
             "evidence_ids": [ids[0]], "status": "observed"},
            *(
                {"id": f"claim-{i + 2}", "text": f"Record {rid} is relevant.",
                 "evidence_ids": [rid], "status": "reported"}
                for i, rid in enumerate(ids[1:])
            ),
        ],
        "graph": {"nodes": [], "edges": []},
        "timeline_events": [],
        "unresolved_questions": [],
    }


def llm(app: FastAPI, *responses: dict[str, Any]) -> FakeAnalysisLLM:
    fake = FakeAnalysisLLM(responses=list(responses) or [output()])
    app.state.analysis_llm = fake
    return fake


def seed(client: TestClient, title: str = "Window") -> str:
    case_id = client.post("/cases", json={"title": title}).json()["case_id"]
    embedder = SteeredEmbedder()
    client.app.state.embedder = embedder
    client.app.state.retrieval_config = CONFIG
    records = [
        EvidenceRecord.model_validate(
            {"record_id": rid, "case_id": case_id, "source_file": "sms.json",
             "source_locator": f"messages[{i}]", "content": text}
        )
        for i, (rid, text) in enumerate(CONTENT.items())
    ]
    with client.app.state.session_factory() as session:
        store_records(session, case_id, records, embedder)
        session.commit()
    return case_id


def post(client: TestClient, url: str, body: dict[str, Any], expect: int = 201) -> dict:
    response = client.post(url, json=body)
    assert response.status_code == expect, response.text
    return response.json()


def analyze(client, case_id, expect=201, **body) -> dict:
    return post(client, f"/cases/{case_id}/analyses", {"question": QUESTION, **body}, expect)


def rerun(client, case_id, analysis_id, window="expanded", expect=201) -> dict:
    url = f"/cases/{case_id}/analyses/{analysis_id}/rerun"
    return post(client, url, {"evidence_window": window}, expect)


def valid(body: dict) -> dict:
    assert not list(REVISION_SCHEMA.iter_errors(body))
    return body


def prompt(fake: FakeAnalysisLLM) -> dict:
    return json.loads(fake.calls[-1][1]["content"])


# --- Coverage on a standard run ------------------------------------------------------------


def test_a_standard_run_records_what_was_left_out_and_offers_more(app, client) -> None:
    case_id = seed(client)
    fake = llm(app)
    base = valid(analyze(client, case_id))

    assert base["retrieved_record_ids"] == ["rec-a"]
    coverage = base["retrieval_coverage"]
    assert coverage == {
        "evidence_window": "standard",
        "max_records": 1,
        "max_excerpt_chars": CONFIG.max_excerpt_chars,
        "skipped": [
            {"record_id": "rec-b", "rank": 2, "reason": "record_limit", "vector_rank": 1},
            {"record_id": "rec-c", "rank": 3, "reason": "record_limit", "vector_rank": 2},
        ],
        "budget_substituted": False,
        "left_out_record_ids": ["rec-b"],  # vector rank 1 fits the window; rec-c's 2 does not
        "offer_expanded_window": True,
    }
    # Saved and reloaded exactly.
    assert client.get(f"/cases/{case_id}/analyses/{base['analysis_id']}").json() == base
    # Skipped records are never shown to the model, neither by ID nor by content.
    sent = json.dumps(fake.calls[-1], ensure_ascii=False)
    assert [r["record_id"] for r in prompt(fake)["source_records"]] == ["rec-a"]
    for rid in ("rec-b", "rec-c"):
        assert rid not in sent and CONTENT[rid] not in sent


def test_a_budget_only_skip_gives_no_offer(app, client) -> None:
    case_id = seed(client)
    app.state.retrieval_config = RetrievalConfig(
        max_records=10, expanded_max_records=12, max_excerpt_chars=len(CONTENT["rec-a"])
    )
    llm(app)
    coverage = analyze(client, case_id)["retrieval_coverage"]
    assert {s["reason"] for s in coverage["skipped"]} == {"budget"}
    assert coverage["left_out_record_ids"] == []
    assert coverage["offer_expanded_window"] is False


# --- The rerun ---------------------------------------------------------------------------


def test_an_expanded_rerun_is_a_new_child_and_the_parent_is_unchanged(app, client) -> None:
    case_id = seed(client)
    llm(app)
    base = analyze(client, case_id)
    fake = llm(app, output("rec-a", "rec-b"))
    child = valid(rerun(client, case_id, base["analysis_id"]))

    assert child["parent_analysis_id"] == base["analysis_id"]
    assert child["question"] == base["question"]
    assert child["retrieved_record_ids"] == ["rec-a", "rec-b"]
    assert [r["record_id"] for r in prompt(fake)["source_records"]] == ["rec-a", "rec-b"]
    # The base's findings are context for keeping identities, never evidence.
    assert prompt(fake)["context"]["label"] == "prior analysis — not evidence"
    coverage = child["retrieval_coverage"]
    assert (coverage["evidence_window"], coverage["max_records"]) == ("expanded", 2)
    assert coverage["max_excerpt_chars"] == CONFIG.max_excerpt_chars  # only the count rises
    # One step only: rec-c (vector rank 2) is still left out, but there is no further offer.
    assert coverage["left_out_record_ids"] == ["rec-c"]
    assert coverage["offer_expanded_window"] is False

    assert client.get(f"/cases/{case_id}/analyses/{base['analysis_id']}").json() == base
    comparison = client.get(f"/cases/{case_id}/analyses/{child['analysis_id']}/comparison")
    assert comparison.status_code == 200
    statuses = {i["compared_ref_id"]: i["support_status"] for i in comparison.json()["items"]}
    assert statuses == {"claim-1": "still_supported", "claim-2": "newly_supported"}


def test_rerunning_in_the_same_window_is_a_conflict(app, client) -> None:
    case_id = seed(client)
    fake = llm(app)
    base = analyze(client, case_id)
    rerun(client, case_id, base["analysis_id"], "standard", expect=409)
    llm(app)
    expanded = rerun(client, case_id, base["analysis_id"])
    fake = llm(app)
    body = rerun(client, case_id, expanded["analysis_id"], "expanded", expect=409)
    assert "already used the expanded evidence window" in body["detail"]
    assert fake.calls == []
    # Going back to the standard window is a different window, so it is allowed.
    llm(app)
    back = rerun(client, case_id, expanded["analysis_id"], "standard")
    assert back["retrieval_coverage"]["evidence_window"] == "standard"


def test_rerun_errors(app, client) -> None:
    case_id = seed(client)
    other = seed(client, "Other")
    llm(app)
    base = analyze(client, case_id)
    fake = llm(app)
    rerun(client, case_id, "analysis-missing", expect=404)
    rerun(client, other, base["analysis_id"], expect=404)  # another case's revision
    rerun(client, case_id, base["analysis_id"], "huge", expect=422)
    url = f"/cases/{case_id}/analyses/{base['analysis_id']}/rerun"
    assert client.post(url, json={}).status_code == 422
    assert client.post(url, json={"evidence_window": "expanded", "x": 1}).status_code == 422
    assert fake.calls == []


# --- Inheritance -------------------------------------------------------------------------


def test_exclusions_are_inherited_and_excluded_records_are_never_listed(app, client) -> None:
    case_id = seed(client)
    llm(app)
    base = analyze(client, case_id)
    llm(app)
    url = f"/cases/{case_id}/analyses/{base['analysis_id']}/exclude"
    excluded = post(client, url, {"record_ids": ["rec-c"]})
    assert excluded["retrieval_coverage"]["evidence_window"] == "standard"

    llm(app, output("rec-a", "rec-b"))
    child = valid(rerun(client, case_id, excluded["analysis_id"]))
    assert child["excluded_record_ids"] == ["rec-c"]
    assert child["retrieved_record_ids"] == ["rec-a", "rec-b"]
    assert child["retrieval_coverage"]["skipped"] == []
    assert child["retrieval_coverage"]["left_out_record_ids"] == []


def test_follow_ups_exclusions_and_prior_analyses_inherit_the_window(app, client) -> None:
    case_id = seed(client)
    llm(app)
    base = analyze(client, case_id)
    llm(app)
    expanded = rerun(client, case_id, base["analysis_id"])
    aid = expanded["analysis_id"]

    def window(body: dict) -> str:
        return body["retrieval_coverage"]["evidence_window"]

    llm(app)
    follow = post(client, f"/cases/{case_id}/analyses/{aid}/follow-up", {"question": FOLLOW_UP})
    assert window(follow) == "expanded"
    llm(app)
    assert window(analyze(client, case_id, analysis_id=aid)) == "expanded"
    llm(app)
    exclude = post(client, f"/cases/{case_id}/analyses/{aid}/exclude", {"record_ids": ["rec-c"]})
    assert window(exclude) == "expanded"
    # Each can be overridden back to the standard window.
    llm(app)
    follow = post(
        client,
        f"/cases/{case_id}/analyses/{aid}/follow-up",
        {"question": FOLLOW_UP, "evidence_window": "standard"},
    )
    assert window(follow) == "standard" and follow["retrieved_record_ids"] == ["rec-a"]
    llm(app)
    assert window(analyze(client, case_id, analysis_id=aid, evidence_window="standard")) == (
        "standard"
    )
    llm(app)
    exclude = post(
        client,
        f"/cases/{case_id}/analyses/{aid}/exclude",
        {"record_ids": ["rec-b"], "evidence_window": "standard"},
    )
    assert window(exclude) == "standard"
    # A fresh question can start expanded; unknown windows are rejected everywhere.
    llm(app)
    assert window(analyze(client, case_id, evidence_window="expanded")) == "expanded"
    analyze(client, case_id, expect=422, evidence_window="wide")
    post(client, f"/cases/{case_id}/analyses/{aid}/follow-up",
         {"question": "Who?", "evidence_window": "wide"}, 422)
    post(client, f"/cases/{case_id}/analyses/{aid}/exclude",
         {"record_ids": ["rec-b"], "evidence_window": "wide"}, 422)


def test_a_restore_returns_the_window_that_evidence_state_was_analyzed_in(app, client) -> None:
    case_id = seed(client)
    llm(app)
    base = analyze(client, case_id)
    llm(app)
    expanded = rerun(client, case_id, base["analysis_id"])
    llm(app)
    excluded = post(
        client,
        f"/cases/{case_id}/analyses/{expanded['analysis_id']}/exclude",
        {"record_ids": ["rec-c"]},
    )
    restored = post(
        client,
        f"/cases/{case_id}/analyses/{excluded['analysis_id']}/restore",
        {"record_ids": ["rec-c"]},
    )
    # The nearest earlier revision with no exclusions is the expanded rerun.
    assert restored["retrieval_coverage"] == expanded["retrieval_coverage"]
    assert restored["parent_analysis_id"] == excluded["analysis_id"]


def test_revisions_saved_before_coverage_count_as_standard(app, client) -> None:
    case_id = seed(client)
    llm(app)
    base = analyze(client, case_id)
    legacy = AnalysisRevision.model_validate(
        {k: v for k, v in base.items() if k != "retrieval_coverage"}
        | {"analysis_id": "analysis_legacy_without_coverage"}
    )
    with app.state.session_factory() as session:
        save_revision(session, legacy)
        assert window_of(get_revision(session, case_id, legacy.analysis_id)) == "standard"
    llm(app)
    follow = post(
        client,
        f"/cases/{case_id}/analyses/{legacy.analysis_id}/follow-up",
        {"question": FOLLOW_UP},
    )
    assert follow["retrieval_coverage"]["evidence_window"] == "standard"
    rerun(client, case_id, legacy.analysis_id, "standard", expect=409)
    llm(app)
    assert rerun(client, case_id, legacy.analysis_id)["retrieval_coverage"]["max_records"] == 2


# --- Validation and contract -------------------------------------------------------------


def _saved(app, client) -> tuple[list[EvidenceRecord], AnalysisRevision]:
    case_id = seed(client)
    llm(app)
    body = analyze(client, case_id)
    records = [
        EvidenceRecord.model_validate(
            {"record_id": rid, "case_id": case_id, "source_file": "sms.json",
             "source_locator": f"messages[{i}]", "content": text}
        )
        for i, (rid, text) in enumerate(CONTENT.items())
    ]
    return records, AnalysisRevision.model_validate(body)


def _with(revision: AnalysisRevision, **changes: Any) -> AnalysisRevision:
    data = revision.to_contract_json()
    coverage = copy.deepcopy(data["retrieval_coverage"])
    coverage.update(changes)
    return AnalysisRevision.model_validate({**data, "retrieval_coverage": coverage})


def test_validate_revision_accepts_server_coverage(app, client) -> None:
    records, revision = _saved(app, client)
    assert validate_revision(records, revision) == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            {"skipped": [{"record_id": "rec-a", "rank": 2, "reason": "record_limit"}],
             "left_out_record_ids": []},
            "lists retrieved records as skipped: rec-a",
        ),
        (
            {"skipped": [{"record_id": "rec-zz", "rank": 2, "reason": "record_limit"}],
             "left_out_record_ids": []},
            "skips records not in this case: rec-zz",
        ),
        (
            {"skipped": [{"record_id": "rec-b", "rank": 1, "reason": "record_limit"}],
             "left_out_record_ids": []},
            "for the record limit at rank 1, inside the 1-record window",
        ),
        (
            {"skipped": [{"record_id": "rec-b", "rank": 2, "reason": "budget"}],
             "left_out_record_ids": []},
            "for the budget at rank 2, outside the 1-record window",
        ),
        (
            {"skipped": [{"record_id": "rec-b", "rank": 2, "reason": "record_limit"},
                         {"record_id": "rec-b", "rank": 3, "reason": "record_limit"}],
             "left_out_record_ids": []},
            "skips rec-b more than once",
        ),
        (
            {"skipped": [{"record_id": "rec-b", "rank": 2, "reason": "record_limit"},
                         {"record_id": "rec-c", "rank": 2, "reason": "record_limit"}],
             "left_out_record_ids": []},
            "repeats skipped rank(s) 2",
        ),
        ({"left_out_record_ids": ["rec-c"]}, "left_out_record_ids must be"),
        ({"left_out_record_ids": [], "offer_expanded_window": False},
         "left_out_record_ids must be"),
        ({"offer_expanded_window": False}, "offer_expanded_window must be true exactly"),
        ({"evidence_window": "expanded"}, "offer_expanded_window must be true exactly"),
        ({"budget_substituted": True}, "budget_substituted is set but no record was skipped"),
        ({"max_records": 1, "skipped": [], "left_out_record_ids": [],
          "offer_expanded_window": False}, None),
    ],
)
def test_validate_revision_rejects_inconsistent_coverage(app, client, change, message) -> None:
    records, revision = _saved(app, client)
    errors = validate_revision(records, _with(revision, **change))
    if message is None:
        assert errors == []
    else:
        assert any(message in e for e in errors), errors


def test_skipped_records_cannot_be_excluded_or_cited(app, client) -> None:
    records, revision = _saved(app, client)
    excluded = revision.model_copy(update={"excluded_record_ids": ["rec-b"]})
    assert any("lists excluded records as skipped: rec-b" in e
               for e in validate_revision(records, excluded))
    data = revision.to_contract_json()
    data["retrieved_record_ids"] = ["rec-a", "rec-c"]
    data["claims"][0]["evidence_ids"] = ["rec-c"]
    errors = validate_revision(records, AnalysisRevision.model_validate(data))
    assert any("lists retrieved records as skipped: rec-c" in e for e in errors)
    assert any("lists cited records as skipped: rec-c" in e for e in errors)
    assert any("but 2 records were retrieved" in e for e in errors)


def test_contract_shape_is_strict_and_omits_unknown_ranks() -> None:
    coverage = {
        "evidence_window": "standard", "max_records": 16, "max_excerpt_chars": 32000,
        "skipped": [{"record_id": "r", "rank": 17, "reason": "record_limit"}],
        "budget_substituted": False, "left_out_record_ids": [], "offer_expanded_window": False,
    }
    model = RetrievalCoverage.model_validate(coverage)
    assert model.to_contract_json() == coverage  # vector/keyword ranks omitted, not null
    for bad in (
        {**coverage, "evidence_window": "wide"},
        {**coverage, "max_records": 0},
        {**coverage, "extra": True},
        {**coverage, "skipped": [{"record_id": "r", "rank": 0, "reason": "record_limit"}]},
        {**coverage, "skipped": [{"record_id": "r", "rank": 2, "reason": "x"}]},
        {**coverage, "skipped": [{"record_id": "r", "rank": 2, "reason": "budget",
                                  "vector_rank": None}]},
        {**coverage, "left_out_record_ids": ["r", "r"]},
    ):
        with pytest.raises(ValidationError):
            RetrievalCoverage.model_validate(bad)
    schema = load_json(CONTRACTS_DIR / "analysis_revision.schema.json")
    assert schema["properties"]["retrieval_coverage"]["required"] == sorted(
        coverage, key=list(coverage).index
    )
    assert "retrieval_coverage" not in schema["required"]  # older revisions omit it


def test_left_out_rule_and_window_config() -> None:
    from app.contracts import SkippedEvidence

    skipped = [
        SkippedEvidence(record_id="k", rank=3, reason="record_limit", keyword_rank=2),
        SkippedEvidence(record_id="v", rank=2, reason="record_limit", vector_rank=4),
        SkippedEvidence(record_id="b", rank=1, reason="budget", vector_rank=1),
        SkippedEvidence(record_id="far", rank=4, reason="record_limit", vector_rank=9),
    ]
    assert left_out(skipped, 4) == ["v", "k"]  # rank order; budget skips never count
    assert left_out(skipped, 1) == []
    expanded = CONFIG.for_window("expanded")
    assert (expanded.max_records, expanded.max_excerpt_chars) == (2, CONFIG.max_excerpt_chars)
    assert CONFIG.for_window("standard") is CONFIG


def test_the_expanded_window_must_be_larger() -> None:
    assert Settings(_env_file=None).RETRIEVAL_EXPANDED_MAX_RECORDS == 24
    assert RetrievalConfig.from_settings(Settings(_env_file=None)).expanded_max_records == 24
    for standard, expanded in ((16, 16), (30, 24)):
        with pytest.raises(ValidationError, match="must be larger than RETRIEVAL_MAX_RECORDS"):
            Settings(
                _env_file=None,
                RETRIEVAL_MAX_RECORDS=standard,
                RETRIEVAL_EXPANDED_MAX_RECORDS=expanded,
            )


def test_provider_failure_on_rerun_is_503_and_a_leak_is_500(app, client, monkeypatch) -> None:
    from app.analysis_llm import AnalysisLLMError
    from app.retrieval import ExcludedRecordLeakError
    from app.routers.analyses import LLM_UNAVAILABLE

    case_id = seed(client)
    llm(app)
    base = analyze(client, case_id)

    class Down(FakeAnalysisLLM):
        def generate(self, messages):
            raise AnalysisLLMError("provider down")

    app.state.analysis_llm = Down()
    assert rerun(client, case_id, base["analysis_id"], expect=503)["detail"] == LLM_UNAVAILABLE
    assert len(client.get(f"/cases/{case_id}/analyses").json()) == 1  # nothing saved

    def leak(*_args, **_kwargs):
        raise ExcludedRecordLeakError("excluded records reached prompt building")

    monkeypatch.setattr("app.routers.analyses.rerun_with_window", leak)
    body = rerun(client, case_id, base["analysis_id"], expect=500)
    assert body["detail"] == "Internal server error"


def test_coverage_never_lists_an_excluded_record_even_if_retrieval_did() -> None:
    from app.evidence_window import coverage_of
    from app.retrieval import RetrievalResult, SkippedRecord

    retrieval = RetrievalResult(
        case_id="case-1", question="q", fts_query="q", records=[], vector_candidates=0,
        keyword_candidates=0, fused_records=2, dropped_by_limit=2, dropped_by_budget=0,
        excerpt_chars=0, excluded_record_ids=[],
        skipped=[
            SkippedRecord(record_id="gone", rank=2, score=0.1, reason="record_limit",
                          vector_rank=1),
            SkippedRecord(record_id="kept", rank=3, score=0.1, reason="record_limit",
                          vector_rank=1, keyword_rank=None),
        ],
    )
    coverage = coverage_of(
        retrieval, "standard", max_records=1, max_excerpt_chars=10,
        excluded_record_ids=["gone"],
    )
    assert [s.record_id for s in coverage.skipped] == ["kept"]
    assert coverage.left_out_record_ids == ["kept"] and coverage.offer_expanded_window
    assert "keyword_rank" not in coverage.to_contract_json()["skipped"][0]
