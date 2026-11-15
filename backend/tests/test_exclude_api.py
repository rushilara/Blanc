"""BLA-33: POST /cases/{case_id}/analyses/{analysis_id}/exclude and the support diff."""

from __future__ import annotations

import copy
import json
from typing import Any

from _paths import CONTRACTS_DIR, RAW, load_json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from app.analyses import prior_findings_context
from app.analysis_llm import AnalysisLLMError, FakeAnalysisLLM
from app.contracts import AnalysisRevision
from app.retrieval import ExcludedRecordLeakError
from app.routers.analyses import LLM_UNAVAILABLE
from evidence_prompt import SYSTEM_PROMPT

REVISION_SCHEMA = Draft202012Validator(
    load_json(CONTRACTS_DIR / "analysis_revision.schema.json")
)
COMPARISON_SCHEMA = Draft202012Validator(
    load_json(CONTRACTS_DIR / "revision_comparison.schema.json")
)
OUTPUT_KEYS = ("answer", "claims", "graph", "timeline_events", "unresolved_questions")
QUESTION = RAW["revision"]["question"]


def output(key: str = "revision") -> dict[str, Any]:
    """The LLM-authored fields of a fixture revision."""
    return {k: copy.deepcopy(RAW[key][k]) for k in OUTPUT_KEYS}


EMPTY = {
    "answer": "",
    "claims": [],
    "graph": {"nodes": [], "edges": []},
    "timeline_events": [],
    "unresolved_questions": ["Nothing left to analyze."],
}


def seed(client: TestClient) -> str:
    from app.contracts import EvidenceRecord
    from app.embeddings import FakeEmbedder
    from app.evidence_store import store_records

    case_id = client.post("/cases", json={"title": "Exclude"}).json()["case_id"]
    embedder = FakeEmbedder()
    client.app.state.embedder = embedder
    records = [EvidenceRecord.model_validate(dict(r, case_id=case_id)) for r in RAW["records"]]
    with client.app.state.session_factory() as session:
        store_records(session, case_id, records, embedder)
        session.commit()
    return case_id


def llm(app: FastAPI, responses: list[Any]) -> FakeAnalysisLLM:
    fake = FakeAnalysisLLM(responses=responses)
    app.state.analysis_llm = fake
    return fake


def base_analysis(app: FastAPI, client: TestClient, case_id: str) -> dict:
    llm(app, [output()])
    response = client.post(f"/cases/{case_id}/analyses", json={"question": QUESTION})
    assert response.status_code == 201, response.text
    return response.json()


def exclude(client: TestClient, case_id: str, analysis_id: str, record_ids, expect=201):
    response = client.post(
        f"/cases/{case_id}/analyses/{analysis_id}/exclude", json={"record_ids": record_ids}
    )
    assert response.status_code == expect, response.text
    return response.json()


def cited(revision: dict) -> set[str]:
    ids: set[str] = set()
    for item in (
        *revision["claims"],
        *revision["graph"]["edges"],
        *revision["timeline_events"],
        *revision["graph"]["nodes"],
    ):
        ids.update(item.get("evidence_ids", []))
    return ids


def prompt_payload(fake: FakeAnalysisLLM, call: int = -1) -> dict:
    return json.loads(fake.calls[call][1]["content"])


# --- The rerun ------------------------------------------------------------------------------


def test_exclusion_reruns_on_permitted_evidence_and_links_the_parent(app, client) -> None:
    case_id = seed(client)
    base = base_analysis(app, client, case_id)
    fake = llm(app, [output("child")])
    child = exclude(client, case_id, base["analysis_id"], ["rec-02"])

    assert not list(REVISION_SCHEMA.iter_errors(child))
    assert child["parent_analysis_id"] == base["analysis_id"]
    assert child["question"] == base["question"]
    assert child["excluded_record_ids"] == ["rec-02"]
    # The excluded record is in neither the retrieval set, the prompt, nor any citation.
    assert child["retrieved_record_ids"] == ["rec-01"]
    assert [r["record_id"] for r in prompt_payload(fake)["source_records"]] == ["rec-01"]
    assert "rec-02" not in cited(child)
    assert child.get("needs_review") is not True
    assert len(fake.calls) == 1


