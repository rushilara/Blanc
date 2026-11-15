"""BLA-20: POST /cases/{case_id}/analyses (retrieve → analyze → save immutable revision)."""

from __future__ import annotations

import copy
import json
from datetime import datetime
from typing import Any

import pytest
from _paths import CONTRACTS_DIR, RAW, load_json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from app.analyses import get_revision, prior_analysis_summary, save_revision
from app.analysis_llm import AnalysisLLMError, FakeAnalysisLLM
from app.contracts import AnalysisRevision, EvidenceRecord
from app.embeddings import FakeEmbedder
from app.evidence_store import store_records
from app.models import AnalysisRevisionRow
from app.retrieval import ExcludedRecordLeakError
from app.routers.analyses import LLM_UNAVAILABLE
from app.schemas.analyses import EMPTY_QUESTION

REVISION_SCHEMA = Draft202012Validator(
    load_json(CONTRACTS_DIR / "analysis_revision.schema.json")
)
OUTPUT_KEYS = ("answer", "claims", "graph", "timeline_events", "unresolved_questions")


def good_output() -> dict[str, Any]:
    return {key: copy.deepcopy(RAW["revision"][key]) for key in OUTPUT_KEYS}


def fabricated(evidence_id: str = "rec-99") -> dict[str, Any]:
    output = good_output()
    output["claims"][0]["evidence_ids"] = [evidence_id]
    return output


def seed_case(client: TestClient, *, title: str = "Analyze") -> str:
    case_id = client.post("/cases", json={"title": title}).json()["case_id"]
    embedder = FakeEmbedder()
    client.app.state.embedder = embedder
    records = [
        EvidenceRecord.model_validate(dict(r, case_id=case_id)) for r in RAW["records"]
    ]
    with client.app.state.session_factory() as session:
        store_records(session, case_id, records, embedder)
        session.commit()
    return case_id


def install_llm(app: FastAPI, responses: list[Any]) -> FakeAnalysisLLM:
    llm = FakeAnalysisLLM(responses=responses)
    app.state.analysis_llm = llm
    return llm


def valid(body: dict) -> dict:
    errors = sorted(REVISION_SCHEMA.iter_errors(body), key=str)
    assert not errors, [e.message for e in errors]
    AnalysisRevision.model_validate_json(json.dumps(body))
    return body


def analyze(
    client: TestClient, case_id: str, question: str, *, analysis_id: str | None = None
) -> Any:
    body: dict[str, Any] = {"question": question}
    if analysis_id is not None:
        body["analysis_id"] = analysis_id
    return client.post(f"/cases/{case_id}/analyses", json=body)


def test_analyze_saves_and_returns_schema_valid_revision(app, client) -> None:
    case_id = seed_case(client)
    llm = install_llm(app, [good_output()])
    question = RAW["revision"]["question"]
    response = analyze(client, case_id, question)
    assert response.status_code == 201, response.text
    body = valid(response.json())

    assert body["case_id"] == case_id
    assert body["question"] == question
    assert body["schema_version"] == "1.0"
    assert body["parent_analysis_id"] is None
    assert body["excluded_record_ids"] == []
    assert set(body["retrieved_record_ids"]) == {"rec-01", "rec-02"}
    assert body["analysis_id"].startswith("analysis_")
    assert body["analysis_id"] != RAW["revision"]["analysis_id"]
    assert body["created_at"] != RAW["revision"]["created_at"]
    datetime.fromisoformat(body["created_at"].replace("Z", "+00:00"))
    assert "needs_review" not in body

    assert len(llm.calls) == 1
    with app.state.session_factory() as session:
        saved = get_revision(session, case_id, body["analysis_id"])
    assert saved.to_contract_json() == body


def test_smoke_end_to_end_with_test_client(app, client) -> None:
    case_id = seed_case(client, title="Smoke")
    install_llm(app, [good_output()])
    response = analyze(client, case_id, "How are Alex and Casey connected?")
    assert response.status_code == 201
    body = valid(response.json())
    assert body["answer"]
    assert body["claims"] and body["graph"]["edges"] and body["timeline_events"]


def test_prior_analysis_sets_parent_and_passes_context(app, client) -> None:
    case_id = seed_case(client)
    install_llm(app, [good_output()])
    first = valid(analyze(client, case_id, "How are Alex and Casey connected?").json())

    llm = install_llm(app, [good_output()])
    follow = valid(
        analyze(
            client, case_id, "What else connects them?", analysis_id=first["analysis_id"]
        ).json()
    )
    assert follow["parent_analysis_id"] == first["analysis_id"]
    assert follow["analysis_id"] != first["analysis_id"]
    assert follow["excluded_record_ids"] == []

    prompt = json.loads(llm.calls[0][-1]["content"])
    assert prompt["context"]["label"] == "prior analysis — not evidence"
    assert first["answer"][:40] in prompt["context"]["text"]


