"""BLA-59: GET /cases/{case_id}/records/{record_id} (+ list), scoped to one case."""

from dataclasses import dataclass

import pytest
from _paths import CONTRACTS_DIR, RAW, load_json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from app.contracts import EvidenceRecord
from app.embeddings import FakeEmbedder
from app.evidence_store import list_chunks, store_records
from app.models import Case
from app.records import InvalidExcerptError, span_excerpt

RECORD_SCHEMA = Draft202012Validator(load_json(CONTRACTS_DIR / "evidence_record.schema.json"))

LONG = " ".join(f"word{i:04d}" for i in range(1200))  # several chunks
UNICODE = "Café ☕ — naïve 東京 meeting at 9:15 👩‍💻 then 'Zoë' left."


@dataclass
class Seeded:
    a: str
    b: str
    # chunk_ids by (case, record_id), in chunk_index order
    chunks: dict[tuple[str, str], list[str]]


def rec(case_id: str, record_id: str, content: str, **extra: object) -> EvidenceRecord:
    return EvidenceRecord.model_validate(
        {
            "record_id": record_id,
            "case_id": case_id,
            "source_file": f"{record_id}.txt",
            "source_locator": f"line {record_id}",
            "content": content,
            **extra,
        }
    )


@pytest.fixture
def seeded(app: FastAPI, client: TestClient) -> Seeded:
    embedder = FakeEmbedder()
    with app.state.session_factory() as session:
        a, b = Case(title="Case A"), Case(title="Case B")
        session.add_all([a, b])
        session.commit()
        fixture = [
            EvidenceRecord.model_validate(dict(r, case_id=a.case_id)) for r in RAW["records"]
        ]
        store_records(
            session,
            a.case_id,
            [
                *fixture,
                rec(a.case_id, "shared", "Case A version of the shared record."),
                rec(a.case_id, "long", LONG),
                rec(a.case_id, "uni", UNICODE, timestamp="2026-04", timestamp_kind="stated"),
                rec(a.case_id, "a-only", "Only in case A."),
            ],
            embedder,
        )
        store_records(
            session,
            b.case_id,
            [
                rec(b.case_id, "shared", "Case B version of the shared record."),
                rec(b.case_id, "b-only", "Only in case B.", metadata={"k": "v"}),
            ],
            embedder,
        )
        session.commit()
        chunks: dict[tuple[str, str], list[str]] = {}
        for case_id in (a.case_id, b.case_id):
            for chunk in list_chunks(session, case_id):
                chunks.setdefault((case_id, chunk.record_id), []).append(chunk.chunk_id)
        return Seeded(a.case_id, b.case_id, chunks)


def get(client: TestClient, case_id: str, record_id: str, **params: object):
    return client.get(f"/cases/{case_id}/records/{record_id}", params=params)


def assert_schema_clean(record_json: dict) -> None:
    errors = [e.message for e in RECORD_SCHEMA.iter_errors(record_json)]
    assert errors == [], errors


# --- Record lookup ----------------------------------------------------------------------


def test_fetch_fixture_record_matches_stored(client, seeded) -> None:
    for raw in RAW["records"]:
        response = get(client, seeded.a, raw["record_id"])
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["excerpt"] is None
        assert body["record"] == dict(raw, case_id=seeded.a)
        assert_schema_clean(body["record"])


def test_omitted_optional_fields_are_left_out_not_null(client, seeded) -> None:
    body = get(client, seeded.a, "a-only").json()
    assert_schema_clean(body["record"])
    assert "timestamp_kind" not in body["record"]
    assert "metadata" not in body["record"]
    assert body["record"]["timestamp"] is None  # schema allows null here


def test_same_record_id_in_two_cases_returns_each_cases_version(client, seeded) -> None:
    a = get(client, seeded.a, "shared").json()["record"]
    b = get(client, seeded.b, "shared").json()["record"]
    assert (a["case_id"], a["content"]) == (seeded.a, "Case A version of the shared record.")
    assert (b["case_id"], b["content"]) == (seeded.b, "Case B version of the shared record.")


def test_record_from_other_case_is_404(client, seeded) -> None:
    response = get(client, seeded.a, "b-only")
    assert response.status_code == 404
    assert response.json() == {"detail": "Record not found"}
    assert "Only in case B" not in response.text
    assert get(client, seeded.b, "a-only").status_code == 404


def test_unknown_case_and_unknown_record_are_404(client, seeded) -> None:
    unknown_case = get(client, "case_does_not_exist", "shared")
    assert unknown_case.status_code == 404
    assert unknown_case.json() == {"detail": "Case not found"}
    assert get(client, seeded.a, "nope").status_code == 404
    # Validation errors on an unknown case still never leak data.
    assert get(client, "case_does_not_exist", "shared", chunk_id="x").status_code == 404


# --- chunk_id excerpts ------------------------------------------------------------------


def test_chunk_excerpt_returns_chunk_text_and_offsets(client, seeded) -> None:
    chunk_ids = seeded.chunks[(seeded.a, "long")]
    assert len(chunk_ids) > 1
    for chunk_id in chunk_ids:
        body = get(client, seeded.a, "long", chunk_id=chunk_id).json()
        excerpt = body["excerpt"]
        assert excerpt["chunk_id"] == chunk_id
        assert excerpt["char_end"] > excerpt["char_start"] >= 0
        assert excerpt["text"] == LONG[excerpt["char_start"] : excerpt["char_end"]]
        assert body["record"]["content"] == LONG
    # Consecutive chunks are distinct windows.
    starts = [
        get(client, seeded.a, "long", chunk_id=c).json()["excerpt"]["char_start"]
        for c in chunk_ids
    ]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)


