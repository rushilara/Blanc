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
from typing import Annotated, Any, Literal, TypeVar

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

T = TypeVar("T")

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
RecordModality = Literal["text", "image", "pdf"]
MediaContentType = Literal["image/png", "image/jpeg", "application/pdf"]
MediaPath = Annotated[
    str, StringConstraints(pattern=r"^media/rec_[0-9a-f]{16}\.(png|jpg|pdf)$")
]
TimestampPrecision = Literal["exact", "day", "month", "approximate", "unknown"]
EventTimestampKind = Literal["observed", "source_stated", "inferred", "unknown"]
BoardItemKind = Literal["claim", "edge", "node", "event"]
# Investigator judgement on a pinned finding (BLA-48); never changes the finding's status.
ReviewStatus = Literal["unreviewed", "reviewed", "disputed"]
BoardNoteKind = Literal["note", "correction"]
ComparisonItemKind = Literal["claim", "edge", "event"]
EvidenceWindow = Literal["standard", "expanded"]
# Image/PDF records in the window that were not sent to the model (all need a modality):
# "media_limit": over a media cap (RETRIEVAL_MAX_MEDIA, ANALYSIS_MAX_IMAGES / _PDFS, or the
# total ANALYSIS_MAX_MEDIA_BYTES) — a larger cap or a narrower question could include it;
# "media_too_large": the one file is over ANALYSIS_MAX_MEDIA_FILE_BYTES, so it cannot be
# sent inline at all (BLA-71); "media": not sent for another reason (the stored file is
# missing or fails its integrity check), and on revisions saved before vision analysis,
# any retrieved media (BLA-70).
SkipReason = Literal["budget", "record_limit", "media", "media_limit", "media_too_large"]
MEDIA_SKIP_REASONS: frozenset[str] = frozenset({"media", "media_limit", "media_too_large"})
MediaModality = Literal["image", "pdf"]
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
Rank = Annotated[int, Field(ge=1)]
UniqueEvidenceIds = Annotated[list[NonEmptyStr], Field(min_length=1), AfterValidator(_unique)]

# Suggested follow-up questions (BLA-54): model-generated, shown to the investigator, never
# findings or evidence. ``app.analysis_llm.sanitize_followups`` trims model output to fit.
MAX_SUGGESTED_FOLLOWUPS = 3
MAX_FOLLOWUP_CHARS = 300
FollowupText = Annotated[
    str, StringConstraints(min_length=1, max_length=MAX_FOLLOWUP_CHARS, pattern=r"\S")
]
SuggestedFollowups = Annotated[
    list[FollowupText],
    Field(min_length=1, max_length=MAX_SUGGESTED_FOLLOWUPS),
    AfterValidator(_unique),
]


def _reject_null(value: object) -> object:
    if value is None:
        raise ValueError("may be omitted but must not be null")
    return value


# Marks a field the schema lets you omit but not set to null. Before-validators only run
# on supplied values, so omitting the field (default None) is fine; an explicit null fails.
# ``to_contract_json`` uses this marker to drop such fields when they are None.
_OMIT_IF_NONE = BeforeValidator(_reject_null)
Omittable = Annotated[T | None, _OMIT_IF_NONE]