def test_follow_up_inherits_prior_exclusions(app, client) -> None:
    """A child question must keep the parent's excluded set (restoring is BLA-35)."""
    case_id = seed_case(client)
    install_llm(app, [good_output()])
    first = valid(analyze(client, case_id, "How are Alex and Casey connected?").json())

    # Craft a sibling of the first answer that excludes rec-02 (valid citations only on rec-01).
    excluded = AnalysisRevision.model_validate(
        {
            **first,
            "analysis_id": "analysis_excluded_parent_for_test",
            "parent_analysis_id": None,
            "excluded_record_ids": ["rec-02"],
            "retrieved_record_ids": ["rec-01"],
            "claims": [
                {
                    "id": "claim-1",
                    "text": "Alex communicated with Jordan.",
                    "evidence_ids": ["rec-01"],
                    "status": "observed",
                }
            ],
            "graph": {
                "nodes": [
                    {"id": "alex", "label": "Alex", "type": "person", "evidence_ids": ["rec-01"]},
                    {
                        "id": "jordan",
                        "label": "Jordan",
                        "type": "person",
                        "evidence_ids": ["rec-01"],
                    },
                ],
                "edges": [
                    {
                        "id": "edge-1",
                        "source": "alex",
                        "target": "jordan",
                        "type": "communicated_with",
                        "evidence_ids": ["rec-01"],
                        "status": "observed",
                    }
                ],
            },
            "timeline_events": [
                {
                    "id": "event-1",
                    "description": "Alex messaged Jordan.",
                    "timestamp": "2024-03-01T10:00:00Z",
                    "timestamp_precision": "exact",
                    "timestamp_kind": "observed",
                    "evidence_ids": ["rec-01"],
                    "status": "observed",
                }
            ],
            "unresolved_questions": [],
            "created_at": first["created_at"],
        }
    )
    with app.state.session_factory() as session:
        save_revision(session, excluded)

    llm = install_llm(app, [good_output()])
    # good_output cites rec-02; with inheritance it must be excluded from retrieval/prompt.
    follow = analyze(
        client,
        case_id,
        "What else connects them?",
        analysis_id="analysis_excluded_parent_for_test",
    )
    # May be 201 with review/partial or still 201 after sanitize; either way exclusions stick.
    assert follow.status_code == 201, follow.text
    body = follow.json()
    assert body["parent_analysis_id"] == "analysis_excluded_parent_for_test"
    assert body["excluded_record_ids"] == ["rec-02"]
    assert "rec-02" not in body["retrieved_record_ids"]
    prompt = json.loads(llm.calls[0][-1]["content"])
    assert all(r["record_id"] != "rec-02" for r in prompt["source_records"])


def test_unknown_prior_analysis_is_404(app, client) -> None:
    case_id = seed_case(client)
    install_llm(app, [good_output()])
    response = analyze(
        client, case_id, "Anything with letters?", analysis_id="analysis_missing_prior_xxxxxxxx"
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Prior analysis not found"


def test_prior_analysis_from_another_case_is_404(app, client) -> None:
    a = seed_case(client, title="A")
    b = seed_case(client, title="B")
    install_llm(app, [good_output()])
    prior = valid(analyze(client, a, "How are Alex and Casey connected?").json())
    install_llm(app, [good_output()])
    response = analyze(client, b, "Follow up question?", analysis_id=prior["analysis_id"])
    assert response.status_code == 404


def test_bad_then_good_triggers_retry_path(app, client) -> None:
    case_id = seed_case(client)
    llm = install_llm(app, [fabricated(), good_output()])
    body = valid(analyze(client, case_id, "How are Alex and Casey connected?").json())
    assert len(llm.calls) == 2
    assert "needs_review" not in body
    assert [c["id"] for c in body["claims"]] == ["claim-1", "claim-2"]


def test_missing_key_or_provider_failure_is_503(app, client) -> None:
    case_id = seed_case(client)

    class BrokenLLM:
        model = "broken"

        def generate(self, messages):
            raise AnalysisLLMError("GEMINI_API_KEY is not set; cannot call the Gemini LLM")

    app.state.analysis_llm = BrokenLLM()
    response = analyze(client, case_id, "How are Alex and Casey connected?")
    assert response.status_code == 503
    assert response.json()["detail"] == LLM_UNAVAILABLE
    assert "GEMINI" not in response.text and "key" not in response.text.lower()


def test_unsearchable_question_is_422(app, client) -> None:
    """Same rule as POST /search: blank or punctuation-only → 422, no LLM call."""
    case_id = seed_case(client)
    llm = install_llm(app, [good_output()])
    for question in ("", "   ", "???", "...", "\t\n"):
        response = analyze(client, case_id, question)
        assert response.status_code == 422, question
        assert EMPTY_QUESTION in response.text
    assert llm.calls == []


def test_unknown_case_and_extra_fields(app, client) -> None:
    case_id = seed_case(client)
    install_llm(app, [good_output()])
    assert (
        client.post("/cases/case_missing/analyses", json={"question": "hi there"}).status_code
        == 404
    )
    bad = client.post(
        f"/cases/{case_id}/analyses", json={"question": "hi there", "extra": True}
    )
    assert bad.status_code == 422


def test_revision_is_immutable_in_the_database(app, client) -> None:
    case_id = seed_case(client)
    install_llm(app, [good_output()])
    body = valid(analyze(client, case_id, "How are Alex and Casey connected?").json())
    with app.state.session_factory() as session:
        with pytest.raises(DatabaseError, match="immutable"):
            session.execute(text("UPDATE analysis_revisions SET parent_analysis_id = 'x'"))
        session.rollback()
        row = session.get(AnalysisRevisionRow, (case_id, body["analysis_id"]))
        assert row is not None and row.revision["analysis_id"] == body["analysis_id"]


def test_excluded_record_leak_is_500(app, client, monkeypatch) -> None:
    case_id = seed_case(client)
    install_llm(app, [good_output()])

    def boom(*_args, **_kwargs):
        raise ExcludedRecordLeakError("excluded records reached prompt building: ['rec-01']")

    monkeypatch.setattr("app.routers.analyses.run_case_analysis", boom)
    response = analyze(client, case_id, "How are Alex and Casey connected?")
    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"


def test_prior_analysis_summary_is_short() -> None:
    revision = AnalysisRevision.model_validate(copy.deepcopy(RAW["revision"]))
    summary = prior_analysis_summary(revision)
    assert revision.answer[:20] in summary
    assert "Alex communicated" in summary