def test_chunk_from_other_record_or_case_is_404(client, seeded) -> None:
    other_record = seeded.chunks[(seeded.a, "a-only")][0]
    other_case = seeded.chunks[(seeded.b, "shared")][0]
    own = seeded.chunks[(seeded.a, "shared")][0]
    for chunk_id in (other_record, other_case, "chk_unknown"):
        response = get(client, seeded.a, "shared", chunk_id=chunk_id)
        assert response.status_code == 404, chunk_id
        assert response.json() == {"detail": "Chunk not found"}
        assert "Case B version" not in response.text
    assert get(client, seeded.a, "shared", chunk_id=own).status_code == 200
    # A case-B chunk under case B's own same-ID record is fine; under case A it is not.
    assert get(client, seeded.b, "shared", chunk_id=other_case).status_code == 200


def test_empty_chunk_id_is_422(client, seeded) -> None:
    assert get(client, seeded.a, "shared", chunk_id="").status_code == 422


# --- char_start/char_end excerpts -------------------------------------------------------


def test_span_excerpt_is_exact_slice(client, seeded) -> None:
    content = "Case A version of the shared record."
    for start, end in [(0, 6), (7, 14), (0, len(content)), (len(content) - 1, len(content))]:
        body = get(client, seeded.a, "shared", char_start=start, char_end=end).json()
        assert body["excerpt"] == {
            "text": content[start:end],
            "char_start": start,
            "char_end": end,
            "chunk_id": None,
        }


def test_span_excerpt_non_ascii_uses_code_point_offsets(client, seeded) -> None:
    for needle in ["Café", "☕", "東京", "👩‍💻", "'Zoë'"]:
        start = UNICODE.index(needle)
        end = start + len(needle)
        body = get(client, seeded.a, "uni", char_start=start, char_end=end).json()
        assert body["excerpt"]["text"] == needle
    body = get(client, seeded.a, "uni", char_start=0, char_end=len(UNICODE)).json()
    assert body["excerpt"]["text"] == UNICODE
    assert body["record"]["content"] == UNICODE
    assert body["record"]["timestamp_kind"] == "stated"
    assert_schema_clean(body["record"])
    # One past the end in code points is out of range even though the UTF-8 is longer.
    assert get(client, seeded.a, "uni", char_start=0, char_end=len(UNICODE) + 1).status_code == 422


@pytest.mark.parametrize(
    "params",
    [
        {"char_start": 5, "char_end": 5},  # empty
        {"char_start": 6, "char_end": 5},  # reversed
        {"char_start": -1, "char_end": 5},  # negative
        {"char_start": 0, "char_end": -1},
        {"char_start": 0, "char_end": 10_000},  # past the end
        {"char_start": 9_999, "char_end": 10_000},
        {"char_start": 0},  # lone offset
        {"char_end": 5},
        {"char_start": "x", "char_end": 5},  # not an int
    ],
)
def test_invalid_span_is_422(client, seeded, params) -> None:
    response = get(client, seeded.a, "shared", **params)
    assert response.status_code == 422, response.text
    assert "Case A version" not in response.text


def test_both_modes_is_422(client, seeded) -> None:
    chunk_id = seeded.chunks[(seeded.a, "shared")][0]
    response = get(client, seeded.a, "shared", chunk_id=chunk_id, char_start=0, char_end=4)
    assert response.status_code == 422
    assert "not both" in response.json()["detail"]


def test_span_on_other_cases_record_is_404_not_422(client, seeded) -> None:
    # Record existence is checked before offsets, so out-of-range offsets cannot be used to
    # probe another case's content length.
    assert get(client, seeded.a, "b-only", char_start=0, char_end=4).status_code == 404
    assert get(client, seeded.a, "b-only", char_start=0, char_end=10_000).status_code == 404


def test_span_excerpt_service_rejects_negative_offsets() -> None:
    # The HTTP layer rejects these first; the service checks too, for direct callers.
    with pytest.raises(InvalidExcerptError):
        span_excerpt("abc", -1, 2)


# --- List -------------------------------------------------------------------------------


def test_list_records_is_case_scoped_and_paginated(client, seeded) -> None:
    page = client.get(f"/cases/{seeded.a}/records").json()
    ids = [r["record_id"] for r in page["items"]]
    assert page["total"] == len(ids) == len(RAW["records"]) + 4
    assert "b-only" not in ids
    assert all(r["case_id"] == seeded.a for r in page["items"])
    for r in page["items"]:
        assert_schema_clean(r)
    ordered = sorted(page["items"], key=lambda r: (r["source_file"], r["record_id"]))
    assert page["items"] == ordered

    first = client.get(f"/cases/{seeded.a}/records", params={"limit": 2, "offset": 1}).json()
    assert [r["record_id"] for r in first["items"]] == ids[1:3]
    assert (first["total"], first["limit"], first["offset"]) == (page["total"], 2, 1)

    b = client.get(f"/cases/{seeded.b}/records").json()
    assert sorted(r["record_id"] for r in b["items"]) == ["b-only", "shared"]
    assert b["items"][0]["metadata"] == {"k": "v"}


def test_openapi_references_evidence_record_contract(client) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    ref = {"$ref": "#/components/schemas/EvidenceRecord"}
    assert schemas["RecordRead"]["properties"]["record"] == ref
    assert schemas["RecordPage"]["properties"]["items"]["items"] == ref


def test_list_records_errors(client, seeded) -> None:
    assert client.get("/cases/case_nope/records").status_code == 404
    for params in ({"limit": 0}, {"limit": 201}, {"offset": -1}):
        assert client.get(f"/cases/{seeded.a}/records", params=params).status_code == 422
