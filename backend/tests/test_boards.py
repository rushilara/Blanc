"""BLA-31: board API (create, pin/unpin, annotate), append-only snapshots."""

import copy
import json
from collections.abc import Collection
from dataclasses import dataclass

import pytest
from _paths import CONTRACTS_DIR, RAW, load_json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app import boards as boards_module
from app.boards import item_id_for, no_saved_revisions
from app.contracts import AnalysisRevision, EvidenceRecord, InvestigationBoard
from app.embeddings import FakeEmbedder
from app.evidence_store import store_records
from app.models import BoardSnapshotRow, Case
from app.routers.boards import get_revision_lookup

BOARD_SCHEMA = Draft202012Validator(load_json(CONTRACTS_DIR / "investigation_board.schema.json"))
BASE_ID = "analysis-synthetic-1"
CHILD_ID = "analysis-synthetic-2"


@dataclass
class Seeded:
    case_id: str
    other_case_id: str
    lookups: list[set[str]]  # analysis IDs asked for, per lookup call


def revision_in(case_id: str, key: str) -> AnalysisRevision:
    return AnalysisRevision.model_validate(dict(copy.deepcopy(RAW[key]), case_id=case_id))


@pytest.fixture
def seeded(app: FastAPI, client: TestClient) -> Seeded:
    with app.state.session_factory() as session:
        case, other = Case(title="Boards"), Case(title="Other case")
        session.add_all([case, other])
        session.commit()
        for case_id in (case.case_id, other.case_id):
            records = [
                EvidenceRecord.model_validate(dict(r, case_id=case_id)) for r in RAW["records"]
            ]
            store_records(session, case_id, records, FakeEmbedder())
        session.commit()
        case_id, other_id = case.case_id, other.case_id

    saved = {
        (case_id, BASE_ID): revision_in(case_id, "revision"),
        (case_id, CHILD_ID): revision_in(case_id, "child"),
        (other_id, "analysis-other"): AnalysisRevision.model_validate(
            dict(copy.deepcopy(RAW["revision"]), case_id=other_id, analysis_id="analysis-other")
        ),
    }
    seen = Seeded(case_id, other_id, [])

    def lookup(session: Session, case: str, ids: Collection[str]) -> list[AnalysisRevision]:
        seen.lookups.append(set(ids))
        return [saved[(case, i)] for i in ids if (case, i) in saved]

    app.dependency_overrides[get_revision_lookup] = lambda: lookup
    return seen


def url(case_id: str, *parts: object) -> str:
    return "/".join([f"/cases/{case_id}/boards", *(str(p) for p in parts)])


def valid(body: dict) -> dict:
    errors = sorted(BOARD_SCHEMA.iter_errors(body), key=str)
    assert not errors, [e.message for e in errors]
    InvestigationBoard.model_validate_json(json.dumps(body))
    return body


def create(client: TestClient, case_id: str, **body: object) -> dict:
    response = client.post(url(case_id), json={"title": "Alex to Casey", **body})
    assert response.status_code == 201, response.text
    return valid(response.json())


def patch(client: TestClient, case_id: str, board_id: str, expect: int = 200, **body: object):
    response = client.patch(url(case_id, board_id), json=body)
    assert response.status_code == expect, response.text
    return response.json()


def pin(kind: str, ref_id: str, analysis_id: str = BASE_ID) -> dict:
    return {"kind": kind, "source_analysis_id": analysis_id, "ref_id": ref_id}


# --- Create and read --------------------------------------------------------------------


def test_create_makes_an_empty_first_snapshot(client, seeded) -> None:
    board = create(client, seeded.case_id)
    assert board["board_id"].startswith("board_")
    assert (board["revision"], board["parent_revision"]) == (1, None)
    assert board["items"] == [] and board["notes"] == []
    assert board["active_analysis_id"] is None
    assert board["case_id"] == seeded.case_id
    assert board["created_at"] == board["updated_at"]

    assert client.get(url(seeded.case_id, board["board_id"])).json() == board
    assert client.get(url(seeded.case_id)).json() == [board]


