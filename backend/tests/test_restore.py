"""BLA-35: restoring excluded records reuses an earlier evidence state; nothing is lost."""

import copy
import json
from datetime import UTC, datetime, timedelta

import pytest
from _paths import CONTRACTS_DIR, RAW, load_json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from app.analyses import (
    InvalidRestoreError,
    NoPriorStateError,
    get_revision,
    restore_records,
    save_revision,
)
from app.contracts import AnalysisRevision, EvidenceRecord
from app.embeddings import FakeEmbedder
from app.evidence_store import store_records
from app.models import Case

REVISION_SCHEMA = Draft202012Validator(
    load_json(CONTRACTS_DIR / "analysis_revision.schema.json")
)
BASE = "analysis-synthetic-1"  # no exclusions
CHILD = "analysis-synthetic-2"  # rec-02 excluded
T0 = datetime(2026, 11, 14, 20, 0, tzinfo=UTC)


def revision(case_id: str, key: str = "revision", **update: object) -> AnalysisRevision:
    """A fixture revision in ``case_id``, with contract-JSON fields replaced by ``update``."""
    data = {**copy.deepcopy(RAW[key]), "case_id": case_id, **update}
    if isinstance(data["created_at"], datetime):
        data["created_at"] = data["created_at"].isoformat()
    return AnalysisRevision.model_validate_json(json.dumps(data))


@pytest.fixture
def case_id(app: FastAPI, client: TestClient) -> str:
    with app.state.session_factory() as session:
        case = Case(title="Restore")
        session.add(case)
        session.commit()
        records = [
            EvidenceRecord.model_validate(dict(r, case_id=case.case_id)) for r in RAW["records"]
        ]
        store_records(session, case.case_id, records, FakeEmbedder())
        session.commit()
        save_revision(session, revision(case.case_id))
        save_revision(session, revision(case.case_id, "child"))
        return case.case_id


def restore(client: TestClient, case_id: str, analysis_id: str, record_ids, expect=201):
    response = client.post(
        f"/cases/{case_id}/analyses/{analysis_id}/restore", json={"record_ids": record_ids}
    )
    assert response.status_code == expect, response.text
    return response


def test_restoring_reuses_the_prior_inclusion_state(client, case_id) -> None:
    response = restore(client, case_id, CHILD, ["rec-02"])
    restored = response.json()
    assert not list(REVISION_SCHEMA.iter_errors(restored))
    assert response.headers["X-Restored-From"] == BASE
    base = revision(case_id).to_contract_json()
    # The base revision's evidence state and findings, under a new ID, after the child.
    assert restored["excluded_record_ids"] == []
    assert restored["retrieved_record_ids"] == base["retrieved_record_ids"]
    for key in ("question", "answer", "claims", "graph", "timeline_events"):
        assert restored[key] == base[key]
    assert restored["analysis_id"].startswith("analysis_")
    assert restored["analysis_id"] not in (BASE, CHILD)
    assert restored["parent_analysis_id"] == CHILD


def test_no_revision_is_deleted_or_rewritten(app, client, case_id) -> None:
    before = {a: client.get(f"/cases/{case_id}/analyses/{a}").json() for a in (BASE, CHILD)}
    new_id = restore(client, case_id, CHILD, ["rec-02"]).json()["analysis_id"]
    for analysis_id, body in before.items():
        assert client.get(f"/cases/{case_id}/analyses/{analysis_id}").json() == body
    with app.state.session_factory() as session:
        with pytest.raises(DatabaseError, match="immutable"):
            session.execute(text("UPDATE revision_restores SET restored_from_analysis_id = 'x'"))
        session.rollback()
        assert get_revision(session, case_id, new_id).parent_analysis_id == CHILD


def test_the_restored_state_is_visible_in_the_history(client, case_id) -> None:
    new_id = restore(client, case_id, CHILD, ["rec-02"]).json()["analysis_id"]
    history = client.get(f"/cases/{case_id}/analyses").json()
    assert [
        (r["analysis_id"], r["excluded_record_ids"], r["restored_from_analysis_id"])
        for r in history
    ] == [(new_id, [], BASE), (CHILD, ["rec-02"], None), (BASE, [], None)]
    lineage = client.get(f"/cases/{case_id}/analyses/{new_id}/lineage").json()
    assert [r["analysis_id"] for r in lineage] == [BASE, CHILD, new_id]
    assert lineage[-1]["restored_from_analysis_id"] == BASE