def test_the_excluded_record_never_appears_even_if_the_model_cites_it(app, client) -> None:
    case_id = seed(client)
    base = base_analysis(app, client, case_id)
    # The model ignores the exclusion and returns the base findings, which cite rec-02.
    llm(app, [output(), output()])
    child = exclude(client, case_id, base["analysis_id"], ["rec-02"])
    assert "rec-02" not in cited(child)
    assert "rec-02" not in child["retrieved_record_ids"]
    assert child["needs_review"] is True  # findings citing it were stripped and flagged


def test_the_excluded_record_stays_in_the_corpus_and_the_history(app, client) -> None:
    case_id = seed(client)
    base = base_analysis(app, client, case_id)
    llm(app, [output("child")])
    child = exclude(client, case_id, base["analysis_id"], ["rec-02"])

    assert client.get(f"/cases/{case_id}/records/rec-02").status_code == 200
    assert client.get(f"/cases/{case_id}/analyses/{base['analysis_id']}").json() == base
    history = client.get(f"/cases/{case_id}/analyses").json()
    assert {(h["analysis_id"], tuple(h["excluded_record_ids"])) for h in history} == {
        (base["analysis_id"], ()),
        (child["analysis_id"], ("rec-02",)),
    }
    # Restoring (BLA-35) brings back the base's state without a rerun.
    restored = client.post(
        f"/cases/{case_id}/analyses/{child['analysis_id']}/restore",
        json={"record_ids": ["rec-02"]},
    )
    assert restored.status_code == 201
    assert restored.headers["X-Restored-From"] == base["analysis_id"]


def test_exclusions_accumulate_across_reruns(app, client) -> None:
    case_id = seed(client)
    base = base_analysis(app, client, case_id)
    llm(app, [output("child")])
    child = exclude(client, case_id, base["analysis_id"], ["rec-02"])
    fake = llm(app, [EMPTY])
    grandchild = exclude(client, case_id, child["analysis_id"], ["rec-01", "rec-02"])
    assert grandchild["excluded_record_ids"] == ["rec-01", "rec-02"]
    assert grandchild["retrieved_record_ids"] == []
    assert prompt_payload(fake)["source_records"] == []
    assert cited(grandchild) == set()


# --- What the rerun is told -----------------------------------------------------------------


