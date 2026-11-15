import re
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import Settings
from app.deps import CaseDep
from app.ids import id_pattern
from app.main import create_app

CASE_ID_RE = re.compile(id_pattern("case"))


def create(client: TestClient, **body: object) -> dict:
    response = client.post("/cases", json={"title": "Case", **body})
    assert response.status_code == 201, response.text
    return response.json()


def test_create_get_list(client: TestClient) -> None:
    created = create(
        client,
        title="  Warehouse fire  ",
        description="Synthetic test case",
        source_corpus_metadata={"source": "synthetic", "files": 3},
    )
    assert CASE_ID_RE.match(created["case_id"])
    assert created["title"] == "Warehouse fire"
    assert created["description"] == "Synthetic test case"
    assert created["import_status"] == "empty"
    assert created["source_corpus_metadata"] == {"source": "synthetic", "files": 3}
    assert datetime.fromisoformat(created["created_at"]).utcoffset() is not None

    fetched = client.get(f"/cases/{created['case_id']}")
    assert fetched.status_code == 200
    assert fetched.json() == created

    listed = client.get("/cases")
    assert listed.status_code == 200
    assert listed.json() == [created]


def test_optional_fields_default_to_null(client: TestClient) -> None:
    created = create(client, title="Minimal")
    assert created["description"] is None
    assert created["source_corpus_metadata"] is None


def test_list_empty_then_newest_first(client: TestClient) -> None:
    assert client.get("/cases").json() == []
    ids = [create(client, title=f"Case {i}")["case_id"] for i in range(5)]
    assert [c["case_id"] for c in client.get("/cases").json()] == list(reversed(ids))


def test_case_ids_are_distinct_and_prefixed(client: TestClient) -> None:
    ids = [create(client, title=f"Case {i}")["case_id"] for i in range(50)]
    assert len(set(ids)) == 50
    assert all(CASE_ID_RE.match(case_id) for case_id in ids)


def test_unknown_case_is_404(client: TestClient) -> None:
    create(client)
    assert client.get("/cases/case_01JZZZZZZZZZZZZZZZZZZZZZZZ").status_code == 404
    assert client.get("/cases/not-a-case").status_code == 404


def test_empty_or_blank_title_is_422(client: TestClient) -> None:
    for body in ({"title": ""}, {"title": "   "}, {}, {"title": None}):
        assert client.post("/cases", json=body).status_code == 422, body
    assert client.get("/cases").json() == []


def test_server_fields_cannot_be_set_by_client(client: TestClient) -> None:
    for extra in ({"case_id": "case_mine"}, {"import_status": "ready"}):
        assert client.post("/cases", json={"title": "x", **extra}).status_code == 422


def test_two_cases_are_independent(client: TestClient) -> None:
    a = create(client, title="Alpha", source_corpus_metadata={"k": "a"})
    b = create(client, title="Beta", source_corpus_metadata={"k": "b"})
    assert a["case_id"] != b["case_id"]
    assert client.get(f"/cases/{a['case_id']}").json() == a
    assert client.get(f"/cases/{b['case_id']}").json() == b


def test_cases_persist_across_restarts(db_url: str) -> None:
    settings = Settings(_env_file=None, DATABASE_URL=db_url)
    with TestClient(create_app(settings)) as first:
        created = create(first, title="Durable")
    with TestClient(create_app(settings)) as second:
        assert second.get(f"/cases/{created['case_id']}").json() == created


def test_sqlite_parent_dir_created_and_foreign_keys_on(tmp_path: Path) -> None:
    db_file = tmp_path / "nested" / "dir" / "blanc.db"
    app = create_app(Settings(_env_file=None, DATABASE_URL=f"sqlite:///{db_file}"))
    with TestClient(app):
        assert db_file.parent.is_dir()
        with app.state.engine.connect() as conn:
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_in_memory_database_works() -> None:
    app = create_app(Settings(_env_file=None, DATABASE_URL="sqlite://"))
    with TestClient(app) as client:
        created = create(client)
        assert client.get("/cases").json() == [created]


def test_require_case_guards_nested_routes(app: FastAPI) -> None:
    """Future /cases/{case_id}/... routes resolve the case via CaseDep and 404 otherwise."""

    @app.get("/cases/{case_id}/probe")
    def probe(case: CaseDep) -> dict[str, str]:
        return {"case_id": case.case_id}

    with TestClient(app) as client:
        a = create(client)["case_id"]
        assert client.get(f"/cases/{a}/probe").json() == {"case_id": a}
        assert client.get("/cases/case_01JZZZZZZZZZZZZZZZZZZZZZZZ/probe").status_code == 404


def test_a_case_with_stored_records_is_listed_as_ready(client: TestClient) -> None:
    """The workspace only lets investigators ask about a ``ready`` case (#45)."""
    from _paths import RAW

    from app.contracts import EvidenceRecord
    from app.embeddings import FakeEmbedder
    from app.evidence_store import store_records

    with_records, without = create(client)["case_id"], create(client)["case_id"]
    records = [
        EvidenceRecord.model_validate(dict(r, case_id=with_records)) for r in RAW["records"]
    ]
    with client.app.state.session_factory() as session:
        store_records(session, with_records, records, FakeEmbedder())
        session.commit()

    statuses = {c["case_id"]: c["import_status"] for c in client.get("/cases").json()}
    assert statuses == {with_records: "ready", without: "empty"}
    assert client.get(f"/cases/{with_records}").json()["import_status"] == "ready"