def test_create_checks_the_active_revision(client, seeded) -> None:
    board = create(client, seeded.case_id, active_analysis_id=BASE_ID)
    assert board["active_analysis_id"] == BASE_ID

    response = client.post(
        url(seeded.case_id), json={"title": "x", "active_analysis_id": "analysis-missing"}
    )
    assert response.status_code == 422
    assert "active_analysis_id analysis-missing is not a revision" in response.text
    # A revision of another case is not this case's revision either.
    response = client.post(
        url(seeded.case_id), json={"title": "x", "active_analysis_id": "analysis-other"}
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "body",
    [{}, {"title": "  "}, {"title": "x", "board_id": "mine"}, {"title": "x", "revision": 5}],
)
def test_create_rejects_bad_or_server_owned_fields(client, seeded, body) -> None:
    assert client.post(url(seeded.case_id), json=body).status_code == 422


def test_boards_are_scoped_to_their_case(client, seeded) -> None:
    board = create(client, seeded.case_id)
    other = seeded.other_case_id
    assert client.get(url(other, board["board_id"])).status_code == 404
    assert client.get(url(other)).json() == []
    assert client.patch(url(other, board["board_id"]), json={"title": "y"}).status_code == 404
    assert client.get(url(other, board["board_id"], "revisions")).status_code == 404
    assert client.get(url("case_missing")).status_code == 404
    assert client.post(url("case_missing"), json={"title": "x"}).status_code == 404


# --- Pinning ----------------------------------------------------------------------------


def test_pinning_copies_evidence_and_status_from_the_source_revision(client, seeded) -> None:
    board = create(client, seeded.case_id)
    updated = valid(
        patch(
            client,
            seeded.case_id,
            board["board_id"],
            pin=[
                pin("claim", "claim-2"),
                pin("edge", "edge-1"),
                pin("event", "event-1"),
                pin("node", "jordan"),
            ],
        )
    )
    assert (updated["revision"], updated["parent_revision"]) == (2, 1)
    items = {item["ref_id"]: item for item in updated["items"]}
    assert items["claim-2"]["evidence_ids"] == ["rec-02"]
    assert items["claim-2"]["status"] == "reported"  # copied, never upgraded to "verified"
    assert items["edge-1"]["evidence_ids"] == ["rec-01"]
    assert items["jordan"]["evidence_ids"] == ["rec-01", "rec-02"]
    assert "status" not in items["jordan"]  # graph nodes carry no status
    assert items["claim-2"]["item_id"] == item_id_for(BASE_ID, "claim", "claim-2")
    assert [i["ref_id"] for i in updated["items"]] == ["claim-2", "edge-1", "event-1", "jordan"]
    assert set(items["claim-2"]) == {
        "item_id",
        "kind",
        "source_analysis_id",
        "ref_id",
        "evidence_ids",
        "status",
        "added_at",
    }


def test_the_request_cannot_set_evidence_or_status(client, seeded) -> None:
    board = create(client, seeded.case_id)
    forged = {**pin("claim", "claim-2"), "evidence_ids": ["rec-01"], "status": "observed"}
    patch(client, seeded.case_id, board["board_id"], 422, pin=[forged])
    patch(client, seeded.case_id, board["board_id"], 422, pin=[pin("verdict", "claim-2")])


def test_pinning_the_same_finding_again_is_a_no_op(client, seeded) -> None:
    board_id = create(client, seeded.case_id)["board_id"]
    first = patch(client, seeded.case_id, board_id, pin=[pin("claim", "claim-1")])
    again = patch(
        client, seeded.case_id, board_id, pin=[pin("claim", "claim-1"), pin("claim", "claim-1")]
    )
    assert again == first  # no new snapshot, same item
    assert len(client.get(url(seeded.case_id, board_id, "revisions")).json()) == 2

    # The same ref ID in a different revision is a different finding.
    both = patch(client, seeded.case_id, board_id, pin=[pin("claim", "claim-1", CHILD_ID)])
    assert [i["source_analysis_id"] for i in both["items"]] == [BASE_ID, CHILD_ID]


