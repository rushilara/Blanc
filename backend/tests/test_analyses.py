"""BLA-28: saved analysis revisions: immutable, append-only, reloadable after a restart."""

import copy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from _paths import CONTRACTS_DIR, RAW, load_json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from app import analyses as analyses_module
from app.analyses import (
    InvalidRevisionError,
    RevisionConflictError,
    RevisionNotFoundError,
    get_revision,
    list_revisions,
    revision_lineage,
    save_revision,
    saved_revision_lookup,
)
from app.config import Settings
from app.contracts import AnalysisRevision, EvidenceRecord
from app.embeddings import FakeEmbedder
from app.evidence_store import store_records
from app.main import create_app
from app.models import AnalysisRevisionRow, Case

REVISION_SCHEMA = Draft202012Validator(
    load_json(CONTRACTS_DIR / "analysis_revision.schema.json")
)
BASE_ID = "analysis-synthetic-1"
CHILD_ID = "analysis-synthetic-2"


@dataclass
class Seeded:
    case_id: str
    other_case_id: str


def revision(case_id: str, key: str = "revision", **update: object) -> AnalysisRevision:
    data = dict(copy.deepcopy(RAW[key]), case_id=case_id)
    return AnalysisRevision.model_validate(data).model_copy(update=update)


@pytest.fixture
def seeded(app: FastAPI, client: TestClient) -> Seeded:
    with app.state.session_factory() as session:
        case, other = Case(title="Saved revisions"), Case(title="Other case")
        session.add_all([case, other])
        session.commit()
        for case_id in (case.case_id, other.case_id):
            records = [
                EvidenceRecord.model_validate(dict(r, case_id=case_id)) for r in RAW["records"]
            ]
            store_records(session, case_id, records, FakeEmbedder())
        session.commit()
        return Seeded(case.case_id, other.case_id)


def save(app: FastAPI, rev: AnalysisRevision) -> AnalysisRevision:
    with app.state.session_factory() as session:
        return save_revision(session, rev)


# --- Save and reload ----------------------------------------------------------------------


def test_a_saved_revision_reloads_exactly(app, client, seeded) -> None:
    base = revision(seeded.case_id)
    saved = save(app, base)
    assert saved == base

    response = client.get(f"/cases/{seeded.case_id}/analyses/{BASE_ID}")
    assert response.status_code == 200
    body = response.json()
    assert not list(REVISION_SCHEMA.iter_errors(body))
    assert body == base.to_contract_json()
    # Everything the issue lists comes back: question, answer, graph, timeline, sources.
    assert body["question"] == RAW["revision"]["question"]
    assert body["answer"] == RAW["revision"]["answer"]
    assert body["graph"] == RAW["revision"]["graph"]
    assert body["timeline_events"] == RAW["revision"]["timeline_events"]
    assert body["retrieved_record_ids"] == ["rec-01", "rec-02"]


def test_everything_reloads_after_a_server_restart(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'restart.db'}"
    first = create_app(Settings(_env_file=None, DATABASE_URL=url))
    with TestClient(first) as client:
        case_id = client.post("/cases", json={"title": "Restart"}).json()["case_id"]
        with first.state.session_factory() as session:
            records = [
                EvidenceRecord.model_validate(dict(r, case_id=case_id)) for r in RAW["records"]
            ]
            store_records(session, case_id, records, FakeEmbedder())
            session.commit()
        save(first, revision(case_id))
        save(first, revision(case_id, "child"))
        board = client.post(
            f"/cases/{case_id}/boards", json={"title": "B", "active_analysis_id": BASE_ID}
        ).json()
        client.patch(
            f"/cases/{case_id}/boards/{board['board_id']}",
            json={
                "pin": [{"kind": "claim", "source_analysis_id": BASE_ID, "ref_id": "claim-2"}],
                "notes": [{"text": "Was the meeting real?"}],
            },
        )
        before_revision = client.get(f"/cases/{case_id}/analyses/{CHILD_ID}").json()
        before_board = client.get(f"/cases/{case_id}/boards/{board['board_id']}").json()

    # A brand-new app on the same database file: nothing lives in memory any more.
    second = create_app(Settings(_env_file=None, DATABASE_URL=url))
    with TestClient(second) as client:
        assert client.get(f"/cases/{case_id}/analyses/{CHILD_ID}").json() == before_revision
        assert [r["analysis_id"] for r in client.get(f"/cases/{case_id}/analyses").json()] == [
            CHILD_ID,
            BASE_ID,
        ]
        after_board = client.get(f"/cases/{case_id}/boards/{board['board_id']}").json()
        assert after_board == before_board
        assert after_board["notes"][0]["text"] == "Was the meeting real?"  # annotations
        assert after_board["items"][0]["ref_id"] == "claim-2"


