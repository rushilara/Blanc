"""BLA-29: POST /cases/{case_id}/analyses/{analysis_id}/follow-up."""

from __future__ import annotations

import copy
import json
from typing import Any

from _paths import CONTRACTS_DIR, RAW, load_json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from app.analysis_llm import AnalysisLLMError, FakeAnalysisLLM
from app.contracts import EvidenceRecord
from app.embeddings import FakeEmbedder
from app.evidence_store import store_records
from app.follow_up import MAX_TEXT_CHARS, _clip
from app.routers.analyses import LLM_UNAVAILABLE
from app.schemas.analyses import EMPTY_QUESTION

REVISION_SCHEMA = Draft202012Validator(
    load_json(CONTRACTS_DIR / "analysis_revision.schema.json")
)
OUTPUT_KEYS = ("answer", "claims", "graph", "timeline_events", "unresolved_questions")
QUESTION = RAW["revision"]["question"]
FOLLOW_UP = "When did Jordan contact Casey about the meeting?"
NOTE = "Investigator hunch: check the warehouse cameras."


def output(key: str = "revision") -> dict[str, Any]:
    return {k: copy.deepcopy(RAW[key][k]) for k in OUTPUT_KEYS}


def seed(client: TestClient) -> str:
    case_id = client.post("/cases", json={"title": "Follow-up"}).json()["case_id"]
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


def follow_up(client, case_id, analysis_id, question=FOLLOW_UP, board_id=None, expect=201):
    body: dict[str, Any] = {"question": question}
    if board_id is not None:
        body["board_id"] = board_id
    response = client.post(f"/cases/{case_id}/analyses/{analysis_id}/follow-up", json=body)
    assert response.status_code == expect, response.text
    return response.json()


def board_with_pins_and_note(client: TestClient, case_id: str, analysis_id: str) -> str:
    board_id = client.post(f"/cases/{case_id}/boards", json={"title": "Alex–Casey link"}).json()[
        "board_id"
    ]
    pins = [
        {"kind": kind, "source_analysis_id": analysis_id, "ref_id": ref}
        for kind, ref in [
            ("claim", "claim-1"),
            ("claim", "claim-2"),
            ("node", "jordan"),
            ("edge", "edge-1"),
            ("event", "event-2"),
        ]
    ]
    response = client.patch(
        f"/cases/{case_id}/boards/{board_id}", json={"pin": pins, "notes": [{"text": NOTE}]}
    )
    assert response.status_code == 200, response.text
    return board_id


def prompt(fake: FakeAnalysisLLM) -> dict:
    return json.loads(fake.calls[-1][1]["content"])


# --- The child revision ---------------------------------------------------------------------


def test_a_follow_up_is_a_new_child_with_fresh_retrieval(app, client) -> None:
    case_id = seed(client)
    base = base_analysis(app, client, case_id)
    fake = llm(app, [output()])
    child = follow_up(client, case_id, base["analysis_id"])

    assert not list(REVISION_SCHEMA.iter_errors(child))
    assert child["parent_analysis_id"] == base["analysis_id"]
    assert child["question"] == FOLLOW_UP
    assert child["analysis_id"] != base["analysis_id"]
    payload = prompt(fake)
    assert payload["investigator_question"] == FOLLOW_UP  # retrieval and prompt use it
    assert {r["record_id"] for r in payload["source_records"]} == {"rec-01", "rec-02"}
    # The parent is unchanged.
    assert client.get(f"/cases/{case_id}/analyses/{base['analysis_id']}").json() == base


def test_the_parent_is_passed_as_context_not_evidence(app, client) -> None:
    case_id = seed(client)
    base = base_analysis(app, client, case_id)
    fake = llm(app, [output()])
    follow_up(client, case_id, base["analysis_id"])

    payload = prompt(fake)
    assert payload["context"]["label"] == "prior analysis — not evidence"
    context = json.loads(payload["context"]["text"])
    assert context["previous_question"] == QUESTION
    assert context["previous_answer"] == base["answer"]
    assert [c["text"] for c in context["previous_claims"]] == [c["text"] for c in base["claims"]]
    assert context["unresolved_questions"] == base["unresolved_questions"]
    assert "board" not in context  # no board named


def test_the_board_topic_and_pinned_findings_are_passed_but_never_notes(app, client) -> None:
    case_id = seed(client)
    base = base_analysis(app, client, case_id)
    board_id = board_with_pins_and_note(client, case_id, base["analysis_id"])
    fake = llm(app, [output()])
    follow_up(client, case_id, base["analysis_id"], board_id=board_id)

    context = json.loads(prompt(fake)["context"]["text"])
    assert context["board"]["topic"] == "Alex–Casey link"
    assert context["board"]["pinned"] == [
        {
            "kind": "claim",
            "text": "Alex communicated with Jordan about a document delivery.",
            "status": "reported",
        },
        {
            "kind": "claim",
            "text": "Jordan discussed a possible meeting with Casey.",
            "status": "reported",
        },
        {"kind": "node", "text": "Jordan (person)"},
        {
            "kind": "edge",
            "text": "Alex communicated with Jordan: Email communication",
            "status": "reported",
        },
        {
            "kind": "event",
            "text": "Jordan messaged Casey about a possible meeting. (2026-04-10T10:30:00Z)",
            "status": "reported",
        },
    ]
    # Investigator notes never reach the model.
    assert NOTE not in json.dumps(prompt(fake), ensure_ascii=False)
    assert NOTE not in json.dumps(fake.calls[-1], ensure_ascii=False)