def test_the_rerun_sees_only_earlier_findings_that_do_not_rely_on_the_excluded_record(
    app, client
) -> None:
    case_id = seed(client)
    base = base_analysis(app, client, case_id)
    fake = llm(app, [output("child")])
    exclude(client, case_id, base["analysis_id"], ["rec-02"])

    assert fake.calls[0][0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert "reuse that finding's id and its exact wording" in SYSTEM_PROMPT
    context = json.loads(prompt_payload(fake)["context"]["text"])
    assert prompt_payload(fake)["context"]["label"] == "prior analysis — not evidence"
    assert [c["id"] for c in context["claims"]] == ["claim-1"]  # claim-2 cites rec-02
    assert [e["id"] for e in context["edges"]] == ["edge-1"]
    assert [e["id"] for e in context["timeline_events"]] == ["event-1"]
    nodes = {n["id"]: n for n in context["nodes"]}
    assert set(nodes) == {"alex", "jordan"}  # casey's only record is excluded
    assert nodes["jordan"]["evidence_ids"] == ["rec-01"]  # rec-02 stripped from the node
    # Nothing written with the excluded record in view reaches the prompt.
    text = json.dumps(prompt_payload(fake))
    assert base["answer"] not in text
    for claim in base["claims"]:
        if "rec-02" in claim["evidence_ids"]:
            assert claim["text"] not in text
    assert "rec-02" not in text.replace('"excluded_record_ids"', "")


def test_prior_findings_context_drops_edges_whose_endpoints_were_dropped() -> None:
    base = AnalysisRevision.model_validate(copy.deepcopy(RAW["revision"]))
    # Excluding both records leaves nothing to show.
    context = json.loads(prior_findings_context(base, ["rec-01", "rec-02"]))
    assert context["claims"] == context["edges"] == context["timeline_events"] == []
    assert context["nodes"] == []
    # Excluding rec-01 drops alex and the edge to it, even though jordan survives.
    context = json.loads(prior_findings_context(base, ["rec-01"]))
    assert {n["id"] for n in context["nodes"]} == {"jordan", "casey"}
    assert [e["id"] for e in context["edges"]] == ["edge-2"]


# --- Comparison -----------------------------------------------------------------------------


def test_an_unaffected_finding_is_still_supported_after_the_exclusion(app, client) -> None:
    case_id = seed(client)
    base = base_analysis(app, client, case_id)
    llm(app, [output("child")])
    child = exclude(client, case_id, base["analysis_id"], ["rec-02"])

    response = client.get(f"/cases/{case_id}/analyses/{child['analysis_id']}/comparison")
    assert response.status_code == 200
    comparison = response.json()
    assert not list(COMPARISON_SCHEMA.iter_errors(comparison))
    statuses = {
        (i["kind"], i["base_ref_id"]): i["support_status"] for i in comparison["items"]
    }
    assert statuses[("claim", "claim-1")] == "still_supported"
    assert statuses[("edge", "edge-1")] == "still_supported"
    assert statuses[("event", "event-1")] == "still_supported"
    assert statuses[("claim", "claim-2")] == "not_supported"
    assert comparison["excluded_record_ids"] == ["rec-02"]
    assert comparison["base_analysis_id"] == base["analysis_id"]
    assert comparison["compared_analysis_id"] == child["analysis_id"]
    # Deterministic: created_at is the child's, so a second request is identical.
    assert comparison["created_at"] == child["created_at"]
    again = client.get(f"/cases/{case_id}/analyses/{child['analysis_id']}/comparison")
    assert again.json() == comparison


def test_a_revision_without_a_parent_has_no_comparison(app, client) -> None:
    case_id = seed(client)
    base = base_analysis(app, client, case_id)
    response = client.get(f"/cases/{case_id}/analyses/{base['analysis_id']}/comparison")
    assert response.status_code == 404
    assert "has no parent" in response.json()["detail"]
    missing = client.get(f"/cases/{case_id}/analyses/analysis-missing/comparison")
    assert missing.status_code == 404


# --- Errors ---------------------------------------------------------------------------------


def test_invalid_exclusions_are_rejected_without_an_llm_call(app, client) -> None:
    case_id = seed(client)
    base = base_analysis(app, client, case_id)
    fake = llm(app, [output("child")])
    body = exclude(client, case_id, base["analysis_id"], ["rec-99"], expect=422)
    assert body["detail"] == "not records of this case, so they cannot be excluded: rec-99"
    exclude(client, case_id, base["analysis_id"], [], expect=422)
    assert fake.calls == []  # rejected before retrieval or the model
    child = exclude(client, case_id, base["analysis_id"], ["rec-02"])
    fake = llm(app, [output("child")])
    body = exclude(client, case_id, child["analysis_id"], ["rec-02"], expect=422)
    assert body["detail"] == f"already excluded in analysis {child['analysis_id']}: rec-02"
    assert fake.calls == []
    response = client.post(
        f"/cases/{case_id}/analyses/{base['analysis_id']}/exclude",
        json={"record_ids": ["rec-01"], "extra": True},
    )
    assert response.status_code == 422


def test_exclusion_is_scoped_to_the_case(app, client) -> None:
    case_id = seed(client)
    other = seed(client)
    base = base_analysis(app, client, case_id)
    exclude(client, other, base["analysis_id"], ["rec-02"], expect=404)
    exclude(client, case_id, "analysis-missing", ["rec-02"], expect=404)
    assert client.post(
        "/cases/case-missing/analyses/x/exclude", json={"record_ids": ["rec-02"]}
    ).status_code == 404


class FailingLLM(FakeAnalysisLLM):
    def generate(self, messages):
        raise AnalysisLLMError("provider down")


def test_provider_failure_is_503_and_saves_nothing(app, client) -> None:
    case_id = seed(client)
    base = base_analysis(app, client, case_id)
    app.state.analysis_llm = FailingLLM()
    body = exclude(client, case_id, base["analysis_id"], ["rec-02"], expect=503)
    assert body["detail"] == LLM_UNAVAILABLE
    assert len(client.get(f"/cases/{case_id}/analyses").json()) == 1


def test_an_exclusion_leak_is_a_500(app, client, monkeypatch) -> None:
    import app.analyses as analyses_module

    def leak(result, excluded):
        raise ExcludedRecordLeakError("leaked")

    case_id = seed(client)
    base = base_analysis(app, client, case_id)
    monkeypatch.setattr(analyses_module, "permitted_records", leak)
    exclude(client, case_id, base["analysis_id"], ["rec-02"], expect=500)