def test_history_lists_newest_first_with_lineage(app, client, seeded) -> None:
    save(app, revision(seeded.case_id))
    save(app, revision(seeded.case_id, "child"))
    later = revision(
        seeded.case_id,
        "child",
        analysis_id="analysis-synthetic-3",
        parent_analysis_id=CHILD_ID,
        created_at=revision(seeded.case_id, "child").created_at + timedelta(minutes=5),
    )
    save(app, later)

    history = client.get(f"/cases/{seeded.case_id}/analyses").json()
    assert [(r["analysis_id"], r["parent_analysis_id"]) for r in history] == [
        ("analysis-synthetic-3", CHILD_ID),
        (CHILD_ID, BASE_ID),
        (BASE_ID, None),
    ]
    assert all(r["needs_review"] is False for r in history)
    assert set(history[0]) == {
        "analysis_id",
        "parent_analysis_id",
        "question",
        "needs_review",
        "created_at",
        "excluded_record_ids",
        "restored_from_analysis_id",
    }
    assert [r["excluded_record_ids"] for r in history] == [["rec-02"], ["rec-02"], []]
    assert all(r["restored_from_analysis_id"] is None for r in history)
    lineage = client.get(f"/cases/{seeded.case_id}/analyses/analysis-synthetic-3/lineage")
    assert [r["analysis_id"] for r in lineage.json()] == [
        BASE_ID,
        CHILD_ID,
        "analysis-synthetic-3",
    ]
    # The parent is untouched by its children.
    assert client.get(f"/cases/{seeded.case_id}/analyses/{BASE_ID}").json() == (
        revision(seeded.case_id).to_contract_json()
    )


def test_a_revision_flagged_for_review_keeps_its_flags(app, client, seeded) -> None:
    flagged = revision(
        seeded.case_id,
        needs_review=True,
        validation_errors=["Attempt 1: 1 citation was removed."],
    )
    save(app, flagged)
    body = client.get(f"/cases/{seeded.case_id}/analyses/{BASE_ID}").json()
    assert body["needs_review"] is True
    assert body["validation_errors"] == ["Attempt 1: 1 citation was removed."]
    assert client.get(f"/cases/{seeded.case_id}/analyses").json()[0]["needs_review"] is True


# --- Immutability ------------------------------------------------------------------------


def test_saving_the_same_revision_again_is_a_no_op(app, seeded) -> None:
    save(app, revision(seeded.case_id))
    assert save(app, revision(seeded.case_id)) == revision(seeded.case_id)
    with app.state.session_factory() as session:
        assert len(list_revisions(session, seeded.case_id)) == 1


def test_a_saved_revision_cannot_be_changed(app, seeded) -> None:
    save(app, revision(seeded.case_id))
    with pytest.raises(RevisionConflictError, match="already saved with different content"):
        save(app, revision(seeded.case_id, answer="A rewritten answer."))
    with app.state.session_factory() as session:
        assert get_revision(session, seeded.case_id, BASE_ID).answer == RAW["revision"]["answer"]
        with pytest.raises(DatabaseError, match="immutable"):
            session.execute(text("UPDATE analysis_revisions SET question = 'changed'"))
        session.rollback()