def test_exclusions_are_inherited_and_excluded_pins_are_dropped(app, client) -> None:
    case_id = seed(client)
    base = base_analysis(app, client, case_id)
    board_id = board_with_pins_and_note(client, case_id, base["analysis_id"])
    llm(app, [output("child")])
    excluded = client.post(
        f"/cases/{case_id}/analyses/{base['analysis_id']}/exclude",
        json={"record_ids": ["rec-02"]},
    ).json()

    fake = llm(app, [output("child")])
    child = follow_up(client, case_id, excluded["analysis_id"], board_id=board_id)
    assert child["excluded_record_ids"] == ["rec-02"]
    assert child["retrieved_record_ids"] == ["rec-01"]
    payload = prompt(fake)
    assert [r["record_id"] for r in payload["source_records"]] == ["rec-01"]
    pinned = json.loads(payload["context"]["text"])["board"]["pinned"]
    # claim-2, the jordan node (rec-01 and rec-02) and event-2 cite rec-02: left out.
    assert [p["text"] for p in pinned] == [
        "Alex communicated with Jordan about a document delivery.",
        "Alex communicated with Jordan: Email communication",
    ]
    assert "Jordan discussed a possible meeting with Casey." not in json.dumps(payload)


# --- Comparison guard ----------------------------------------------------------------------


def test_a_follow_up_has_no_support_comparison(app, client) -> None:
    case_id = seed(client)
    base = base_analysis(app, client, case_id)
    llm(app, [output()])
    child = follow_up(client, case_id, base["analysis_id"])
    response = client.get(f"/cases/{case_id}/analyses/{child['analysis_id']}/comparison")
    assert response.status_code == 422
    assert "asks a different question than its parent" in response.json()["detail"]


# --- Errors ---------------------------------------------------------------------------------


def test_follow_up_errors(app, client) -> None:
    case_id = seed(client)
    other = seed(client)
    base = base_analysis(app, client, case_id)
    fake = llm(app, [output()])
    body = follow_up(client, case_id, base["analysis_id"], question="???", expect=422)
    assert EMPTY_QUESTION in json.dumps(body)
    follow_up(client, case_id, "analysis-missing", expect=404)
    follow_up(client, other, base["analysis_id"], expect=404)  # another case's revision
    follow_up(client, case_id, base["analysis_id"], board_id="board_missing", expect=404)
    other_board = client.post(f"/cases/{other}/boards", json={"title": "Other"}).json()
    follow_up(client, case_id, base["analysis_id"], board_id=other_board["board_id"], expect=404)
    assert fake.calls == []  # nothing reached the model
    response = client.post(
        f"/cases/{case_id}/analyses/{base['analysis_id']}/follow-up",
        json={"question": FOLLOW_UP, "extra": 1},
    )
    assert response.status_code == 422


class FailingLLM(FakeAnalysisLLM):
    def generate(self, messages):
        raise AnalysisLLMError("provider down")


def test_provider_failure_is_503_and_saves_nothing(app, client) -> None:
    case_id = seed(client)
    base = base_analysis(app, client, case_id)
    app.state.analysis_llm = FailingLLM()
    body = follow_up(client, case_id, base["analysis_id"], expect=503)
    assert body["detail"] == LLM_UNAVAILABLE
    assert len(client.get(f"/cases/{case_id}/analyses").json()) == 1


def test_long_context_text_is_clipped() -> None:
    assert _clip("a  b\n c") == "a b c"
    clipped = _clip("x" * (MAX_TEXT_CHARS + 50))
    assert len(clipped) == MAX_TEXT_CHARS and clipped.endswith("…")


def test_review_status_and_corrections_never_reach_the_model(app, client) -> None:
    """BLA-48: investigator judgement stays on the board, like notes."""
    case_id = seed(client)
    base = base_analysis(app, client, case_id)
    board_id = board_with_pins_and_note(client, case_id, base["analysis_id"])
    board = client.get(f"/cases/{case_id}/boards/{board_id}").json()
    claim_item = next(i["item_id"] for i in board["items"] if i["ref_id"] == "claim-1")
    correction = "Investigator correction: the delivery was cancelled."
    response = client.patch(
        f"/cases/{case_id}/boards/{board_id}",
        json={
            "review": [{"item_id": claim_item, "review_status": "disputed"}],
            "notes": [{"text": correction, "kind": "correction", "target_item_id": claim_item}],
        },
    )
    assert response.status_code == 200, response.text
    fake = llm(app, [output()])
    follow_up(client, case_id, base["analysis_id"], board_id=board_id)

    sent = json.dumps(fake.calls[-1], ensure_ascii=False)
    assert correction not in sent and "disputed" not in sent and "review_status" not in sent
    pinned = json.loads(prompt(fake)["context"]["text"])["board"]["pinned"]
    assert pinned[0] == {
        "kind": "claim",
        "text": "Alex communicated with Jordan about a document delivery.",
        "status": "reported",  # the model's label only
    }
