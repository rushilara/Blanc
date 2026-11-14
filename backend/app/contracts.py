"""Pydantic models for the shared Blanc data contracts.

These mirror the JSON Schemas in ``contracts/`` one to one (see ``contracts/README.md``):

- ``extra="forbid"`` mirrors ``additionalProperties: false``.
- ``strict=True`` mirrors JSON Schema typing: no silent ``"1"`` -> ``1`` or ``1`` -> ``"1"``
  coercion.
- Optional fields that the schema does not allow to be ``null`` may be absent but reject
  an explicit ``null``; fields typed ``["string", "null"]`` accept ``None``.
- ``*_at`` fields are parsed into offset-aware ``datetime`` values; naive or unparseable
  date-times are rejected (stricter than JSON Schema, which does not enforce ``format``).

``tests/test_contracts.py`` validates every fixture, and a set of mutated fixtures, through
both these models and the JSON Schemas so the two cannot drift apart silently.

Cross-reference rules that need more than one object (cited IDs exist, excluded records
are not cited, board items match their source revision, ...) live in ``app.validation``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

SchemaVersion = Literal["1.0"]

ClaimStatus = Literal["observed", "reported", "inferred", "unresolved"]
# Graph edges and timeline events cannot be "unresolved" (see analysis_revision.schema.json).
EdgeStatus = Literal["observed", "reported", "inferred"]
EventStatus = Literal["observed", "reported", "inferred"]
BoardItemStatus = Literal["observed", "reported", "inferred", "unresolved"]

NodeType = Literal["person", "account", "device", "location", "organization", "document", "other"]
RecordTimestampKind = Literal[
    "sent", "received", "created", "modified", "observed", "stated", "unknown"
]
TimestampPrecision = Literal["exact", "day", "month", "approximate", "unknown"]
EventTimestampKind = Literal["observed", "source_stated", "inferred", "unknown"]
BoardItemKind = Literal["claim", "edge", "node", "event"]
ComparisonItemKind = Literal["claim", "edge", "event"]
SupportStatus = Literal[
    "still_supported", "weakened", "not_supported", "newly_supported", "unresolved"
]


def parse_datetime(value: object) -> datetime | None:
    """Parse an ISO-8601 date-time with a UTC offset (``Z`` or ``+HH:MM``/``-HH:MM``).

    Returns ``None`` if the value is not a string, is unparseable, or is naive (no offset),
    so that callers only ever compare offset-aware instants.
    """
    if not isinstance(value, str):
        return None
    text = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _offset_datetime(value: object) -> object:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("date-time must include a UTC offset ('Z' or +HH:MM)")
        return value
    parsed = parse_datetime(value)
    if parsed is None:
        raise ValueError(
            f"must be an ISO-8601 date-time with a UTC offset ('Z' or +HH:MM), got {value!r}"
        )
    return parsed


def _unique(values: list[str]) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError("items must be unique")
    return values


OffsetDateTime = Annotated[datetime, BeforeValidator(_offset_datetime)]
NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
UniqueIds = Annotated[list[NonEmptyStr], AfterValidator(_unique)]
EvidenceIds = Annotated[list[NonEmptyStr], Field(min_length=1)]
UniqueEvidenceIds = Annotated[list[NonEmptyStr], Field(min_length=1), AfterValidator(_unique)]


def _reject_null(cls: type, value: object) -> object:
    """Reusable before-validator: the field may be omitted, but not sent as null."""
    if value is None:
        raise ValueError("may be omitted but must not be null")
    return value


class ContractModel(BaseModel):
    """Base for all contract models: no unknown fields, no type coercion."""

    model_config = ConfigDict(extra="forbid", strict=True)


# --- EvidenceRecord -------------------------------------------------------------------


class EvidenceRecord(ContractModel):
    """One immutable, traceable source record (evidence_record.schema.json)."""

    record_id: NonEmptyStr
    case_id: NonEmptyStr
    source_file: NonEmptyStr
    source_locator: NonEmptyStr
    content: str
    # Evidence timestamps may be partial or approximate, so they stay strings.
    timestamp: str | None = None
    timestamp_kind: RecordTimestampKind | None = None
    original_sha256: str | None = None
    metadata: dict[str, Any] | None = None

    _not_null = field_validator("timestamp_kind", "metadata", mode="before")(_reject_null)


# --- AnalysisRevision -----------------------------------------------------------------


class Claim(ContractModel):
    id: NonEmptyStr
    text: NonEmptyStr
    evidence_ids: EvidenceIds
    status: ClaimStatus


class GraphNode(ContractModel):
    id: NonEmptyStr
    label: NonEmptyStr
    type: NodeType
    evidence_ids: list[NonEmptyStr] | None = None

    _not_null = field_validator("evidence_ids", mode="before")(_reject_null)


class GraphEdge(ContractModel):
    id: NonEmptyStr
    source: NonEmptyStr
    target: NonEmptyStr
    type: NonEmptyStr
    evidence_ids: EvidenceIds
    status: EdgeStatus
    description: str | None = None

    _not_null = field_validator("description", mode="before")(_reject_null)


class Graph(ContractModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class TimelineEvent(ContractModel):
    id: NonEmptyStr
    description: NonEmptyStr
    # Source-derived and possibly partial, so not parsed as a date-time.
    timestamp: str | None
    timestamp_precision: TimestampPrecision | None = None
    timestamp_kind: EventTimestampKind | None = None
    evidence_ids: EvidenceIds
    status: EventStatus

    _not_null = field_validator("timestamp_precision", "timestamp_kind", mode="before")(
        _reject_null
    )


class AnalysisRevision(ContractModel):
    """One immutable, question-specific reconstruction (analysis_revision.schema.json)."""

    schema_version: SchemaVersion
    analysis_id: NonEmptyStr
    parent_analysis_id: str | None = None
    case_id: NonEmptyStr
    question: NonEmptyStr
    excluded_record_ids: UniqueIds
    retrieved_record_ids: UniqueIds
    answer: str
    claims: list[Claim]
    graph: Graph
    timeline_events: list[TimelineEvent]
    unresolved_questions: list[str]
    needs_review: bool | None = None
    validation_errors: list[NonEmptyStr] | None = None
    created_at: OffsetDateTime

    _not_null = field_validator("needs_review", "validation_errors", mode="before")(_reject_null)


# --- InvestigationBoard ---------------------------------------------------------------


class BoardItem(ContractModel):
    """A pinned finding. Pinning never asserts the finding is verified."""

    item_id: NonEmptyStr
    kind: BoardItemKind
    source_analysis_id: NonEmptyStr
    ref_id: NonEmptyStr
    evidence_ids: UniqueEvidenceIds
    status: BoardItemStatus | None = None
    added_at: OffsetDateTime

    _not_null = field_validator("status", mode="before")(_reject_null)

    @model_validator(mode="after")
    def _status_required_unless_node(self) -> BoardItem:
        if self.kind != "node" and self.status is None:
            raise ValueError(f"status is required for {self.kind} items")
        return self


class BoardNote(ContractModel):
    """Investigator-authored note; never passed to the model as evidence."""

    note_id: NonEmptyStr
    text: NonEmptyStr
    target_item_id: str | None = None
    author: NonEmptyStr
    created_at: OffsetDateTime


class InvestigationBoard(ContractModel):
    """One append-only board snapshot (investigation_board.schema.json)."""

    schema_version: SchemaVersion
    board_id: NonEmptyStr
    case_id: NonEmptyStr
    title: NonEmptyStr
    revision: Annotated[int, Field(ge=1)]
    parent_revision: Annotated[int, Field(ge=1)] | None = None
    active_analysis_id: str | None
    items: list[BoardItem]
    notes: list[BoardNote]
    created_at: OffsetDateTime
    updated_at: OffsetDateTime


# --- RevisionComparison ---------------------------------------------------------------


class ComparisonItem(ContractModel):
    kind: ComparisonItemKind
    base_ref_id: str | None
    compared_ref_id: str | None
    support_status: SupportStatus
    base_evidence_ids: UniqueIds
    compared_evidence_ids: UniqueIds
    explanation: str | None = None

    _not_null = field_validator("explanation", mode="before")(_reject_null)

    @model_validator(mode="after")
    def _null_ref_means_no_evidence(self) -> ComparisonItem:
        if self.base_ref_id is None and self.base_evidence_ids:
            raise ValueError("base_evidence_ids must be empty when base_ref_id is null")
        if self.compared_ref_id is None and self.compared_evidence_ids:
            raise ValueError("compared_evidence_ids must be empty when compared_ref_id is null")
        return self


class RevisionComparison(ContractModel):
    """Support changes between a base revision and its exclusion child
    (revision_comparison.schema.json)."""

    schema_version: SchemaVersion
    case_id: NonEmptyStr
    base_analysis_id: NonEmptyStr
    compared_analysis_id: NonEmptyStr
    excluded_record_ids: UniqueIds
    items: list[ComparisonItem]
    created_at: OffsetDateTime
