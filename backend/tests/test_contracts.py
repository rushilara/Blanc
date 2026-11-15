"""Drift tests: the Pydantic contract models must accept and reject the same inputs as the
JSON Schemas in contracts/."""

from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone

import pytest
from _paths import CONTRACTS_DIR, FIXTURE_FILES, FIXTURES_DIR, RAW, load_json
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ValidationError

from app.contracts import (
    AnalysisRevision,
    BoardItem,
    BoardNote,
    Claim,
    ComparisonItem,
    ContractModel,
    EvidenceRecord,
    Graph,
    GraphEdge,
    GraphNode,
    InvestigationBoard,
    RevisionComparison,
    TimelineEvent,
    parse_datetime,
    to_contract_json,
)

SCHEMAS = {
    "evidence": ("evidence_record.schema.json", EvidenceRecord),
    "revision": ("analysis_revision.schema.json", AnalysisRevision),
    "board": ("investigation_board.schema.json", InvestigationBoard),
    "comparison": ("revision_comparison.schema.json", RevisionComparison),
}
FIXTURE_CONTRACT = {
    "records": "evidence",
    "revision": "revision",
    "child": "revision",
    "board": "board",
    "comparison": "comparison",
}
VALIDATORS = {
    key: Draft202012Validator(load_json(CONTRACTS_DIR / name)) for key, (name, _) in SCHEMAS.items()
}


def schema_accepts(contract: str, instance: object) -> bool:
    return VALIDATORS[contract].is_valid(instance)


def model_accepts(contract: str, instance: object) -> bool:
    model: type[BaseModel] = SCHEMAS[contract][1]
    try:
        model.model_validate(instance)
    except ValidationError:
        return False
    return True


def instances(fixture: str) -> list[object]:
    data = RAW[fixture]
    return data if isinstance(data, list) else [data]


def test_every_contract_has_a_model_and_every_fixture_is_covered() -> None:
    assert sorted(p.name for p in CONTRACTS_DIR.glob("*.schema.json")) == sorted(
        name for name, _ in SCHEMAS.values()
    )
    assert sorted(p.name for p in FIXTURES_DIR.glob("*.json")) == sorted(FIXTURE_FILES.values())


@pytest.mark.parametrize("contract", sorted(SCHEMAS))
def test_schema_is_valid_json_schema(contract: str) -> None:
    Draft202012Validator.check_schema(load_json(CONTRACTS_DIR / SCHEMAS[contract][0]))


@pytest.mark.parametrize("fixture", sorted(FIXTURE_CONTRACT))
def test_fixture_accepted_by_schema_and_model(fixture: str) -> None:
    contract = FIXTURE_CONTRACT[fixture]
    for instance in instances(fixture):
        assert list(VALIDATORS[contract].iter_errors(instance)) == []
        SCHEMAS[contract][1].model_validate(instance)


@pytest.mark.parametrize("fixture", sorted(FIXTURE_CONTRACT))
def test_fixture_round_trips_through_model(fixture: str) -> None:
    model = SCHEMAS[FIXTURE_CONTRACT[fixture]][1]
    for instance in instances(fixture):
        assert to_contract_json(model.model_validate(instance)) == instance


# --- Serialization: to_contract_json is the only schema-valid way out ---------------------