def test_unpin_then_pin_again_keeps_the_stable_item_id(client, seeded) -> None:
    board_id = create(client, seeded.case_id)["board_id"]
    pinned = patch(client, seeded.case_id, board_id, pin=[pin("edge", "edge-2")])
    item_id = pinned["items"][0]["item_id"]
    unpinned = patch(client, seeded.case_id, board_id, unpin=[item_id])
    assert unpinned["items"] == [] and unpinned["revision"] == 3
    repinned = patch(client, seeded.case_id, board_id, pin=[pin("edge", "edge-2")])
    assert repinned["items"][0]["item_id"] == item_id and repinned["revision"] == 4


@pytest.mark.parametrize(
    ("bad_pin", "message"),
    [
        (pin("claim", "claim-1", "analysis-missing"), "no saved revision analysis-missing"),
        (pin("claim", "claim-1", "analysis-other"), "no saved revision analysis-other"),
        (pin("claim", "claim-9"), "the revision has no claim with that ID"),
        (pin("edge", "claim-1"), "the revision has no edge with that ID"),
    ],
)
def test_an_invalid_pin_saves_nothing(client, seeded, bad_pin, message) -> None:
    board_id = create(client, seeded.case_id)["board_id"]
    body = patch(client, seeded.case_id, board_id, 422, pin=[pin("claim", "claim-1"), bad_pin])
    assert any(message in error for error in body["detail"])
    latest = client.get(url(seeded.case_id, board_id)).json()
    assert latest["revision"] == 1 and latest["items"] == []  # the valid pin was not saved


def test_a_node_without_evidence_cannot_be_pinned(app, client, seeded) -> None:
    revision = revision_in(seeded.case_id, "revision")
    bare = revision.graph.nodes[0].model_copy(update={"id": "bare", "evidence_ids": None})
    revision.graph.nodes.append(bare)
    app.dependency_overrides[get_revision_lookup] = lambda: (
        lambda session, case, ids: [revision] if BASE_ID in ids else []
    )
    board_id = create(client, seeded.case_id)["board_id"]
    body = patch(client, seeded.case_id, board_id, 422, pin=[pin("node", "bare")])
    assert "it cites no evidence" in body["detail"][0]


def test_without_saved_revisions_nothing_can_be_pinned_yet(app, client, seeded) -> None:
    # The app default until BLA-28 persists revisions.
    app.dependency_overrides.pop(get_revision_lookup)
    assert get_revision_lookup() is no_saved_revisions
    board_id = create(client, seeded.case_id)["board_id"]
    body = patch(client, seeded.case_id, board_id, 422, pin=[pin("claim", "claim-1")])
    assert "no saved revision analysis-synthetic-1 in this case" in body["detail"][0]
    # Nor can the active revision be set yet.
    body = patch(client, seeded.case_id, board_id, 422, active_analysis_id=BASE_ID)
    assert "active_analysis_id analysis-synthetic-1 is not a revision" in body["detail"][0]
    response = client.post(url(seeded.case_id), json={"title": "x", "active_analysis_id": BASE_ID})
    assert response.status_code == 422
    # Notes still work.
    noted = patch(client, seeded.case_id, board_id, notes=[{"text": "Check rec-02."}])
    assert noted["notes"][0]["text"] == "Check rec-02."


def test_unpinning_an_item_that_is_not_on_the_board_fails(client, seeded) -> None:
    board_id = create(client, seeded.case_id)["board_id"]
    body = patch(client, seeded.case_id, board_id, 422, unpin=["item_missing"])
    assert body["detail"] == ["cannot unpin item_missing: it is not on this board"]