def test_restoring_one_of_two_exclusions_uses_the_matching_intermediate_state(
    app, client, case_id
) -> None:
    # base (none) -> child (rec-02) -> grandchild (rec-01, rec-02): an empty answer, since
    # nothing is left to cite.
    grandchild = revision(
        case_id,
        "child",
        analysis_id="analysis-synthetic-3",
        parent_analysis_id=CHILD,
        excluded_record_ids=["rec-01", "rec-02"],
        retrieved_record_ids=[],
        answer="",
        claims=[],
        timeline_events=[],
        graph={"nodes": [], "edges": []},
        unresolved_questions=["Nothing left to analyze."],
        created_at=T0 + timedelta(hours=1),
    )
    with app.state.session_factory() as session:
        save_revision(session, grandchild)
    # Restoring rec-01 leaves rec-02 excluded: exactly the child's state.
    response = restore(client, case_id, "analysis-synthetic-3", ["rec-01"])
    assert response.headers["X-Restored-From"] == CHILD
    assert response.json()["excluded_record_ids"] == ["rec-02"]
    # Restoring both goes back to the base.
    response = restore(client, case_id, "analysis-synthetic-3", ["rec-01", "rec-02"])
    assert response.headers["X-Restored-From"] == BASE
    # Restoring rec-02 alone would leave only rec-01 excluded, which no revision had.
    body = restore(client, case_id, "analysis-synthetic-3", ["rec-02"], expect=409).json()
    assert "no earlier revision of this question had exactly these exclusions" in body["detail"]
    assert "remaining: rec-01" in body["detail"]


def test_a_restore_can_itself_be_restored_from_later(app, client, case_id) -> None:
    # child -> restored (none) -> excluded again (rec-02) -> restore reuses the nearest state.
    restored_id = restore(client, case_id, CHILD, ["rec-02"]).json()["analysis_id"]
    again = revision(
        case_id,
        "child",
        analysis_id="analysis-again",
        parent_analysis_id=restored_id,
        created_at=T0 + timedelta(hours=2),
    )
    with app.state.session_factory() as session:
        save_revision(session, again)
    response = restore(client, case_id, "analysis-again", ["rec-02"])
    assert response.headers["X-Restored-From"] == restored_id  # the nearest matching state


def test_only_excluded_records_can_be_restored(client, case_id) -> None:
    body = restore(client, case_id, CHILD, ["rec-01"], expect=422).json()
    assert body["detail"] == (
        "not excluded in analysis analysis-synthetic-2, so nothing to restore: rec-01"
    )
    restore(client, case_id, BASE, ["rec-02"], expect=422)
    restore(client, case_id, CHILD, [], expect=422)
    response = client.post(
        f"/cases/{case_id}/analyses/{CHILD}/restore", json={"record_ids": ["rec-02"], "x": 1}
    )
    assert response.status_code == 422


def test_a_different_question_is_never_reused(app, client, case_id) -> None:
    # A follow-up question excludes rec-02; its only ancestor without exclusions asked
    # something else, so there is no prior state for this question to reuse.
    follow_up = revision(
        case_id,
        "child",
        analysis_id="analysis-follow-up",
        parent_analysis_id=BASE,
        question="When did Jordan contact Casey?",
        created_at=T0 + timedelta(hours=3),
    )
    with app.state.session_factory() as session:
        save_revision(session, follow_up)
    restore(client, case_id, "analysis-follow-up", ["rec-02"], expect=409)


def test_restore_is_scoped_to_the_case(app, client, case_id) -> None:
    with app.state.session_factory() as session:
        other = Case(title="Other")
        session.add(other)
        session.commit()
        other_id = other.case_id
    restore(client, other_id, CHILD, ["rec-02"], expect=404)
    restore(client, case_id, "analysis-missing", ["rec-02"], expect=404)
    assert client.post(
        "/cases/case-missing/analyses/x/restore", json={"record_ids": ["rec-02"]}
    ).status_code == 404


def test_the_store_function_takes_an_id_and_time(app, case_id) -> None:
    moment = T0 + timedelta(days=1)
    with app.state.session_factory() as session:
        result = restore_records(
            session, case_id, CHILD, ["rec-02"], new_analysis_id="analysis-restored", now=moment
        )
        assert result.revision.analysis_id == "analysis-restored"
        assert result.revision.created_at == moment
        assert result.restored_from.analysis_id == BASE
        assert get_revision(session, case_id, "analysis-restored") == result.revision
        with pytest.raises(InvalidRestoreError, match="at least one"):
            restore_records(session, case_id, CHILD, [])
        # The restored revision has no exclusions, so a further restore has nothing to do.
        with pytest.raises(InvalidRestoreError, match="nothing to restore: rec-01"):
            restore_records(session, case_id, "analysis-restored", ["rec-01"])


def test_no_prior_state_raises_from_the_store(app, case_id) -> None:
    follow_up = revision(
        case_id,
        "child",
        analysis_id="analysis-follow-up",
        parent_analysis_id=BASE,
        question="When did Jordan contact Casey?",
        created_at=T0 + timedelta(hours=3),
    )
    with app.state.session_factory() as session:
        save_revision(session, follow_up)
        with pytest.raises(NoPriorStateError, match="remaining: none"):
            restore_records(session, case_id, "analysis-follow-up", ["rec-02"])