def _built_in_python() -> dict[str, ContractModel]:
    """Models built in code (as the server will), leaving every omittable field out."""
    revision = AnalysisRevision(
        schema_version="1.0",
        analysis_id="analysis-1",
        case_id="case-1",
        question="Who met whom?",
        excluded_record_ids=[],
        retrieved_record_ids=["rec-1"],
        answer="",
        claims=[Claim(id="c1", text="A met B.", evidence_ids=["rec-1"], status="reported")],
        graph=Graph(
            nodes=[GraphNode(id="a", label="A", type="person")],
            edges=[
                GraphEdge(
                    id="e1",
                    source="a",
                    target="a",
                    type="met",
                    evidence_ids=["rec-1"],
                    status="reported",
                )
            ],
        ),
        timeline_events=[
            TimelineEvent(
                id="t1",
                description="A met B.",
                timestamp=None,
                evidence_ids=["rec-1"],
                status="reported",
            )
        ],
        unresolved_questions=[],
        created_at=datetime(2026, 11, 14, 20, 0, tzinfo=UTC),
    )
    now = datetime(2026, 11, 14, 20, 0, tzinfo=UTC)
    board = InvestigationBoard(
        schema_version="1.0",
        board_id="board-1",
        case_id="case-1",
        title="Board",
        revision=1,
        active_analysis_id=None,
        items=[
            BoardItem(
                item_id="i1",
                kind="node",
                source_analysis_id="analysis-1",
                ref_id="a",
                evidence_ids=["rec-1"],
                added_at=now,
            )
        ],
        notes=[BoardNote(note_id="n1", text="check", author="inv", created_at=now)],
        created_at=now,
        updated_at=now,
    )
    comparison = RevisionComparison(
        schema_version="1.0",
        case_id="case-1",
        base_analysis_id="analysis-1",
        compared_analysis_id="analysis-2",
        excluded_record_ids=[],
        items=[
            ComparisonItem(
                kind="claim",
                base_ref_id="c1",
                compared_ref_id="c1",
                support_status="still_supported",
                base_evidence_ids=["rec-1"],
                compared_evidence_ids=["rec-1"],
            )
        ],
        created_at=now,
    )
    record = EvidenceRecord(
        record_id="rec-1", case_id="case-1", source_file="f.txt", source_locator="l1", content=""
    )
    return {"revision": revision, "board": board, "comparison": comparison, "evidence": record}


@pytest.mark.parametrize("contract", sorted(SCHEMAS))
def test_to_contract_json_is_schema_valid_but_plain_model_dump_is_not(contract: str) -> None:
    model = _built_in_python()[contract]
    errors = list(VALIDATORS[contract].iter_errors(to_contract_json(model)))
    assert errors == []
    assert model.to_contract_json() == to_contract_json(model)
    # Documents the pitfall: plain model_dump writes omittable fields as explicit nulls,
    # which the shared JSON Schema rejects.
    assert not schema_accepts(contract, model.model_dump(mode="json"))


def test_to_contract_json_keeps_schema_nullable_fields() -> None:
    board = to_contract_json(_built_in_python()["board"])
    assert board["active_analysis_id"] is None  # required and nullable: kept
    assert board["parent_revision"] is None  # optional and nullable: kept
    assert board["notes"][0]["target_item_id"] is None
    assert "status" not in board["items"][0]  # omittable, not nullable: dropped
    revision = to_contract_json(_built_in_python()["revision"])
    assert revision["timeline_events"][0]["timestamp"] is None
    assert "evidence_ids" not in revision["graph"]["nodes"][0]
    assert "description" not in revision["graph"]["edges"][0]
    assert "needs_review" not in revision


def _set(path: str, value: object) -> Callable[[dict], None]:
    *parents, last = path.split(".")

    def mutate(doc: dict) -> None:
        target = doc
        for part in parents:
            target = target[int(part)] if isinstance(target, list) else target[part]
        if isinstance(target, list):
            target[int(last)] = value
        else:
            target[last] = value

    return mutate


def _delete(path: str) -> Callable[[dict], None]:
    *parents, last = path.split(".")

    def mutate(doc: dict) -> None:
        target = doc
        for part in parents:
            target = target[int(part)] if isinstance(target, list) else target[part]
        del target[last]

    return mutate


def _append_comparison_item(**item: object) -> Callable[[dict], None]:
    return lambda doc: doc["items"].append(item)


LINK = {
    "ref_id": "claim-x",
    "shared_evidence_ids": ["rec-01"],
    "remaining_sources": 1,
    "original_sources": 2,
}


def _without(key: str) -> dict:
    return {k: v for k, v in LINK.items() if k != key}