# --- Notes ------------------------------------------------------------------------------


def test_notes_are_kept_apart_from_pinned_findings(client, seeded) -> None:
    board_id = create(client, seeded.case_id)["board_id"]
    item_id = item_id_for(BASE_ID, "claim", "claim-2")
    board = valid(
        patch(
            client,
            seeded.case_id,
            board_id,
            pin=[pin("claim", "claim-2")],
            notes=[
                {"text": "  Did the meeting take place?  ", "target_item_id": item_id},
                {"text": "Board-level note."},
            ],
        )
    )
    first, second = board["notes"]
    assert first["text"] == "Did the meeting take place?"  # trimmed, otherwise verbatim
    assert first["target_item_id"] == item_id and first["author"] == "investigator"
    assert second["target_item_id"] is None and second["author"] == "investigator"
    assert first["note_id"].startswith("note_")
    # The note's text appears only in notes, never in an item.
    assert "meeting" not in str(board["items"])


def test_the_server_sets_every_notes_author(client, seeded) -> None:
    board_id = create(client, seeded.case_id)["board_id"]
    patch(client, seeded.case_id, board_id, 422, notes=[{"text": "x", "author": "assistant"}])
    board = patch(client, seeded.case_id, board_id, notes=[{"text": "x"}])
    assert board["notes"][0]["author"] == "investigator"


def test_a_note_must_target_an_item_on_the_board(client, seeded) -> None:
    board_id = create(client, seeded.case_id)["board_id"]
    body = patch(
        client,
        seeded.case_id,
        board_id,
        422,
        notes=[{"text": "x", "target_item_id": "item_missing"}],
    )
    assert any("targets unknown board item item_missing" in e for e in body["detail"])
    patch(client, seeded.case_id, board_id, 422, notes=[{"text": "   "}])


def test_an_item_with_notes_cannot_be_unpinned(client, seeded) -> None:
    board_id = create(client, seeded.case_id)["board_id"]
    item_id = item_id_for(BASE_ID, "event", "event-2")
    patch(
        client,
        seeded.case_id,
        board_id,
        pin=[pin("event", "event-2")],
        notes=[{"text": "Timing matters.", "target_item_id": item_id}],
    )
    body = patch(client, seeded.case_id, board_id, 422, unpin=[item_id])
    assert "notes are attached to it" in body["detail"][0]
    # Nor in the same request that attaches the note.
    other = item_id_for(BASE_ID, "event", "event-1")
    patch(client, seeded.case_id, board_id, pin=[pin("event", "event-1")])
    patch(
        client,
        seeded.case_id,
        board_id,
        422,
        unpin=[other],
        notes=[{"text": "x", "target_item_id": other}],
    )


# --- Snapshots --------------------------------------------------------------------------


def test_every_change_appends_a_snapshot_and_old_ones_stay(client, seeded) -> None:
    board = create(client, seeded.case_id)
    board_id = board["board_id"]
    patch(client, seeded.case_id, board_id, pin=[pin("claim", "claim-1")])
    patch(client, seeded.case_id, board_id, title="Renamed", notes=[{"text": "n"}])

    history = client.get(url(seeded.case_id, board_id, "revisions")).json()
    assert [(b["revision"], b["parent_revision"]) for b in history] == [(1, None), (2, 1), (3, 2)]
    assert history[0] == board  # the first snapshot is unchanged
    assert [b["title"] for b in history] == ["Alex to Casey", "Alex to Casey", "Renamed"]
    assert [len(b["items"]) for b in history] == [0, 1, 1]
    assert all(valid(b) for b in history)
    assert client.get(url(seeded.case_id, board_id, "revisions", 2)).json() == history[1]
    assert client.get(url(seeded.case_id, board_id, "revisions", 9)).status_code == 404
    assert history[-1]["created_at"] == board["created_at"]
    assert history[-1]["updated_at"] >= history[1]["updated_at"]


