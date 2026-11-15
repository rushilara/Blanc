"""BLA-13: POST /cases/{case_id}/search (debug view of hybrid retrieval)."""

import pytest
from fastapi.testclient import TestClient

from app.contracts import EvidenceRecord
from app.embeddings import FakeEmbedder
from app.evidence_store import store_records
from app.routers.search import EMPTY_QUESTION


def seed(client: TestClient) -> tuple[str, str]:
    a = client.post("/cases", json={"title": "Case A"}).json()["case_id"]
    b = client.post("/cases", json={"title": "Case B"}).json()["case_id"]
    embedder = FakeEmbedder()
    client.app.state.embedder = embedder
    data = {
        a: [("rec-1", "Alex hands Jordan the warehouse key on Friday."),
            ("rec-2", "The cafeteria menu lists soup.")],
        b: [("rec-1", "Morgan hides the warehouse key in the boathouse.")],
    }
    with client.app.state.session_factory() as session:
        for case_id, items in data.items():
            records = [
                EvidenceRecord.model_validate(
                    {"record_id": rid, "case_id": case_id, "source_file": "notes.txt",
                     "source_locator": f"line {i}", "content": content,
                     "timestamp": "2026-04-10", "timestamp_kind": "sent"}
                )
                for i, (rid, content) in enumerate(items, start=1)
            ]
            store_records(session, case_id, records, embedder)
        session.commit()
    return a, b


def test_search_returns_scoped_results(client: TestClient) -> None:
    a, b = seed(client)
    response = client.post(f"/cases/{a}/search", json={"question": "Who has the warehouse key?"})
    assert response.status_code == 200
    body = response.json()
    assert body["case_id"] == a
    assert body["fts_query"] == '"warehouse" OR "key"'
    top = body["records"][0]
    assert top["record_id"] == "rec-1"
    assert top["matched_by"] == "both"
    assert top["source_file"] == "notes.txt"
    assert top["source_locator"] == "line 1"
    assert (top["timestamp"], top["timestamp_kind"]) == ("2026-04-10", "sent")
    assert top["excerpts"][0]["text"] == "Alex hands Jordan the warehouse key on Friday."
    assert top["keyword"]["rank"] == 1 and top["vector"]["rank"] >= 1
    assert {r["record_id"] for r in body["records"]} == {"rec-1", "rec-2"}
    # Case B's rec-1 (same ID, different content) is never returned for A.
    assert all("boathouse" not in e["text"] for r in body["records"] for e in r["excerpts"])

    b_body = client.post(f"/cases/{b}/search", json={"question": "boathouse"}).json()
    assert [r["record_id"] for r in b_body["records"]] == ["rec-1"]
    assert "boathouse" in b_body["records"][0]["excerpts"][0]["text"]
    assert client.post(f"/cases/{a}/search", json={"question": "boathouse"}).json()[
        "keyword_candidates"
    ] == 0


def test_search_limit_and_empty_question(client: TestClient) -> None:
    a, _ = seed(client)
    limited = client.post(f"/cases/{a}/search", json={"question": "key", "limit": 1}).json()
    assert len(limited["records"]) == 1
    assert limited["dropped_by_limit"] == 1
    assert limited["budget_substituted"] is False
    [skipped] = limited["skipped"]
    assert skipped["reason"] == "record_limit"
    assert skipped["rank"] == 2
    assert skipped["record_id"] in {"rec-1", "rec-2"}
    assert skipped["excerpt_chars"] is None
    assert set(skipped) == {"record_id", "rank", "score", "reason", "excerpt_chars"}  # no text

    full = client.post(f"/cases/{a}/search", json={"question": "key"}).json()
    assert (full["skipped"], full["budget_substituted"]) == ([], False)


@pytest.mark.parametrize("question", ["", "   ", "  ?? ", "*:()", '""'])
def test_search_rejects_empty_or_punctuation_only_question(client: TestClient, question) -> None:
    a, _ = seed(client)
    response = client.post(f"/cases/{a}/search", json={"question": question})
    assert response.status_code == 422
    assert EMPTY_QUESTION in response.json()["detail"][0]["msg"]


def test_search_unknown_case_and_bad_body(client: TestClient) -> None:
    a, _ = seed(client)
    assert client.post("/cases/case_missing/search", json={"question": "key"}).status_code == 404
    bad_limit = client.post(f"/cases/{a}/search", json={"question": "key", "limit": 0})
    assert bad_limit.status_code == 422
    assert client.post(f"/cases/{a}/search", json={"q": "key"}).status_code == 422