# (fixture, description, mutation). Every mutation must be rejected by BOTH validators.
MUTATIONS: list[tuple[str, str, Callable[[dict], None]]] = [
    # EvidenceRecord
    ("records", "extra top-level field", _set("0.extra", 1)),
    ("records", "missing record_id", _delete("0.record_id")),
    ("records", "empty record_id", _set("0.record_id", "")),
    ("records", "bad timestamp_kind enum", _set("0.timestamp_kind", "whenever")),
    ("records", "null timestamp_kind", _set("0.timestamp_kind", None)),
    ("records", "content not a string", _set("0.content", 42)),
    ("records", "metadata not an object", _set("0.metadata", ["a"])),
    # AnalysisRevision
    ("revision", "extra top-level field", _set("verified", True)),
    ("revision", "wrong schema_version", _set("schema_version", "2.0")),
    ("revision", "missing required answer", _delete("answer")),
    ("revision", "claim with empty evidence_ids", _set("claims.0.evidence_ids", [])),
    ("revision", "claim missing evidence_ids", _delete("claims.0.evidence_ids")),
    ("revision", "claim with empty evidence ID", _set("claims.0.evidence_ids", [""])),
    ("revision", "claim bad status enum", _set("claims.0.status", "verified")),
    ("revision", "claim extra field", _set("claims.0.confidence", 0.9)),
    ("revision", "edge cannot be unresolved", _set("graph.edges.0.status", "unresolved")),
    ("revision", "edge with empty evidence_ids", _set("graph.edges.0.evidence_ids", [])),
    ("revision", "edge null description", _set("graph.edges.0.description", None)),
    ("revision", "node bad type enum", _set("graph.nodes.0.type", "alien")),
    ("revision", "node null evidence_ids", _set("graph.nodes.0.evidence_ids", None)),
    ("revision", "graph extra field", _set("graph.clusters", [])),
    ("revision", "event cannot be unresolved", _set("timeline_events.0.status", "unresolved")),
    ("revision", "event with empty evidence_ids", _set("timeline_events.0.evidence_ids", [])),
    ("revision", "event missing timestamp key", _delete("timeline_events.0.timestamp")),
    ("revision", "event bad precision", _set("timeline_events.0.timestamp_precision", "exactish")),
    ("revision", "duplicate retrieved IDs", _set("retrieved_record_ids", ["rec-01", "rec-01"])),
    ("revision", "needs_review not a bool", _set("needs_review", "yes")),
    ("revision", "needs_review null", _set("needs_review", None)),
    ("revision", "empty validation error", _set("validation_errors", [""])),
    ("revision", "created_at not a string", _set("created_at", 1763150400)),
    # InvestigationBoard
    ("board", "extra top-level field", _set("verified", True)),
    ("board", "revision zero", _set("revision", 0)),
    ("board", "revision as string", _set("revision", "2")),
    ("board", "revision as bool", _set("revision", True)),
    ("board", "parent_revision zero", _set("parent_revision", 0)),
    ("board", "missing active_analysis_id", _delete("active_analysis_id")),
    ("board", "claim item without status", _delete("items.2.status")),
    ("board", "edge item without status", _delete("items.0.status")),
    ("board", "item null status", _set("items.4.status", None)),
    ("board", "item bad kind", _set("items.0.kind", "answer")),
    ("board", "item verified status", _set("items.0.status", "verified")),
    ("board", "item empty evidence", _set("items.0.evidence_ids", [])),
    ("board", "item duplicate evidence", _set("items.4.evidence_ids", ["rec-01", "rec-01"])),
    ("board", "item extra field", _set("items.0.verified", True)),
    ("board", "note missing author", _delete("notes.0.author")),
    ("board", "note empty text", _set("notes.0.text", "")),
    ("board", "note extra field", _set("notes.0.is_evidence", True)),
    # RevisionComparison
    ("comparison", "extra top-level field", _set("summary", "x")),
    ("comparison", "missing items", _delete("items")),
    ("comparison", "bad support_status", _set("items.0.support_status", "disproved")),
    ("comparison", "node kind not comparable", _set("items.0.kind", "node")),
    (
        "comparison",
        "null compared ref with evidence",
        _set("items.1.compared_evidence_ids", ["fabricated-record"]),
    ),
    (
        "comparison",
        "null base ref with evidence",
        _append_comparison_item(
            kind="claim",
            base_ref_id=None,
            compared_ref_id="claim-1",
            support_status="newly_supported",
            base_evidence_ids=["rec-01"],
            compared_evidence_ids=["rec-01"],
        ),
    ),
    ("comparison", "missing compared_ref_id key", _delete("items.0.compared_ref_id")),
    ("comparison", "duplicate excluded IDs", _set("excluded_record_ids", ["rec-02", "rec-02"])),
    ("comparison", "null explanation", _set("items.0.explanation", None)),
    # possible_matches (BLA-63); item 0 has both ref IDs, item 1 only base_ref_id.
    ("comparison", "link on an item with both refs", _set("items.0.possible_matches", [LINK])),
    ("comparison", "null possible_matches", _set("items.1.possible_matches", None)),
    ("comparison", "empty possible_matches", _set("items.1.possible_matches", [])),
    ("comparison", "link extra field", _set("items.1.possible_matches", [{**LINK, "same": True}])),
    ("comparison", "link missing ref_id", _set("items.1.possible_matches", [_without("ref_id")])),
    ("comparison", "link empty ref_id", _set("items.1.possible_matches", [{**LINK, "ref_id": ""}])),
    (
        "comparison",
        "link without shared records",
        _set("items.1.possible_matches", [{**LINK, "shared_evidence_ids": []}]),
    ),
    (
        "comparison",
        "link duplicate shared records",
        _set("items.1.possible_matches", [{**LINK, "shared_evidence_ids": ["rec-01", "rec-01"]}]),
    ),
    (
        "comparison",
        "link zero remaining",
        _set("items.1.possible_matches", [{**LINK, "remaining_sources": 0}]),
    ),
    (
        "comparison",
        "link count as string",
        _set("items.1.possible_matches", [{**LINK, "original_sources": "2"}]),
    ),
    (
        "comparison",
        "link count as bool",
        _set("items.1.possible_matches", [{**LINK, "remaining_sources": True}]),
    ),
    (
        "comparison",
        "link missing counts",
        _set("items.1.possible_matches", [_without("original_sources")]),
    ),
]