def test_snapshots_cannot_be_rewritten_in_the_database(app, client, seeded) -> None:
    board_id = create(client, seeded.case_id)["board_id"]
    with app.state.session_factory() as session:
        with pytest.raises(DatabaseError, match="append-only"):
            session.execute(text("UPDATE board_snapshots SET revision = 7"))
        session.rollback()
        row = session.get(BoardSnapshotRow, (seeded.case_id, board_id, 1))
        assert row is not None and row.snapshot["revision"] == 1


def test_a_stale_expected_revision_is_a_conflict(client, seeded) -> None:
    board_id = create(client, seeded.case_id)["board_id"]
    patch(client, seeded.case_id, board_id, expected_revision=1, title="Second")
    body = patch(client, seeded.case_id, board_id, 409, expected_revision=1, title="Lost")
    assert "is at revision 2, not 1" in body["detail"]
    assert client.get(url(seeded.case_id, board_id)).json()["title"] == "Second"


def test_a_concurrent_save_of_the_same_revision_is_a_conflict(client, seeded, monkeypatch) -> None:
    board = create(client, seeded.case_id)
    board_id = board["board_id"]
    stale = InvestigationBoard.model_validate_json(json.dumps(board))
    patch(client, seeded.case_id, board_id, title="Won the race")
    # The losing request read revision 1 before the winner saved revision 2.
    monkeypatch.setattr(boards_module, "get_board", lambda *args, **kwargs: stale)
    body = patch(client, seeded.case_id, board_id, 409, title="Lost the race")
    assert "was saved by another request" in body["detail"]
    monkeypatch.undo()
    assert client.get(url(seeded.case_id, board_id)).json()["title"] == "Won the race"


def test_a_change_that_alters_nothing_writes_no_snapshot(client, seeded) -> None:
    board = create(client, seeded.case_id, active_analysis_id=BASE_ID)
    assert patch(client, seeded.case_id, board["board_id"]) == board
    assert patch(client, seeded.case_id, board["board_id"], title="Alex to Casey") == board


def test_the_active_revision_can_be_changed_cleared_or_kept(client, seeded) -> None:
    board_id = create(client, seeded.case_id, active_analysis_id=BASE_ID)["board_id"]
    kept = patch(client, seeded.case_id, board_id, title="T")
    assert kept["active_analysis_id"] == BASE_ID
    moved = patch(client, seeded.case_id, board_id, active_analysis_id=CHILD_ID)
    assert moved["active_analysis_id"] == CHILD_ID
    cleared = patch(client, seeded.case_id, board_id, active_analysis_id=None)
    assert cleared["active_analysis_id"] is None and cleared["revision"] == 4
    patch(client, seeded.case_id, board_id, 422, active_analysis_id="analysis-other")


def test_boards_are_listed_newest_first(client, seeded) -> None:
    older = create(client, seeded.case_id, title="Older")["board_id"]
    newer = create(client, seeded.case_id, title="Newer")["board_id"]
    listed = client.get(url(seeded.case_id)).json()
    assert [b["board_id"] for b in listed] == [newer, older]
    patch(client, seeded.case_id, older, title="Older, edited")
    listed = client.get(url(seeded.case_id)).json()
    assert [b["board_id"] for b in listed] == [older, newer]
    assert listed[0]["revision"] == 2  # the latest snapshot only


# --- Adding a whole reconstruction (BLA-30) -----------------------------------------------