def test_a_concurrent_save_of_the_same_id_is_a_conflict(app, seeded, monkeypatch) -> None:
    real_validate = analyses_module.validate_revision

    def validate_while_another_request_saves(records, rev):
        monkeypatch.setattr(analyses_module, "validate_revision", real_validate)
        save(app, rev)  # the other request wins the race
        return real_validate(records, rev)

    monkeypatch.setattr(analyses_module, "validate_revision", validate_while_another_request_saves)
    with pytest.raises(RevisionConflictError, match="saved by another request"):
        save(app, revision(seeded.case_id))
    with app.state.session_factory() as session:
        assert len(list_revisions(session, seeded.case_id)) == 1


# --- Validation before saving ---------------------------------------------------------------


def test_an_invalid_revision_is_not_saved(app, seeded) -> None:
    bad = revision(seeded.case_id)
    bad.claims[0] = bad.claims[0].model_copy(update={"evidence_ids": ["rec-99"]})
    with pytest.raises(InvalidRevisionError) as raised:
        save(app, bad)
    assert any("rec-99" in error for error in raised.value.errors)
    with app.state.session_factory() as session:
        assert list_revisions(session, seeded.case_id) == []


def test_a_revision_cannot_cite_another_cases_records(app, seeded) -> None:
    # Saved under a case that has none of the records it retrieves.
    with app.state.session_factory() as session:
        empty = Case(title="No records")
        session.add(empty)
        session.commit()
        empty_id = empty.case_id
    with pytest.raises(InvalidRevisionError, match="retrieved IDs are not in the case corpus"):
        save(app, revision(empty_id))


def test_the_parent_must_be_a_saved_revision_of_the_same_case(app, seeded) -> None:
    with pytest.raises(RevisionNotFoundError, match="parent analysis analysis-synthetic-1"):
        save(app, revision(seeded.case_id, "child"))
    save(app, revision(seeded.other_case_id))  # the parent exists, but in another case
    with pytest.raises(RevisionNotFoundError, match="is not a saved revision of case"):
        save(app, revision(seeded.case_id, "child"))


def test_a_revision_needs_an_existing_case(app, seeded) -> None:
    with pytest.raises(RevisionNotFoundError, match="does not exist"):
        save(app, revision("case-missing"))


# --- Case scoping ----------------------------------------------------------------------------


def test_revisions_are_scoped_to_their_case(app, client, seeded) -> None:
    save(app, revision(seeded.case_id))
    other = seeded.other_case_id
    assert client.get(f"/cases/{other}/analyses/{BASE_ID}").status_code == 404
    assert client.get(f"/cases/{other}/analyses/{BASE_ID}/lineage").status_code == 404
    assert client.get(f"/cases/{other}/analyses").json() == []
    assert client.get(f"/cases/{seeded.case_id}/analyses/analysis-missing").status_code == 404
    assert client.get("/cases/case-missing/analyses").status_code == 404
    with app.state.session_factory() as session:
        assert saved_revision_lookup(session, other, [BASE_ID]) == []
        assert [r.analysis_id for r in saved_revision_lookup(
            session, seeded.case_id, [BASE_ID, "analysis-missing"]
        )] == [BASE_ID]
        assert saved_revision_lookup(session, seeded.case_id, []) == []
        with pytest.raises(RevisionNotFoundError):
            revision_lineage(session, other, BASE_ID)


def test_the_same_analysis_id_can_exist_in_two_cases(app, seeded) -> None:
    save(app, revision(seeded.case_id))
    save(app, revision(seeded.other_case_id, answer="A different case's answer."))
    with app.state.session_factory() as session:
        assert get_revision(session, seeded.case_id, BASE_ID).answer == RAW["revision"]["answer"]
        other = get_revision(session, seeded.other_case_id, BASE_ID)
        assert other.answer == "A different case's answer."
        rows = session.query(AnalysisRevisionRow).count()
        assert rows == 2


def test_created_at_is_kept_with_its_instant(app, seeded) -> None:
    moment = datetime(2026, 11, 15, 3, 4, 5, tzinfo=UTC)
    save(app, revision(seeded.case_id, created_at=moment))
    with app.state.session_factory() as session:
        assert get_revision(session, seeded.case_id, BASE_ID).created_at == moment
        assert list_revisions(session, seeded.case_id)[0].created_at == moment