@pytest.mark.parametrize(
    ("fixture", "mutate"),
    [(f, m) for f, _, m in MUTATIONS],
    ids=[f"{f}: {d}" for f, d, _ in MUTATIONS],
)
def test_mutation_rejected_by_schema_and_model(
    fixture: str, mutate: Callable[[dict], None]
) -> None:
    contract = FIXTURE_CONTRACT[fixture]
    doc = deepcopy(RAW[fixture])
    mutate(doc)
    instance = doc[0] if isinstance(doc, list) else doc
    assert not schema_accepts(contract, instance), "JSON Schema unexpectedly accepted"
    assert not model_accepts(contract, instance), "Pydantic model unexpectedly accepted"


# Valid edge cases both must accept, so the model is not stricter than the schema by accident.
ACCEPTED: list[tuple[str, str, Callable[[dict], None]]] = [
    (
        "records",
        "minimal record",
        lambda d: [
            d[0].pop(k) for k in ("timestamp", "timestamp_kind", "original_sha256", "metadata")
        ],
    ),
    ("records", "null timestamp and sha", _set("0.timestamp", None)),
    ("records", "empty content", _set("0.content", "")),
    (
        "revision",
        "needs_review with errors",
        lambda d: d.update(needs_review=True, validation_errors=["claim-1 cites rec-99"]),
    ),
    ("revision", "parent_analysis_id omitted", _delete("parent_analysis_id")),
    ("revision", "node without evidence_ids", _delete("graph.nodes.0.evidence_ids")),
    ("revision", "event null timestamp", _set("timeline_events.0.timestamp", None)),
    ("revision", "claim may be unresolved", _set("claims.0.status", "unresolved")),
    ("revision", "offset created_at", _set("created_at", "2026-11-14T15:00:00-05:00")),
    ("board", "node item with status", _set("items.4.status", "observed")),
    ("board", "unresolved claim item", _set("items.2.status", "unresolved")),
    ("board", "first snapshot", lambda d: d.update(revision=1, parent_revision=None)),
    ("board", "no active analysis", _set("active_analysis_id", None)),
    ("board", "note target omitted", _delete("notes.1.target_item_id")),
    ("comparison", "no explanation", _delete("items.0.explanation")),
    ("comparison", "link on a base-only item", _set("items.1.possible_matches", [LINK])),
    (
        "comparison",
        "two links on a newly supported item",
        _append_comparison_item(
            kind="claim",
            base_ref_id=None,
            compared_ref_id="claim-x",
            support_status="newly_supported",
            base_evidence_ids=[],
            compared_evidence_ids=["rec-01"],
            possible_matches=[
                {**LINK, "ref_id": "claim-2"},
                {**LINK, "ref_id": "claim-3", "remaining_sources": 2, "original_sources": 3},
            ],
        ),
    ),
    (
        "comparison",
        "newly supported item",
        _append_comparison_item(
            kind="claim",
            base_ref_id=None,
            compared_ref_id="claim-1",
            support_status="newly_supported",
            base_evidence_ids=[],
            compared_evidence_ids=["rec-01"],
        ),
    ),
]