def test_adding_a_reconstruction_pins_every_finding_with_its_sources(client, seeded) -> None:
    board_id = create(client, seeded.case_id)["board_id"]
    board = valid(
        patch(client, seeded.case_id, board_id, add_reconstruction={"analysis_id": BASE_ID})
    )
    pinned = [(i["kind"], i["ref_id"]) for i in board["items"]]
    assert pinned == [
        ("claim", "claim-1"),
        ("claim", "claim-2"),
        ("node", "alex"),
        ("node", "jordan"),
        ("node", "casey"),
        ("edge", "edge-1"),
        ("edge", "edge-2"),
        ("event", "event-1"),
        ("event", "event-2"),
    ]
    source = revision_in(seeded.case_id, "revision")
    statuses = {c.id: c.status for c in [*source.claims, *source.graph.edges]}
    for item in board["items"]:
        assert item["source_analysis_id"] == BASE_ID  # originating revision kept
        assert item["evidence_ids"]  # source references kept
        if item["kind"] in ("claim", "edge"):
            assert item["status"] == statuses[item["ref_id"]]  # not marked verified
    assert board["revision"] == 2  # one change, one snapshot


def test_adding_the_same_reconstruction_again_changes_nothing(client, seeded) -> None:
    board_id = create(client, seeded.case_id)["board_id"]
    first = patch(client, seeded.case_id, board_id, add_reconstruction={"analysis_id": BASE_ID})
    again = patch(client, seeded.case_id, board_id, add_reconstruction={"analysis_id": BASE_ID})
    assert again == first
    # A single pin that is already part of it is deduplicated too.
    assert patch(client, seeded.case_id, board_id, pin=[pin("edge", "edge-1")]) == first


def test_findings_from_two_revisions_are_appended_and_nodes_never_merged(
    client, seeded
) -> None:
    board_id = create(client, seeded.case_id)["board_id"]
    patch(client, seeded.case_id, board_id, add_reconstruction={"analysis_id": BASE_ID})
    board = valid(
        patch(client, seeded.case_id, board_id, add_reconstruction={"analysis_id": CHILD_ID})
    )
    nodes = [(i["source_analysis_id"], i["ref_id"]) for i in board["items"] if i["kind"] == "node"]
    # "alex" and "jordan" appear in both revisions with the same labels; both copies stay.
    assert nodes == [
        (BASE_ID, "alex"),
        (BASE_ID, "jordan"),
        (BASE_ID, "casey"),
        (CHILD_ID, "alex"),
        (CHILD_ID, "jordan"),
    ]
    child_jordan = next(
        i for i in board["items"] if i["source_analysis_id"] == CHILD_ID and i["ref_id"] == "jordan"
    )
    assert child_jordan["evidence_ids"] == ["rec-01"]  # rec-02 is excluded in the child
    assert len({i["item_id"] for i in board["items"]}) == len(board["items"])


def test_similar_node_names_in_one_revision_stay_separate(app, client, seeded) -> None:
    revision = revision_in(seeded.case_id, "revision")
    alias = revision.graph.nodes[0].model_copy(update={"id": "alex-2", "label": "Alex "})
    revision.graph.nodes.append(alias)
    app.dependency_overrides[get_revision_lookup] = lambda: (
        lambda session, case, ids: [revision] if BASE_ID in ids else []
    )
    board_id = create(client, seeded.case_id)["board_id"]
    board = patch(
        client,
        seeded.case_id,
        board_id,
        add_reconstruction={"analysis_id": BASE_ID, "kinds": ["node"]},
    )
    assert [i["ref_id"] for i in board["items"]] == ["alex", "jordan", "casey", "alex-2"]


def test_kinds_limit_what_is_added_and_bare_nodes_are_skipped(app, client, seeded) -> None:
    revision = revision_in(seeded.case_id, "revision")
    bare = revision.graph.nodes[0].model_copy(update={"id": "bare", "evidence_ids": None})
    revision.graph.nodes.append(bare)
    app.dependency_overrides[get_revision_lookup] = lambda: (
        lambda session, case, ids: [revision] if BASE_ID in ids else []
    )
    board_id = create(client, seeded.case_id)["board_id"]
    board = patch(
        client,
        seeded.case_id,
        board_id,
        add_reconstruction={"analysis_id": BASE_ID, "kinds": ["event", "node"]},
    )
    assert [(i["kind"], i["ref_id"]) for i in board["items"]] == [
        ("node", "alex"),
        ("node", "jordan"),
        ("node", "casey"),
        ("event", "event-1"),
        ("event", "event-2"),
    ]
    patch(
        client,
        seeded.case_id,
        board_id,
        422,
        add_reconstruction={"analysis_id": BASE_ID, "kinds": []},
    )


