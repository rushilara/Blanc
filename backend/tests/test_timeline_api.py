"""The frontend adapter must use the existing normalizer, not infer new dates."""

import pytest

from app.contracts import TimelineEvent
from app.timeline import normalize_timeline


def event(event_id, timestamp, precision=None):
    result = {
        "id": event_id,
        "description": "Synthetic event",
        "timestamp": timestamp,
        "evidence_ids": ["rec-test"],
        "status": "reported",
    }
    if precision is not None:
        result["timestamp_precision"] = precision
    return result


def test_normalization_endpoint_matches_service_without_case_lookup(client):
    events = [
        event("later", "2026-04-10T10:00:00Z"),
        event("earlier", "2026-04-10T11:00:00+02:00"),
        event("day", "2026-04-09", "exact"),
        event("month", "2026-03", "month"),
        event("approximate", "2026", "approximate"),
        event("no-zone", "2026-04-10T08:00:00"),
        event("ambiguous", "10/04/2026"),
        event("missing", None),
    ]
    response = client.post("/timeline/normalize", json={"events": events})
    assert response.status_code == 200
    assert (
        response.json()
        == normalize_timeline([TimelineEvent.model_validate(e) for e in events]).as_dict()
    )
    dated = response.json()["dated"]
    ids = [e["event_id"] for e in dated]
    assert ids.index("earlier") < ids.index("later")
    assert next(e for e in dated if e["event_id"] == "day")["display"] == "2026-04-09"
    assert next(e for e in dated if e["event_id"] == "no-zone")["timezone_known"] is False
    assert {e["event_id"] for e in response.json()["undated"]} == {"ambiguous", "missing"}


def test_empty_timeline(client):
    assert client.post("/timeline/normalize", json={"events": []}).json() == {
        "dated": [],
        "undated": [],
    }


@pytest.mark.parametrize(
    "body",
    [
        {"events": [event("same", None), event("same", None)]},
        {"events": [{**event("bad", None), "timestamp_precision": "invented"}]},
        {"events": [event("a", None)], "case_id": "not-a-lookup"},
    ],
)
def test_invalid_normalization_requests_are_422(client, body):
    assert client.post("/timeline/normalize", json=body).status_code == 422
