"""GET /cases/{case_id}/records/{record_id}/context: the record in its original file."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.contracts import EvidenceRecord
from app.embeddings import FakeEmbedder
from app.evidence_store import store_records
from app.models import Case

CHAT = "Discord/Cache/data_2"
HISTORY = "Chrome/User Data/Default/History"


def rec(case_id: str, record_id: str, source_file: str, locator: str, **extra: object):
    return EvidenceRecord.model_validate(
        {
            "record_id": record_id,
            "case_id": case_id,
            "source_file": source_file,
            "source_locator": locator,
            "content": extra.pop("content", f"content of {record_id}"),
            **extra,
        }
    )


def webkit(value: int) -> str:
    return json.dumps({"last_visit_time": value, "title": "t", "url": "https://example.test/"})


@pytest.fixture
def cases(app: FastAPI) -> tuple[str, str]:
    with app.state.session_factory() as session:
        a, b = Case(title="A"), Case(title="B")
        session.add_all([a, b])
        session.commit()
        records = [
            # Stored out of order; times decide.
            rec(a.case_id, "m3", CHAT, "JSON array index 0", timestamp="2019-02-01T00:00:00Z"),
            rec(a.case_id, "m1", CHAT, "JSON array index 2", timestamp="2019-01-28T21:17:30Z"),
            rec(a.case_id, "m2", CHAT, "JSON array index 1",
                timestamp="2019-01-28T23:00:00+02:00"),
            # No ISO time: raw WebKit times order these, across both tables.
            rec(a.case_id, "visit-9", HISTORY, "SQLite urls.id=9",
                content=webkit(13193453785132940)),
            rec(a.case_id, "download-7", HISTORY, "SQLite downloads.id=7",
                content=json.dumps({"start_time": 13193453792736438, "target_path": "x.exe"})),
            rec(a.case_id, "visit-10", HISTORY, "SQLite urls.id=10",
                content=webkit(13193453832071048)),
            # No time at all: natural locator order (line 9 before line 10).
            *[rec(a.case_id, f"line-{n}", "notes.txt", f"line {n}") for n in (10, 9, 11, 1)],
            rec(a.case_id, "alone", "photo.jpg", "whole file"),
        ]
        store_records(session, a.case_id, records, FakeEmbedder())
        store_records(
            session,
            b.case_id,
            [rec(b.case_id, "other-case", CHAT, "JSON array index 3",
                 timestamp="2019-01-29T00:00:00Z")],
            FakeEmbedder(),
        )
        session.commit()
        return a.case_id, b.case_id


def context(client: TestClient, case_id: str, record_id: str, **params: object):
    return client.get(f"/cases/{case_id}/records/{record_id}/context", params=params)


def ids(body: dict) -> list[str]:
    return [item["record_id"] for item in body["items"]]


def test_orders_by_time_and_points_at_the_record(client, cases) -> None:
    a, _ = cases
    body = context(client, a, "m2").json()
    # 23:00+02:00 is 21:00Z, before m1 at 21:17:30Z: compared in UTC, not as text.
    assert ids(body) == ["m2", "m1", "m3"]
    assert body["ordered_by"] == "time"
    assert body["total"] == 3
    assert body["items"][body["index"] - body["start"]]["record_id"] == "m2"
    assert body["source_file"] == CHAT


def test_other_cases_records_are_never_included(client, cases) -> None:
    a, b = cases
    assert "other-case" not in ids(context(client, a, "m1").json())
    assert ids(context(client, b, "other-case").json()) == ["other-case"]
    assert context(client, b, "m1").status_code == 404


def test_browser_rows_merge_by_raw_time_and_content_is_unchanged(client, cases) -> None:
    a, _ = cases
    body = context(client, a, "download-7").json()
    assert ids(body) == ["visit-9", "download-7", "visit-10"]
    assert json.loads(body["items"][1]["content"])["start_time"] == 13193453792736438
    assert body["items"][1]["timestamp"] is None


def test_untimed_records_follow_natural_locator_order(client, cases) -> None:
    a, _ = cases
    body = context(client, a, "line-10").json()
    assert ids(body) == ["line-1", "line-9", "line-10", "line-11"]
    assert body["ordered_by"] == "locator"
    assert body["index"] == 2


def test_window_is_clipped_around_the_record(client, cases) -> None:
    a, _ = cases
    body = context(client, a, "line-10", before=1, after=0).json()
    assert ids(body) == ["line-9", "line-10"]
    assert (body["start"], body["index"], body["total"]) == (1, 2, 4)


def test_single_record_file(client, cases) -> None:
    a, _ = cases
    body = context(client, a, "alone").json()
    assert ids(body) == ["alone"] and body["total"] == 1


def test_unknown_record_and_bad_window(client, cases) -> None:
    a, _ = cases
    assert context(client, a, "missing").status_code == 404
    assert context(client, a, "m1", before=-1).status_code == 422
    assert context(client, a, "m1", after=10_000).status_code == 422