@pytest.mark.parametrize("analysis_id", ["analysis-missing", "analysis-other"])
def test_an_unknown_reconstruction_adds_nothing(client, seeded, analysis_id) -> None:
    board_id = create(client, seeded.case_id)["board_id"]
    body = patch(
        client,
        seeded.case_id,
        board_id,
        422,
        add_reconstruction={"analysis_id": analysis_id},
        notes=[{"text": "kept only if everything is valid"}],
    )
    assert body["detail"] == [
        f"cannot add reconstruction {analysis_id}: no saved revision with that ID in this case"
    ]
    assert client.get(url(seeded.case_id, board_id)).json()["revision"] == 1


def test_citations_are_validated_before_a_reconstruction_is_added(app, client, seeded) -> None:
    # A revision whose claim cites a record the case does not have.
    revision = revision_in(seeded.case_id, "revision")
    revision.claims[0] = revision.claims[0].model_copy(update={"evidence_ids": ["rec-99"]})
    app.dependency_overrides[get_revision_lookup] = lambda: (
        lambda session, case, ids: [revision] if BASE_ID in ids else []
    )
    board_id = create(client, seeded.case_id)["board_id"]
    body = patch(client, seeded.case_id, board_id, 422, add_reconstruction={"analysis_id": BASE_ID})
    assert any("cites evidence not in case" in error for error in body["detail"])
    assert client.get(url(seeded.case_id, board_id)).json()["items"] == []


def test_a_long_but_valid_finding_id_can_be_pinned(app, client, seeded) -> None:
    # Revision IDs have no length limit, so pinning must not apply a request-size limit
    # to them (it used to crash bulk-add with a 500).
    long_id = "claim-" + "x" * 201
    revision = revision_in(seeded.case_id, "revision")
    revision.claims[0] = revision.claims[0].model_copy(update={"id": long_id})
    app.dependency_overrides[get_revision_lookup] = lambda: (
        lambda session, case, ids: [revision] if BASE_ID in ids else []
    )
    board_id = create(client, seeded.case_id)["board_id"]
    board = valid(
        patch(
            client,
            seeded.case_id,
            board_id,
            add_reconstruction={"analysis_id": BASE_ID, "kinds": ["claim"]},
        )
    )
    assert [i["ref_id"] for i in board["items"]] == [long_id, "claim-2"]
    assert board["items"][0]["item_id"] == item_id_for(BASE_ID, "claim", long_id)

    # A single pin of the same finding is the same item (deduplicated), not an error.
    again = patch(client, seeded.case_id, board_id, pin=[pin("claim", long_id)])
    assert again == board


def test_a_long_but_valid_analysis_id_can_be_added_and_selected(app, client, seeded) -> None:
    long_analysis = "analysis-" + "y" * 250
    revision = revision_in(seeded.case_id, "revision").model_copy(
        update={"analysis_id": long_analysis}
    )
    app.dependency_overrides[get_revision_lookup] = lambda: (
        lambda session, case, ids: [revision] if long_analysis in ids else []
    )
    board = create(client, seeded.case_id, active_analysis_id=long_analysis)
    added = valid(
        patch(
            client,
            seeded.case_id,
            board["board_id"],
            add_reconstruction={"analysis_id": long_analysis, "kinds": ["event"]},
        )
    )
    assert [i["source_analysis_id"] for i in added["items"]] == [long_analysis] * 2
    assert added["active_analysis_id"] == long_analysis