class ContractModel(BaseModel):
    """Base for all contract models: no unknown fields, no type coercion."""

    model_config = ConfigDict(extra="forbid", strict=True)

    @classmethod
    def _omit_if_none_fields(cls) -> frozenset[str]:
        return frozenset(
            name for name, field in cls.model_fields.items() if _OMIT_IF_NONE in field.metadata
        )

    def to_contract_json(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict that validates against the shared schema.

        Use this (or ``response_model_exclude_unset=True`` on the route) whenever a contract
        model leaves the server. Plain ``model_dump()`` is NOT schema-valid: it writes
        omittable fields such as an edge ``description`` or a node board item's ``status``
        as explicit ``null``, which ``contracts/*.schema.json`` rejects. This method drops
        those fields when they are None (at every nesting level) and keeps the fields the
        schema allows to be null (``parent_analysis_id: null``, ``base_ref_id: null``, ...).
        """
        data = self.model_dump(mode="json")
        _drop_omitted_nones(self, data)
        return data


def to_contract_json(model: ContractModel) -> dict[str, Any]:
    """Schema-valid JSON dict for a contract model. See ``ContractModel.to_contract_json``."""
    return model.to_contract_json()


def _drop_omitted_nones(model: ContractModel, data: dict[str, Any]) -> None:
    omit = model._omit_if_none_fields()
    for name in type(model).model_fields:
        value = getattr(model, name)
        if value is None and name in omit:
            data.pop(name, None)
        elif isinstance(value, ContractModel):
            _drop_omitted_nones(value, data[name])
        elif isinstance(value, list):
            for item, item_data in zip(value, data[name], strict=True):
                if isinstance(item, ContractModel):
                    _drop_omitted_nones(item, item_data)


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
    timestamp_kind: Omittable[RecordTimestampKind] = None
    original_sha256: str | None = None
    # Media records (BLA-68): bytes live on disk under the case; these fields describe them.
    # Text records omit them. ``content`` for media is empty until multimodal embedding.
    modality: Omittable[RecordModality] = None
    media_path: Omittable[MediaPath] = None
    content_type: Omittable[MediaContentType] = None
    metadata: Omittable[dict[str, Any]] = None

    @model_validator(mode="after")
    def _media_fields_required_together(self) -> EvidenceRecord:
        if self.modality in ("image", "pdf") and (
            self.media_path is None or self.content_type is None
        ):
            raise ValueError(
                "media_path and content_type are required when modality is image or pdf"
            )
        return self


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
    evidence_ids: Omittable[list[NonEmptyStr]] = None


class GraphEdge(ContractModel):
    id: NonEmptyStr
    source: NonEmptyStr
    target: NonEmptyStr
    type: NonEmptyStr
    evidence_ids: EvidenceIds
    status: EdgeStatus
    description: Omittable[str] = None


class Graph(ContractModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class TimelineEvent(ContractModel):
    id: NonEmptyStr
    description: NonEmptyStr
    # Source-derived and possibly partial, so not parsed as a date-time.
    timestamp: str | None
    timestamp_precision: Omittable[TimestampPrecision] = None
    timestamp_kind: Omittable[EventTimestampKind] = None
    evidence_ids: EvidenceIds
    status: EventStatus


class SkippedEvidence(ContractModel):
    """A record retrieval ranked but left out of the evidence window: an ID, never content."""

    record_id: NonEmptyStr
    rank: Rank  # fused rank among all candidates
    reason: SkipReason
    vector_rank: Omittable[Rank] = None  # rank in the vector search alone, if it found it
    keyword_rank: Omittable[Rank] = None  # rank in the keyword search alone, if it found it
    modality: Omittable[MediaModality] = None  # set for image/PDF records; absent = text


class RetrievalCoverage(ContractModel):
    """What the evidence window held back (BLA-64). Server-assigned, never LLM-produced."""

    evidence_window: EvidenceWindow
    max_records: Rank
    max_excerpt_chars: Rank
    skipped: list[SkippedEvidence]
    budget_substituted: bool
    # record_limit skips that the vector or keyword search alone ranked within max_records
    left_out_record_ids: UniqueIds
    # standard window and at least one left-out record: the UI may offer the expanded window
    offer_expanded_window: bool


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
    # Model-generated questions for the investigator to choose from (BLA-54). Not findings,
    # never cited, never run automatically. Omitted when there are none.
    suggested_followups: Omittable[SuggestedFollowups] = None
    needs_review: Omittable[bool] = None
    validation_errors: Omittable[list[NonEmptyStr]] = None
    retrieval_coverage: Omittable[RetrievalCoverage] = None
    created_at: OffsetDateTime


# --- InvestigationBoard ---------------------------------------------------------------


class BoardItem(ContractModel):
    """A pinned finding. Pinning never asserts the finding is verified."""

    item_id: NonEmptyStr
    kind: BoardItemKind
    source_analysis_id: NonEmptyStr
    ref_id: NonEmptyStr
    evidence_ids: UniqueEvidenceIds
    status: Omittable[BoardItemStatus] = None
    # Investigator review (BLA-48). Absent means unreviewed. Separate from ``status``,
    # which is the model's label copied from the source revision.
    review_status: Omittable[ReviewStatus] = None
    added_at: OffsetDateTime

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
    # "correction" flags a pinned finding as wrong and says why (BLA-48); absent = "note".
    kind: Omittable[BoardNoteKind] = None
    author: NonEmptyStr
    created_at: OffsetDateTime

    @model_validator(mode="after")
    def _correction_targets_an_item(self) -> BoardNote:
        if self.kind == "correction" and self.target_item_id is None:
            raise ValueError("a correction must target a board item")
        return self


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


class PossibleMatch(ContractModel):
    """An advisory link to a finding on the other side of a comparison (BLA-63).

    Server-computed from citations, never LLM-generated, and never a change of
    ``support_status``: it only says the two are *possibly* the same finding, for the
    investigator to confirm. Count consistency (``remaining_sources`` <= ``original_sources``
    and so on) needs the other item, so it is checked in ``app.validation``.
    """

    ref_id: NonEmptyStr
    shared_evidence_ids: UniqueEvidenceIds
    remaining_sources: Annotated[int, Field(ge=1)]
    original_sources: Annotated[int, Field(ge=1)]


class ComparisonItem(ContractModel):
    kind: ComparisonItemKind
    base_ref_id: str | None
    compared_ref_id: str | None
    support_status: SupportStatus
    base_evidence_ids: UniqueIds
    compared_evidence_ids: UniqueIds
    explanation: Omittable[str] = None
    # On a base-only item each ref_id is a compared_ref_id; on a newly supported item each
    # is a base_ref_id. Omitted (not empty, not null) when there are none.
    possible_matches: Omittable[Annotated[list[PossibleMatch], Field(min_length=1)]] = None

    @model_validator(mode="after")
    def _null_ref_means_no_evidence(self) -> ComparisonItem:
        if self.base_ref_id is None and self.base_evidence_ids:
            raise ValueError("base_evidence_ids must be empty when base_ref_id is null")
        if self.compared_ref_id is None and self.compared_evidence_ids:
            raise ValueError("compared_evidence_ids must be empty when compared_ref_id is null")
        if self.possible_matches is not None and None not in (
            self.base_ref_id,
            self.compared_ref_id,
        ):
            raise ValueError("possible_matches is only for items with one ref ID null")
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