@pytest.mark.parametrize(
    ("fixture", "mutate"),
    [(f, m) for f, _, m in ACCEPTED],
    ids=[f"{f}: {d}" for f, d, _ in ACCEPTED],
)
def test_variant_accepted_by_schema_and_model(fixture: str, mutate: Callable[[dict], None]) -> None:
    contract = FIXTURE_CONTRACT[fixture]
    doc = deepcopy(RAW[fixture])
    mutate(doc)
    instance = doc[0] if isinstance(doc, list) else doc
    assert list(VALIDATORS[contract].iter_errors(instance)) == []
    model = SCHEMAS[contract][1].model_validate(instance)
    assert list(VALIDATORS[contract].iter_errors(to_contract_json(model))) == []


# --- Date-times: the models are deliberately stricter than JSON Schema ------------------


@pytest.mark.parametrize("value", ["2026-11-14T20:00:00", "last tuesday", "", "2026-13-01T00:00Z"])
@pytest.mark.parametrize(
    ("fixture", "field"),
    [
        ("revision", "created_at"),
        ("board", "created_at"),
        ("board", "updated_at"),
        ("board", "items.0.added_at"),
        ("board", "notes.0.created_at"),
        ("comparison", "created_at"),
    ],
)
def test_model_rejects_naive_or_unparseable_datetime(fixture: str, field: str, value: str) -> None:
    # JSON Schema does not enforce `format: date-time` by default; contracts/README requires
    # the server to parse it and reject naive values, so only the model rejects these.
    doc = deepcopy(RAW[fixture])
    _set(field, value)(doc)
    assert not model_accepts(FIXTURE_CONTRACT[fixture], doc)


def test_datetimes_parse_to_offset_aware_instants() -> None:
    board = InvestigationBoard.model_validate(RAW["board"])
    assert board.created_at == datetime(2026, 11, 14, 20, 1, tzinfo=UTC)
    node_item = board.items[4]
    assert node_item.added_at.utcoffset() == timedelta(hours=-5)
    assert node_item.added_at == datetime(2026, 11, 14, 20, 2, 30, tzinfo=UTC)


def test_model_rejects_naive_datetime_object() -> None:
    doc = deepcopy(RAW["comparison"])
    doc["created_at"] = datetime(2026, 11, 14, 20, 0)
    assert not model_accepts("comparison", doc)
    doc["created_at"] = datetime(2026, 11, 14, 15, 0, tzinfo=timezone(timedelta(hours=-5)))
    assert model_accepts("comparison", doc)


def test_parse_datetime() -> None:
    assert parse_datetime("2026-11-14T20:00:00Z") == parse_datetime("2026-11-14T15:00:00-05:00")
    assert parse_datetime("2026-11-14T20:00:00z") is not None
    assert parse_datetime("2026-11-14T20:00:00") is None
    assert parse_datetime("last tuesday") is None
    assert parse_datetime(None) is None
    assert parse_datetime(1763150400) is None


def test_model_validate_json_matches_python_validation() -> None:
    text = (FIXTURES_DIR / FIXTURE_FILES["board"]).read_text()
    assert InvestigationBoard.model_validate_json(text) == InvestigationBoard.model_validate(
        RAW["board"]
    )
